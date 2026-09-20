"""منسّق الاقتراح التلقائي (المرحلة 2).

المسار:

    Ingest → Transcribe → Translate → [Diarize] → Analyze
           → Content Gen → [Safety] → قائمة مقترحات جاهزة

المخرج ``Suggestion`` يحمل كل ما يلزم لإنتاج المقطع لاحقاً، ويتحوّل إلى
``ClipRequest`` بسطر واحد — فيندمج مع خط أنابيب المرحلة 1 دون أي تعديل
عليه (المبدأ 5: البنية المعيارية).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analyze.heuristics import MomentCandidate
from .analyze.moments import analyze_transcript, split_long_moment
from .common.config import Settings, load_settings
from .common.logging_utils import get_logger
from .common.schemas import ClipRequest, SourceVideo, Transcript
from .common.text_utils import human_duration, slugify
from .content_gen.generator import ClipContent, generate_for_moments
from .ingest.downloader import suggest_workspace_name
from .pipeline import PipelineOptions, ProgressFn, stage_ingest, stage_transcript, _noop
from .safety.checker import SafetyReport, check_transcript

log = get_logger(__name__)


@dataclass
class Suggestion:
    """مقطع مقترح جاهز للمراجعة أو الإنتاج."""

    index: int
    start: float
    end: float
    score: float
    kind: str
    reason: str
    title: str = ""
    description: str = ""
    hashtags: List[str] = field(default_factory=list)
    hook: str = ""
    speaker: Optional[str] = None
    part: Optional[int] = None
    total_parts: Optional[int] = None
    text: str = ""
    translation: str = ""
    generated_by: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def clip_name(self) -> str:
        """اسم ملف آمن مشتق من العنوان."""
        base = slugify(self.title or f"clip-{self.index:02d}", max_len=40)
        if self.part:
            base = f"{base}-part{self.part}"
        return base or f"clip-{self.index:02d}"

    def to_clip_request(self) -> ClipRequest:
        """التحويل إلى مدخل خط أنابيب المرحلة 1."""
        return ClipRequest(
            start=self.start, end=self.end, name=self.clip_name, title=self.title
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["duration"] = round(self.duration, 2)
        d["clip_name"] = self.clip_name
        return d


@dataclass
class SuggestionSet:
    """نتيجة تحليل فيديو كامل."""

    source: SourceVideo
    suggestions: List[Suggestion] = field(default_factory=list)
    transcript_path: Optional[str] = None
    safety: Optional[SafetyReport] = None
    speakers: Dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "transcript_path": self.transcript_path,
            "speakers": self.speakers,
            "elapsed": round(self.elapsed, 1),
            "count": len(self.suggestions),
            "safety": self.safety.to_dict() if self.safety else None,
            "suggestions": [s.to_dict() for s in self.suggestions],
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return p

    def to_clip_requests(self, indices: Optional[List[int]] = None) -> List[ClipRequest]:
        """يحوّل المقترحات (أو مجموعة مختارة منها) إلى مدخلات إنتاج."""
        chosen = (
            [s for s in self.suggestions if s.index in indices]
            if indices is not None
            else self.suggestions
        )
        return [s.to_clip_request() for s in chosen]


# ============================================================ محطة فصل المتحدثين


def stage_diarize(
    video: SourceVideo,
    transcript: Transcript,
    settings: Settings,
    progress: ProgressFn = _noop,
) -> Dict[str, str]:
    """يفصل المتحدثين ويُلحقهم بالترانسكربت. آمن الفشل تماماً."""
    from .diarize.speakers import diarize_transcript, friendly_names, is_available

    ok, message = is_available(settings)
    if not ok:
        log.info("تخطّي فصل المتحدثين: %s", message)
        return {}

    progress("diarize", "فصل المتحدثين (من يتكلم ومتى)")
    try:
        diarize_transcript(video.path, transcript, settings=settings)
        return friendly_names(transcript)
    except Exception as exc:
        log.warning("تعذّر فصل المتحدثين (%s) — المتابعة بدونه.", exc)
        return {}


# ============================================================ الواجهة العامة


def suggest_clips(
    source: str | Path,
    *,
    options: Optional[PipelineOptions] = None,
    settings: Optional[Settings] = None,
    progress: ProgressFn = _noop,
    max_moments: Optional[int] = None,
    analyze_engine: Optional[str] = None,
    diarize: Optional[bool] = None,
    content: Optional[bool] = None,
    safety: Optional[bool] = None,
    transcript: Optional[Transcript] = None,
    source_video: Optional[SourceVideo] = None,
) -> SuggestionSet:
    """يحلّل فيديو ويرجع قائمة مقاطع مقترحة جاهزة — دون إنتاج أي فيديو."""
    settings = settings or load_settings()
    options = options or PipelineOptions()
    settings.ensure_dirs()
    started = time.time()

    # 1) الإدخال
    video = source_video or stage_ingest(source, options, settings, progress)

    # 2) التفريغ + الترجمة (يُعاد استخدام المحفوظ إن وُجد)
    transcript_path: Optional[Path] = None
    if transcript is None:
        transcript, transcript_path = stage_transcript(video, options, settings, progress)

    if not transcript.segments:
        log.warning("لا يوجد كلام في هذا الفيديو — لا مقترحات.")
        return SuggestionSet(source=video, elapsed=time.time() - started)

    # 3) فصل المتحدثين (اختياري)
    speakers: Dict[str, str] = {}
    want_diarize = diarize if diarize is not None else settings.get("diarize.enabled", True)
    if want_diarize:
        speakers = stage_diarize(video, transcript, settings, progress)
        if speakers and transcript_path:
            transcript.save(transcript_path)  # حفظ بيانات المتحدثين

    # 4) التحليل
    progress("analyze", "اكتشاف أقوى اللحظات")
    moments = analyze_transcript(
        transcript,
        settings=settings,
        engine=analyze_engine,
        max_moments=max_moments,
    )

    # 5) تقسيم الأفكار الطويلة (Part 1 / Part 2)
    if settings.get("analyze.split_long", True):
        max_dur = float(settings.get("analyze.max_duration", 70.0))
        expanded: List[MomentCandidate] = []
        for m in moments:
            expanded.extend(split_long_moment(m, max_duration=max_dur))
        if len(expanded) != len(moments):
            log.info("قُسّمت أفكار طويلة: %d → %d مقطعاً.", len(moments), len(expanded))
        moments = expanded

    if not moments:
        log.warning("لم يُعثر على لحظات قوية.")
        return SuggestionSet(
            source=video,
            transcript_path=str(transcript_path) if transcript_path else None,
            speakers=speakers,
            elapsed=time.time() - started,
        )

    # 6) توليد المحتوى النصي
    want_content = content if content is not None else settings.get("content_gen.enabled", True)
    if want_content:
        progress("content", "توليد العناوين والهاشتاغات والـ Hook")
        contents = generate_for_moments(
            moments, settings=settings, use_llm=settings.get("content_gen.use_llm", True)
        )
    else:
        contents = [ClipContent() for _ in moments]

    # 7) تجميع المقترحات
    suggestions: List[Suggestion] = []
    for i, (moment, c) in enumerate(zip(moments, contents)):
        speaker_label = speakers.get(moment.speaker or "", moment.speaker)
        suggestions.append(
            Suggestion(
                index=i,
                start=round(moment.start, 2),
                end=round(moment.end, 2),
                score=moment.score,
                kind=moment.kind,
                reason=moment.reason,
                title=c.title,
                description=c.description,
                hashtags=c.hashtags,
                hook=c.hook,
                speaker=speaker_label,
                part=moment.signals.get("part"),
                total_parts=moment.signals.get("total_parts"),
                text=(moment.text or "")[:1500],
                translation=(moment.translation or "")[:1500],
                generated_by=c.generated_by,
            )
        )

    # 8) فحص السلامة
    report = None
    want_safety = safety if safety is not None else settings.get("safety.enabled", True)
    if want_safety:
        progress("safety", "فحص الكلمات الحساسة وجودة الترجمة")
        report = check_transcript(
            transcript, settings=settings, use_llm=settings.get("safety.use_llm", False)
        )

    result = SuggestionSet(
        source=video,
        suggestions=suggestions,
        transcript_path=str(transcript_path) if transcript_path else None,
        safety=report,
        speakers=speakers,
        elapsed=time.time() - started,
    )

    # 9) حفظ النتيجة للرجوع إليها لاحقاً
    out_dir = settings.path("paths.registry")
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = result.save(out_dir / f"{suggest_workspace_name(video)}.suggestions.json")
    log.info(
        "اكتمل التحليل: %d مقترح خلال %s — %s",
        len(suggestions),
        human_duration(result.elapsed),
        saved.name,
    )
    return result


def load_suggestions(path: str | Path) -> SuggestionSet:
    """يحمّل مجموعة مقترحات محفوظة."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    source = SourceVideo.from_dict(data.get("source", {}))
    suggestions = []
    for item in data.get("suggestions", []):
        known = {f for f in Suggestion.__dataclass_fields__}  # type: ignore[attr-defined]
        suggestions.append(Suggestion(**{k: v for k, v in item.items() if k in known}))
    return SuggestionSet(
        source=source,
        suggestions=suggestions,
        transcript_path=data.get("transcript_path"),
        speakers=data.get("speakers", {}),
        elapsed=float(data.get("elapsed", 0.0)),
    )
