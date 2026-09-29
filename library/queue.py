"""الطابور — المرحلة 5، الخطوة 3.

معيار النجاح المعلن: «أضف 10 فيديوهات قبل النوم، واستيقظ على عشرات المقاطع
الجاهزة». هذا ما يحوّل الأداة من *أداة* إلى *نظام*.

التصميم: الطابور مخزَّن في **SQLite** لا في الذاكرة، ولهذا سببان:
1. ينجو من إغلاق البرنامج — تُكمل من حيث توقفت
2. يمكن لعملية أخرى (الواجهة) أن تقرأ الحالة أثناء العمل

المعالجة تسلسلية بخيط واحد عمداً: الترميز يشبع المعالج أصلاً، وتشغيل
مهمتين معاً يُبطئ الاثنتين ويخنق الجهاز.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.common.config import Settings, load_settings
from core.common.logging_utils import get_logger
from core.store.db import connect, rows_to_dicts

log = get_logger(__name__)

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class QueueItem:
    id: int
    source: str
    options: Dict[str, Any] = field(default_factory=dict)
    status: str = PENDING
    priority: int = 0
    attempts: int = 0
    error: str = ""
    result: str = ""
    added_at: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "options": self.options,
            "status": self.status,
            "priority": self.priority,
            "attempts": self.attempts,
            "error": self.error[:400],
            "result": self.result,
            "added_at": self.added_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _row_to_item(row) -> QueueItem:
    try:
        options = json.loads(row["options"] or "{}")
    except (TypeError, ValueError):
        options = {}
    return QueueItem(
        id=row["id"], source=row["source"], options=options, status=row["status"],
        priority=row["priority"], attempts=row["attempts"], error=row["error"],
        result=row["result"], added_at=row["added_at"],
        started_at=row["started_at"], finished_at=row["finished_at"],
    )


# ============================================================ إدارة الطابور


def enqueue(
    source: str,
    *,
    options: Optional[Dict[str, Any]] = None,
    priority: int = 0,
    settings: Optional[Settings] = None,
) -> int:
    """يضيف مصدراً للطابور ويرجع رقمه."""
    settings = settings or load_settings()
    payload = json.dumps(options or {}, ensure_ascii=False)
    with connect(settings) as conn:
        cur = conn.execute(
            "INSERT INTO queue (source, options, priority, added_at) VALUES (?,?,?,?)",
            (str(source), payload, int(priority), _now()),
        )
        item_id = int(cur.lastrowid)
    log.info("أُضيف للطابور #%d: %s", item_id, source)
    return item_id


def enqueue_many(
    sources: List[str],
    *,
    options: Optional[Dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> List[int]:
    return [enqueue(s, options=options, settings=settings) for s in sources]


def list_items(
    *, status: str = "", settings: Optional[Settings] = None, limit: int = 100
) -> List[QueueItem]:
    settings = settings or load_settings()
    sql = "SELECT * FROM queue"
    params: List[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY priority DESC, id ASC LIMIT ?"
    params.append(int(limit))
    with connect(settings) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_item(r) for r in rows]


def next_pending(*, settings: Optional[Settings] = None) -> Optional[QueueItem]:
    settings = settings or load_settings()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT * FROM queue WHERE status = ? ORDER BY priority DESC, id ASC LIMIT 1",
            (PENDING,),
        ).fetchone()
    return _row_to_item(row) if row else None


def update_status(
    item_id: int,
    status: str,
    *,
    error: str = "",
    result: str = "",
    settings: Optional[Settings] = None,
) -> None:
    settings = settings or load_settings()
    with connect(settings) as conn:
        if status == RUNNING:
            conn.execute(
                "UPDATE queue SET status=?, started_at=?, attempts=attempts+1 WHERE id=?",
                (status, _now(), item_id),
            )
        elif status in (DONE, FAILED, CANCELLED):
            conn.execute(
                "UPDATE queue SET status=?, finished_at=?, error=?, result=? WHERE id=?",
                (status, _now(), error[:2000], result[:4000], item_id),
            )
        else:
            conn.execute("UPDATE queue SET status=? WHERE id=?", (status, item_id))


def cancel(item_id: int, *, settings: Optional[Settings] = None) -> bool:
    """يلغي مهمة معلّقة. المهمة الجارية لا تُلغى (لا نقطع ترميزاً)."""
    settings = settings or load_settings()
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE queue SET status=?, finished_at=? WHERE id=? AND status=?",
            (CANCELLED, _now(), item_id, PENDING),
        )
        return cur.rowcount > 0


def clear(
    *, status: str = "", settings: Optional[Settings] = None
) -> int:
    """يحذف مهام من الطابور. بلا ``status`` يحذف المنتهية فقط."""
    settings = settings or load_settings()
    with connect(settings) as conn:
        if status:
            cur = conn.execute("DELETE FROM queue WHERE status = ?", (status,))
        else:
            cur = conn.execute(
                "DELETE FROM queue WHERE status IN (?,?,?)", (DONE, FAILED, CANCELLED)
            )
        return int(cur.rowcount)


def reset_stuck(*, settings: Optional[Settings] = None) -> int:
    """يعيد المهام العالقة في ``running`` إلى ``pending``.

    يحدث لو أُغلق البرنامج أثناء المعالجة. بدون هذا تبقى المهمة عالقة للأبد.
    """
    settings = settings or load_settings()
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE queue SET status=? WHERE status=?", (PENDING, RUNNING)
        )
        count = int(cur.rowcount)
    if count:
        log.info("أُعيدت %d مهمة عالقة إلى قائمة الانتظار.", count)
    return count


def summary(*, settings: Optional[Settings] = None) -> Dict[str, int]:
    settings = settings or load_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM queue GROUP BY status"
        ).fetchall()
    counts = {r["status"]: int(r["n"]) for r in rows}
    counts["total"] = sum(counts.values())
    return counts


# ============================================================ التشغيل


@dataclass
class QueueRun:
    """حصيلة تشغيل الطابور."""

    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    clips: int = 0
    elapsed: float = 0.0
    errors: List[str] = field(default_factory=list)


def run_queue(
    *,
    settings: Optional[Settings] = None,
    worker: Optional[Callable[[QueueItem], Any]] = None,
    limit: int = 0,
    stop_event: Optional[threading.Event] = None,
    on_event: Optional[Callable[[str, QueueItem, Any], None]] = None,
    continue_on_error: bool = True,
) -> QueueRun:
    """يعالج الطابور حتى ينتهي أو يُطلب التوقف.

    ``worker`` قابل للحقن لتسهيل الاختبار؛ الافتراضي ينفّذ خط الأنابيب
    الكامل عبر ``_default_worker``.
    """
    settings = settings or load_settings()
    worker = worker or _default_worker
    run = QueueRun()
    started = time.time()

    reset_stuck(settings=settings)

    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("أُوقف الطابور بناءً على طلب.")
            break
        if limit and run.processed >= limit:
            break

        item = next_pending(settings=settings)
        if item is None:
            break

        run.processed += 1
        update_status(item.id, RUNNING, settings=settings)
        if on_event:
            on_event("start", item, None)
        log.info("▶ المهمة #%d: %s", item.id, item.source)

        try:
            outcome = worker(item)
            count = len(outcome) if isinstance(outcome, (list, tuple)) else 1
            run.succeeded += 1
            run.clips += count
            update_status(
                item.id, DONE,
                result=json.dumps({"clips": count}, ensure_ascii=False),
                settings=settings,
            )
            if on_event:
                on_event("done", item, outcome)
            log.info("✅ المهمة #%d اكتملت (%d مقطع).", item.id, count)
        except Exception as exc:
            run.failed += 1
            detail = f"{type(exc).__name__}: {exc}"
            run.errors.append(f"#{item.id} {detail}")
            update_status(item.id, FAILED, error=traceback.format_exc(), settings=settings)
            if on_event:
                on_event("error", item, exc)
            log.error("❌ المهمة #%d فشلت: %s", item.id, detail)
            # مهمة واحدة فاشلة لا توقف ليلة كاملة من المعالجة
            if not continue_on_error:
                break

    run.elapsed = time.time() - started
    return run


def _default_worker(item: QueueItem) -> Any:
    """ينفّذ خط الأنابيب الكامل على عنصر طابور.

    يُستورد داخلياً لتفادي الاستيراد الدائري (pipeline يعرف المكتبة).
    """
    from core.common.schemas import ClipRequest
    from core.pipeline import PipelineOptions, run_pipeline
    from core.suggest import suggest_clips

    options = dict(item.options or {})
    settings = load_settings().clone()

    preset = options.get("preset")
    if preset:
        from core.presets import apply_preset_to_settings, get_preset

        apply_preset_to_settings(get_preset(preset), settings)

    template = options.get("template")
    if template:
        from core.design.templates import apply_template, get_template

        apply_template(get_template(template, settings), settings)

    if options.get("face_track"):
        settings.data.setdefault("reframe", {})["mode"] = "face_track"
    if options.get("animated_subs"):
        settings.data.setdefault("subtitles", {})["animated"] = True

    pipeline_options = PipelineOptions(
        license_note=options.get("license", "personal_test"),
        translate=bool(options.get("translate", True)),
        subtitles=bool(options.get("subtitles", True)),
    )

    # وضعان: مدى صريح، أو اقتراح تلقائي ثم إنتاج الأفضل
    if options.get("start") is not None and options.get("end") is not None:
        requests = [
            ClipRequest(
                start=float(options["start"]),
                end=float(options["end"]),
                name=options.get("name"),
            )
        ]
        return run_pipeline(
            item.source, requests, options=pipeline_options, settings=settings
        )

    count = int(options.get("count", 3))
    analysis = suggest_clips(
        item.source, count=count, options=pipeline_options, settings=settings
    )
    chosen = analysis.suggestions[:count]
    if not chosen:
        return []

    requests = [
        ClipRequest(
            start=s.start, end=s.end, name=f"auto-{i + 1:02d}",
            title=s.title, hook=s.hook,
        )
        for i, s in enumerate(chosen)
    ]
    return run_pipeline(
        item.source, requests, options=pipeline_options, settings=settings,
        source_video=analysis.source, transcript=analysis.transcript,
    )
