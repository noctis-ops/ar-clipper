"""غلاف موحّد حول ffmpeg / ffprobe.

كل وحدة تحتاج معالجة وسائط تمر من هنا — لا نستدعي ``subprocess`` مباشرة في
أي مكان آخر. هذا يسمح باستبدال المحرك مستقبلاً دون كسر بقية النظام
(المبدأ 5: بنية معيارية).

ترتيب اكتشاف الملف التنفيذي:
1. ``runtime.ffmpeg_path`` في settings.yaml
2. متغير البيئة ``FFMPEG_BINARY`` / ``FFPROBE_BINARY``
3. ``PATH`` النظام
4. حزمة ``imageio-ffmpeg`` (نسخة ثابتة تُثبَّت مع pip — تضمن العمل بلا صلاحيات root)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .config import load_settings
from .errors import DependencyError, MediaError
from .logging_utils import get_logger

log = get_logger(__name__)


# ============================================================ اكتشاف الثنائيات


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    """يرجع مسار ffmpeg القابل للتنفيذ."""
    settings = load_settings()
    configured = settings.get("runtime.ffmpeg_path")
    if configured:
        return str(configured)

    import os

    env = os.getenv("FFMPEG_BINARY")
    if env:
        return env

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise DependencyError(
            "ffmpeg غير موجود. ثبّته على النظام (apt install ffmpeg) "
            "أو ثبّت الحزمة البديلة: pip install imageio-ffmpeg"
        ) from exc


@lru_cache(maxsize=1)
def ffprobe_bin() -> str:
    """يرجع مسار ffprobe؛ يستنتجه من مجلد ffmpeg إن لم يكن في PATH."""
    settings = load_settings()
    configured = settings.get("runtime.ffprobe_path")
    if configured:
        return str(configured)

    import os

    env = os.getenv("FFPROBE_BINARY")
    if env:
        return env

    found = shutil.which("ffprobe")
    if found:
        return found

    # نسخة imageio-ffmpeg الثابتة لا تتضمن ffprobe؛ نجرب بجانب ffmpeg
    sibling = Path(ffmpeg_bin()).with_name("ffprobe")
    if sibling.exists():
        return str(sibling)
    return ""


def has_ffprobe() -> bool:
    return bool(ffprobe_bin())


# ============================================================ التشغيل


def run(
    args: Sequence[str],
    *,
    capture: bool = True,
    check: bool = True,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """ينفّذ أمراً خارجياً ويرفع ``MediaError`` عند الفشل."""
    printable = " ".join(str(a) for a in args)
    log.debug("تنفيذ: %s", printable[:800])
    try:
        proc = subprocess.run(
            [str(a) for a in args],
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DependencyError(f"الأمر غير موجود: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"انتهت مهلة تنفيذ الأمر بعد {timeout}s: {printable[:200]}") from exc

    if check and proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-15:]
        raise MediaError(
            "فشل تنفيذ الأمر (rc=%s):\n%s\n--- آخر مخرجات ---\n%s"
            % (proc.returncode, printable[:400], "\n".join(tail))
        )
    return proc


def run_ffmpeg(args: Iterable[str], *, overwrite: bool = True, timeout: Optional[int] = None):
    """ينفّذ ffmpeg مع أعلام صامتة موحّدة."""
    base = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if overwrite:
        base.append("-y")
    return run([*base, *[str(a) for a in args]], timeout=timeout)


# ============================================================ الاستعلام عن الوسائط


@dataclass
class MediaInfo:
    """معلومات أساسية عن ملف وسائط."""

    path: Path
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    video_codec: str = ""
    audio_codec: str = ""

    @property
    def aspect(self) -> float:
        return (self.width / self.height) if self.height else 0.0


def _parse_fraction(value: str) -> float:
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    """يستخرج معلومات الملف عبر ffprobe، مع تراجع (fallback) إلى ffmpeg."""
    p = Path(path)
    if not p.exists():
        raise MediaError(f"الملف غير موجود: {p}")

    if has_ffprobe():
        proc = run(
            [
                ffprobe_bin(),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(p),
            ]
        )
        payload = json.loads(proc.stdout or "{}")
        info = MediaInfo(path=p)
        fmt = payload.get("format", {})
        info.duration = float(fmt.get("duration") or 0.0)
        for stream in payload.get("streams", []):
            kind = stream.get("codec_type")
            if kind == "video" and not info.has_video:
                info.has_video = True
                info.width = int(stream.get("width") or 0)
                info.height = int(stream.get("height") or 0)
                info.fps = _parse_fraction(stream.get("avg_frame_rate") or "0/0") or _parse_fraction(
                    stream.get("r_frame_rate") or "0/0"
                )
                info.video_codec = stream.get("codec_name", "")
                if not info.duration:
                    info.duration = float(stream.get("duration") or 0.0)
            elif kind == "audio" and not info.has_audio:
                info.has_audio = True
                info.audio_codec = stream.get("codec_name", "")
                if not info.duration:
                    info.duration = float(stream.get("duration") or 0.0)
        return info

    return _probe_via_ffmpeg(p)


def _probe_via_ffmpeg(p: Path) -> MediaInfo:
    """تراجع: قراءة المعلومات من مخرجات ffmpeg النصية عند غياب ffprobe."""
    import re

    proc = run([ffmpeg_bin(), "-hide_banner", "-i", str(p)], check=False)
    text = (proc.stderr or "") + (proc.stdout or "")
    info = MediaInfo(path=p)

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
    if m:
        h, mnt, s = m.groups()
        info.duration = int(h) * 3600 + int(mnt) * 60 + float(s)

    mv = re.search(r"Stream #\d+:\d+.*?: Video: (\w+).*?, (\d+)x(\d+)", text, re.S)
    if mv:
        info.has_video = True
        info.video_codec = mv.group(1)
        info.width = int(mv.group(2))
        info.height = int(mv.group(3))
        mfps = re.search(r"(\d+(?:\.\d+)?) fps", text)
        if mfps:
            info.fps = float(mfps.group(1))

    ma = re.search(r"Stream #\d+:\d+.*?: Audio: (\w+)", text)
    if ma:
        info.has_audio = True
        info.audio_codec = ma.group(1)

    if not info.duration and not info.has_video and not info.has_audio:
        raise MediaError(f"تعذّر قراءة معلومات الملف: {p}")
    return info


# ============================================================ عمليات جاهزة


def extract_audio(
    source: str | Path,
    destination: str | Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    """يستخرج مساراً صوتياً WAV أحادياً 16kHz (الصيغة المثالية لـ Whisper)."""
    dst = Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(source),
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )
    if not dst.exists() or dst.stat().st_size == 0:
        raise MediaError(f"فشل استخراج الصوت إلى: {dst}")
    return dst


def cut(
    source: str | Path,
    destination: str | Path,
    start: float,
    end: float,
    *,
    reencode: bool = True,
    crf: int = 20,
    preset: str = "medium",
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    audio_bitrate: str = "160k",
) -> Path:
    """يقص مقطعاً بين ``start`` و ``end`` (بالثواني).

    ``reencode=True`` (الافتراضي) يضمن دقة القص على مستوى الإطار؛
    ``False`` أسرع بكثير لكنه يلتصق بأقرب keyframe.
    """
    if end <= start:
        raise MediaError(f"مدى زمني غير صالح: start={start} end={end}")

    dst = Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start

    if reencode:
        args = [
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-c:v",
            video_codec,
            "-crf",
            str(crf),
            "-preset",
            preset,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            audio_codec,
            "-b:a",
            audio_bitrate,
            "-movflags",
            "+faststart",
            str(dst),
        ]
    else:
        args = [
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(dst),
        ]

    run_ffmpeg(args)
    if not dst.exists() or dst.stat().st_size == 0:
        raise MediaError(f"فشل قص المقطع إلى: {dst}")
    return dst


def apply_filters(
    source: str | Path,
    destination: str | Path,
    *,
    video_filter: str = "",
    audio_filter: str = "",
    crf: int = 20,
    preset: str = "medium",
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    audio_bitrate: str = "160k",
    fps: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> Path:
    """يطبّق سلسلة فلاتر ffmpeg على ملف ويعيد الترميز."""
    dst = Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)

    args: List[str] = ["-i", str(source)]
    if video_filter:
        args += ["-vf", video_filter]
    if audio_filter:
        args += ["-af", audio_filter]
    if fps:
        args += ["-r", str(fps)]
    args += [
        "-c:v",
        video_codec,
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        audio_codec,
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
    ]
    if extra_args:
        args += [str(a) for a in extra_args]
    args.append(str(dst))

    run_ffmpeg(args)
    if not dst.exists() or dst.stat().st_size == 0:
        raise MediaError(f"فشل تطبيق الفلاتر إلى: {dst}")
    return dst


def escape_filter_path(path: str | Path) -> str:
    """يهرّب مساراً لاستخدامه داخل فلتر ffmpeg (مثل ``ass=`` أو ``subtitles=``)."""
    text = str(path)
    text = text.replace("\\", "/")
    text = text.replace(":", r"\:")
    text = text.replace("'", r"\'")
    text = text.replace("[", r"\[").replace("]", r"\]")
    text = text.replace(",", r"\,")
    return text
