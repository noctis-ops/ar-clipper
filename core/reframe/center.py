"""محطة إعادة التأطير (Reframe) — تحويل الفيديو إلى مقاس عمودي 9:16.

الوضعان المدعومان عبر نفس الواجهة (المبدأ 5 — وحدات قابلة للاستبدال):
- ``center``     (المرحلة 1): قصّ مركزي ثابت — بسيط وسريع وبلا تبعيات.
- ``face_track`` (المرحلة 3): يتبع وجه المتحدث عبر OpenCV، ويتراجع تلقائياً
  إلى القص المركزي إن لم يُكتشف وجه — فلا يفشل أبداً.

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


def _build_face_track_filter(
    src: Path, info, *, out_w: int, out_h: int, settings: Settings
) -> Tuple[str, str]:
    """يبني فلتر تتبّع الوجه. يرجع ("", سبب) عند التعذّر ليتراجع المستدعي بأمان."""
    from .face_track import (
        average_focus,
        build_dynamic_crop,
        build_split_screen_filter,
        detect_dialogue,
        smooth_track,
        track_faces,
        track_spread,
    )

    try:
        tracked = track_faces(src, settings=settings)
    except Exception as exc:  # التتبّع تحسين، لا يجوز أن يُفشل الإنتاج
        log.warning("تعذّر تتبّع الوجه (%s) — قص مركزي.", exc)
        return "", "قص مركزي — تعذّر التتبّع"

    min_rate = float(settings.get("reframe.min_detection_rate", 0.25))
    if not tracked.found or tracked.detection_rate < min_rate:
        log.info(
            "لم يُكتشف وجه كافٍ (%.0f%% < %.0f%%) — قص مركزي.",
            tracked.detection_rate * 100,
            min_rate * 100,
        )
        return "", "قص مركزي — لا وجه واضح"

    # حوار بين متحدثَين: تتبّع وجه واحد يقطع الآخر تماماً، والقص المركزي
    # يقطع الاثنين. الشاشة المنقسمة تُظهرهما معاً.
    if settings.get("reframe.split_screen", True):
        layout = detect_dialogue(
            tracked.samples,
            min_ratio=float(settings.get("reframe.dialogue_min_ratio", 0.4)),
            min_gap=float(settings.get("reframe.dialogue_min_gap", 0.25)),
        )
        if layout.usable:
            return (
                build_split_screen_filter(
                    layout,
                    src_w=info.width,
                    src_h=info.height,
                    out_w=out_w,
                    out_h=out_h,
                ),
                f"شاشة منقسمة — متحدثان (ثقة {layout.confidence:.0%})",
            )

    samples = smooth_track(
        tracked.samples,
        window=int(settings.get("reframe.smooth_window", 5)),
        max_step=float(settings.get("reframe.max_step", 0.04)),
    )

    target_aspect = out_w / out_h
    crop_w, crop_h, _, _ = compute_crop(info.width, info.height, target_aspect)

    # وجه شبه ثابت لا يستحق تعبيراً متحركاً: قص ثابت على مركز ثقله أنظف وأسرع
    spread = track_spread(samples)
    if spread < float(settings.get("reframe.static_threshold", 0.04)):
        fx, fy = average_focus(samples)
        return (
            build_reframe_filter(
                info.width,
                info.height,
                out_w=out_w,
                out_h=out_h,
                focus_x=fx,
                focus_y=fy,
            ),
            f"تتبّع الوجه — ثابت عند {fx:.2f}",
        )

    crop_expr = build_dynamic_crop(
        samples, src_w=info.width, src_h=info.height, crop_w=crop_w, crop_h=crop_h
    )
    return (
        f"{crop_expr},scale={out_w}:{out_h}:flags=lanczos,setsar=1",
        f"تتبّع الوجه — متحرك ({len(samples)} نقطة)",
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
    extra_video_filter: str = "",
    extra_audio_filter: str = "",
    branding=None,
) -> Path:
    """يحوّل الفيديو إلى المقاس العمودي المطلوب.

    ``extra_*`` تُدمج في نفس التمريرة — تُستخدم عندما لا تكون هناك محطة حرق
    لاحقة تحمل اللمسات النهائية.
    """
    settings = settings or load_settings()
    src = Path(source_path)
    if not src.exists():
        raise MediaError(f"الفيديو غير موجود: {src}")

    selected_mode = (mode or settings.get("reframe.mode", "center")).lower()
    if selected_mode not in ("center", "face_track"):
        raise MediaError(f"وضع إعادة تأطير غير معروف: {selected_mode}")

    out_w = int(width or settings.get("reframe.width", 1080))
    out_h = int(height or settings.get("reframe.height", 1920))
    fx = float(focus_x if focus_x is not None else settings.get("reframe.focus_x", 0.5))
    fy = float(focus_y if focus_y is not None else settings.get("reframe.focus_y", 0.5))

    info = probe(src)
    if not info.has_video:
        raise MediaError(f"الملف لا يحتوي مساراً مرئياً: {src}")

    vf = ""
    applied_mode = "قص مركزي"
    if selected_mode == "face_track":
        vf, applied_mode = _build_face_track_filter(
            src, info, out_w=out_w, out_h=out_h, settings=settings
        )
    if not vf:
        vf = build_reframe_filter(
            info.width, info.height, out_w=out_w, out_h=out_h, focus_x=fx, focus_y=fy
        )
    log.info(
        "إعادة التأطير: %dx%d → %dx%d (%s)",
        info.width,
        info.height,
        out_w,
        out_h,
        applied_mode,
    )

    # تطبيع الصوت يُدمج هنا مجاناً: هذه التمريرة تعيد ترميز الصوت أصلاً،
    # فإضافة loudnorm إليها لا تكلّف دورة ترميز إضافية (المبدأ: أقل تمريرات).
    af = ""
    if settings.get("polish.enabled", True) and settings.get(
        "polish.normalize_audio", True
    ):
        af = (
            f"loudnorm=I={float(settings.get('polish.loudness_target', -16.0))}"
            f":TP={float(settings.get('polish.true_peak', -1.5))}"
            f":LRA={float(settings.get('polish.loudness_range', 11.0))}"
        )
        log.info("تطبيع جهارة الصوت مدمج في هذه التمريرة (EBU R128).")

    if extra_video_filter:
        vf = f"{vf},{extra_video_filter}"
    if extra_audio_filter:
        af = f"{af},{extra_audio_filter}" if af else extra_audio_filter

    # الهوية البصرية تُركَّب في نفس التمريرة (المرحلة 3)
    extra_inputs = []
    if branding is not None and getattr(branding, "active", False):
        from ..design.branding import compose_filter_graph

        vf = compose_filter_graph(branding, vf)
        extra_inputs = list(branding.extra_inputs)

    return apply_filters(
        src,
        output_path,
        video_filter=vf,
        audio_filter=af,
        extra_inputs=extra_inputs,
        crf=int(settings.get("export.crf", 20)),
        preset=str(settings.get("export.preset", "medium")),
        video_codec=str(settings.get("export.video_codec", "libx264")),
        audio_codec=str(settings.get("export.audio_codec", "aac")),
        audio_bitrate=str(settings.get("export.audio_bitrate", "160k")),
        fps=settings.get("export.fps"),
    )
