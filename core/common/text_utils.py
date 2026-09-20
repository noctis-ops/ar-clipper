"""أدوات نصية مشتركة: تنظيف، كشف العربية، لف الأسطر، معالجة RTL.

ملاحظة مهمة حول العربية في ملفات ASS/SRT:
libass (المستخدم داخل ffmpeg) يتعامل مع الاتجاه والتشكيل عبر FriBidi/HarfBuzz،
لذلك نكتب النص العربي **منطقياً** (Logical order) كما هو ولا نعكسه يدوياً.
كل ما نفعله هو إضافة علامة الاتجاه (RLM/RLE) لضمان بداية السطر من اليمين
عند اختلاط الأرقام والحروف اللاتينية بالنص العربي.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List

RLM = "\u200f"  # Right-to-Left Mark
LRM = "\u200e"  # Left-to-Right Mark

_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

_WS_RE = re.compile(r"[ \t\u00a0]+")
_MULTI_NL_RE = re.compile(r"\n{2,}")


def is_arabic_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _ARABIC_RANGES)


def contains_arabic(text: str) -> bool:
    return any(is_arabic_char(c) for c in text)


def is_rtl_text(text: str, threshold: float = 0.2) -> bool:
    """يعتبر النص عربياً إذا تجاوزت نسبة الحروف العربية العتبة."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    arabic = sum(1 for c in letters if is_arabic_char(c))
    return (arabic / len(letters)) >= threshold


def normalize_text(text: str) -> str:
    """تطبيع Unicode + تنظيف المسافات الزائدة."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n", text)
    return text.strip()


def strip_tatweel(text: str) -> str:
    """يحذف التطويل (ـ) الذي يُفسد قياس طول السطر."""
    return text.replace("\u0640", "")


def wrap_text(text: str, max_chars: int = 38, max_lines: int = 2) -> str:
    """يلفّ النص على أسطر بحد أقصى للأحرف، مع محاولة موازنة أطوال الأسطر.

    إذا تجاوز عدد الأسطر ``max_lines`` نعيد التوزيع بالتساوي بدل القصّ،
    حتى لا نفقد كلمات من الترجمة.
    """
    text = normalize_text(text)
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    words = text.split()
    if not words:
        return text

    # توزيع متوازن: نحسب عدد الأسطر المطلوب ثم نوزّع بالعرض المستهدف
    n_lines = max(1, min(max_lines, -(-len(text) // max_chars)))
    target = max(1, -(-len(text) // n_lines))

    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > target and len(lines) < n_lines - 1:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    return "\n".join(lines)


def apply_rtl_marks(text: str, enabled: bool = True) -> str:
    """يضيف RLM في بداية كل سطر عربي لضبط اتجاه العرض في libass."""
    if not enabled or not text:
        return text
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line and contains_arabic(line) and not line.startswith(RLM):
            line = RLM + line
        out.append(line)
    return "\n".join(out)


def prepare_subtitle_text(
    text: str,
    *,
    max_chars: int = 38,
    max_lines: int = 2,
    rtl: bool = True,
) -> str:
    """التحضير الكامل لنص سيُعرض كترجمة على الشاشة."""
    cleaned = strip_tatweel(normalize_text(text))
    wrapped = wrap_text(cleaned, max_chars=max_chars, max_lines=max_lines)
    return apply_rtl_marks(wrapped, enabled=rtl and contains_arabic(wrapped))


# علامات ترقيم عربية لا يجوز بقاؤها في أسماء الملفات (داخل نطاق العربية)
_ARABIC_PUNCT = "،؛؟٪«»ـٰ۔٫٬"


def slugify(text: str, max_len: int = 60, fallback: str = "clip") -> str:
    """اسم ملف آمن: يبقي على العربية واللاتينية والأرقام فقط."""
    text = normalize_text(text)
    out = []
    for ch in text:
        if ch in _ARABIC_PUNCT:
            out.append("-")
        elif ch.isalnum() or is_arabic_char(ch):
            out.append(ch)
        elif ch in " -_.":
            out.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(out)).strip("-._")
    slug = slug[:max_len].strip("-._")
    return slug or fallback


def format_timestamp(seconds: float, *, sep: str = ",", hours_digits: int = 2) -> str:
    """تنسيق SRT/VTT: ``HH:MM:SS,mmm`` أو ``HH:MM:SS.mmm``."""
    seconds = max(0.0, float(seconds))
    ms_total = int(round(seconds * 1000))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:0{hours_digits}d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def parse_timestamp(value: str | float | int) -> float:
    """يحلّل توقيتاً مرناً: ``90`` أو ``1:30`` أو ``00:01:30.5`` → ثوانٍ."""
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace(",", ".")
    if not raw:
        raise ValueError("توقيت فارغ")
    parts = raw.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"صيغة توقيت غير صالحة: {value}") from exc
    if len(nums) == 1:
        total = nums[0]
    elif len(nums) == 2:
        total = nums[0] * 60 + nums[1]
    elif len(nums) == 3:
        total = nums[0] * 3600 + nums[1] * 60 + nums[2]
    else:
        raise ValueError(f"صيغة توقيت غير صالحة: {value}")
    if total < 0:
        raise ValueError(f"التوقيت لا يمكن أن يكون سالباً: {value}")
    return total


def human_duration(seconds: float) -> str:
    """مدة مقروءة للبشر: ``1:23`` أو ``1:02:03``."""
    seconds = int(max(0, round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
