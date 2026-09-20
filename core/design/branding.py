"""الهوية البصرية الثابتة — المرحلة 3، الخطوة 4.

يضيف على المقطع العناصر التي تجعله قابلاً للتمييز كعلامة:
- شعار (صورة PNG شفافة) في أحد الأركان.
- اسم الحساب كعلامة مائية خفيفة.
- شارة "الجزء 1/2" حين يكون المقطع جزءاً من سلسلة.

كل ذلك يُبنى كسلسلة فلاتر ffmpeg تُدمج في تمريرة الترميز القائمة (قاعدة القسم
4.14: لا تمريرة جديدة لأي لمسة بصرية). الشعار يُمرَّر كمدخل إضافي عبر
``extra_inputs`` ويُركَّب بفلتر ``overlay``.

**لماذا ASS بدل drawtext للنصوص؟** سببان قاطعان:
1. ``drawtext`` غير مُجمَّع في نسخة ffmpeg المرفقة عبر imageio-ffmpeg (تحقّقنا
   عملياً)، فالاعتماد عليه يكسر التثبيت الافتراضي بلا ffmpeg نظامي.
2. الأهم: ``drawtext`` لا يشكّل الحروف العربية ولا يصلها — يعرضها منفصلة
   ومقلوبة. أما ``ass``/libass فيمرّ عبر HarfBuzz ويعرض العربية بشكل صحيح،
   وهو المحرك نفسه الذي نحرق به الترجمة أصلاً.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from ..common.config import Settings, load_settings
from ..common.ffmpeg import escape_filter_path
from ..common.logging_utils import get_logger

log = get_logger(__name__)

# الأركان المدعومة → (تعبير x، تعبير y) بدلالة أبعاد الفيديو والشعار
CORNERS = {
    "top_left": ("{m}", "{m}"),
    "top_right": ("W-w-{m}", "{m}"),
    "bottom_left": ("{m}", "H-h-{m}"),
    "bottom_right": ("W-w-{m}", "H-h-{m}"),
    "top_center": ("(W-w)/2", "{m}"),
    "bottom_center": ("(W-w)/2", "H-h-{m}"),
}


@dataclass
class BrandingPlan:
    """ما سيُركَّب على الفيديو: فلاتر + مدخلات إضافية."""

    filters: List[str] = field(default_factory=list)
    extra_inputs: List[str] = field(default_factory=list)
    text_ass: Optional[Path] = None

    @property
    def active(self) -> bool:
        return bool(self.filters)


def escape_drawtext(value: str) -> str:
    """يهرّب النص لفلتر drawtext — النقطتان والفاصلة تكسران سلسلة الفلاتر."""
    out = str(value)
    for char, replacement in (
        ("\\", r"\\"),
        (":", r"\:"),
        ("'", r"\'"),
        ("%", r"\%"),
        (",", r"\,"),
        ("[", r"\["),
        ("]", r"\]"),
    ):
        out = out.replace(char, replacement)
    return out


def _corner_position(corner: str, margin: int) -> Tuple[str, str]:
    x, y = CORNERS.get(corner, CORNERS["top_right"])
    return x.format(m=margin), y.format(m=margin)


def build_logo_filter(
    logo_path: str | Path,
    *,
    video_width: int,
    corner: str = "top_right",
    margin: int = 40,
    scale_ratio: float = 0.14,
    opacity: float = 0.85,
    input_index: int = 1,
) -> str:
    """يبني فلتر تركيب الشعار.

    الشعار يُحجَّم نسبةً لعرض الفيديو حتى يظهر متسقاً مهما اختلف المقاس.
    """
    logo_w = max(24, int(video_width * scale_ratio))
    x, y = _corner_position(corner, margin)
    opacity = min(1.0, max(0.0, opacity))

    # نهيّئ الشعار في سلسلة فرعية: تحجيم + ضبط الشفافية، ثم overlay
    return (
        f"[{input_index}:v]scale={logo_w}:-1,"
        f"format=rgba,colorchannelmixer=aa={opacity:.2f}[logo];"
        f"[vmain][logo]overlay={x}:{y}"
    )


def build_overlay_ass(
    entries: List[Tuple[str, str]],
    path: str | Path,
    *,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    font_name: str = "DejaVu Sans",
) -> Path:
    """يكتب ملف ASS يحتوي نصوص الهوية (علامة مائية/شارة جزء).

    ``entries`` = [(اسم النمط، النص)] — الأنماط مُعرَّفة أدناه بمواضع ثابتة.
    نستخدم ASS لأنه الطريق الوحيد لعرض العربية موصولة بشكل صحيح.
    """
    header = [
        "[Script Info]",
        "Title: AR-Clipper Branding",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Handle: أسفل الوسط، شبه شفاف (Alignment 2 = أسفل وسط)
        f"Style: Handle,{font_name},{int(play_res_x * 0.028)},&H8CFFFFFF,&H00FFFFFF,"
        f"&H96000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,40,40,{int(play_res_y * 0.035)},1",
        # Badge: أعلى الوسط مع خلفية معتمة (Alignment 8 = أعلى وسط)
        f"Style: Badge,{font_name},{int(play_res_x * 0.034)},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H73000000,-1,0,0,0,100,100,0,0,3,6,0,8,40,40,{int(play_res_y * 0.022)},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = [
        f"Dialogue: 0,0:00:00.00,9:59:59.00,{style},,0,0,0,,{text}"
        for style, text in entries
        if text
    ]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header + events) + "\n", encoding="utf-8")
    return out


def build_branding(
    *,
    settings: Optional[Settings] = None,
    video_width: int = 1080,
    video_height: int = 1920,
    part: Optional[int] = None,
    total_parts: Optional[int] = None,
    work_dir: Optional[str | Path] = None,
    name_hint: str = "branding",
) -> BrandingPlan:
    """يجمع كل عناصر الهوية في خطة واحدة جاهزة للدمج.

    يتخطّى بصمت أي عنصر غير مُعدّ (شعار غير موجود مثلاً) بدل أن يُفشل الإنتاج.
    """
    settings = settings or load_settings()
    plan = BrandingPlan()
    if not settings.get("branding.enabled", False):
        return plan

    # 1) الشعار — صورة تُركَّب بـoverlay
    logo = settings.get("branding.logo_path", "") or ""
    if logo:
        logo_file = Path(logo)
        if not logo_file.is_absolute():
            logo_file = settings.root / logo_file
        if logo_file.exists():
            plan.extra_inputs.append(str(logo_file))
            plan.filters.append(
                build_logo_filter(
                    logo_file,
                    video_width=video_width,
                    corner=str(settings.get("branding.logo_corner", "top_right")),
                    margin=int(settings.get("branding.margin", 40)),
                    scale_ratio=float(settings.get("branding.logo_scale", 0.14)),
                    opacity=float(settings.get("branding.logo_opacity", 0.85)),
                    input_index=len(plan.extra_inputs),
                )
            )
        else:
            log.warning("ملف الشعار غير موجود، سيُتجاهل: %s", logo_file)

    # 2) النصوص (علامة مائية + شارة جزء) — عبر ASS لدعم العربية الموصولة
    entries: List[Tuple[str, str]] = []
    handle = str(settings.get("branding.handle", "") or "").strip()
    if handle:
        entries.append(("Handle", handle))
    if part and total_parts and total_parts > 1:
        entries.append(("Badge", f"الجزء {part}/{total_parts}"))

    if entries:
        target_dir = Path(work_dir) if work_dir else settings.path("paths.tmp")
        ass_path = build_overlay_ass(
            entries,
            target_dir / f"{name_hint}__branding.ass",
            play_res_x=video_width,
            play_res_y=video_height,
            font_name=str(settings.get("subtitles.font_name", "DejaVu Sans")),
        )
        plan.text_ass = ass_path
        plan.filters.append(f"ass='{escape_filter_path(ass_path)}'")

    if plan.active:
        log.info("الهوية البصرية: %d عنصراً.", len(plan.filters))
    return plan


def compose_filter_graph(plan: BrandingPlan, base_video_filter: str = "") -> str:
    """يدمج فلاتر الهوية مع سلسلة الفيديو الأساسية.

    عند وجود شعار نحتاج ``-filter_complex`` بمخرج مُسمّى ``[vout]``؛ وبلا شعار
    تكفي سلسلة ``-vf`` عادية. هذه الدالة تتكفّل بالفرق فلا يعرفه المستدعي.
    """
    if not plan.active:
        return base_video_filter

    overlays = [f for f in plan.filters if "overlay=" in f]
    simple = [f for f in plan.filters if "overlay=" not in f]

    if not overlays:
        # كلها فلاتر بسيطة (نص فقط) — سلسلة عادية تكفي
        chain = [f for f in ([base_video_filter] if base_video_filter else []) + simple]
        return ",".join(chain)

    # سلسلة الأساس تُنتج [vmain]، ثم تُركّب الطبقات فوقها
    head = f"[0:v]{base_video_filter}[vmain]" if base_video_filter else "[0:v]null[vmain]"
    graph = [head]
    graph.extend(overlays)
    result = ";".join(graph)

    if simple:
        result += "," + ",".join(simple)
    return result + "[vout]"
