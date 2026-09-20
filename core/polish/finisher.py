"""محطة اللمسات النهائية (Polish) — تطبيع الصوت + ظهور/اختفاء ناعم.

لماذا وحدة مستقلة؟ لأنها تمريرة ترميز واحدة تجمع تحسينين يحتاجهما كل مقطع
قبل النشر، ويمكن تعطيلها بالكامل دون أن تتأثر بقية المحطات (المبدأ 5).

**بلا أي تبعية جديدة** — الفلتران مدمجان في ffmpeg المرفق مع المشروع:

- ``loudnorm``: تطبيع الجهارة إلى معيار EBU R128. هذا ما تستخدمه المنصات
  فعلياً، فتوحيد المستوى مسبقاً يمنع خفض الصوت أو رفعه عند النشر، ويجعل
  كل مقاطعك بنفس الجهارة بدل تفاوت مزعج بين مقطع وآخر.
- ``fade``/``afade``: ظهور واختفاء قصير يمنع "القطع الجاف" في بداية
  المقطع ونهايته — أرخص فرق بصري/سمعي ملموس.

القيم الافتراضية (I=-16 LUFS، TP=-1.5 dBTP) هي التوصية الشائعة لمقاطع
الموبايل القصيرة: عالية بما يكفي للسماع في بيئة صاخبة، وبعيدة عن الـclipping.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..common.config import Settings, load_settings
from ..common.ffmpeg import apply_filters, probe
from ..common.logging_utils import get_logger

log = get_logger(__name__)


def build_audio_filter(
    settings: Settings, *, duration: float = 0.0, include_loudnorm: bool = True
) -> str:
    """يبني سلسلة فلاتر الصوت (تطبيع + اختفاء ناعم).

    ``include_loudnorm=False`` عندما يكون التطبيع قد طُبِّق مسبقاً في محطة
    إعادة التأطير — فلا نطبّقه مرتين (التطبيع المزدوج يُفقد الديناميكية).
    """
    parts: List[str] = []

    if include_loudnorm and settings.get("polish.normalize_audio", True):
        target_i = float(settings.get("polish.loudness_target", -16.0))
        target_tp = float(settings.get("polish.true_peak", -1.5))
        target_lra = float(settings.get("polish.loudness_range", 11.0))
        parts.append(f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}")

    fade = float(settings.get("polish.fade_duration", 0.0))
    if fade > 0 and duration > 0:
        # لا نضع اختفاءً أطول من المقطع نفسه
        fade = min(fade, max(0.05, duration / 4))
        parts.append(f"afade=t=in:st=0:d={fade:.2f}")
        parts.append(f"afade=t=out:st={max(0.0, duration - fade):.2f}:d={fade:.2f}")

    return ",".join(parts)


def build_video_filter(settings: Settings, *, duration: float = 0.0) -> str:
    """يبني فلتر الفيديو (ظهور/اختفاء ناعم)."""
    fade = float(settings.get("polish.fade_duration", 0.0))
    if fade <= 0 or duration <= 0:
        return ""
    fade = min(fade, max(0.05, duration / 4))
    return (
        f"fade=t=in:st=0:d={fade:.2f},"
        f"fade=t=out:st={max(0.0, duration - fade):.2f}:d={fade:.2f}"
    )


def is_enabled(settings: Optional[Settings] = None) -> bool:
    """هل هناك أي عمل فعلي لهذه المحطة؟

    التطبيع وحده لا يكفي لتشغيلها: محطة إعادة التأطير تدمجه مجاناً.
    التمريرة المنفصلة لا تُبرَّر إلا بالـfade.
    """
    settings = settings or load_settings()
    if not settings.get("polish.enabled", True):
        return False
    return float(settings.get("polish.fade_duration", 0.0)) > 0


def polish_clip(
    source: str | Path,
    destination: str | Path,
    *,
    settings: Optional[Settings] = None,
    duration: Optional[float] = None,
    loudnorm_done: bool = True,
) -> Path:
    """يطبّق اللمسات النهائية في تمريرة واحدة.

    يرجع مسار المصدر نفسه إن لم يكن هناك أي تحسين مفعّل — فلا نهدر
    دورة ترميز بلا فائدة.
    """
    settings = settings or load_settings()
    src = Path(source)

    if not is_enabled(settings):
        return src

    if duration is None:
        try:
            duration = probe(src).duration
        except Exception:
            duration = 0.0

    vf = build_video_filter(settings, duration=duration or 0.0)
    af = build_audio_filter(
        settings, duration=duration or 0.0, include_loudnorm=not loudnorm_done
    )
    if not vf and not af:
        return src

    log.info("اللمسات النهائية: ظهور/اختفاء ناعم")

    return apply_filters(
        src,
        destination,
        video_filter=vf,
        audio_filter=af,
        crf=int(settings.get("export.crf", 20)),
        preset=str(settings.get("export.preset", "veryfast")),
        video_codec=str(settings.get("export.video_codec", "libx264")),
        audio_codec=str(settings.get("export.audio_codec", "aac")),
        audio_bitrate=str(settings.get("export.audio_bitrate", "160k")),
    )
