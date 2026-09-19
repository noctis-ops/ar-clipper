"""محطة الإدخال (Ingest) — تحميل فيديو من رابط أو قبول ملف محلي.

المسؤوليات:
- التحقق من تأكيد الترخيص قبل أي معالجة (المبدأ 4 — إلزامي).
- التحميل عبر yt-dlp بجودة محدودة (720p افتراضياً — تكفي للمعالجة).
- قراءة البيانات الوصفية للملف الناتج.
- إخراج كائن ``SourceVideo`` موحّد لبقية خط الأنابيب.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Optional

from ..common.config import Settings, load_settings
from ..common.errors import DependencyError, IngestError, LicenseError
from ..common.ffmpeg import probe
from ..common.logging_utils import get_logger
from ..common.schemas import SourceVideo
from ..common.text_utils import slugify

log = get_logger(__name__)

URL_PREFIXES = ("http://", "https://", "www.")


def is_url(value: str) -> bool:
    v = str(value).strip().lower()
    return v.startswith(URL_PREFIXES)


def make_video_id(seed: str) -> str:
    """معرّف قصير ومستقر مشتق من الرابط/المسار."""
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _check_license(license_note: str, settings: Settings) -> str:
    """يفرض تسجيل سند الترخيص إن كان مطلوباً في الإعدادات."""
    note = (license_note or "").strip()
    if settings.get("ingest.require_license_ack", True) and not note:
        raise LicenseError(
            "مطلوب تسجيل مصدر/سند الترخيص قبل المعالجة (المبدأ 4 في الوثيقة).\n"
            "مرّر ‎--license \"وصف الإذن أو الرخصة\"‎ أو عطّل الاشتراط عبر "
            "ingest.require_license_ack=false في config/settings.yaml."
        )
    return note or "غير مسجّل (الاشتراط معطّل في الإعدادات)"


# ============================================================ ملف محلي


def ingest_local(
    path: str | Path,
    *,
    license_note: str = "",
    settings: Optional[Settings] = None,
    copy_into_library: bool = False,
) -> SourceVideo:
    """يقبل ملف فيديو محلياً ويجهّز بياناته الوصفية."""
    settings = settings or load_settings()
    note = _check_license(license_note, settings)

    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise IngestError(f"ملف الفيديو غير موجود: {src}")
    if src.is_dir():
        raise IngestError(f"المسار مجلد وليس ملفاً: {src}")

    final = src
    if copy_into_library:
        dest_dir = settings.path("paths.raw_videos")
        dest_dir.mkdir(parents=True, exist_ok=True)
        final = dest_dir / src.name
        if final.resolve() != src:
            shutil.copy2(src, final)
            log.info("نُسخ الملف إلى المكتبة: %s", final)

    info = probe(final)
    if not info.has_video and not info.has_audio:
        raise IngestError(f"الملف لا يحتوي مساراً صوتياً أو مرئياً صالحاً: {final}")

    video = SourceVideo(
        path=str(final),
        title=src.stem,
        origin="local",
        url=None,
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        license_note=note,
        video_id=make_video_id(str(final)),
    )
    log.info(
        "تم قبول الملف المحلي: %s (%.1fs, %dx%d)",
        final.name,
        video.duration,
        video.width,
        video.height,
    )
    return video


# ============================================================ رابط


def ingest_url(
    url: str,
    *,
    license_note: str = "",
    settings: Optional[Settings] = None,
    max_height: Optional[int] = None,
) -> SourceVideo:
    """يحمّل فيديو من رابط عبر yt-dlp (مفتوح المصدر، مجاني)."""
    settings = settings or load_settings()
    note = _check_license(license_note, settings)

    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("yt-dlp غير مثبّت. نفّذ: pip install yt-dlp") from exc

    height = int(max_height or settings.get("ingest.max_height", 720))
    container = str(settings.get("ingest.container", "mp4"))
    out_dir = settings.path("paths.raw_videos")
    out_dir.mkdir(parents=True, exist_ok=True)

    vid = make_video_id(url)
    outtmpl = str(out_dir / f"%(title).80B__{vid}.%(ext)s")

    ydl_opts = {
        "format": (
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={height}]/best"
        ),
        "merge_output_format": container,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
        "concurrent_fragment_downloads": 4,
    }

    log.info("جاري التحميل من الرابط (حد الجودة %dp)...", height)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=True)
            downloaded = Path(ydl.prepare_filename(meta))
    except Exception as exc:
        raise IngestError(f"فشل تحميل الفيديو من الرابط: {exc}") from exc

    if not downloaded.exists():
        # yt-dlp قد يغيّر الامتداد بعد الدمج
        candidates = sorted(out_dir.glob(f"*{vid}.*"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise IngestError(f"لم يُعثر على الملف المحمَّل في: {out_dir}")
        downloaded = candidates[-1]

    info = probe(downloaded)
    title = str(meta.get("title") or downloaded.stem)
    video = SourceVideo(
        path=str(downloaded),
        title=title,
        origin="url",
        url=url,
        duration=info.duration or float(meta.get("duration") or 0.0),
        width=info.width,
        height=info.height,
        fps=info.fps,
        license_note=note,
        video_id=vid,
    )
    log.info("اكتمل التحميل: %s (%.1fs)", downloaded.name, video.duration)
    return video


# ============================================================ واجهة موحّدة


def ingest(
    source: str | Path,
    *,
    license_note: str = "",
    settings: Optional[Settings] = None,
    copy_into_library: bool = False,
) -> SourceVideo:
    """نقطة الدخول الموحّدة: تكتشف تلقائياً إن كان المصدر رابطاً أو ملفاً."""
    text = str(source).strip()
    if is_url(text):
        return ingest_url(text, license_note=license_note, settings=settings)
    return ingest_local(
        text,
        license_note=license_note,
        settings=settings,
        copy_into_library=copy_into_library,
    )


def suggest_workspace_name(video: SourceVideo) -> str:
    """اسم مجلد عمل آمن ومميّز لهذا المصدر."""
    return f"{slugify(video.title, max_len=48)}__{video.video_id}"
