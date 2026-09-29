"""كشف التكرار بين المقاطع المقترحة.

المشكلة الواقعية: المحلّل قد يقترح مقطعين يتحدثان عن الفكرة نفسها بكلمات
متقاربة، فتنشر نسختين من محتوى واحد. الأدوات التجارية تعاني منها أيضاً
(المراجعات تقول 20-40% من مقاطع Opus Clip تُرمى).

المنهج: **تشابه جاكارد على الكلمات الدالة** بعد التطبيع وإزالة كلمات الوقف.
بلا تضمينات ولا نماذج — مقارنة مجموعات بسيطة تكفي لالتقاط إعادة الصياغة
القريبة، وهي الحالة الشائعة. نضيف إليها التداخل الزمني لأن مقطعين
متداخلين زمنياً يحتويان الكلام نفسه حرفياً.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..common.config import Settings, load_settings
from ..common.logging_utils import get_logger
from ..subtitles.animated import ARABIC_STOPWORDS

log = get_logger(__name__)

_WORD = re.compile(r"[^\w\u0600-\u06FF]+|[\u060C\u061B\u061F\u066A-\u066D\u06D4]+")
_DIACRITICS = re.compile(r"[\u064B-\u0652\u0640]")

ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "are", "was", "were", "be", "been", "this", "that",
    "it", "i", "you", "we", "they", "he", "she", "what", "how", "so",
}


def normalize(text: str) -> str:
    text = _DIACRITICS.sub("", text or "")
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه").replace("ى", "ي")
    return text.lower()


def content_words(text: str, *, min_length: int = 3) -> set:
    """الكلمات الدالة فقط — بلا أدوات الربط والضمائر."""
    cleaned = _WORD.sub(" ", normalize(text))
    stops = ARABIC_STOPWORDS | ENGLISH_STOPWORDS
    return {
        w for w in cleaned.split()
        if len(w) >= min_length and w not in stops
    }


def jaccard(left: set, right: set) -> float:
    """نسبة التقاطع إلى الاتحاد — 1.0 يعني تطابقاً تاماً."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


def time_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """نسبة تداخل مدَيين زمنيين إلى أقصرهما."""
    overlap = min(a_end, b_end) - max(a_start, b_start)
    if overlap <= 0:
        return 0.0
    shortest = min(a_end - a_start, b_end - b_start)
    return overlap / shortest if shortest > 0 else 0.0


@dataclass
class DuplicatePair:
    """زوج مقترحات متشابهة."""

    kept_index: int
    dropped_index: int
    text_similarity: float
    time_overlap: float
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kept": self.kept_index,
            "dropped": self.dropped_index,
            "text_similarity": round(self.text_similarity, 3),
            "time_overlap": round(self.time_overlap, 3),
            "reason": self.reason,
        }


@dataclass
class DedupeResult:
    kept: List[Any] = field(default_factory=list)
    duplicates: List[DuplicatePair] = field(default_factory=list)

    @property
    def removed(self) -> int:
        return len(self.duplicates)


def _score_of(item: Any) -> float:
    return float(getattr(item, "score", 0.0) or 0.0)


def _text_of(item: Any) -> str:
    parts = [
        getattr(item, "text", "") or "",
        getattr(item, "title", "") or "",
        getattr(item, "hook", "") or "",
    ]
    return " ".join(p for p in parts if p)


def deduplicate(
    items: Sequence[Any],
    *,
    settings: Optional[Settings] = None,
    text_threshold: Optional[float] = None,
    overlap_threshold: Optional[float] = None,
) -> DedupeResult:
    """يزيل المقترحات المكرّرة مع الإبقاء على الأعلى درجةً.

    يعمل على أي كائن فيه ``start``/``end``/``text``/``score`` — أي على
    ``Suggestion`` و``Moment`` معاً.
    """
    settings = settings or load_settings()
    result = DedupeResult()
    if not items:
        return result

    if not settings.get("dedupe.enabled", True):
        result.kept = list(items)
        return result

    text_threshold = (
        text_threshold if text_threshold is not None
        else float(settings.get("dedupe.text_threshold", 0.55))
    )
    overlap_threshold = (
        overlap_threshold if overlap_threshold is not None
        else float(settings.get("dedupe.overlap_threshold", 0.6))
    )

    # الأعلى درجةً أولاً: عند التكرار نُبقي الأفضل لا الأسبق
    ordered = sorted(
        enumerate(items), key=lambda pair: (-_score_of(pair[1]), pair[0])
    )

    kept: List[Tuple[int, Any, set]] = []
    for index, item in ordered:
        words = content_words(_text_of(item))
        start = float(getattr(item, "start", 0.0) or 0.0)
        end = float(getattr(item, "end", 0.0) or 0.0)

        duplicate_of = None
        for kept_index, kept_item, kept_words in kept:
            similarity = jaccard(words, kept_words)
            overlap = time_overlap(
                start, end,
                float(getattr(kept_item, "start", 0.0) or 0.0),
                float(getattr(kept_item, "end", 0.0) or 0.0),
            )
            if similarity >= text_threshold:
                duplicate_of = (kept_index, similarity, overlap, "نص متشابه")
                break
            if overlap >= overlap_threshold:
                duplicate_of = (kept_index, similarity, overlap, "تداخل زمني")
                break

        if duplicate_of:
            kept_index, similarity, overlap, reason = duplicate_of
            result.duplicates.append(
                DuplicatePair(
                    kept_index=kept_index, dropped_index=index,
                    text_similarity=similarity, time_overlap=overlap, reason=reason,
                )
            )
        else:
            kept.append((index, item, words))

    # نعيد الترتيب الأصلي حتى لا نفاجئ المستدعي
    result.kept = [item for _, item, _ in sorted(kept, key=lambda triple: triple[0])]

    if result.duplicates:
        log.info(
            "كشف التكرار: أُسقط %d مقترح متشابه من أصل %d.",
            result.removed, len(items),
        )
    return result
