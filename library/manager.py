"""المكتبة والبحث — المرحلة 5، الخطوتان 1 و2.

تحوّل الأداة من «معالجة فيديو واحد» إلى نظام يعرف كل ما أنتجه: أي فيديو
عولج، وأي مقاطع خرجت منه، وأين قيل كل شيء.

البحث يستخدم **FTS5** المدمج في SQLite: يفهرس كل جملة من كل ترانسكربت،
فتسأل «أين تحدّث عن الاستثمار؟» عبر مئة فيديو في أجزاء من الثانية. بلا أي
تبعية خارجية.

العربية: المفهرس مضبوط على ``remove_diacritics 2`` فيطابق «الذكاء» مع
«الذَّكاء»، ونطبّع الاستعلام بأنفسنا لتوحيد الألف والتاء المربوطة.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from core.common.config import Settings, load_settings
from core.common.logging_utils import get_logger
from core.common.schemas import ClipResult, SourceVideo, Transcript
from core.store.db import connect, rows_to_dicts

log = get_logger(__name__)


# ============================================================ التطبيع


_DIACRITICS = re.compile(r"[\u064B-\u0652\u0640]")
_FTS_SPECIAL = re.compile(r'["*^(){}\[\]:]')


def normalize_arabic(text: str) -> str:
    """توحيد أشكال الحروف حتى يطابق البحث ما كتبه المستخدم فعلاً."""
    text = _DIACRITICS.sub("", text or "")
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه").replace("ى", "ي")
    text = text.replace("ؤ", "و").replace("ئ", "ي")
    return text.strip()


# سوابق عربية شائعة: العربية لصقية، فـ«استثمار» يجب أن تجد «الاستثمار»
# و«للاستثمار» و«بالاستثمار». FTS5 يطابق كلمات كاملة فقط، فنولّد بدائل.
_PREFIXES = ("ال", "و", "ف", "ب", "ل", "ك", "بال", "كال", "فال", "وال", "لل", "ولل")


def _word_variants(word: str) -> List[str]:
    """يولّد أشكال الكلمة مع السوابق الشائعة وبحث البادئة."""
    variants = {word}
    for prefix in _PREFIXES:
        variants.add(prefix + word)
        # والعكس: المستخدم كتب «الاستثمار» ونريد مطابقة «استثمار»
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            variants.add(word[len(prefix):])
    # بحث البادئة يغطي اللواحق (الجمع، الضمائر): استثمار* → استثمارات
    return sorted(variants)


def _fts_query(text: str, *, expand: bool = True) -> str:
    """يحوّل نص المستخدم إلى استعلام FTS5 آمن.

    الرموز ``* " ( )`` لها معنى خاص في FTS5 وقد تُفشل الاستعلام أو تُغيّر
    دلالته، فنزيلها ونقتبس كل كلمة على حدة.

    ``expand`` يفعّل التوسعة الصرفية العربية: كل كلمة تصير مجموعة بدائل
    (سوابق + بحث بادئة) مربوطة بـOR، والكلمات فيما بينها بـAND.
    """
    cleaned = _FTS_SPECIAL.sub(" ", normalize_arabic(text))
    words = [w for w in cleaned.split() if w]
    if not words:
        return ""

    if not expand:
        return " ".join(f'"{w}"' for w in words)

    groups = []
    for word in words:
        if len(word) < 3:  # كلمة قصيرة: لا توسعة (ضجيج أكثر من فائدة)
            groups.append(f'"{word}"')
            continue
        alts = [f'"{v}"*' for v in _word_variants(word)]
        groups.append("(" + " OR ".join(alts) + ")")
    return " AND ".join(groups)


# ============================================================ النماذج


@dataclass
class LibraryVideo:
    id: int
    video_id: str
    title: str
    source: str
    duration: float
    clips_count: int = 0
    added_at: str = ""
    language: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "video_id": self.video_id,
            "title": self.title,
            "source": self.source,
            "duration": round(self.duration, 2),
            "clips_count": self.clips_count,
            "added_at": self.added_at,
            "language": self.language,
        }


@dataclass
class SearchHit:
    """نتيجة بحث: الجملة وموضعها الزمني في فيديو محدّد."""

    video_id: str
    title: str
    track: str
    text: str
    start: float
    end: float
    snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "track": self.track,
            "text": self.text,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "snippet": self.snippet,
            "timestamp": f"{int(self.start // 60):02d}:{int(self.start % 60):02d}",
        }


# ============================================================ الفهرسة


def index_video(
    source: SourceVideo,
    *,
    settings: Optional[Settings] = None,
    workspace: str = "",
) -> int:
    """يسجّل فيديو مصدر في المكتبة ويرجع مفتاحه الداخلي."""
    settings = settings or load_settings()
    key = workspace or getattr(source, "video_id", "") or Path(source.path).stem

    with connect(settings) as conn:
        conn.execute(
            """
            INSERT INTO videos (video_id, title, source, origin, duration,
                                width, height, language, license_note)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                duration = excluded.duration,
                width = excluded.width,
                height = excluded.height,
                license_note = excluded.license_note
            """,
            (
                key,
                getattr(source, "title", "") or "",
                str(getattr(source, "path", "") or ""),
                getattr(source, "origin", "local") or "local",
                float(getattr(source, "duration", 0) or 0),
                int(getattr(source, "width", 0) or 0),
                int(getattr(source, "height", 0) or 0),
                getattr(source, "language", "") or "",
                getattr(source, "license_note", "") or "",
            ),
        )
        row = conn.execute("SELECT id FROM videos WHERE video_id = ?", (key,)).fetchone()
    return int(row["id"])


def index_transcript(
    transcript: Transcript,
    *,
    video_key: str,
    track: str = "source",
    path: str = "",
    settings: Optional[Settings] = None,
) -> int:
    """يفهرس ترانسكربتاً للبحث. يرجع عدد الجمل المفهرسة."""
    settings = settings or load_settings()
    segments = list(getattr(transcript, "segments", []) or [])
    if not segments:
        return 0

    with connect(settings) as conn:
        row = conn.execute("SELECT id FROM videos WHERE video_id = ?", (video_key,)).fetchone()
        if not row:
            log.warning("الفيديو %s غير مسجَّل — تخطّي فهرسة الترانسكربت.", video_key)
            return 0
        vid = int(row["id"])

        full_text = " ".join(s.text for s in segments)
        conn.execute(
            """
            INSERT INTO transcripts (video_id, track, path, text) VALUES (?,?,?,?)
            ON CONFLICT(video_id, track) DO UPDATE SET
                path = excluded.path, text = excluded.text
            """,
            (vid, track, str(path or ""), full_text),
        )

        # إعادة الفهرسة: نحذف القديم أولاً حتى لا تتكرر النتائج
        conn.execute(
            "DELETE FROM transcript_fts WHERE video_id = ? AND track = ?", (vid, track)
        )
        conn.executemany(
            "INSERT INTO transcript_fts (text, video_id, track, start, end) VALUES (?,?,?,?,?)",
            [
                (normalize_arabic(s.text), vid, track, float(s.start), float(s.end))
                for s in segments
                if (s.text or "").strip()
            ],
        )
    log.info("فُهرست %d جملة من %s (%s).", len(segments), video_key, track)
    return len(segments)


def index_clip(
    result: ClipResult,
    *,
    video_key: str,
    settings: Optional[Settings] = None,
    title: str = "",
    hook: str = "",
    hashtags: Optional[Sequence[str]] = None,
    score: float = 0.0,
    campaign: Optional[str] = None,
) -> int:
    """يسجّل مقطعاً مُنتجاً في المكتبة."""
    settings = settings or load_settings()

    with connect(settings) as conn:
        row = conn.execute("SELECT id FROM videos WHERE video_id = ?", (video_key,)).fetchone()
        vid = int(row["id"]) if row else None

        campaign_id = None
        if campaign:
            crow = conn.execute(
                "SELECT id FROM campaigns WHERE name = ?", (campaign,)
            ).fetchone()
            campaign_id = int(crow["id"]) if crow else None

        conn.execute(
            """
            INSERT INTO clips (clip_id, video_id, campaign_id, path, title, hook,
                               hashtags, start, end, duration, score, width, height, thumbnail)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(video_id, clip_id) DO UPDATE SET
                path = excluded.path, title = excluded.title, hook = excluded.hook,
                hashtags = excluded.hashtags, duration = excluded.duration,
                score = excluded.score, thumbnail = excluded.thumbnail,
                campaign_id = COALESCE(excluded.campaign_id, clips.campaign_id)
            """,
            (
                result.clip_id, vid, campaign_id, str(result.video_path),
                title, hook, json.dumps(list(hashtags or []), ensure_ascii=False),
                float(result.start), float(result.end), float(result.duration),
                float(score), int(result.width), int(result.height),
                str(getattr(result, "thumbnail_path", "") or ""),
            ),
        )
        out = conn.execute(
            "SELECT id FROM clips WHERE clip_id = ? AND (video_id IS ? OR video_id = ?)",
            (result.clip_id, vid, vid),
        ).fetchone()
    return int(out["id"]) if out else 0


# ============================================================ الاستعلام


def list_videos(
    *, settings: Optional[Settings] = None, limit: int = 100
) -> List[LibraryVideo]:
    settings = settings or load_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            """
            SELECT v.*, COUNT(c.id) AS clips_count
            FROM videos v LEFT JOIN clips c ON c.video_id = v.id
            GROUP BY v.id ORDER BY v.added_at DESC, v.id DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        LibraryVideo(
            id=r["id"], video_id=r["video_id"], title=r["title"], source=r["source"],
            duration=r["duration"], clips_count=r["clips_count"],
            added_at=r["added_at"], language=r["language"],
        )
        for r in rows
    ]


def list_clips(
    *,
    settings: Optional[Settings] = None,
    video_key: str = "",
    campaign: str = "",
    limit: int = 200,
) -> List[Dict[str, Any]]:
    settings = settings or load_settings()
    query = [
        """
        SELECT c.*, v.video_id AS source_key, v.title AS source_title,
               cam.name AS campaign_name
        FROM clips c
        LEFT JOIN videos v ON v.id = c.video_id
        LEFT JOIN campaigns cam ON cam.id = c.campaign_id
        """
    ]
    params: List[Any] = []
    conditions = []
    if video_key:
        conditions.append("v.video_id = ?")
        params.append(video_key)
    if campaign:
        conditions.append("cam.name = ?")
        params.append(campaign)
    if conditions:
        query.append("WHERE " + " AND ".join(conditions))
    query.append("ORDER BY c.created_at DESC, c.id DESC LIMIT ?")
    params.append(int(limit))

    with connect(settings) as conn:
        rows = conn.execute(" ".join(query), params).fetchall()
    return rows_to_dicts(rows)


def search(
    query: str,
    *,
    settings: Optional[Settings] = None,
    limit: int = 20,
    video_key: str = "",
    exact: bool = False,
) -> List[SearchHit]:
    """بحث نصي كامل عبر كل الترانسكربتات المفهرسة.

    ``exact=True`` يعطّل التوسعة الصرفية (مطابقة حرفية للكلمات).
    """
    settings = settings or load_settings()
    match = _fts_query(query, expand=not exact)
    if not match:
        return []

    sql = [
        """
        SELECT f.text, f.start, f.end, f.track, v.video_id, v.title,
               snippet(transcript_fts, 0, '«', '»', '…', 12) AS snip
        FROM transcript_fts f
        JOIN videos v ON v.id = f.video_id
        WHERE transcript_fts MATCH ?
        """
    ]
    params: List[Any] = [match]
    if video_key:
        sql.append("AND v.video_id = ?")
        params.append(video_key)
    sql.append("ORDER BY rank LIMIT ?")
    params.append(int(limit))

    with connect(settings) as conn:
        try:
            rows = conn.execute(" ".join(sql), params).fetchall()
        except Exception as exc:  # استعلام غير صالح رغم التنظيف
            log.warning("فشل البحث (%s) — تحقّق من صياغة الاستعلام.", exc)
            return []

    return [
        SearchHit(
            video_id=r["video_id"], title=r["title"], track=r["track"],
            text=r["text"], start=r["start"], end=r["end"], snippet=r["snip"],
        )
        for r in rows
    ]


def stats(*, settings: Optional[Settings] = None) -> Dict[str, Any]:
    settings = settings or load_settings()
    with connect(settings) as conn:
        def one(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0] or 0)

        return {
            "videos": one("SELECT COUNT(*) FROM videos"),
            "clips": one("SELECT COUNT(*) FROM clips"),
            "transcripts": one("SELECT COUNT(*) FROM transcripts"),
            "indexed_segments": one("SELECT COUNT(*) FROM transcript_fts"),
            "campaigns": one("SELECT COUNT(*) FROM campaigns"),
            "posts": one("SELECT COUNT(*) FROM posts"),
            "total_clip_seconds": float(
                conn.execute("SELECT COALESCE(SUM(duration),0) FROM clips").fetchone()[0]
            ),
        }


def forget_video(video_key: str, *, settings: Optional[Settings] = None) -> bool:
    """يحذف فيديو من المكتبة (مع مقاطعه وفهرسه). لا يحذف الملفات."""
    settings = settings or load_settings()
    with connect(settings) as conn:
        row = conn.execute("SELECT id FROM videos WHERE video_id = ?", (video_key,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM transcript_fts WHERE video_id = ?", (row["id"],))
        conn.execute("DELETE FROM videos WHERE id = ?", (row["id"],))
    return True
