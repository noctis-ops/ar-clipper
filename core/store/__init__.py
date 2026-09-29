"""طبقة التخزين المشتركة (SQLite)."""

from .db import connect, database_path, table_names

__all__ = ["connect", "database_path", "table_names"]
