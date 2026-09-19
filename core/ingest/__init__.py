"""محطة الإدخال — تحميل الفيديو من رابط أو ملف محلي."""

from .downloader import ingest, ingest_local, ingest_url, is_url, suggest_workspace_name

__all__ = ["ingest", "ingest_local", "ingest_url", "is_url", "suggest_workspace_name"]
