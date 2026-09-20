"""منسّق خط الأنابيب (Pipeline Orchestrator) — المرحلة 1.

تسلسل المحطات:

    Ingest → Transcribe → Translate → [اختيار المدى] → Cut
           → Remove Silence → Reframe 9:16 → Subtitles (ASS) → Burn → Export

كل محطة مستقلة تماماً ويمكن تعطيلها عبر ``PipelineOptions`` أو استبدالها،
والمنسّق يمرّر بين المحطات أنواع البيانات الموحّدة في ``core.common.schemas``
فقط — وهذا ما يحافظ على البنية المعيارية (المبدأ 5).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .clip.cutter import cut_clip, snap_to_speech, validate_range
from .common.config import Settings, load_settings
from .common.errors import PipelineError
from .common.ffmpeg import probe
from .common.logging_utils import get_logger
from .common.schemas import ClipRequest, ClipResult, SourceVideo, Transcript
from .common.text_utils import human_duration, slugify
from .export.exporter import cleanup_temp, finalize_clip
from .polish.finisher import build_audio_filter as polish_audio_filter
from .polish.finisher import build_video_filter as polish_video_filter
from .polish.finisher import is_enabled as polish_enabled
from .ingest.downloader import ingest as ingest_source
from .ingest.downloader import suggest_workspace_name
from .maintenance import clip_exists
from .reframe.center import reframe as reframe_video
from .silence.remover import remap_transcript, remove_silence
from .subtitles.builder import burn_subtitles, write_ass, write_sidecars
from .transcribe.engine import transcribe as run_transcribe
from .translate.engine import translate_transcript

log = get_logger(__name__)

ProgressFn = Callable[[str, str], None]


# ============================================================ الخيارات


@dataclass
class PipelineOptions:
    """مفاتيح تشغيل/تعطيل كل محطة + تجاوزات الإعدادات."""

    license_note: str = ""
    language: Optional[str] = None
    target_lang: Optional[str] = None
    translate: bool = True
    translate_engine: Optional[str] = None
    transcribe_model: Optional[str] = None
    remove_silence: bool = True
    reframe: bool = True
    subtitles: bool = True
    burn_subtitles: bool = True
    subtitle_track: Optional[str] = None  # ar | source | bilingual
    snap_to_speech: bool = True
    fast_cut: bool = False
    polish: bool = True
    skip_existing: bool = False
    keep_temp: bool = False
    clip_name: Optional[str] = None
    reuse_transcript: bool = True


@dataclass
class PipelineContext:
    """الحالة المتنقّلة بين المحطات."""

    source: Optional[SourceVideo] = None
    transcript: Optional[Transcript] = None
    transcript_path: Optional[Path] = None
    workspace: str = ""
    temp_files: List[Path] = field(default_factory=list)
    stages: List[str] = field(default_factory=list)


def _noop(stage: str, message: str) -> None:
    pass


# ============================================================ المحطات المنفصلة


def stage_ingest(
    source: str | Path,
    options: PipelineOptions,
    settings: Settings,
    progress: ProgressFn = _noop,
) -> SourceVideo:
    progress("ingest", "تحميل/قراءة الفيديو المصدر")
    video = ingest_source(source, license_note=options.license_note, settings=settings)
    log.info(
        "المصدر: %s | المدة: %s | الترخيص: %s",
        video.title,
        human_duration(video.duration),
        video.license_note,
    )
    return video


def stage_transcript(
    video: SourceVideo,
    options: PipelineOptions,
    settings: Settings,
    progress: ProgressFn = _noop,
) -> tuple[Transcript, Path]:
    """يفرّغ ويترجم، مع إعادة استخدام ملف محفوظ إن وُجد (توفير وقت كبير)."""
    transcripts_dir = settings.path("paths.transcripts")
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    cache_path = transcripts_dir / f"{suggest_workspace_name(video)}.json"

    if options.reuse_transcript and cache_path.exists():
        try:
            cached = Transcript.load(cache_path)
            has_translation = any(s.translation for s in cached.segments)
            if cached.segments and (has_translation or not options.translate):
                log.info("إعادة استخدام ترانسكربت محفوظ: %s", cache_path.name)
                progress("transcribe", "استخدام ترانسكربت محفوظ")
                return cached, cache_path
        except Exception as exc:
            log.warning("تعذّرت قراءة الترانسكربت المحفوظ (%s) — سيُعاد التفريغ.", exc)

    progress("transcribe", "تفريغ الكلام إلى نص موقّت")
    transcript = run_transcribe(
        video.path,
        settings=settings,
        model=options.transcribe_model,
        language=options.language,
    )

    if options.translate:
        progress("translate", "ترجمة النص إلى العربية")
        transcript = translate_transcript(
            transcript,
            target_lang=options.target_lang,
            engine=options.translate_engine,
            settings=settings,
        )

    transcript.save(cache_path)
    log.info("حُفظ الترانسكربت: %s", cache_path)
    return transcript, cache_path


def stage_render_clip(
    video: SourceVideo,
    transcript: Optional[Transcript],
    request: ClipRequest,
    options: PipelineOptions,
    settings: Settings,
    ctx: PipelineContext,
    progress: ProgressFn = _noop,
) -> ClipResult:
    """ينفّذ سلسلة: قص → حذف صمت → 9:16 → ترجمة محروقة → تصدير."""
    tmp_dir = settings.path("paths.tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    source_info = probe(video.path)
    start, end = validate_range(request.start, request.end, source_info.duration)
    if options.snap_to_speech and transcript:
        start, end = snap_to_speech(start, end, transcript)
        start, end = validate_range(start, end, source_info.duration)

    base = options.clip_name or request.name or f"{int(start):05d}-{int(end):05d}"
    clip_id = slugify(base, max_len=48, fallback="clip")
    stages: List[str] = []

    # ---------------------------------------------- 0) الاستئناف
    # مقطع مكتمل سابقاً لا يُعاد ترميزه. هذا يجعل إعادة تشغيل دفعة فاشلة
    # تُكمل من حيث توقفت بدل البدء من الصفر.
    if options.skip_existing:
        existing = clip_exists(ctx.workspace, clip_id, settings)
        if existing:
            log.info("المقطع %s موجود مسبقاً — تخطّيه (--overwrite لإعادة الإنتاج).", clip_id)
            progress("skip", f"تخطّي {clip_id} (موجود مسبقاً)")
            info = probe(existing)
            return ClipResult(
                clip_id=clip_id,
                video_path=str(existing),
                duration=info.duration,
                width=info.width,
                height=info.height,
                subtitle_files={
                    fmt: str(existing.with_suffix(f".{fmt}"))
                    for fmt in ("srt", "vtt", "ass")
                    if existing.with_suffix(f".{fmt}").exists()
                },
                stages=["skipped"],
                start=start,
                end=end,
            )

    # ---------------------------------------------- 1) القص
    progress("cut", f"قص المقطع {human_duration(start)} → {human_duration(end)}")
    cut_path = tmp_dir / f"{clip_id}__01_cut.mp4"
    current = cut_clip(
        video.path,
        ClipRequest(start=start, end=end, name=clip_id),
        cut_path,
        settings=settings,
        fast=options.fast_cut,
    )
    ctx.temp_files.append(current)
    stages.append("cut")

    # الترانسكربت المقابل للمقطع، بتوقيت يبدأ من الصفر
    clip_transcript: Optional[Transcript] = (
        transcript.slice(start, end) if transcript and transcript.segments else None
    )

    # ---------------------------------------------- 2) حذف الصمت
    if options.remove_silence and settings.get("silence.enabled", True):
        progress("silence", "حذف فترات الصمت الطويلة")
        silence_out = tmp_dir / f"{clip_id}__02_nosilence.mp4"
        result = remove_silence(current, silence_out, settings=settings)
        if result.applied:
            current = result.output_path
            ctx.temp_files.append(current)
            stages.append("silence")
            if clip_transcript and result.kept_ranges:
                clip_transcript = remap_transcript(clip_transcript, result.kept_ranges)
                log.info("أُعيد ضبط توقيت الترجمة بعد حذف الصمت.")

    # ---------------------------------------------- 3) إعادة التأطير 9:16
    out_w = int(settings.get("reframe.width", 1080))
    out_h = int(settings.get("reframe.height", 1920))
    # اللمسات النهائية تُدمج في آخر تمريرة ترميز موجودة بدل إنشاء تمريرة
    # خامسة: توفير دورة كاملة (كانت ~8s لمقطع 20s) وجودة أعلى.
    clip_dur = max(0.0, end - start)
    want_polish = options.polish and polish_enabled(settings)
    will_burn = bool(
        clip_transcript
        and options.subtitles
        and settings.get("subtitles.enabled", True)
        and options.burn_subtitles
        and settings.get("subtitles.burn", True)
    )
    fade_vf = polish_video_filter(settings, duration=clip_dur) if want_polish else ""
    fade_af = (
        polish_audio_filter(settings, duration=clip_dur, include_loudnorm=False)
        if want_polish
        else ""
    )

    did_reframe = False
    if options.reframe and settings.get("reframe.enabled", True):
        progress("reframe", "تحويل المقطع إلى مقاس 9:16")
        reframe_out = tmp_dir / f"{clip_id}__03_vertical.mp4"
        current = reframe_video(
            current,
            reframe_out,
            settings=settings,
            extra_video_filter="" if will_burn else fade_vf,
            extra_audio_filter="" if will_burn else fade_af,
        )
        if not will_burn and (fade_vf or fade_af):
            stages.append("polish")
        ctx.temp_files.append(current)
        stages.append("reframe")
        # التطبيع دُمج داخل هذه التمريرة
        did_reframe = bool(
            settings.get("polish.enabled", True)
            and settings.get("polish.normalize_audio", True)
        )
    else:
        info = probe(current)
        out_w, out_h = info.width, info.height

    # ---------------------------------------------- 4) الترجمة المرئية
    subtitle_files = {}
    track = options.subtitle_track or str(settings.get("subtitles.track", "ar"))
    if options.subtitles and settings.get("subtitles.enabled", True) and clip_transcript:
        progress("subtitles", "توليد ملفات الترجمة")
        subtitle_files = write_sidecars(
            clip_transcript,
            tmp_dir / clip_id,
            track=track,
            settings=settings,
            play_res_x=out_w,
            play_res_y=out_h,
        )
        for path in subtitle_files.values():
            ctx.temp_files.append(Path(path))
        stages.append("subtitles")

        if options.burn_subtitles and settings.get("subtitles.burn", True):
            progress("burn", "حرق الترجمة على الفيديو")
            ass_path = Path(
                subtitle_files.get("ass")
                or write_ass(
                    clip_transcript,
                    tmp_dir / f"{clip_id}.ass",
                    track=track,
                    settings=settings,
                    play_res_x=out_w,
                    play_res_y=out_h,
                )
            )
            burned = tmp_dir / f"{clip_id}__04_subbed.mp4"
            current = burn_subtitles(
                current,
                ass_path,
                burned,
                settings=settings,
                extra_video_filter=fade_vf,
                audio_filter=fade_af,
            )
            ctx.temp_files.append(current)
            stages.append("burn")
            if fade_vf or fade_af:
                stages.append("polish")
    elif options.subtitles and not clip_transcript:
        log.warning("لا يوجد ترانسكربت لهذا المدى — تخطّي الترجمة المرئية.")

    # ---------------------------------------------- 5) التصدير
    progress("export", "التصدير النهائي")
    stages.append("export")  # يُسجَّل قبل الكتابة حتى يظهر في ملف البيانات الوصفية
    ctx.temp_files = [p for p in ctx.temp_files if Path(p) != Path(current)]
    result = finalize_clip(
        current,
        clip_id=clip_id,
        workspace=ctx.workspace,
        transcript=clip_transcript,
        source=video,
        subtitle_files=subtitle_files,
        stages=stages,
        start=start,
        end=end,
        settings=settings,
    )
    result.stages = stages
    return result


# ============================================================ الواجهة العامة


def run_pipeline(
    source: str | Path,
    clips: List[ClipRequest],
    *,
    options: Optional[PipelineOptions] = None,
    settings: Optional[Settings] = None,
    progress: ProgressFn = _noop,
    transcript: Optional[Transcript] = None,
    source_video: Optional[SourceVideo] = None,
) -> List[ClipResult]:
    """ينفّذ خط الأنابيب الكامل للمرحلة 1 على مقطع واحد أو أكثر."""
    settings = settings or load_settings()
    options = options or PipelineOptions()
    settings.ensure_dirs()

    if not clips:
        raise PipelineError("لم تُحدَّد أي مقاطع للمعالجة (start/end مطلوبة).")

    started = time.time()
    ctx = PipelineContext()

    # 1) الإدخال
    ctx.source = source_video or stage_ingest(source, options, settings, progress)
    ctx.workspace = suggest_workspace_name(ctx.source)

    # 2) التفريغ + الترجمة
    # التفريغ أبطأ محطة بفارق كبير، ويحتاج تحميل نموذج. لا نشغّله إطلاقاً
    # إن كان الناتج لن يُستخدم (لا ترجمة مرئية ولا محاذاة مع الكلام).
    needs_transcript = bool(
        options.subtitles and settings.get("subtitles.enabled", True)
    ) or options.snap_to_speech

    if transcript is not None:
        ctx.transcript = transcript
    elif needs_transcript:
        ctx.transcript, ctx.transcript_path = stage_transcript(
            ctx.source, options, settings, progress
        )
    else:
        log.info("لا حاجة للتفريغ في هذا المسار — تم تخطّيه لتوفير الوقت.")
        ctx.transcript = None

    # 3) إنتاج كل مقطع
    results: List[ClipResult] = []
    for idx, request in enumerate(clips, start=1):
        log.info("── المقطع %d/%d ──", idx, len(clips))
        if len(clips) > 1 and not request.name and not options.clip_name:
            request = ClipRequest(
                start=request.start, end=request.end, name=f"clip-{idx:02d}", title=request.title
            )
        results.append(
            stage_render_clip(
                ctx.source, ctx.transcript, request, options, settings, ctx, progress
            )
        )

    # 4) تنظيف الملفات الوسيطة
    if not options.keep_temp:
        cleanup_temp(list(ctx.temp_files), settings=settings)

    log.info(
        "اكتمل خط الأنابيب: %d مقطع خلال %s",
        len(results),
        human_duration(time.time() - started),
    )
    return results
