"""محطة التصدير (Export) — وضع الملف النهائي وملفاته المرافقة في مكانها.

تنظيم المخرجات (يمهّد لوحدة المكتبة في المرحلة 5):

    data/clips/<workspace>/<clip_id>/
        ├── <clip_id>.mp4          ← الفيديو النهائي
        ├── <clip_id>.srt/.vtt/.ass ← ملفات الترجمة المرافقة
        ├── <clip_id>.transcript.json
        └── <clip_id>.metadata.json
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from ..common.config import Settings, load_settings
from ..common.errors import MediaError
from ..common.ffmpeg import probe
from ..common.logging_utils import get_logger
from ..common.schemas import ClipResult, SourceVideo, Transcript

log = get_logger(__name__)


def clip_directory(workspace: str, clip_id: str, settings: Optional[Settings] = None) -> Path:
    settings = settings or load_settings()
    path = settings.path("paths.clips") / workspace / clip_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def finalize_clip(
    rendered_video: str | Path,
    *,
    clip_id: str,
    workspace: str,
    transcript: Optional[Transcript] = None,
    source: Optional[SourceVideo] = None,
    subtitle_files: Optional[Dict[str, str]] = None,
    stages: Optional[list[str]] = None,
    start: float = 0.0,
    end: float = 0.0,
    extra_metadata: Optional[Dict] = None,
    settings: Optional[Settings] = None,
) -> ClipResult:
    """ينقل الفيديو النهائي وملفاته المرافقة إلى مجلد المقطع ويكتب البيانات الوصفية."""
    settings = settings or load_settings()
    src = Path(rendered_video)
    if not src.exists():
        raise MediaError(f"الفيديو النهائي غير موجود: {src}")

    out_dir = clip_directory(workspace, clip_id, settings)
    final_video = out_dir / f"{clip_id}.mp4"
    if src.resolve() != final_video.resolve():
        shutil.move(str(src), str(final_video))

    # نقل ملفات الترجمة المرافقة بجانب الفيديو
    moved_subs: Dict[str, str] = {}
    for fmt, path in (subtitle_files or {}).items():
        p = Path(path)
        if not p.exists():
            continue
        target = out_dir / f"{clip_id}.{fmt}"
        if p.resolve() != target.resolve():
            shutil.copy2(p, target)
        moved_subs[fmt] = str(target)

    transcript_path = None
    if transcript is not None:
        transcript_path = out_dir / f"{clip_id}.transcript.json"
        transcript.save(transcript_path)

    info = probe(final_video)
    metadata = {
        "clip_id": clip_id,
        "workspace": workspace,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source.to_dict() if source else None,
        "range": {"start": round(start, 3), "end": round(end, 3)},
        "output": {
            "path": str(final_video),
            "duration": round(info.duration, 3),
            "width": info.width,
            "height": info.height,
            "fps": round(info.fps, 3),
            "size_bytes": final_video.stat().st_size,
        },
        "subtitles": moved_subs,
        "stages": stages or [],
        "pipeline_version": 1,
        "phase": 1,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    metadata_path = out_dir / f"{clip_id}.metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log.info("اكتمل التصدير: %s (%.1fs, %dx%d)", final_video, info.duration, info.width, info.height)

    return ClipResult(
        clip_id=clip_id,
        video_path=str(final_video),
        start=start,
        end=end,
        duration=info.duration,
        subtitle_files=moved_subs,
        transcript_path=str(transcript_path) if transcript_path else None,
        metadata_path=str(metadata_path),
        source=source,
        stages=stages or [],
        width=info.width,
        height=info.height,
    )


def cleanup_temp(paths: list[str | Path], settings: Optional[Settings] = None) -> None:
    """يحذف الملفات الوسيطة بعد نجاح المعالجة (القسم 14: توفير مساحة التخزين)."""
    settings = settings or load_settings()
    if not settings.get("runtime.cleanup_temp", True):
        return
    for p in paths:
        try:
            path = Path(p)
            if path.exists() and path.is_file():
                path.unlink()
        except OSError as exc:  # pragma: no cover
            log.debug("تعذّر حذف ملف مؤقت %s: %s", p, exc)
