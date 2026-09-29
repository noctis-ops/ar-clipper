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

import hashlib

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
from core.common.ffmpeg import probe, run_ffmpeg
from core.common.logging_utils import get_logger
from core.common.schemas import ClipRequest
from core.common.text_utils import human_duration, parse_timestamp
from core.ingest.licensing import describe_presets
from core.pipeline import PipelineOptions, run_pipeline
from core.design.templates import apply_template, get_template, load_templates
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
# نتائج التحليل محفوظة بالذاكرة ليتمكّن المستخدم من اختيار ما يُنتَج
ANALYSES: Dict[str, Any] = {}

# كل SuggestionSet يحمل ترانسكربتاً كاملاً، وكل Job يحمل سجل أحداثه.
# بلا سقف تنمو الذاكرة بلا حد في الجلسات الطويلة — نُبقي الأحدث فقط.
MAX_JOBS = 40
MAX_ANALYSES = 10


def _prune(store: Dict[str, Any], limit: int) -> None:
    """يحذف الأقدم عند تجاوز السقف (الإدراج في dict مرتّب زمنياً)."""
    while len(store) > limit:
        store.pop(next(iter(store)), None)


# ============================================================ النماذج


class ClipPayload(BaseModel):
    source: str
    start: str = "00:00:00"
    end: str = "00:00:45"
    preset: str = "campaign"
    license_key: str = "personal_test"
    name: Optional[str] = None
    # المرحلة 3
    template: str = ""
    face_track: bool = False
    animated_subs: bool = False
    hook: str = ""


class ProbePayload(BaseModel):
    source: str


class SuggestPayload(BaseModel):
    """طلب تحليل واقتراح مقاطع (المرحلة 2)."""

    source: str
    count: int = 6
    engine: str = ""          # heuristic | llm | hybrid (فارغ = من الإعدادات)
    license_key: str = "personal_test"
    diarize: bool = True
    translate: bool = True


class ProducePayload(BaseModel):
    """إنتاج مقاطع مختارة من نتيجة تحليل سابقة."""

    analysis_id: str
    indices: List[int] = []
    preset: str = "campaign"
    license_key: str = "personal_test"


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
        "templates": [
            {"key": k, "label": t.label, "description": t.description}
            for k, t in load_templates(settings).items()
        ],
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
        # نسخة مستقلة لكل مهمة: الكائن المُخزَّن بالكاش مشترك بين كل الطلبات،
        # فتطبيق مسار جاهز عليه مباشرةً يسرّب إعدادات مهمة إلى التالية.
        settings = load_settings().clone()

        chosen = get_preset(payload.preset)
        apply_preset_to_settings(chosen, settings)

        # المرحلة 3: القالب أولاً ثم تتجاوزه المفاتيح الصريحة
        if getattr(payload, "template", ""):
            try:
                apply_template(get_template(payload.template, settings), settings)
            except ArClipperError as exc:
                log.warning("قالب غير صالح (%s) — سيُتجاهل.", exc)
        if getattr(payload, "face_track", False):
            settings.data.setdefault("reframe", {})["mode"] = "face_track"
        if getattr(payload, "animated_subs", False):
            settings.data.setdefault("subtitles", {})["animated"] = True

        options = PipelineOptions(license_note=payload.license_key)
        for key, value in chosen.options.items():
            if hasattr(options, key):
                setattr(options, key, value)
        if payload.name:
            options.clip_name = payload.name

        job.emit("stage", f"المسار الجاهز: {chosen.label}")

        requests = [
            ClipRequest(
                start=parse_timestamp(payload.start),
                end=parse_timestamp(payload.end),
                hook=getattr(payload, "hook", "") or None,
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
                    # المرحلة 3: الصورة المصغّرة المولَّدة تلقائياً
                    "thumbnail_path": r.thumbnail_path,
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
    _prune(JOBS, MAX_JOBS)
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


def _run_suggest(job: Job, payload: SuggestPayload) -> None:
    """يحلّل المصدر ويقترح مقاطع، في خيط منفصل مع بثّ حي."""
    from core.suggest import suggest_clips

    try:
        job.status = "running"
        settings = load_settings().clone()
        options = PipelineOptions(
            license_note=payload.license_key, translate=payload.translate
        )
        result = suggest_clips(
            payload.source,
            options=options,
            settings=settings,
            progress=lambda stage, msg: job.emit("stage", msg, stage=stage),
            max_moments=payload.count,
            analyze_engine=payload.engine or None,
            diarize=payload.diarize,
        )

        ANALYSES[job.id] = result
        _prune(ANALYSES, MAX_ANALYSES)
        job.results = [s.to_dict() for s in result.suggestions]
        job.status = "done"

        if not result.suggestions:
            job.emit("done", "لم يُعثر على مقاطع مقترحة — جرّب محرك heuristic.")
        else:
            job.emit(
                "done",
                f"{len(result.suggestions)} مقترح جاهز للمراجعة",
                speakers=result.speakers,
                safety=result.safety.to_dict() if result.safety else None,
            )
    except ArClipperError as exc:
        job.status = "error"
        job.error = str(exc)
        job.emit("error", str(exc))
    except Exception as exc:  # pragma: no cover
        job.status = "error"
        job.error = f"خطأ غير متوقع: {exc}"
        job.emit("error", job.error)
        log.exception("فشل غير متوقع في التحليل %s", job.id)


@app.post("/api/suggest")
def create_suggestion(payload: SuggestPayload) -> Dict[str, str]:
    """يبدأ تحليلاً يقترح أفضل المقاطع تلقائياً."""
    if not payload.source.strip():
        raise HTTPException(status_code=400, detail="حدّد رابطاً أو مسار ملف.")
    job = Job(id=uuid.uuid4().hex[:12], source=payload.source)
    JOBS[job.id] = job
    _prune(JOBS, MAX_JOBS)
    threading.Thread(target=_run_suggest, args=(job, payload), daemon=True).start()
    return {"job_id": job.id}


def _run_produce(job: Job, payload: ProducePayload, analysis: Any) -> None:
    """ينتج المقاطع المختارة من تحليل سابق (بلا إعادة تفريغ)."""
    try:
        job.status = "running"
        settings = load_settings().clone()
        chosen = get_preset(payload.preset)
        apply_preset_to_settings(chosen, settings)

        options = PipelineOptions(license_note=payload.license_key)
        for key, value in chosen.options.items():
            if hasattr(options, key):
                setattr(options, key, value)

        requests = analysis.to_clip_requests(payload.indices or None)
        if not requests:
            raise ArClipperError("لم يُطابق أي مقترح الأرقام المحددة.")

        job.emit("stage", f"إنتاج {len(requests)} مقطعاً — {chosen.label}")

        results = run_pipeline(
            analysis.source.path,
            requests,
            options=options,
            settings=settings,
            progress=lambda stage, msg: job.emit("stage", msg, stage=stage),
            source_video=analysis.source,
            transcript=analysis.transcript,  # لا إعادة تفريغ
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
                    # المرحلة 3: الصورة المصغّرة المولَّدة تلقائياً
                    "thumbnail_path": r.thumbnail_path,
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
        log.exception("فشل غير متوقع في الإنتاج %s", job.id)


@app.post("/api/produce")
def produce_from_analysis(payload: ProducePayload) -> Dict[str, str]:
    """ينتج مقاطع مختارة من نتيجة تحليل سابقة."""
    analysis = ANALYSES.get(payload.analysis_id)
    if analysis is None:
        raise HTTPException(
            status_code=404, detail="نتيجة التحليل غير موجودة — أعد التحليل."
        )
    job = Job(id=uuid.uuid4().hex[:12], source=analysis.source.path)
    JOBS[job.id] = job
    _prune(JOBS, MAX_JOBS)
    threading.Thread(target=_run_produce, args=(job, payload, analysis), daemon=True).start()
    return {"job_id": job.id}


@app.get("/api/preview")
def preview_moment(source: str, start: float = 0.0, end: float = 0.0):
    """معاينة سريعة للحظة من المصدر قبل إنتاجها.

    لا ترميز كامل ولا إعادة تأطير — مجرد نسخ المجرى (``-c copy``) لبضع
    ثوانٍ. الغرض أن يسمع المستخدم اللحظة ويقرّر قبل دفع كلفة الإنتاج،
    وهي الخطوة التي كانت ناقصة مقارنةً بالأدوات التجارية.
    """
    src = Path(source)
    if not src.exists():
        raise HTTPException(status_code=404, detail="المصدر غير موجود.")

    duration = max(1.0, min(float(end) - float(start), 90.0))
    settings = load_settings()
    cache = settings.path("paths.tmp") / "previews"
    cache.mkdir(parents=True, exist_ok=True)

    key = hashlib.sha1(
        f"{src.resolve()}|{start:.2f}|{duration:.2f}".encode("utf-8")
    ).hexdigest()[:16]
    out = cache / f"{key}.mp4"

    if not out.exists():
        try:
            run_ffmpeg([
                "-ss", f"{float(start):.3f}", "-i", str(src),
                "-t", f"{duration:.3f}",
                # نسخ المجرى: شبه فوري ولا يستهلك المعالج
                "-c", "copy", "-avoid_negative_ts", "make_zero",
                str(out),
            ])
        except Exception:
            # بعض الصيغ لا تقبل القصّ بالنسخ عند نقطة ليست keyframe
            run_ffmpeg([
                "-ss", f"{float(start):.3f}", "-i", str(src),
                "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                "-vf", "scale=-2:360", "-c:a", "aac", "-b:a", "96k",
                str(out),
            ])

    return FileResponse(out, media_type="video/mp4")


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


# ============================================================ المكتبة والحملات (المرحلتان 4 و5)


@app.get("/api/library/videos")
def api_library_videos(limit: int = 50) -> Dict[str, Any]:
    from library.manager import list_videos

    return {"videos": [v.to_dict() for v in list_videos(limit=limit)]}


@app.get("/api/library/clips")
def api_library_clips(video: str = "", campaign: str = "", limit: int = 100) -> Dict[str, Any]:
    from library.manager import list_clips

    return {"clips": list_clips(video_key=video, campaign=campaign, limit=limit)}


@app.get("/api/library/search")
def api_library_search(q: str, limit: int = 20, exact: bool = False) -> Dict[str, Any]:
    from library.manager import search as lib_search

    hits = lib_search(q, limit=limit, exact=exact)
    return {"query": q, "count": len(hits), "hits": [h.to_dict() for h in hits]}


@app.get("/api/library/stats")
def api_library_stats() -> Dict[str, Any]:
    from library.manager import stats as lib_stats

    return lib_stats()


@app.get("/api/queue")
def api_queue_list(status: str = "") -> Dict[str, Any]:
    from library.queue import list_items, summary

    return {
        "summary": summary(),
        "items": [i.to_dict() for i in list_items(status=status)],
    }


class QueuePayload(BaseModel):
    sources: List[str]
    count: int = 3
    preset: str = "campaign"
    template: str = ""
    face_track: bool = False
    animated_subs: bool = False
    license_key: str = "personal_test"


@app.post("/api/queue")
def api_queue_add(payload: QueuePayload) -> Dict[str, Any]:
    from library.queue import enqueue

    options = {
        "count": payload.count, "preset": payload.preset,
        "license": payload.license_key, "face_track": payload.face_track,
        "animated_subs": payload.animated_subs,
    }
    if payload.template:
        options["template"] = payload.template

    ids = [enqueue(src, options=options) for src in payload.sources if src.strip()]
    return {"added": len(ids), "ids": ids}


@app.post("/api/queue/run")
def api_queue_run() -> Dict[str, Any]:
    """يشغّل الطابور في خيط خلفي ويرجع فوراً."""
    from library.queue import run_queue, summary

    pending = summary().get("pending", 0)
    if not pending:
        return {"started": False, "reason": "لا مهام معلّقة."}

    def worker():
        try:
            run_queue()
        except Exception as exc:  # pragma: no cover
            log.error("فشل تشغيل الطابور: %s", exc)

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True, "pending": pending}


@app.post("/api/queue/{item_id}/cancel")
def api_queue_cancel(item_id: int) -> Dict[str, Any]:
    from library.queue import cancel

    return {"cancelled": cancel(item_id)}


@app.get("/api/campaigns")
def api_campaigns() -> Dict[str, Any]:
    from campaign.manager import list_campaigns

    return {"campaigns": [c.to_dict() for c in list_campaigns()]}


class CampaignPayload(BaseModel):
    name: str
    rate_per_1000_views: float
    budget: float = 0.0
    platform_link: str = ""
    currency: str = "USD"
    rules_notes: str = ""


@app.post("/api/campaigns")
def api_campaign_add(payload: CampaignPayload) -> Dict[str, Any]:
    from campaign.manager import add_campaign

    try:
        campaign_id = add_campaign(
            payload.name, rate_per_1000_views=payload.rate_per_1000_views,
            budget=payload.budget, platform_link=payload.platform_link,
            currency=payload.currency, rules_notes=payload.rules_notes,
        )
    except ArClipperError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": campaign_id}


class PostPayload(BaseModel):
    clip_id: str
    platform: str
    post_link: str = ""
    views: int = 0
    status: str = "pending"
    campaign: str = ""


@app.post("/api/posts")
def api_post_add(payload: PostPayload) -> Dict[str, Any]:
    from campaign.manager import add_post

    try:
        post_id = add_post(
            payload.clip_id, platform=payload.platform, post_link=payload.post_link,
            views=payload.views, status=payload.status, campaign=payload.campaign,
        )
    except ArClipperError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": post_id}


@app.patch("/api/posts/{post_id}")
def api_post_update(post_id: int, views: int = 0, status: str = "") -> Dict[str, Any]:
    from campaign.manager import update_post

    try:
        ok = update_post(post_id, views=views or None, status=status or None)
    except ArClipperError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"updated": ok}


@app.get("/api/earnings")
def api_earnings(accepted_only: bool = False) -> Dict[str, Any]:
    from campaign.manager import earnings_report, list_posts, totals

    return {
        "rows": [r.to_dict() for r in earnings_report(only_accepted=accepted_only)],
        "totals": totals(),
        "posts": list_posts(),
    }


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
