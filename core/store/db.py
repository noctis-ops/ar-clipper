"""طبقة SQLite المشتركة — أساس المرحلتين 4 و5.

قاعدة بيانات واحدة تخدم المكتبة (المرحلة 5) والحملات (المرحلة 4)، لأن
الربط بينهما جوهري: كل مقطع يعود لفيديو في المكتبة، وقد يرتبط بحملة.

لماذا SQLite؟ مدمجة في Python (صفر تبعيات)، ملف واحد قابل للنسخ، وتدعم
FTS5 للبحث النصي الكامل — وهو ما تحتاجه المرحلة 5 بالضبط.

المخطط يُرقّى تلقائياً عبر ``PRAGMA user_version`` فلا يفقد المستخدم بياناته
عند تحديث الأداة.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..common.config import Settings, load_settings
from ..common.logging_utils import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 1

# القفل يحمي من كتابة متزامنة من خيوط الطابور (المرحلة 5، الخطوة 3)
_LOCK = threading.RLock()


SCHEMA = """
-- ==================== المكتبة (المرحلة 5) ====================

CREATE TABLE IF NOT EXISTS videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT UNIQUE NOT NULL,   -- معرّف المصدر (workspace)
    title        TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    origin       TEXT NOT NULL DEFAULT 'local',
    duration     REAL NOT NULL DEFAULT 0,
    width        INTEGER NOT NULL DEFAULT 0,
    height       INTEGER NOT NULL DEFAULT 0,
    language     TEXT NOT NULL DEFAULT '',
    license_note TEXT NOT NULL DEFAULT '',
    added_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transcripts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    track      TEXT NOT NULL DEFAULT 'source',  -- source | ar
    path       TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL DEFAULT '',
    UNIQUE(video_id, track)
);

-- بحث نصي كامل داخل كل الترانسكربتات (الخطوة 2 من المرحلة 5)
CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
    text,
    video_id UNINDEXED,
    track UNINDEXED,
    start UNINDEXED,
    end UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id     TEXT NOT NULL,
    video_id    INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    path        TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    hook        TEXT NOT NULL DEFAULT '',
    hashtags    TEXT NOT NULL DEFAULT '',
    start       REAL NOT NULL DEFAULT 0,
    end         REAL NOT NULL DEFAULT 0,
    duration    REAL NOT NULL DEFAULT 0,
    score       REAL NOT NULL DEFAULT 0,
    width       INTEGER NOT NULL DEFAULT 0,
    height      INTEGER NOT NULL DEFAULT 0,
    thumbnail   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(video_id, clip_id)
);

-- ==================== الحملات (المرحلة 4) ====================

CREATE TABLE IF NOT EXISTS campaigns (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT UNIQUE NOT NULL,
    platform_link       TEXT NOT NULL DEFAULT '',
    budget              REAL NOT NULL DEFAULT 0,
    rate_per_1000_views REAL NOT NULL DEFAULT 0,
    currency            TEXT NOT NULL DEFAULT 'USD',
    rules_notes         TEXT NOT NULL DEFAULT '',
    start_date          TEXT NOT NULL DEFAULT '',
    end_date            TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id         INTEGER REFERENCES clips(id) ON DELETE CASCADE,
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    platform        TEXT NOT NULL DEFAULT '',
    post_link       TEXT NOT NULL DEFAULT '',
    views_count     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    posted_at       TEXT NOT NULL DEFAULT '',
    last_checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    notes           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    author     TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL DEFAULT '',
    likes      INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ==================== الطابور (المرحلة 5، الخطوة 3) ====================

CREATE TABLE IF NOT EXISTS queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    options      TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'pending',
    priority     INTEGER NOT NULL DEFAULT 0,
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT NOT NULL DEFAULT '',
    result       TEXT NOT NULL DEFAULT '',
    added_at     TEXT NOT NULL DEFAULT (datetime('now')),
    started_at   TEXT NOT NULL DEFAULT '',
    finished_at  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);
CREATE INDEX IF NOT EXISTS idx_clips_campaign ON clips(campaign_id);
CREATE INDEX IF NOT EXISTS idx_posts_clip ON posts(clip_id);
CREATE INDEX IF NOT EXISTS idx_posts_campaign ON posts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status, priority DESC, id);
"""


def database_path(settings: Optional[Settings] = None) -> Path:
    settings = settings or load_settings()
    configured = settings.get("store.path", "data/arclipper.db")
    path = Path(configured)
    if not path.is_absolute():
        path = settings.root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _migrate(conn: sqlite3.Connection) -> None:
    """يطبّق المخطط ويرقّيه دون فقدان بيانات."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.executescript(SCHEMA)
    if current < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if current:
            log.info("رُقّي مخطط قاعدة البيانات: %d → %d", current, SCHEMA_VERSION)
    conn.commit()


@contextmanager
def connect(
    settings: Optional[Settings] = None, *, path: Optional[Path] = None
) -> Iterator[sqlite3.Connection]:
    """اتصال مُهيّأ بقاعدة البيانات (ينشئ المخطط عند أول استخدام)."""
    target = Path(path) if path else database_path(settings)
    target.parent.mkdir(parents=True, exist_ok=True)

    with _LOCK:
        conn = sqlite3.connect(str(target), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            # WAL يسمح بقارئ أثناء الكتابة — مهم للطابور مع الواجهة
            conn.execute("PRAGMA journal_mode = WAL")
            _migrate(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def rows_to_dicts(rows) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def table_names(settings: Optional[Settings] = None, *, path: Optional[Path] = None) -> List[str]:
    with connect(settings, path=path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]
