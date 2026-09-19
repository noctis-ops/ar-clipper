"""واجهة ويب محلية بسيطة (FastAPI) — بديل بصري عن CLI.

تعمل محلياً بالكامل على جهازك، لا ترفع أي شيء لأي خادم خارجي
(المبدأ 2: محلي أولاً).

التشغيل:
    ar-clipper serve
    ثم افتح:  http://127.0.0.1:8000

المعمارية: هذه الواجهة **طبقة رقيقة فوق ``core/``** ولا تحتوي أي منطق
معالجة خاص بها — وهذا ما يُبقي الباب مفتوحاً للمرحلة 6 (SaaS) دون
إعادة كتابة.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from core.common.config import load_settings
from core.common.errors import ArClipperError
from core.common.ffmpeg import probe
from core.common.logging_utils import get_logger
from core.common.schemas import ClipRequest
from core.common.text_utils import human_duration, parse_timestamp
from core.ingest.licensing import describe_presets
from core.pipeline import PipelineOptions, run_pipeline
from core.presets import apply_preset_to_settings, get_preset, load_presets

log = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="AR-Clipper", docs_url="/api/docs")


# ============================================================ حالة المهام


@dataclass
class Job:
    """مهمة معالجة واحدة، مع سجل أحداث للبث الحي إلى المتصفح."""

    id: str
    status: str = "pending"  # pending | running | done | error
    source: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    _listeners: List[queue.Queue] = field(default_factory=list)

    def emit(self, kind: str, message: str, **extra: Any) -> None:
        event = {"kind": kind, "message": message, "ts": time.time(), **extra}
        self.events.append(event)
        for q in list(self._listeners):
            try:
                q.put_nowait(event)
            except Exception:  # pragma: no cover
                pass

    def listen(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        for event in self.events:  # أعد إرسال ما فات
            q.put_nowait(event)
        self._listeners.append(q)
        return q


JOBS: Dict[str, Job] = {}


# ============================================================ النماذج


class ClipPayload(BaseModel):
    source: str
    start: str = "00:00:00"
    end: str = "00:00:45"
    preset: str = "campaign"
    license_key: str = "personal_test"
    name: Optional[str] = None


class ProbePayload(BaseModel):
    source: str


# ============================================================ المسارات


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/options")
def options() -> Dict[str, Any]:
    """كل ما تحتاجه الواجهة لبناء القوائم — مصدرها نفس ملفات الإعدادات."""
    settings = load_settings()
    return {
        "presets": [
            {"key": k, "label": p.label, "description": p.description}
            for k, p in load_presets().items()
        ],
        "licenses": describe_presets(),
        "defaults": {
            "license_mode": settings.get("ingest.license_mode", "default"),
            "default_license": settings.get("ingest.default_license", "personal_test"),
        },
    }


@app.post("/api/probe")
def probe_source(payload: ProbePayload) -> Dict[str, Any]:
    """معلومات ملف محلي — تساعد المستخدم على اختيار المدى."""
    src = payload.source.strip()
    if src.lower().startswith(("http://", "https://", "www.")):
        return {"kind": "url", "message": "رابط — ستُقرأ التفاصيل عند التحميل."}
    path = Path(src).expanduser()
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"الملف غير موجود: {path}")
    try:
        info = probe(path)
    except ArClipperError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "kind": "local",
        "duration": info.duration,
        "duration_human": human_duration(info.duration),
        "width": info.width,
        "height": info.height,
        "has_audio": info.has_audio,
    }


def _run_job(job: Job, payload: ClipPayload) -> None:
    """ينفّذ خط الأنابيب في خيط منفصل ويبثّ التقدّم."""
    try:
        job.status = "running"
        settings = load_settings()

        chosen = get_preset(payload.preset)
        apply_preset_to_settings(chosen, settings)

        options = PipelineOptions(license_note=payload.license_key)
        for key, value in chosen.options.items():
            if hasattr(options, key):
                setattr(options, key, value)
        if payload.name:
            options.clip_name = payload.name

        job.emit("stage", f"المسار الجاهز: {chosen.label}")

        requests = [
            ClipRequest(
                start=parse_timestamp(payload.start), end=parse_timestamp(payload.end)
            )
        ]

        results = run_pipeline(
            payload.source,
            requests,
            options=options,
            settings=settings,
            progress=lambda stage, msg: job.emit("stage", msg, stage=stage),
        )

        for r in results:
            job.results.append(
                {
                    "clip_id": r.clip_id,
                    "video_path": r.video_path,
                    "duration": r.duration,
                    "duration_human": human_duration(r.duration),
                    "width": r.width,
                    "height": r.height,
                    "subtitles": r.subtitle_files,
                    "stages": r.stages,
                }
            )
        job.status = "done"
        job.emit("done", f"اكتمل — {len(results)} مقطع")

    except ArClipperError as exc:
        job.status = "error"
        job.error = str(exc)
        job.emit("error", str(exc))
    except Exception as exc:  # pragma: no cover
        job.status = "error"
        job.error = f"خطأ غير متوقع: {exc}"
        job.emit("error", job.error)
        log.exception("فشل غير متوقع في المهمة %s", job.id)


@app.post("/api/clip")
def create_clip(payload: ClipPayload) -> Dict[str, str]:
    if not payload.source.strip():
        raise HTTPException(status_code=400, detail="حدّد رابطاً أو مسار ملف.")
    job = Job(id=uuid.uuid4().hex[:12], source=payload.source)
    JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job, payload), daemon=True).start()
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="المهمة غير موجودة.")
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "results": job.results,
        "events": job.events[-40:],
    }


@app.get("/api/jobs/{job_id}/stream")
def job_stream(job_id: str) -> StreamingResponse:
    """بث حي للتقدّم عبر Server-Sent Events."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="المهمة غير موجودة.")

    def generate():
        import json

        q = job.listen()
        while True:
            try:
                event = q.get(timeout=30)
            except queue.Empty:
                yield ": keepalive\n\n"
                if job.status in {"done", "error"}:
                    break
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["kind"] in {"done", "error"}:
                break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/file")
def get_file(path: str):
    """يقدّم المقطع الناتج للمعاينة/التحميل (ضمن مجلد المخرجات فقط)."""
    settings = load_settings()
    target = Path(path).resolve()
    allowed = settings.path("paths.clips").resolve()
    if not str(target).startswith(str(allowed)):
        raise HTTPException(status_code=403, detail="مسار غير مسموح.")
    if not target.exists():
        raise HTTPException(status_code=404, detail="الملف غير موجود.")
    return FileResponse(target)


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
