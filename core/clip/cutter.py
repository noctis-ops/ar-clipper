"""محطة القص (Clip) — استخراج مدى زمني محدد من الفيديو الأصلي.

في المرحلة 1 المستخدم يحدّد البداية والنهاية يدوياً (أو تفاعلياً عبر CLI).
في المرحلة 2 ستأتي هذه المدَيات تلقائياً من محطة التحليل (Analyze).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..common.config import Settings, load_settings
from ..common.errors import MediaError
from ..common.ffmpeg import cut as ffmpeg_cut
from ..common.ffmpeg import probe
from ..common.logging_utils import get_logger
from ..common.schemas import ClipRequest, Transcript
from ..common.text_utils import human_duration

log = get_logger(__name__)


def validate_range(start: float, end: float, source_duration: float) -> tuple[float, float]:
    """يضبط المدى ضمن حدود الفيديو ويرفع خطأً إن كان غير منطقي."""
    if start < 0:
        raise MediaError(f"وقت البداية سالب: {start}")
    if end <= start:
        raise MediaError(
            f"وقت النهاية ({human_duration(end)}) يجب أن يكون بعد البداية "
            f"({human_duration(start)})."
        )
    if source_duration > 0:
        if start >= source_duration:
            raise MediaError(
                f"وقت البداية ({human_duration(start)}) خارج مدة الفيديو "
                f"({human_duration(source_duration)})."
            )
        if end > source_duration:
            log.warning(
                "وقت النهاية (%s) يتجاوز مدة الفيديو (%s) — سيُضبط تلقائياً.",
                human_duration(end),
                human_duration(source_duration),
            )
            end = source_duration
    return float(start), float(end)


def snap_to_speech(
    start: float,
    end: float,
    transcript: Optional[Transcript],
    *,
    max_shift: float = 1.5,
) -> tuple[float, float]:
    """يحاذي حدود القص مع أقرب حدود جملة لتجنّب بتر الكلمات.

    لا يُزيح أكثر من ``max_shift`` ثانية حتى لا يبتعد عن اختيار المستخدم.
    """
    if not transcript or not transcript.segments:
        return start, end

    starts = [s.start for s in transcript.segments]
    ends = [s.end for s in transcript.segments]

    best_start = min(starts, key=lambda v: abs(v - start))
    if abs(best_start - start) <= max_shift:
        start = max(0.0, best_start - 0.15)

    best_end = min(ends, key=lambda v: abs(v - end))
    if abs(best_end - end) <= max_shift:
        end = best_end + 0.25

    return start, end


def cut_clip(
    source_path: str | Path,
    request: ClipRequest,
    output_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    fast: bool = False,
) -> Path:
    """يقصّ المقطع المطلوب من الفيديو الأصلي."""
    settings = settings or load_settings()
    src = Path(source_path)
    if not src.exists():
        raise MediaError(f"الفيديو المصدر غير موجود: {src}")

    info = probe(src)
    start, end = validate_range(request.start, request.end, info.duration)

    log.info(
        "قص المقطع: %s → %s (المدة %s)",
        human_duration(start),
        human_duration(end),
        human_duration(end - start),
    )

    return ffmpeg_cut(
        src,
        output_path,
        start,
        end,
        reencode=not fast,
        crf=int(settings.get("export.crf", 20)),
        preset=str(settings.get("export.preset", "medium")),
        video_codec=str(settings.get("export.video_codec", "libx264")),
        audio_codec=str(settings.get("export.audio_codec", "aac")),
        audio_bitrate=str(settings.get("export.audio_bitrate", "160k")),
    )


def preview_transcript(
    transcript: Transcript,
    *,
    track: str = "both",
    limit: Optional[int] = None,
) -> List[str]:
    """يبني أسطر عرض الترانسكربت للاختيار اليدوي في الـ CLI."""
    lines: List[str] = []
    segments = transcript.segments[:limit] if limit else transcript.segments
    for seg in segments:
        stamp = f"[{human_duration(seg.start)} → {human_duration(seg.end)}]"
        if track == "ar" and seg.translation:
            body = seg.translation
        elif track == "source":
            body = seg.text
        elif seg.translation and seg.translation.strip() != seg.text.strip():
            body = f"{seg.text}\n      ↳ {seg.translation}"
        else:
            body = seg.text
        lines.append(f"{seg.id:>4}. {stamp} {body}")
    return lines
