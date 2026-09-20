"""اكتشاف اللحظات القوية (Analyze) — المحطة الأهم في المرحلة 2.

ثلاثة أوضاع، كلها تُرجع نفس النوع ``MomentCandidate``:

- ``heuristic`` : استدلال صرف، بلا نموذج وبلا انتظار.
- ``llm``       : تحليل كامل عبر نموذج محلي (Ollama).
- ``hybrid``    : الافتراضي — الاستدلال يرشّح، والنموذج يحكم ويشرح.

``hybrid`` هو الأذكى: الترانسكربت الطويل لا يدخل كاملاً في سياق النموذج،
فنستخدم الاستدلال لتقليص المرشّحين أولاً ثم نطلب من النموذج ترتيبها
وتفسيرها. هذا يوفّر وقتاً كبيراً ويرفع الدقة معاً.
"""

from __future__ import annotations

import json
from typing import List, Optional

from ..common.config import Settings, load_settings
from ..common.logging_utils import get_logger
from ..common.schemas import Transcript
from ..common.text_utils import human_duration
from .heuristics import MomentCandidate, find_moments
from .llm_client import BaseLLM, LLMError, build_llm

log = get_logger(__name__)


SYSTEM_PROMPT = (
    "أنت خبير في اختيار مقاطع البودكاست القابلة للانتشار على TikTok و Reels و Shorts. "
    "مهمتك اختيار اللحظات التي تُوقف تمرير الإصبع في أول ثانيتين. "
    "تُجيب بصيغة JSON صالحة فقط، بلا أي شرح خارجها."
)


def _format_transcript_window(transcript: Transcript, max_chars: int = 12000) -> str:
    """يبني نصاً موقّتاً مضغوطاً ليدخل في سياق النموذج."""
    lines = []
    total = 0
    for seg in transcript.segments:
        speaker = f" [{seg.speaker}]" if seg.speaker else ""
        line = f"[{seg.start:.0f}-{seg.end:.0f}]{speaker} {seg.text.strip()}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _rank_prompt(candidates: List[MomentCandidate], max_moments: int) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        speaker = f" (المتحدث: {c.speaker})" if c.speaker else ""
        body = (c.text or "")[:700]
        blocks.append(
            f"--- مرشّح {i} | {c.start:.0f}s → {c.end:.0f}s "
            f"({human_duration(c.duration)}){speaker}\n{body}"
        )
    joined = "\n\n".join(blocks)

    return f"""أمامك مقاطع مرشّحة من بودكاست. اختر أفضل {max_moments} منها للنشر كمقاطع قصيرة.

معايير الاختيار:
1. فكرة مكتملة ومفهومة دون سياق خارجي.
2. بداية قوية تُوقف التمرير في أول ثانيتين.
3. قيمة حقيقية: قصة، رأي صادم، معلومة مفيدة، أو سؤال يثير الفضول.
4. تجنّب: المقدمات، الترحيب، الإعلانات، الكلام الإداري.

{joined}

أرجع JSON فقط بهذا الشكل:
[
  {{
    "index": 0,
    "score": 8.5,
    "kind": "story",
    "reason": "سبب الاختيار بالعربية في جملة واحدة",
    "trim_start_offset": 0,
    "trim_end_offset": 0
  }}
]

القيم المسموحة لـ kind: story | opinion | question | surprise | insight | number
الحقلان trim_*_offset بالثواني لضبط البداية/النهاية (موجب = تأخير، سالب = تبكير)، واتركهما 0 إن كان المدى جيداً.
رتّب النتائج من الأقوى إلى الأضعف، ولا تُرجع أكثر من {max_moments}."""


def _discover_prompt(window: str, max_moments: int) -> str:
    return f"""هذا ترانسكربت بودكاست مع التوقيت بالثواني.

{window}

اختر أفضل {max_moments} لحظات قابلة للانتشار كمقاطع قصيرة (20-70 ثانية لكل مقطع).

أرجع JSON فقط:
[
  {{
    "start": 125,
    "end": 168,
    "kind": "story",
    "score": 9,
    "reason": "سبب الاختيار بالعربية"
  }}
]

القيم المسموحة لـ kind: story | opinion | question | surprise | insight | number
تأكد أن كل مقطع يحوي فكرة مكتملة، وأن التوقيت ضمن حدود الترانسكربت أعلاه."""


# ============================================================ الأوضاع


def _analyze_llm_only(
    transcript: Transcript, llm: BaseLLM, max_moments: int, min_dur: float, max_dur: float
) -> List[MomentCandidate]:
    """يطلب من النموذج اكتشاف اللحظات مباشرةً من الترانسكربت."""
    window = _format_transcript_window(transcript)
    data = llm.generate_json(_discover_prompt(window, max_moments), system=SYSTEM_PROMPT)

    if isinstance(data, dict):
        data = data.get("moments") or data.get("clips") or []

    duration = transcript.duration or (
        transcript.segments[-1].end if transcript.segments else 0.0
    )
    out: List[MomentCandidate] = []
    for item in data if isinstance(data, list) else []:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        # ضبط ضمن الحدود المنطقية
        start = max(0.0, start)
        end = min(end, duration) if duration else end
        if end - start < 5.0:
            continue
        if end - start > max_dur:
            end = start + max_dur

        sub = transcript.slice(start, end, rebase=False)
        out.append(
            MomentCandidate(
                start=start,
                end=end,
                score=float(item.get("score", 5.0)),
                kind=str(item.get("kind", "general")),
                reason=str(item.get("reason", "اختيار النموذج")),
                text=sub.full_text,
                translation=sub.full_translation,
                speaker=sub.segments[0].speaker if sub.segments else None,
            )
        )
    return out[:max_moments]


def _analyze_hybrid(
    transcript: Transcript,
    llm: BaseLLM,
    max_moments: int,
    min_dur: float,
    max_dur: float,
) -> List[MomentCandidate]:
    """الاستدلال يرشّح، والنموذج يرتّب ويشرح — الأدق والأسرع."""
    pool = find_moments(
        transcript,
        max_moments=max(max_moments * 2, 12),
        min_duration=min_dur,
        max_duration=max_dur,
        min_score=1.0,
    )
    if not pool:
        return []

    log.info("الاستدلال رشّح %d مقطعاً — عرضها على النموذج للترتيب...", len(pool))
    data = llm.generate_json(_rank_prompt(pool, max_moments), system=SYSTEM_PROMPT)

    if isinstance(data, dict):
        data = data.get("moments") or data.get("results") or []

    duration = transcript.duration or 0.0
    picked: List[MomentCandidate] = []
    seen = set()

    for item in data if isinstance(data, list) else []:
        try:
            idx = int(item["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(pool) or idx in seen:
            continue
        seen.add(idx)

        base = pool[idx]
        start = max(0.0, base.start + float(item.get("trim_start_offset", 0) or 0))
        end = base.end + float(item.get("trim_end_offset", 0) or 0)
        if duration:
            end = min(end, duration)
        if end - start < 5.0:  # تعديل النموذج أفسد المدى — نرجع للأصل
            start, end = base.start, base.end

        sub = transcript.slice(start, end, rebase=False)
        picked.append(
            MomentCandidate(
                start=start,
                end=end,
                score=float(item.get("score", base.score)),
                kind=str(item.get("kind", base.kind)),
                reason=str(item.get("reason", base.reason)),
                text=sub.full_text or base.text,
                translation=sub.full_translation or base.translation,
                speaker=base.speaker,
                signals=base.signals,
            )
        )

    return picked[:max_moments] if picked else pool[:max_moments]


# ============================================================ الواجهة العامة


def analyze_transcript(
    transcript: Transcript,
    *,
    settings: Optional[Settings] = None,
    engine: Optional[str] = None,
    max_moments: Optional[int] = None,
    min_duration: Optional[float] = None,
    max_duration: Optional[float] = None,
) -> List[MomentCandidate]:
    """يكتشف أقوى اللحظات في الترانسكربت.

    يسقط تلقائياً إلى الاستدلال إن تعذّر الوصول للنموذج — الأداة لا تتوقف
    أبداً بسبب غياب نموذج (المبدأ 1 و 3).
    """
    settings = settings or load_settings()
    if not transcript.segments:
        log.warning("الترانسكربت فارغ — لا توجد لحظات للتحليل.")
        return []

    mode = (engine or settings.get("analyze.engine", "hybrid")).strip().lower()
    n = int(max_moments or settings.get("analyze.max_moments", 8))
    min_dur = float(min_duration or settings.get("analyze.min_duration", 20.0))
    max_dur = float(max_duration or settings.get("analyze.max_duration", 70.0))

    if mode == "heuristic":
        log.info("التحليل بالاستدلال (بلا نموذج)...")
        return find_moments(
            transcript, max_moments=n, min_duration=min_dur, max_duration=max_dur
        )

    try:
        llm = build_llm(settings)
        if mode == "llm":
            log.info("التحليل عبر النموذج مباشرةً...")
            moments = _analyze_llm_only(transcript, llm, n, min_dur, max_dur)
        else:
            log.info("التحليل الهجين (استدلال + نموذج)...")
            moments = _analyze_hybrid(transcript, llm, n, min_dur, max_dur)

        if moments:
            log.info("تم اكتشاف %d لحظة قوية.", len(moments))
            return moments
        log.warning("النموذج لم يُرجع نتائج صالحة — التراجع إلى الاستدلال.")

    except (LLMError, Exception) as exc:
        log.warning("تعذّر استخدام النموذج (%s) — التراجع إلى الاستدلال.", exc)

    return find_moments(
        transcript, max_moments=n, min_duration=min_dur, max_duration=max_dur
    )


def split_long_moment(
    moment: MomentCandidate, *, max_duration: float = 70.0
) -> List[MomentCandidate]:
    """يقسّم فكرة طويلة إلى أجزاء (Part 1 / Part 2) عند تجاوز الحد."""
    if moment.duration <= max_duration:
        return [moment]

    parts = int(moment.duration // max_duration) + 1
    span = moment.duration / parts
    out: List[MomentCandidate] = []
    for i in range(parts):
        start = moment.start + i * span
        out.append(
            MomentCandidate(
                start=start,
                end=min(start + span, moment.end),
                score=moment.score,
                kind=moment.kind,
                reason=f"{moment.reason} (جزء {i + 1} من {parts})",
                text=moment.text if i == 0 else "",
                translation=moment.translation if i == 0 else "",
                speaker=moment.speaker,
                signals={**moment.signals, "part": i + 1, "total_parts": parts},
            )
        )
    return out
