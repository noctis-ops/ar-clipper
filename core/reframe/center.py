"""محطة إعادة التأطير (Reframe) — تحويل الفيديو إلى مقاس عمودي 9:16.

المرحلة 1: قصّ مركزي ثابت (Center Crop) — بسيط وسريع وبلا تبعيات.
المرحلة 3: ستُضاف نسخة ``face_track`` باستخدام mediapipe + OpenCV، وستستخدم
نفس واجهة ``reframe()`` فلا تحتاج بقية الوحدات لأي تعديل (المبدأ 5).

منطق القص:
- إن كان المصدر أعرض من النسبة المطلوبة → نقصّ من العرض (الحالة الشائعة 16:9 → 9:16).
- إن كان أضيق → نقصّ من الارتفاع.
- ``focus_x/focus_y`` (0..1) يحدّدان مركز القص؛ 0.5 = المنتصف.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from ..common.config import Settings, load_settings
from ..common.errors import MediaError
from ..common.ffmpeg import apply_filters, probe
from ..common.logging_utils import get_logger

log = get_logger(__name__)


def parse_aspect(value: str) -> float:
    """``"9:16"`` → 0.5625"""
    raw = str(value).strip()
    if ":" in raw:
        w, h = raw.split(":", 1)
        return float(w) / float(h)
    return float(raw)


def compute_crop(
    src_w: int,
    src_h: int,
    target_aspect: float,
    *,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
) -> Tuple[int, int, int, int]:
    """يحسب (العرض، الارتفاع، x، y) لنافذة القص. القيم زوجية دائماً (متطلب yuv420p)."""
    if src_w <= 0 or src_h <= 0:
        raise MediaError(f"أبعاد مصدر غير صالحة: {src_w}x{src_h}")

    src_aspect = src_w / src_h
    if src_aspect > target_aspect:  # المصدر أعرض → نقصّ العرض
        crop_h = src_h
        crop_w = int(round(src_h * target_aspect))
    else:  # المصدر أضيق → نقصّ الارتفاع
        crop_w = src_w
        crop_h = int(round(src_w / target_aspect))

    crop_w = max(2, min(src_w, crop_w - (crop_w % 2)))
    crop_h = max(2, min(src_h, crop_h - (crop_h % 2)))

    focus_x = min(1.0, max(0.0, focus_x))
    focus_y = min(1.0, max(0.0, focus_y))
    x = int(round((src_w - crop_w) * focus_x))
    y = int(round((src_h - crop_h) * focus_y))
    x = max(0, min(src_w - crop_w, x - (x % 2)))
    y = max(0, min(src_h - crop_h, y - (y % 2)))

    return crop_w, crop_h, x, y


def build_reframe_filter(
    src_w: int,
    src_h: int,
    *,
    out_w: int,
    out_h: int,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
) -> str:
    """يبني سلسلة فلاتر ffmpeg: crop ثم scale ثم pad احتياطي."""
    target_aspect = out_w / out_h
    crop_w, crop_h, x, y = compute_crop(
        src_w, src_h, target_aspect, focus_x=focus_x, focus_y=focus_y
    )
    return (
        f"crop={crop_w}:{crop_h}:{x}:{y},"
        f"scale={out_w}:{out_h}:flags=lanczos,"
        f"setsar=1"
    )


def reframe(
    source_path: str | Path,
    output_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    mode: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    focus_x: Optional[float] = None,
    focus_y: Optional[float] = None,
) -> Path:
    """يحوّل الفيديو إلى المقاس العمودي المطلوب."""
    settings = settings or load_settings()
    src = Path(source_path)
    if not src.exists():
        raise MediaError(f"الفيديو غير موجود: {src}")

    selected_mode = (mode or settings.get("reframe.mode", "center")).lower()
    if selected_mode == "face_track":
        log.warning(
            "وضع تتبّع الوجه (face_track) يأتي في المرحلة 3 — سيُستخدم القص المركزي الآن."
        )
        selected_mode = "center"
    if selected_mode != "center":
        raise MediaError(f"وضع إعادة تأطير غير معروف: {selected_mode}")

    out_w = int(width or settings.get("reframe.width", 1080))
    out_h = int(height or settings.get("reframe.height", 1920))
    fx = float(focus_x if focus_x is not None else settings.get("reframe.focus_x", 0.5))
    fy = float(focus_y if focus_y is not None else settings.get("reframe.focus_y", 0.5))

    info = probe(src)
    if not info.has_video:
        raise MediaError(f"الملف لا يحتوي مساراً مرئياً: {src}")

    vf = build_reframe_filter(
        info.width, info.height, out_w=out_w, out_h=out_h, focus_x=fx, focus_y=fy
    )
    log.info(
        "إعادة التأطير: %dx%d → %dx%d (قص مركزي)", info.width, info.height, out_w, out_h
    )

    return apply_filters(
        src,
        output_path,
        video_filter=vf,
        crf=int(settings.get("export.crf", 20)),
        preset=str(settings.get("export.preset", "medium")),
        video_codec=str(settings.get("export.video_codec", "libx264")),
        audio_codec=str(settings.get("export.audio_codec", "aac")),
        audio_bitrate=str(settings.get("export.audio_bitrate", "160k")),
        fps=settings.get("export.fps"),
    )
