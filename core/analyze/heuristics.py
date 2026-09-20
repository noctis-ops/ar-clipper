"""كشف اللحظات القوية بالاستدلال (Heuristics) — بلا أي نموذج ذكاء اصطناعي.

يعتمد على إشارات قابلة للقياس مباشرةً من الترانسكربت:

- **الأسئلة**       : الجمل الاستفهامية تفتح حلقات فضول (Open loops).
- **الجمل المفاجئة**: كلمات إشارية مثل "في الحقيقة"، "لن تصدّق"، "actually".
- **كثافة الكلام**  : ارتفاع الكلمات/الثانية يدل على حماس أو ذروة.
- **الأرقام**       : الإحصاءات والمبالغ تجذب الانتباه.
- **الصمت المحيط**  : وقفة قبل الجملة أو بعدها = تأكيد درامي.
- **تبديل المتحدث** : ردّ الضيف على سؤال المضيف عادةً هو الجوهر.

الفائدة: يعطي نحو 60% من قيمة الـLLM بصفر تحميل وصفر انتظار، ويصلح
كطبقة ترشيح أولى تُقلّل ما يُرسل للنموذج لاحقاً.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..common.schemas import Segment, Transcript
from ..common.text_utils import contains_arabic, normalize_text

# ============================================================ القواميس الإشارية

HOOK_MARKERS_EN = {
    "actually", "surprisingly", "the truth is", "nobody tells you", "here's the thing",
    "i was wrong", "the biggest mistake", "what most people", "the secret", "honestly",
    "believe it or not", "turns out", "the problem is", "let me tell you", "i realized",
    "never", "always", "the reason", "that's why", "imagine", "crazy", "insane",
}

HOOK_MARKERS_AR = {
    "في الحقيقة", "الحقيقة", "لن تصدق", "المفاجأة", "السر", "أكبر خطأ", "الخطأ الأكبر",
    "معظم الناس", "لا أحد يخبرك", "اكتشفت", "أدركت", "تخيل", "المشكلة", "السبب",
    "لهذا السبب", "صدقني", "بصراحة", "الغريب", "المدهش", "أبداً", "دائماً",
}

STORY_MARKERS_EN = {
    "when i was", "one day", "i remember", "back then", "the first time",
    "years ago", "a friend of mine", "it happened", "so i", "then i",
    "a story", "story about", "long story", "i used to", "growing up",
    "at the time", "ended up", "what happened",
}

STORY_MARKERS_AR = {
    "عندما كنت", "ذات يوم", "أتذكر", "في ذلك الوقت", "أول مرة",
    "قبل سنوات", "صديق لي", "حدث أن", "فقمت", "ثم",
    "قصة", "أحكي لك", "كنت في", "انتهى بي", "ما حدث", "في الماضي",
}

OPINION_MARKERS_EN = {
    "i think", "i believe", "in my opinion", "the way i see it", "i'd argue",
    "people are wrong", "disagree", "the real issue", "matters most",
}

OPINION_MARKERS_AR = {
    "أعتقد", "برأيي", "في رأيي", "من وجهة نظري", "أرى أن",
    "الناس مخطئون", "لا أتفق", "القضية الحقيقية", "الأهم",
}

QUESTION_RE = re.compile(r"[?؟]\s*$")
NUMBER_RE = re.compile(r"\b\d[\d,.]*\s*(%|percent|بالمئة|مليون|ألف|million|thousand|k|x)?\b", re.I)


# أولوية حسم التعادل بين الأنواع (الأعلى = يُفضَّل عند تساوي الدرجة)
KIND_PRIORITY = {
    "story": 6,
    "question": 5,
    "opinion": 4,
    "surprise": 3,
    "number": 2,
    "dense": 1,
}


@dataclass
class MomentCandidate:
    """لحظة مرشّحة للقص، بتوقيت ودرجة وسبب."""

    start: float
    end: float
    score: float
    kind: str  # question | story | opinion | surprise | dense | number | mixed
    reason: str
    text: str = ""
    translation: str = ""
    speaker: Optional[str] = None
    signals: Dict[str, float] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# ============================================================ الإشارات المفردة


def _marker_hits(text: str, markers_en: set, markers_ar: set) -> int:
    low = text.lower()
    hits = sum(1 for m in markers_en if m in low)
    if contains_arabic(text):
        hits += sum(1 for m in markers_ar if m in text)
    return hits


def speech_density(seg: Segment) -> float:
    """كلمات في الثانية — مؤشر على الحماس أو الذروة."""
    if seg.duration <= 0:
        return 0.0
    count = len(seg.words) or len(seg.text.split())
    return count / seg.duration


def silence_around(transcript: Transcript, index: int) -> Tuple[float, float]:
    """طول الصمت قبل الجملة وبعدها."""
    segs = transcript.segments
    before = segs[index].start - segs[index - 1].end if index > 0 else 0.0
    after = segs[index + 1].start - segs[index].end if index < len(segs) - 1 else 0.0
    return max(0.0, before), max(0.0, after)


def score_segment(
    transcript: Transcript, index: int, avg_density: float
) -> Tuple[float, str, Dict[str, float]]:
    """يحسب درجة جملة واحدة ويرجع (الدرجة، النوع، الإشارات)."""
    seg = transcript.segments[index]
    text = normalize_text(seg.text)
    signals: Dict[str, float] = {}
    score = 0.0
    kinds: List[Tuple[float, str]] = []

    # سؤال
    if QUESTION_RE.search(text):
        signals["question"] = 1.0
        score += 2.0
        kinds.append((2.0, "question"))

    # كلمات إشارية
    surprise = _marker_hits(text, HOOK_MARKERS_EN, HOOK_MARKERS_AR)
    if surprise:
        v = min(3.0, surprise * 1.5)
        signals["surprise"] = float(surprise)
        score += v
        kinds.append((v, "surprise"))

    story = _marker_hits(text, STORY_MARKERS_EN, STORY_MARKERS_AR)
    if story:
        v = min(2.5, story * 1.5)
        signals["story"] = float(story)
        score += v
        kinds.append((v, "story"))

    opinion = _marker_hits(text, OPINION_MARKERS_EN, OPINION_MARKERS_AR)
    if opinion:
        v = min(2.5, opinion * 1.3)
        signals["opinion"] = float(opinion)
        score += v
        kinds.append((v, "opinion"))

    # أرقام وإحصاءات
    numbers = len(NUMBER_RE.findall(text))
    if numbers:
        v = min(1.5, numbers * 0.75)
        signals["numbers"] = float(numbers)
        score += v
        kinds.append((v, "number"))

    # كثافة الكلام مقارنةً بالمتوسط
    density = speech_density(seg)
    if avg_density > 0:
        ratio = density / avg_density
        signals["density_ratio"] = round(ratio, 2)
        if ratio > 1.25:
            v = min(1.5, (ratio - 1.25) * 3)
            score += v
            kinds.append((v, "dense"))

    # وقفة درامية
    before, after = silence_around(transcript, index)
    if before > 0.6 or after > 0.6:
        signals["pause"] = round(max(before, after), 2)
        score += 0.8

    # طول مناسب للمقطع القصير
    if 3.0 <= seg.duration <= 30.0:
        score += 0.5

    # تبديل المتحدث (يتطلب بيانات Diarization)
    if seg.speaker and index > 0:
        prev = transcript.segments[index - 1].speaker
        if prev and prev != seg.speaker:
            signals["speaker_change"] = 1.0
            score += 0.7

    # ترتيب النوع: الأعلى درجةً، وعند التعادل تُرجَّح الأنواع الأوضح سردياً
    # (بدل الترتيب الأبجدي العشوائي الذي كان يجعل "surprise" يغلب "story" دائماً)
    kinds.sort(key=lambda kv: (kv[0], KIND_PRIORITY.get(kv[1], 0)), reverse=True)
    kind = kinds[0][1] if kinds else "general"
    return score, kind, signals


# ============================================================ بناء المقاطع


def _build_reason(kind: str, signals: Dict[str, float]) -> str:
    parts = {
        "question": "سؤال يفتح حلقة فضول",
        "story": "بداية قصة شخصية",
        "opinion": "رأي واضح وصريح",
        "surprise": "جملة مفاجئة أو مخالفة للتوقع",
        "number": "رقم أو إحصائية لافتة",
        "dense": "كثافة كلام عالية (ذروة حماس)",
        "general": "محتوى متماسك",
    }
    reason = parts.get(kind, "لحظة لافتة")
    if signals.get("speaker_change"):
        reason += " + تبديل متحدث"
    if signals.get("pause"):
        reason += " + وقفة درامية"
    return reason


def find_moments(
    transcript: Transcript,
    *,
    max_moments: int = 8,
    min_duration: float = 15.0,
    max_duration: float = 75.0,
    min_score: float = 2.0,
) -> List[MomentCandidate]:
    """يكتشف أقوى اللحظات في الترانسكربت بالاستدلال فقط.

    الخوارزمية: نقيّم كل جملة، ننطلق من الأعلى درجةً، ثم نوسّع حولها حتى
    نبلغ المدة المطلوبة، مع منع التداخل بين المقاطع المختارة.
    """
    segments = transcript.segments
    if not segments:
        return []

    densities = [speech_density(s) for s in segments if s.duration > 0]
    avg_density = (sum(densities) / len(densities)) if densities else 0.0

    scored = []
    for i in range(len(segments)):
        score, kind, signals = score_segment(transcript, i, avg_density)
        scored.append((score, i, kind, signals))
    scored.sort(reverse=True, key=lambda x: (x[0], -x[1]))

    used: List[Tuple[float, float]] = []
    results: List[MomentCandidate] = []

    for score, idx, kind, signals in scored:
        if score < min_score or len(results) >= max_moments:
            continue

        # التوسّع حول الجملة البذرة حتى بلوغ المدة الدنيا
        start_i = end_i = idx
        start = segments[idx].start
        end = segments[idx].end

        while end - start < min_duration:
            grew = False
            # نفضّل التوسّع للأمام (سياق ما بعد الفكرة أهم عادةً)
            if end_i + 1 < len(segments):
                nxt = segments[end_i + 1]
                if nxt.end - start <= max_duration:
                    end_i += 1
                    end = nxt.end
                    grew = True
            if end - start < min_duration and start_i > 0:
                prv = segments[start_i - 1]
                if end - prv.start <= max_duration:
                    start_i -= 1
                    start = prv.start
                    grew = True
            if not grew:
                break

        if end - start < min(8.0, min_duration):
            continue

        if any(start < u_end and end > u_start for u_start, u_end in used):
            continue

        used.append((start, end))
        chunk = segments[start_i : end_i + 1]
        results.append(
            MomentCandidate(
                start=start,
                end=end,
                score=round(score, 2),
                kind=kind,
                reason=_build_reason(kind, signals),
                text=" ".join(s.text for s in chunk).strip(),
                translation=" ".join(s.translation or "" for s in chunk).strip(),
                speaker=segments[idx].speaker,
                signals=signals,
            )
        )

    results.sort(key=lambda m: m.start)
    return results
