"""محطة الإدخال — تحميل الفيديو من رابط أو ملف محلي."""

from .downloader import ingest, ingest_local, ingest_url, is_url, suggest_workspace_name

__all__ = ["ingest", "ingest_local", "ingest_url", "is_url", "suggest_workspace_name"]
from .licensing import (
    LICENSE_PRESETS,
    describe_presets,
    expand as expand_license,
    is_publishable,
    preset_keys,
    resolve as resolve_license,
)

__all__ += [
    "LICENSE_PRESETS",
    "describe_presets",
    "expand_license",
    "is_publishable",
    "preset_keys",
    "resolve_license",
]
