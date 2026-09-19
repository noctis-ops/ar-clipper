"""محطة حذف الصمت (Silence Removal).

محركان:
- ``ffmpeg``      : افتراضي — يعتمد على ``silencedetect`` ثم يعيد بناء الفيديو
  من الأجزاء الناطقة عبر فلاتر ``trim/concat``. لا يحتاج أي تبعية إضافية.
- ``auto_editor`` : أداة مخصصة لهذا الغرض (مفتوحة المصدر) إن كانت مثبّتة.

المخرج الجانبي المهم: ``kept_ranges`` — قائمة المدَيات المحتفظ بها في التوقيت
الأصلي، وهي ضرورية لإعادة حساب توقيت الترجمة بعد الحذف.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..common.config import Settings, load_settings
from ..common.errors import MediaError
from ..common.ffmpeg import ffmpeg_bin, probe, run, run_ffmpeg
from ..common.logging_utils import get_logger
from ..common.schemas import Segment, Transcript
from ..common.text_utils import human_duration

log = get_logger(__name__)

Range = Tuple[float, float]


@dataclass
class SilenceResult:
    """ناتج محطة حذف الصمت."""

    output_path: Path
    kept_ranges: List[Range]
    removed_seconds: float
    original_duration: float
    applied: bool

    @property
    def new_duration(self) -> float:
        return sum(e - s for s, e in self.kept_ranges) if self.kept_ranges else self.original_duration


# ============================================================ كشف الصمت


def detect_silences(
    path: str | Path,
    *,
    threshold_db: float = -34.0,
    min_silence_ms: int = 700,
) -> List[Range]:
    """يرجع قائمة مدَيات الصمت عبر فلتر ``silencedetect``."""
    min_s = max(0.05, min_silence_ms / 1000.0)
    proc = run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={threshold_db}dB:d={min_s}",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    text = (proc.stderr or "") + (proc.stdout or "")

    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", text)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", text)]

    ranges: List[Range] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is None:  # صمت ممتد حتى نهاية الملف
            try:
                e = probe(path).duration
            except Exception:
                continue
        if e > s:
            ranges.append((max(0.0, s), e))
    return ranges


def invert_ranges(
    silences: List[Range], total_duration: float, *, padding: float = 0.12
) -> List[Range]:
    """يحوّل مدَيات الصمت إلى مدَيات الكلام المحتفظ بها، مع هامش أمان."""
    if total_duration <= 0:
        return []
    if not silences:
        return [(0.0, total_duration)]

    kept: List[Range] = []
    cursor = 0.0
    for s_start, s_end in sorted(silences):
        # نترك هامشاً حتى لا تُقطع بدايات/نهايات الكلمات
        seg_end = min(total_duration, s_start + padding)
        if seg_end > cursor + 0.05:
            kept.append((cursor, seg_end))
        cursor = max(cursor, min(total_duration, s_end - padding))
    if cursor < total_duration - 0.05:
        kept.append((cursor, total_duration))

    # دمج المدَيات المتلاصقة
    merged: List[Range] = []
    for rng in kept:
        if merged and rng[0] - merged[-1][1] < 0.05:
            merged[-1] = (merged[-1][0], rng[1])
        else:
            merged.append(rng)
    return [(s, e) for s, e in merged if e - s > 0.08]


# ============================================================ إعادة البناء


def _rebuild_with_ffmpeg(
    source: Path, destination: Path, kept: List[Range], settings: Settings
) -> Path:
    """يعيد بناء الفيديو من المدَيات الناطقة عبر فلاتر trim/concat."""
    if not kept:
        raise MediaError("لا توجد أجزاء ناطقة للاحتفاظ بها.")

    info = probe(source)
    parts_v, parts_a, labels = [], [], []
    for i, (s, e) in enumerate(kept):
        parts_v.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        if info.has_audio:
            parts_a.append(
                f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
            )
            labels.append(f"[v{i}][a{i}]")
        else:
            labels.append(f"[v{i}]")

    n = len(kept)
    concat = (
        "".join(labels)
        + f"concat=n={n}:v=1:a={1 if info.has_audio else 0}"
        + ("[outv][outa]" if info.has_audio else "[outv]")
    )
    filter_complex = ";".join([*parts_v, *parts_a, concat])

    args = ["-i", str(source), "-filter_complex", filter_complex, "-map", "[outv]"]
    if info.has_audio:
        args += ["-map", "[outa]", "-c:a", str(settings.get("export.audio_codec", "aac")),
                 "-b:a", str(settings.get("export.audio_bitrate", "160k"))]
    args += [
        "-c:v",
        str(settings.get("export.video_codec", "libx264")),
        "-crf",
        str(settings.get("export.crf", 20)),
        "-preset",
        str(settings.get("export.preset", "medium")),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(destination),
    ]

    destination.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args)
    return destination


def _run_auto_editor(source: Path, destination: Path, settings: Settings) -> Path:
    """يشغّل auto-editor إن كان مثبّتاً (لا يعطي kept_ranges بدقة)."""
    exe = shutil.which("auto-editor")
    if not exe:
        raise MediaError("auto-editor غير مثبّت. نفّذ: pip install auto-editor")
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            exe,
            str(source),
            "--output",
            str(destination),
            "--no-open",
            "--margin",
            f"{int(settings.get('silence.keep_padding_ms', 120))}ms",
        ],
        timeout=3600,
    )
    if not destination.exists():
        raise MediaError(f"auto-editor لم ينتج ملفاً: {destination}")
    return destination


# ============================================================ الواجهة العامة


def remove_silence(
    source_path: str | Path,
    output_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    engine: Optional[str] = None,
) -> SilenceResult:
    """يحذف فترات الصمت الطويلة من المقطع."""
    settings = settings or load_settings()
    src = Path(source_path)
    dst = Path(output_path)

    info = probe(src)
    if not info.has_audio:
        log.info("لا يوجد مسار صوتي — تخطّي حذف الصمت.")
        return SilenceResult(src, [(0.0, info.duration)], 0.0, info.duration, applied=False)

    name = (engine or settings.get("silence.engine", "ffmpeg")).lower()

    if name == "auto_editor":
        out = _run_auto_editor(src, dst, settings)
        new_info = probe(out)
        return SilenceResult(
            output_path=out,
            kept_ranges=[],  # auto-editor لا يكشف المدَيات — الترجمة تبقى بتوقيت المصدر
            removed_seconds=max(0.0, info.duration - new_info.duration),
            original_duration=info.duration,
            applied=True,
        )

    silences = detect_silences(
        src,
        threshold_db=float(settings.get("silence.threshold_db", -34.0)),
        min_silence_ms=int(settings.get("silence.min_silence_ms", 700)),
    )
    padding = float(settings.get("silence.keep_padding_ms", 120)) / 1000.0
    kept = invert_ranges(silences, info.duration, padding=padding)

    removed = info.duration - sum(e - s for s, e in kept)
    if not kept or removed < 0.4:
        log.info("لا توجد فترات صمت تستحق الحذف (%.2fs) — تخطّي.", max(0.0, removed))
        return SilenceResult(src, [(0.0, info.duration)], 0.0, info.duration, applied=False)

    log.info(
        "حذف الصمت: %d جزء ناطق | سيُحذف %s من أصل %s",
        len(kept),
        human_duration(removed),
        human_duration(info.duration),
    )
    out = _rebuild_with_ffmpeg(src, dst, kept, settings)
    return SilenceResult(
        output_path=out,
        kept_ranges=kept,
        removed_seconds=removed,
        original_duration=info.duration,
        applied=True,
    )


# ============================================================ إعادة تعيين التوقيت


def remap_time(original_time: float, kept_ranges: List[Range]) -> Optional[float]:
    """يحوّل توقيتاً من الفيديو الأصلي إلى التوقيت بعد حذف الصمت."""
    if not kept_ranges:
        return original_time
    elapsed = 0.0
    for s, e in kept_ranges:
        if original_time < s:
            return elapsed  # وقع داخل فترة محذوفة → ألصقه ببداية الجزء التالي
        if original_time <= e:
            return elapsed + (original_time - s)
        elapsed += e - s
    return elapsed


def remap_transcript(transcript: Transcript, kept_ranges: List[Range]) -> Transcript:
    """يعيد حساب توقيت كل جملة/كلمة بعد حذف الصمت.

    بدون هذه الخطوة تنزاح الترجمة عن الكلام بعد حذف الفراغات.
    """
    if not kept_ranges:
        return transcript

    total_kept = sum(e - s for s, e in kept_ranges)
    new_segments: List[Segment] = []

    for seg in transcript.segments:
        new_start = remap_time(seg.start, kept_ranges)
        new_end = remap_time(seg.end, kept_ranges)
        if new_start is None or new_end is None:
            continue
        if new_end - new_start < 0.15:  # الجملة ابتُلعت كلياً ضمن المحذوف
            continue
        words = []
        for w in seg.words:
            ws, we = remap_time(w.start, kept_ranges), remap_time(w.end, kept_ranges)
            if ws is None or we is None or we <= ws:
                continue
            words.append(type(w)(w.text, ws, we, w.score))
        new_segments.append(
            Segment(
                id=len(new_segments),
                start=new_start,
                end=new_end,
                text=seg.text,
                words=words,
                translation=seg.translation,
                speaker=seg.speaker,
            )
        )

    return Transcript(
        source_path=transcript.source_path,
        language=transcript.language,
        segments=new_segments,
        duration=total_kept,
        engine=transcript.engine,
        model=transcript.model,
        translated_to=transcript.translated_to,
        translate_engine=transcript.translate_engine,
    )
