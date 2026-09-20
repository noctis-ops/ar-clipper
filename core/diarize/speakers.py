"""فصل المتحدثين (Speaker Diarization) — من يتكلم ومتى.

الأداة: ``pyannote.audio`` — مجانية ومفتوحة المصدر، والنماذج صغيرة
(نموذج التقطيع ~6 MB + نموذج بصمة الصوت ~25 MB).

⚠️ متطلب لمرة واحدة: النماذج محجوبة خلف توكن HuggingFace **مجاني**:
  1. أنشئ حساباً على https://huggingface.co
  2. اقبل الشروط على:
       https://hf.co/pyannote/speaker-diarization-3.1
       https://hf.co/pyannote/segmentation-3.0
  3. أنشئ توكن قراءة من https://hf.co/settings/tokens
  4. صدّره:  export HF_TOKEN=hf_xxxxx

الفائدة في المحتوى الحواري (مضيف + ضيف): تمييز من يتكلم يرفع جودة اختيار
اللحظات كثيراً — ردّ الضيف على سؤال المضيف هو الجوهر عادةً.

النظام يعمل بكامل وظائفه بدون هذه المحطة (اختيارية تماماً).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..common.config import Settings, load_settings
from ..common.errors import DependencyError, PipelineError
from ..common.ffmpeg import extract_audio
from ..common.logging_utils import get_logger
from ..common.schemas import Transcript

log = get_logger(__name__)


@dataclass
class SpeakerTurn:
    """فترة كلام لمتحدث واحد."""

    start: float
    end: float
    speaker: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def overlap(self, start: float, end: float) -> float:
        """مقدار التداخل الزمني مع مدى معيّن."""
        return max(0.0, min(self.end, end) - max(self.start, start))


def hf_token(settings: Optional[Settings] = None) -> str:
    """يقرأ توكن HuggingFace من البيئة أو الإعدادات."""
    settings = settings or load_settings()
    configured = settings.get("diarize.hf_token")
    if configured:
        return str(configured)
    for var in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.getenv(var)
        if value:
            return value
    return ""


SETUP_HELP = (
    "فصل المتحدثين يحتاج توكن HuggingFace مجاني (خطوات لمرة واحدة):\n\n"
    "  1) أنشئ حساباً مجانياً:  https://huggingface.co/join\n"
    "  2) اقبل شروط النموذجين (زر Agree):\n"
    "       https://hf.co/pyannote/speaker-diarization-3.1\n"
    "       https://hf.co/pyannote/segmentation-3.0\n"
    "  3) أنشئ توكن قراءة:  https://hf.co/settings/tokens\n"
    "  4) صدّره في الطرفية:\n"
    "       export HF_TOKEN=hf_xxxxx\n\n"
    "أو عطّل الميزة:  diarize.enabled: false  في config/settings.yaml"
)


def is_available(settings: Optional[Settings] = None) -> Tuple[bool, str]:
    """يفحص جاهزية فصل المتحدثين دون رمي استثناء."""
    try:
        import pyannote.audio  # noqa: F401
    except ImportError:
        return False, "pyannote.audio غير مثبّت. نفّذ: pip install pyannote.audio"
    if not hf_token(settings):
        return False, "توكن HuggingFace غير موجود (HF_TOKEN)."
    return True, "فصل المتحدثين جاهز"


# ============================================================ التشغيل


def diarize_audio(
    media_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> List[SpeakerTurn]:
    """يرجع قائمة فترات الكلام مع رقم المتحدث لكل فترة."""
    settings = settings or load_settings()

    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise DependencyError(
            "pyannote.audio غير مثبّت.\n"
            "نفّذ:  pip install pyannote.audio\n\n" + SETUP_HELP
        ) from exc

    token = hf_token(settings)
    if not token:
        raise PipelineError(SETUP_HELP)

    model_id = str(settings.get("diarize.model", "pyannote/speaker-diarization-3.1"))
    log.info("تحميل نموذج فصل المتحدثين: %s", model_id)

    try:
        pipeline = Pipeline.from_pretrained(model_id, use_auth_token=token)
    except Exception as exc:
        raise PipelineError(
            f"تعذّر تحميل نموذج فصل المتحدثين.\nالتفاصيل: {exc}\n\n" + SETUP_HELP
        ) from exc

    if pipeline is None:
        raise PipelineError(
            "أرجع pyannote قيمة فارغة — غالباً لم تقبل شروط الاستخدام بعد.\n\n" + SETUP_HELP
        )

    # تسريع على GPU إن توفّر
    try:
        import torch

        if torch.cuda.is_available():
            pipeline.to(torch.device("cuda"))
            log.info("فصل المتحدثين يعمل على GPU.")
    except Exception:
        pass

    # pyannote يتوقع WAV أحادياً 16kHz
    src = Path(media_path)
    tmp_dir = settings.path("paths.tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav = tmp_dir / f"{src.stem}__diar16k.wav"
    created = False
    if src.suffix.lower() != ".wav":
        extract_audio(src, wav)
        created = True
    else:
        wav = src

    kwargs = {}
    lo = min_speakers if min_speakers is not None else settings.get("diarize.min_speakers")
    hi = max_speakers if max_speakers is not None else settings.get("diarize.max_speakers")
    if lo:
        kwargs["min_speakers"] = int(lo)
    if hi:
        kwargs["max_speakers"] = int(hi)

    log.info("جاري فصل المتحدثين (قد يستغرق وقتاً على CPU)...")
    try:
        annotation = pipeline(str(wav), **kwargs)
    except Exception as exc:
        raise PipelineError(f"فشل فصل المتحدثين: {exc}") from exc
    finally:
        if created and settings.get("runtime.cleanup_temp", True):
            wav.unlink(missing_ok=True)

    turns = [
        SpeakerTurn(start=float(seg.start), end=float(seg.end), speaker=str(label))
        for seg, _, label in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda t: t.start)

    speakers = sorted({t.speaker for t in turns})
    log.info("اكتُشف %d متحدث عبر %d فترة كلام.", len(speakers), len(turns))
    return turns


# ============================================================ الدمج مع الترانسكربت


def assign_speakers(transcript: Transcript, turns: List[SpeakerTurn]) -> Transcript:
    """يُلحق كل جملة بالمتحدث صاحب أكبر تداخل زمني معها."""
    if not turns or not transcript.segments:
        return transcript

    for seg in transcript.segments:
        best_speaker = None
        best_overlap = 0.0
        for turn in turns:
            if turn.start > seg.end:
                break  # مرتّبة زمنياً
            ov = turn.overlap(seg.start, seg.end)
            if ov > best_overlap:
                best_overlap = ov
                best_speaker = turn.speaker
        if best_speaker and best_overlap > 0:
            seg.speaker = best_speaker

    labelled = sum(1 for s in transcript.segments if s.speaker)
    log.info("تم تصنيف %d/%d جملة بالمتحدث.", labelled, len(transcript.segments))
    return transcript


def friendly_names(transcript: Transcript) -> dict:
    """يحوّل SPEAKER_00 إلى أسماء مقروءة حسب حجم المشاركة.

    الأكثر كلاماً في البودكاست الحواري هو المضيف عادةً.
    """
    totals: dict = {}
    for seg in transcript.segments:
        if seg.speaker:
            totals[seg.speaker] = totals.get(seg.speaker, 0.0) + seg.duration
    if not totals:
        return {}

    ordered = sorted(totals, key=lambda k: totals[k], reverse=True)
    names = {}
    for i, key in enumerate(ordered):
        if i == 0:
            names[key] = "المضيف"
        elif i == 1:
            names[key] = "الضيف"
        else:
            names[key] = f"متحدث {i + 1}"
    return names


def diarize_transcript(
    media_path: str | Path,
    transcript: Transcript,
    *,
    settings: Optional[Settings] = None,
) -> Transcript:
    """المسار الكامل: فصل المتحدثين ثم دمجه مع الترانسكربت."""
    turns = diarize_audio(media_path, settings=settings)
    return assign_speakers(transcript, turns)
