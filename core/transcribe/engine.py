"""محطة التفريغ (Transcribe) — تحويل الكلام إلى نص موقّت.

المحرك الافتراضي: ``faster-whisper`` (CTranslate2) — أسرع بعدة أضعاف من
Whisper الأصلي على نفس الجهاز، ويعطي توقيتاً على مستوى الكلمة.
محرك بديل اختياري: ``whisperx`` إن كان مثبّتاً.

الإضافة محرك جديد = تسجيل دالة في ``ENGINES`` فقط، دون لمس بقية النظام.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..common.config import Settings, load_settings
from ..common.errors import DependencyError, TranscribeError
from ..common.ffmpeg import extract_audio, probe
from ..common.logging_utils import get_logger
from ..common.schemas import Segment, Transcript, Word
from ..common.text_utils import normalize_text

log = get_logger(__name__)


# ============================================================ اكتشاف العتاد


def resolve_device(requested: str = "auto") -> str:
    """يحدّد الجهاز: cuda إن توفّر GPU وإلا cpu."""
    if requested and requested != "auto":
        return requested
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def resolve_compute_type(requested: str, device: str) -> str:
    """نوع الحساب الأمثل: int8 على CPU، float16 على GPU."""
    if requested and requested != "auto":
        return requested
    return "float16" if device == "cuda" else "int8"


# ============================================================ محرك faster-whisper


def _transcribe_faster_whisper(
    audio_path: Path,
    *,
    model_name: str,
    device: str,
    compute_type: str,
    language: Optional[str],
    beam_size: int,
    vad_filter: bool,
    word_timestamps: bool,
) -> Transcript:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise DependencyError(
            "faster-whisper غير مثبّت. نفّذ: pip install faster-whisper"
        ) from exc

    log.info(
        "تحميل نموذج التفريغ: %s (device=%s, compute=%s)", model_name, device, compute_type
    )
    try:
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=max(1, (os.cpu_count() or 4)),
        )
    except Exception as exc:
        raise TranscribeError(
            f"تعذّر تحميل نموذج '{model_name}'. تأكد من الاتصال بالإنترنت لأول تحميل "
            f"أو من توفّر النموذج محلياً في الكاش.\nالتفاصيل: {exc}"
        ) from exc

    try:
        raw_segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=word_timestamps,
        )
    except Exception as exc:
        raise TranscribeError(f"فشل التفريغ الصوتي: {exc}") from exc

    segments: List[Segment] = []
    for idx, seg in enumerate(raw_segments):
        text = normalize_text(seg.text or "")
        if not text:
            continue
        words: List[Word] = []
        for w in getattr(seg, "words", None) or []:
            wt = (w.word or "").strip()
            if not wt:
                continue
            words.append(
                Word(
                    text=wt,
                    start=float(w.start if w.start is not None else seg.start),
                    end=float(w.end if w.end is not None else seg.end),
                    score=float(w.probability) if getattr(w, "probability", None) else None,
                )
            )
        segments.append(
            Segment(
                id=len(segments),
                start=float(seg.start),
                end=float(seg.end),
                text=text,
                words=words,
            )
        )
        if idx % 25 == 0 and idx:
            log.info("تم تفريغ %d جملة حتى الآن (%.0fs)...", len(segments), seg.end)

    return Transcript(
        source_path=str(audio_path),
        language=getattr(info, "language", language or "") or "",
        segments=segments,
        duration=float(getattr(info, "duration", 0.0) or 0.0),
        engine="faster_whisper",
        model=model_name,
    )


# ============================================================ محرك whisperx (اختياري)


def _transcribe_whisperx(
    audio_path: Path,
    *,
    model_name: str,
    device: str,
    compute_type: str,
    language: Optional[str],
    beam_size: int,
    vad_filter: bool,
    word_timestamps: bool,
) -> Transcript:
    try:
        import whisperx  # type: ignore
    except ImportError as exc:
        raise DependencyError(
            "whisperx غير مثبّت. نفّذ: pip install whisperx  (أو استخدم "
            "transcribe.engine=faster_whisper وهو الافتراضي)"
        ) from exc

    log.info("تحميل WhisperX: %s (device=%s)", model_name, device)
    model = whisperx.load_model(model_name, device, compute_type=compute_type, language=language)
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, batch_size=8)
    detected = result.get("language", language or "")

    # محاذاة على مستوى الكلمة (ميزة WhisperX الأساسية)
    if word_timestamps:
        try:
            align_model, meta = whisperx.load_align_model(language_code=detected, device=device)
            result = whisperx.align(
                result["segments"], align_model, meta, audio, device, return_char_alignments=False
            )
        except Exception as exc:  # المحاذاة غير متاحة لكل اللغات
            log.warning("تعذّرت المحاذاة على مستوى الكلمة: %s", exc)

    segments: List[Segment] = []
    for seg in result.get("segments", []):
        text = normalize_text(seg.get("text", ""))
        if not text:
            continue
        words = [
            Word(
                text=str(w.get("word", "")).strip(),
                start=float(w.get("start", seg.get("start", 0.0))),
                end=float(w.get("end", seg.get("end", 0.0))),
                score=w.get("score"),
            )
            for w in seg.get("words", [])
            if str(w.get("word", "")).strip()
        ]
        segments.append(
            Segment(
                id=len(segments),
                start=float(seg.get("start", 0.0)),
                end=float(seg.get("end", 0.0)),
                text=text,
                words=words,
            )
        )

    return Transcript(
        source_path=str(audio_path),
        language=detected,
        segments=segments,
        duration=segments[-1].end if segments else 0.0,
        engine="whisperx",
        model=model_name,
    )


ENGINES: Dict[str, Callable[..., Transcript]] = {
    "faster_whisper": _transcribe_faster_whisper,
    "whisperx": _transcribe_whisperx,
}


# ============================================================ الواجهة العامة


def transcribe(
    media_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    engine: Optional[str] = None,
    model: Optional[str] = None,
    language: Optional[str] = None,
    audio_path: Optional[str | Path] = None,
    keep_audio: bool = False,
) -> Transcript:
    """يفرّغ ملف فيديو/صوت ويرجع ``Transcript`` موحّداً.

    يستخرج مساراً صوتياً WAV 16kHz أحادياً أولاً (الصيغة المثلى لـ Whisper)،
    ثم يمرّره للمحرك المختار.
    """
    settings = settings or load_settings()
    src = Path(media_path)
    if not src.exists():
        raise TranscribeError(f"الملف غير موجود: {src}")

    engine_name = (engine or settings.get("transcribe.engine", "faster_whisper")).strip()
    if engine_name not in ENGINES:
        raise TranscribeError(
            f"محرك تفريغ غير معروف: {engine_name} (المتاح: {', '.join(ENGINES)})"
        )

    model_name = model or str(settings.get("transcribe.model", "small"))
    device = resolve_device(str(settings.get("transcribe.device", "auto")))
    compute_type = resolve_compute_type(
        str(settings.get("transcribe.compute_type", "auto")), device
    )
    lang = language if language is not None else settings.get("transcribe.language")
    if isinstance(lang, str) and lang.lower() in {"", "auto", "null", "none"}:
        lang = None

    # تجهيز الصوت
    tmp_dir = settings.path("paths.tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav = Path(audio_path) if audio_path else tmp_dir / f"{src.stem}__16k.wav"
    if not wav.exists():
        log.info("استخراج المسار الصوتي...")
        extract_audio(src, wav)

    transcript = ENGINES[engine_name](
        wav,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        language=lang,
        beam_size=int(settings.get("transcribe.beam_size", 5)),
        vad_filter=bool(settings.get("transcribe.vad_filter", True)),
        word_timestamps=bool(settings.get("transcribe.word_timestamps", True)),
    )

    transcript.source_path = str(src)
    if not transcript.duration:
        try:
            transcript.duration = probe(src).duration
        except Exception:
            transcript.duration = transcript.segments[-1].end if transcript.segments else 0.0

    if not keep_audio and not audio_path and settings.get("runtime.cleanup_temp", True):
        wav.unlink(missing_ok=True)

    log.info(
        "اكتمل التفريغ: %d جملة | اللغة المكتشفة: %s",
        len(transcript.segments),
        transcript.language or "غير معروفة",
    )
    if not transcript.segments:
        log.warning("لم يُعثر على أي كلام في الملف — تحقّق من وجود مسار صوتي.")
    return transcript
