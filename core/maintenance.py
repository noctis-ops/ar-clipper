"""صيانة مساحة العمل: تنظيف المخلفات ومنع تكرار العمل.

ثلاث وظائف صغيرة تحلّ مشاكل تشغيلية حقيقية:

1. ``sweep_tmp``   — الملفات الوسيطة تتراكم عند انقطاع المعالجة (لا يصل
   ``cleanup_temp``)، فيمتلئ القرص صامتاً. هذا يكنس القديم منها.
2. ``clip_exists`` — يمنع إعادة إنتاج مقطع موجود أصلاً (أساس الاستئناف).
3. ``disk_report`` — أين ذهبت المساحة، بلا أي تبعية.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .common.config import Settings, load_settings
from .common.logging_utils import get_logger
from .common.text_utils import slugify

log = get_logger(__name__)


@dataclass
class SweepResult:
    """حصيلة عملية تنظيف."""

    removed: int = 0
    freed_bytes: int = 0
    scanned: int = 0

    @property
    def freed_mb(self) -> float:
        return self.freed_bytes / (1024 * 1024)


def sweep_tmp(
    settings: Optional[Settings] = None,
    *,
    older_than_hours: float = 24.0,
    dry_run: bool = False,
) -> SweepResult:
    """يحذف الملفات الوسيطة الأقدم من المدة المحددة.

    ``older_than_hours=0`` يحذف كل شيء (استخدمه فقط وأنت متأكد أن لا معالجة
    جارية — ملفات المعالجة الحالية تعيش في نفس المجلد).
    """
    settings = settings or load_settings()
    tmp_dir = settings.path("paths.tmp")
    result = SweepResult()
    if not tmp_dir.exists():
        return result

    cutoff = time.time() - (older_than_hours * 3600)
    for path in tmp_dir.rglob("*"):
        if not path.is_file():
            continue
        result.scanned += 1
        try:
            stat = path.stat()
            if stat.st_mtime > cutoff:
                continue
            size = stat.st_size
            if not dry_run:
                path.unlink()
            result.removed += 1
            result.freed_bytes += size
        except OSError as exc:  # pragma: no cover
            log.debug("تعذّر حذف %s: %s", path, exc)

    if result.removed:
        verb = "سيُحذف" if dry_run else "حُذف"
        log.info("%s %d ملفاً مؤقتاً (%.1f MB).", verb, result.removed, result.freed_mb)
    return result


def clip_exists(
    workspace: str, clip_name: str, settings: Optional[Settings] = None
) -> Optional[Path]:
    """يرجع مسار المقطع إن كان منتَجاً مسبقاً، وإلا ``None``.

    أساس الاستئناف: لا معنى لإعادة ترميز مقطع مكتمل عند إعادة التشغيل.
    """
    settings = settings or load_settings()
    clip_id = slugify(clip_name) or clip_name
    candidate = settings.path("paths.clips") / workspace / clip_id / f"{clip_id}.mp4"
    return candidate if candidate.exists() and candidate.stat().st_size > 0 else None


def disk_report(settings: Optional[Settings] = None) -> List[tuple]:
    """يرجع [(الاسم، المسار، الحجم بالبايت، عدد الملفات)] لكل مجلد بيانات."""
    settings = settings or load_settings()
    rows = []
    for label, key in (
        ("المقاطع الناتجة", "paths.clips"),
        ("الفيديوهات الخام", "paths.raw_videos"),
        ("الترانسكربتات", "paths.transcripts"),
        ("سجل المقترحات", "paths.registry"),
        ("ملفات مؤقتة", "paths.tmp"),
    ):
        try:
            d = settings.path(key)
        except Exception:
            continue
        if not d.exists():
            rows.append((label, d, 0, 0))
            continue
        total = 0
        count = 0
        for f in d.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                    count += 1
                except OSError:
                    pass
        rows.append((label, d, total, count))
    return rows
