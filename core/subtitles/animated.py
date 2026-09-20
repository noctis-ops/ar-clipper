"""ترجمة متحركة بأسلوب الكاريوكي — المرحلة 3، الخطوة 2.

المرحلة 1 كتبت ASS ثابتاً: سطر يظهر ويختفي. هنا نضيف ما يرفع نسبة الاحتفاظ
بالمشاهد في المقاطع القصيرة:

1. **تلوين الكلمة أثناء نطقها** — عبر وسم ``\\k`` القياسي في ASS، الذي يحسبه
   libass بنفسه اعتماداً على التوقيت على مستوى الكلمة (المتوفر أصلاً من WhisperX).
2. **إبراز الكلمات المهمة** بلون مختلف — تُختار بمنطق محلي (طول الكلمة،
   الأرقام، ندرتها) بلا أي نموذج ولا خدمة مدفوعة.
3. **حركة ظهور بسيطة** (Pop-in) لكل سطر عبر ``\\fad`` و``\\t``.

كل ذلك نصّ عادي داخل ملف ASS: لا تبعية جديدة، ولا تمريرة ترميز إضافية — نفس
محطة الحرق القائمة تتكفّل بالباقي.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..common.config import Settings, load_settings
from ..common.logging_utils import get_logger
from ..common.schemas import Segment, Transcript, Word

log = get_logger(__name__)

# كلمات وظيفية عربية شائعة لا تصلح للإبراز (حروف جر، ضمائر، أدوات)
ARABIC_STOPWORDS: Set[str] = {
    "في", "من", "على", "إلى", "عن", "مع", "هذا", "هذه", "ذلك", "التي", "الذي",
    "أن", "إن", "كان", "كانت", "يكون", "قد", "لقد", "ما", "لا", "لم", "لن",
    "هو", "هي", "هم", "نحن", "أنا", "أنت", "كل", "بعض", "أو", "ثم", "حتى",
    "لكن", "بل", "أي", "كما", "عند", "بين", "بعد", "قبل", "كذلك", "أيضا",
    "يا", "و", "ف", "ب", "ل", "ك", "الى", "هناك", "هنا", "شيء", "غير",
}

_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_NON_WORD = re.compile(r"[^\w\u0600-\u06FF]+")


def normalize_word(text: str) -> str:
    """يجرّد الكلمة من التشكيل وعلامات الترقيم لمقارنتها بصدق."""
    cleaned = _DIACRITICS.sub("", text or "")
    cleaned = _NON_WORD.sub("", cleaned)
    return cleaned.strip()


def pick_keywords(
    words: Sequence[str], *, max_ratio: float = 0.25, min_len: int = 4
) -> Set[str]:
    """يختار الكلمات الجديرة بالإبراز — منطق محلي بلا نموذج.

    المعايير: ليست كلمة وظيفية، طولها معقول، والأرقام تُبرَز دائماً لأنها
    عادةً بيت القصيد في المقاطع القصيرة.
    """
    scored: List[Tuple[float, str]] = []
    seen: Set[str] = set()

    for raw in words:
        norm = normalize_word(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        if norm in ARABIC_STOPWORDS:
            continue

        score = 0.0
        if any(ch.isdigit() for ch in norm):
            score += 3.0  # الأرقام هي الخلاصة عادةً
        if len(norm) >= min_len:
            score += len(norm) / 4.0
        else:
            continue
        scored.append((score, norm))

    if not scored:
        return set()
    scored.sort(reverse=True)
    limit = max(1, int(len(scored) * max_ratio))
    return {word for _, word in scored[:limit]}


def _centiseconds(value: float) -> int:
    """ASS يقيس مدد الكاريوكي بالسنتي ثانية."""
    return max(1, int(round(value * 100)))


def build_karaoke_text(
    segment: Segment,
    *,
    keywords: Optional[Set[str]] = None,
    highlight_color: str = "&H0000D7FF",
    base_color: Optional[str] = None,
    rtl: bool = True,
) -> str:
    """يحوّل مقطعاً إلى نص ASS بوسوم ``\\k`` لكل كلمة.

    عند غياب توقيت الكلمات نوزّع المدة بالتساوي: الحركة تبقى مقبولة بصرياً
    وأفضل من سطر جامد، والدقة الحقيقية تأتي من WhisperX حين يتوفر.
    """
    keywords = keywords or set()
    words: List[Word] = list(segment.words or [])

    if not words:
        parts = [w for w in (segment.text or "").split() if w]
        if not parts:
            return ""
        share = max(0.08, segment.duration / len(parts))
        words = [
            Word(text=part, start=segment.start + i * share, end=segment.start + (i + 1) * share)
            for i, part in enumerate(parts)
        ]

    chunks: List[str] = []
    for index, word in enumerate(words):
        duration = word.end - word.start
        if duration <= 0:
            duration = 0.12
        # الفجوة قبل الكلمة تُضاف إليها حتى يبقى التوقيت متزامناً مع الصوت
        if index > 0:
            gap = word.start - words[index - 1].end
            if gap > 0:
                duration += gap

        text = (word.text or "").strip()
        if not text:
            continue

        tag = f"\\k{_centiseconds(duration)}"
        if normalize_word(text) in keywords:
            # الكلمة المهمة: لون مميز يعود بعدها للون الأساسي
            reset = f"{{\\c{base_color}}}" if base_color else "{\\c}"
            chunks.append(f"{{{tag}\\c{highlight_color}}}{text}{reset}")
        else:
            chunks.append(f"{{{tag}}}{text}")

    if not chunks:
        return ""
    return " ".join(chunks) if not rtl else " ".join(chunks)


def build_intro_tags(*, fade_ms: int = 120, pop: bool = True) -> str:
    """وسوم بداية السطر: ظهور ناعم + تكبير خفيف (Pop-in)."""
    tags = [f"\\fad({fade_ms},{fade_ms})"]
    if pop:
        # يبدأ بحجم 88% ويصل 100% خلال 140ms — إحساس بالحيوية بلا إزعاج
        tags.append("\\fscx88\\fscy88\\t(0,140,\\fscx100\\fscy100)")
    return "{" + "".join(tags) + "}"


def collect_keywords_from_transcript(
    transcript: Transcript, *, track: str = "ar", settings: Optional[Settings] = None
) -> Set[str]:
    """يجمع كلمات الترانسكربت كاملاً ثم يختار منها الكلمات المبرَزة."""
    settings = settings or load_settings()
    all_words: List[str] = []
    for segment in transcript.segments:
        source = (
            segment.translation
            if track == "ar" and segment.translation
            else segment.text
        )
        all_words.extend((source or "").split())

    return pick_keywords(
        all_words,
        max_ratio=float(settings.get("subtitles.keyword_ratio", 0.25)),
        min_len=int(settings.get("subtitles.keyword_min_len", 4)),
    )
