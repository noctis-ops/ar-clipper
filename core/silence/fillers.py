"""كشف كلمات الحشو وحذفها — الفجوة الأولى مقابل الأدوات التجارية.

«أمم»، «يعني»، «آه»، «uh»، «you know» — تملأ كلام البودكاست الطبيعي وتجعل
المقطع القصير يبدو غير محترف. حذف الصمت وحده لا يمسّها لأنها **كلام** له
طاقة صوتية.

المنهج: نعتمد على توقيت الكلمات الموجود أصلاً في الترانسكربت
(WhisperX/faster-whisper يعطيان `Word.start/end`)، فلا حاجة لأي نموذج
إضافي ولا تنزيل. صفر تبعيات، صفر تكلفة.

الحذر مقصود: العربية تستخدم «يعني» و«طبعاً» كأدوات ربط حقيقية أحياناً.
لذلك نحذف فقط ما كان **منعزلاً** (محاطاً بوقفة أو في بداية الجملة) ولا
نتجاوز نسبة معيّنة من الكلمات حتى لا نمزّق الكلام.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..common.config import Settings, load_settings
from ..common.logging_utils import get_logger
from ..common.schemas import Transcript
from ..common.text_utils import normalize_text

log = get_logger(__name__)

Range = Tuple[float, float]


# كلمات الحشو العربية. ملاحظة: «يعني» و«طبعا» و«المهم» تُستخدم أحياناً
# استخداماً حقيقياً، لذا تُحذف فقط حين تكون منعزلة (انظر ``_is_isolated``).
FILLERS_AR = {
    # أصوات تردد خالصة — تُحذف دائماً
    "امم", "اممم", "ام", "اه", "اهه", "ااه", "ههه", "مم", "ممم",
    "ايه", "ااا", "عفوا",
    # أدوات حشو سياقية — تُحذف حين تنعزل فقط
    "يعني", "طبعا", "المهم", "بصراحه", "بصراحة", "الصراحه", "الصراحة",
    "وهكذا", "اوكي", "اوك", "خلاص", "تمام", "شوف", "شف",
}

# تُحذف دائماً، حتى لو لم تنعزل — لأنها ليست كلمات حقيقية
ALWAYS_FILLER_AR = {
    "امم", "اممم", "ام", "اه", "اهه", "ااه", "مم", "ممم", "ااا",
}

FILLERS_EN = {
    "uh", "um", "umm", "uhh", "er", "erm", "ah", "hmm", "mmm",
    "like", "actually", "basically", "literally", "right",
    "okay", "ok", "so", "well", "anyway",
}

ALWAYS_FILLER_EN = {"uh", "um", "umm", "uhh", "er", "erm", "hmm", "mmm"}

# عبارات من كلمتين أو أكثر
FILLER_PHRASES_EN = [
    ("you", "know"),
    ("i", "mean"),
    ("sort", "of"),
    ("kind", "of"),
]

FILLER_PHRASES_AR = [
    ("ان", "شاء", "الله"),  # لا تُحذف — مثال توضيحي، تُترك فارغة عمداً
]

# ``\w`` في Python يشمل الترقيم العربي (، ؛ ؟) لأنه ضمن نطاق Arabic،
# فلا بد من استثنائه صراحةً وإلا التصق بالكلمة وأفسد المطابقة.
_ARABIC_PUNCT = "\u060C\u061B\u061F\u066A-\u066D\u06D4"
_STRIP = re.compile(rf"[^\w\u0600-\u06FF]+|[{_ARABIC_PUNCT}]+")
_TATWEEL = re.compile(r"[\u0640\u064B-\u0652]")


def normalize_token(text: str) -> str:
    """تطبيع كلمة للمقارنة: بلا تشكيل ولا تطويل ولا ترقيم."""
    text = normalize_text(text or "").strip().lower()
    text = _TATWEEL.sub("", text)
    text = _STRIP.sub("", text)
    # توحيد الألف والهاء/التاء المربوطة
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه").replace("ى", "ي")
    return text


@dataclass
class FillerHit:
    """كلمة حشو مكتشفة."""

    text: str
    start: float
    end: float
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class FillerReport:
    """نتيجة الكشف."""

    hits: List[FillerHit] = field(default_factory=list)
    total_words: int = 0

    @property
    def count(self) -> int:
        return len(self.hits)

    @property
    def removed_seconds(self) -> float:
        return sum(h.duration for h in self.hits)

    @property
    def ratio(self) -> float:
        return (self.count / self.total_words) if self.total_words else 0.0

    def cut_ranges(self, *, pad: float = 0.02) -> List[Range]:
        """المدَيات المطلوب حذفها، مدموجة ومرتّبة."""
        if not self.hits:
            return []
        raw = sorted(
            (max(0.0, h.start - pad), h.end + pad) for h in self.hits
        )
        merged: List[Range] = [raw[0]]
        for start, end in raw[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end + 0.05:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        return merged


def _is_isolated(
    words: Sequence, index: int, *, gap: float = 0.18
) -> bool:
    """هل الكلمة محاطة بوقفة (أو في طرف الجملة)؟

    «يعني» بين كلمتين متلاصقتين غالباً أداة ربط حقيقية؛ أما المسبوقة
    بوقفة والمتبوعة بوقفة فهي تردد.
    """
    word = words[index]
    before_gap = True
    after_gap = True

    if index > 0:
        prev = words[index - 1]
        before_gap = (float(word.start) - float(prev.end)) >= gap
    if index < len(words) - 1:
        nxt = words[index + 1]
        after_gap = (float(nxt.start) - float(word.end)) >= gap

    return before_gap or after_gap


def detect_fillers(
    transcript: Transcript,
    *,
    settings: Optional[Settings] = None,
    language: Optional[str] = None,
) -> FillerReport:
    """يكشف كلمات الحشو في ترانسكربت ذي توقيت كلمات."""
    settings = settings or load_settings()
    report = FillerReport()

    if not settings.get("fillers.enabled", True):
        return report

    max_ratio = float(settings.get("fillers.max_ratio", 0.12))
    gap = float(settings.get("fillers.isolation_gap", 0.18))
    max_duration = float(settings.get("fillers.max_word_duration", 1.2))

    extra = {
        normalize_token(w)
        for w in (settings.get("fillers.extra_words", []) or [])
    }
    keep = {
        normalize_token(w)
        for w in (settings.get("fillers.keep_words", []) or [])
    }

    lang = (language or transcript.language or "").lower()
    if lang.startswith("ar"):
        candidates, always = FILLERS_AR, ALWAYS_FILLER_AR
        phrases = []
    elif lang.startswith("en"):
        candidates, always = FILLERS_EN, ALWAYS_FILLER_EN
        phrases = FILLER_PHRASES_EN
    else:  # لغة غير معروفة: الأصوات الخالصة فقط في اللغتين
        candidates = ALWAYS_FILLER_AR | ALWAYS_FILLER_EN
        always = candidates
        phrases = []

    candidates = (candidates | extra) - keep
    always = (always | extra) - keep

    for segment in transcript.segments:
        words = list(getattr(segment, "words", []) or [])
        if not words:
            continue
        report.total_words += len(words)

        skip_until = -1
        for index, word in enumerate(words):
            if index < skip_until:
                continue

            token = normalize_token(getattr(word, "text", ""))
            if not token:
                continue

            # عبارات متعددة الكلمات أولاً (أطول تطابق)
            matched_phrase = None
            for phrase in phrases:
                if not phrase:
                    continue
                span = words[index : index + len(phrase)]
                if len(span) != len(phrase):
                    continue
                if all(
                    normalize_token(getattr(w, "text", "")) == part
                    for w, part in zip(span, phrase)
                ):
                    matched_phrase = span
                    break

            if matched_phrase:
                report.hits.append(
                    FillerHit(
                        text=" ".join(getattr(w, "text", "") for w in matched_phrase),
                        start=float(matched_phrase[0].start),
                        end=float(matched_phrase[-1].end),
                        reason="phrase",
                    )
                )
                skip_until = index + len(matched_phrase)
                continue

            if token not in candidates:
                continue

            # كلمة طويلة غالباً ليست حشواً (خطأ تفريغ أو كلمة حقيقية)
            if (float(word.end) - float(word.start)) > max_duration:
                continue

            if token in always or _is_isolated(words, index, gap=gap):
                report.hits.append(
                    FillerHit(
                        text=getattr(word, "text", ""),
                        start=float(word.start),
                        end=float(word.end),
                        reason="always" if token in always else "isolated",
                    )
                )

    # حارس أمان: نسبة عالية تعني خطأ في الكشف لا كلاماً مليئاً بالحشو.
    # لا يُطبَّق على العيّنات الصغيرة لأن كلمتين من عشر = 20% وهو طبيعي
    # تماماً في جملة قصيرة.
    min_words = int(settings.get("fillers.ratio_guard_min_words", 40))
    if report.total_words >= min_words and report.ratio > max_ratio:
        log.warning(
            "نسبة كلمات الحشو %.0f%% تتجاوز الحد %.0f%% — سيُتخطّى الحذف "
            "تفادياً لتمزيق الكلام.",
            report.ratio * 100,
            max_ratio * 100,
        )
        return FillerReport(hits=[], total_words=report.total_words)

    if report.hits:
        log.info(
            "كلمات الحشو: %d كلمة (%.1fs، %.1f%% من الكلام).",
            report.count,
            report.removed_seconds,
            report.ratio * 100,
        )
    return report


def keep_ranges_from_cuts(
    cuts: Sequence[Range], *, duration: float, min_keep: float = 0.08
) -> List[Range]:
    """يحوّل مدَيات الحذف إلى مدَيات الإبقاء (ما يحتاجه ffmpeg فعلياً)."""
    keeps: List[Range] = []
    cursor = 0.0
    for start, end in sorted(cuts):
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if start - cursor >= min_keep:
            keeps.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= min_keep:
        keeps.append((cursor, duration))
    return keeps
