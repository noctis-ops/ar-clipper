"""محطة الترجمة المرئية (Subtitles) — توليد SRT / VTT / ASS وحرقها على الفيديو.

ملاحظات مهمة حول العربية:
- نكتب النص بترتيبه المنطقي (Logical order) ونترك التشكيل والاتجاه لـ libass
  (المدمج في ffmpeg مع FriBidi/HarfBuzz) — عكس النص يدوياً يفسد العرض.
- نضيف علامة RLM في بداية كل سطر عربي لضبط الاتجاه عند اختلاط الأرقام.
- في المرحلة 1 نبني ASS بسيطاً (خط واضح + حدود). التلوين المتحرك على مستوى
  الكلمة (Karaoke) مؤجّل للمرحلة 3 حسب الوثيقة.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..common.config import Settings, load_settings
from ..common.errors import MediaError
from ..common.ffmpeg import apply_filters, escape_filter_path
from ..common.logging_utils import get_logger
from ..common.schemas import Segment, Transcript
from ..common.text_utils import format_timestamp, prepare_subtitle_text

log = get_logger(__name__)


# ============================================================ الألوان


def hex_to_ass_color(value: str, default: str = "&H00FFFFFF") -> str:
    """يحوّل ``#RRGGBB`` أو ``#AARRGGBB`` إلى صيغة ASS ``&HAABBGGRR``.

    انتبه: ASS يستخدم ترتيب BGR، وقناة الألفا فيه **معكوسة** (00 = معتم تماماً).
    """
    if not value:
        return default
    raw = str(value).strip()
    if raw.upper().startswith("&H"):
        return raw
    raw = raw.lstrip("#")
    try:
        if len(raw) == 6:
            r, g, b = raw[0:2], raw[2:4], raw[4:6]
            a = "00"
        elif len(raw) == 8:
            a, r, g, b = raw[0:2], raw[2:4], raw[4:6], raw[6:8]
            a = f"{255 - int(a, 16):02X}"  # عكس الألفا لصيغة ASS
        else:
            return default
        return f"&H{a}{b}{g}{r}".upper()
    except ValueError:
        return default


# ============================================================ تجهيز الجمل


def _visible_segments(
    transcript: Transcript, track: str, style_cfg: Dict
) -> List[tuple[float, float, str]]:
    """يرجع (بداية، نهاية، نص جاهز للعرض) لكل جملة غير فارغة."""
    max_chars = int(style_cfg.get("max_line_chars", 38))
    max_lines = int(style_cfg.get("max_lines", 2))
    rtl = bool(style_cfg.get("rtl", True))

    out: List[tuple[float, float, str]] = []
    for seg in transcript.segments:
        raw = seg.display_text(track)
        if not raw or not raw.strip():
            continue
        lines = [
            prepare_subtitle_text(part, max_chars=max_chars, max_lines=max_lines, rtl=rtl)
            for part in raw.split("\n")
            if part.strip()
        ]
        text = "\n".join(l for l in lines if l)
        if not text:
            continue
        start = max(0.0, float(seg.start))
        end = max(start + 0.2, float(seg.end))  # حد أدنى للظهور
        out.append((start, end, text))

    # منع التداخل الزمني بين الجمل المتتالية
    for i in range(len(out) - 1):
        s, e, t = out[i]
        next_start = out[i + 1][0]
        if e > next_start:
            out[i] = (s, max(s + 0.2, next_start - 0.01), t)
    return out


# ============================================================ SRT / VTT


def write_srt(transcript: Transcript, path: str | Path, *, track: str = "ar", settings=None) -> Path:
    settings = settings or load_settings()
    style = settings.section("subtitles")
    entries = _visible_segments(transcript, track, style)

    lines: List[str] = []
    for idx, (start, end, text) in enumerate(entries, start=1):
        lines.append(str(idx))
        lines.append(
            f"{format_timestamp(start, sep=',')} --> {format_timestamp(end, sep=',')}"
        )
        lines.append(text)
        lines.append("")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    log.info("كُتب ملف SRT: %s (%d سطر ترجمة)", p.name, len(entries))
    return p


def write_vtt(transcript: Transcript, path: str | Path, *, track: str = "ar", settings=None) -> Path:
    settings = settings or load_settings()
    style = settings.section("subtitles")
    entries = _visible_segments(transcript, track, style)

    lines: List[str] = ["WEBVTT", ""]
    for start, end, text in entries:
        lines.append(f"{format_timestamp(start, sep='.')} --> {format_timestamp(end, sep='.')}")
        lines.append(text)
        lines.append("")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    log.info("كُتب ملف VTT: %s", p.name)
    return p


# ============================================================ ASS


def _ass_timestamp(seconds: float) -> str:
    """صيغة ASS: ``H:MM:SS.cc`` (مئات الثانية)."""
    seconds = max(0.0, float(seconds))
    cs_total = int(round(seconds * 100))
    hours, rem = divmod(cs_total, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def build_ass_style(style_cfg: Dict, *, play_res_x: int, play_res_y: int) -> str:
    """يبني قسمي [Script Info] و [V4+ Styles] لملف ASS."""
    font = style_cfg.get("font_name", "DejaVu Sans")
    size = int(style_cfg.get("font_size", 54))
    primary = hex_to_ass_color(style_cfg.get("primary_color", "#FFFFFF"), "&H00FFFFFF")
    outline_c = hex_to_ass_color(style_cfg.get("outline_color", "#000000"), "&H00000000")
    back_c = hex_to_ass_color(style_cfg.get("back_color", "#A0000000"), "&H80000000")
    # SecondaryColour = لون الكلمات التي لم تُنطق بعد في وضع الكاريوكي.
    # الافتراضي في ASS أحمر صارخ؛ نجعله رمادياً هادئاً حتى يبرز المنطوق بالتباين.
    secondary = hex_to_ass_color(
        style_cfg.get("secondary_color", "#9AA0A6"), "&H00A0A09A"
    )
    outline = float(style_cfg.get("outline", 3))
    shadow = float(style_cfg.get("shadow", 1))
    bold = -1 if style_cfg.get("bold", True) else 0
    margin_v = int(style_cfg.get("margin_v", 180))
    margin_h = int(style_cfg.get("margin_h", 60))

    return "\n".join(
        [
            "[Script Info]",
            "Title: AR-Clipper Subtitles",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            f"PlayResX: {play_res_x}",
            f"PlayResY: {play_res_y}",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            # Encoding=1 (Default) يعمل بشكل صحيح مع libass للعربية عبر HarfBuzz
            f"Style: Default,{font},{size},{primary},{secondary},{outline_c},{back_c},"
            f"{bold},0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_h},{margin_h},{margin_v},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )


def _animated_events(
    transcript: Transcript, track: str, style_cfg: Dict, settings: Settings
) -> List[str]:
    """يبني أحداث Dialogue بوسوم كاريوكي — المرحلة 3."""
    from .animated import (
        build_intro_tags,
        build_karaoke_text,
        collect_keywords_from_transcript,
    )

    keywords = (
        collect_keywords_from_transcript(transcript, track=track, settings=settings)
        if bool(settings.get("subtitles.highlight_keywords", True))
        else set()
    )
    intro = build_intro_tags(
        fade_ms=int(settings.get("subtitles.fade_ms", 120)),
        pop=bool(settings.get("subtitles.pop_in", True)),
    )
    highlight = hex_to_ass_color(
        settings.get("subtitles.highlight_color", "#FFD700"), "&H0000D7FF"
    )
    base = hex_to_ass_color(style_cfg.get("primary_color", "#FFFFFF"), "&H00FFFFFF")
    rtl = bool(style_cfg.get("rtl", True))

    events: List[str] = []
    ordered = sorted(transcript.segments, key=lambda s: s.start)
    for index, seg in enumerate(ordered):
        if not (seg.display_text(track) or "").strip():
            continue
        body = build_karaoke_text(
            seg,
            keywords=keywords,
            highlight_color=highlight,
            base_color=base,
            rtl=rtl,
        )
        if not body:
            continue
        start = max(0.0, float(seg.start))
        end = max(start + 0.2, float(seg.end))
        # منع التداخل مع الجملة التالية
        if index + 1 < len(ordered):
            nxt = float(ordered[index + 1].start)
            if end > nxt:
                end = max(start + 0.2, nxt - 0.01)
        events.append(
            f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
            f"Default,,0,0,0,,{intro}{body}"
        )

    if events:
        log.info("ترجمة متحركة: %d حدث، %d كلمة مبرَزة.", len(events), len(keywords))
    return events


def write_ass(
    transcript: Transcript,
    path: str | Path,
    *,
    track: str = "ar",
    settings: Optional[Settings] = None,
    play_res_x: Optional[int] = None,
    play_res_y: Optional[int] = None,
    animated: Optional[bool] = None,
) -> Path:
    """يكتب ملف ASS — ثابتاً (المرحلة 1) أو متحركاً بأسلوب الكاريوكي (المرحلة 3).

    الوضع المتحرك يلوّن الكلمة أثناء نطقها ويُبرز الكلمات المهمة؛ يُفعَّل عبر
    ``subtitles.animated`` ويتراجع تلقائياً للثابت إن تعذّر.
    """
    settings = settings or load_settings()
    style = settings.section("subtitles")
    res_x = int(play_res_x or settings.get("reframe.width", 1080))
    res_y = int(play_res_y or settings.get("reframe.height", 1920))

    use_animation = (
        animated if animated is not None else bool(settings.get("subtitles.animated", False))
    )

    header = build_ass_style(style, play_res_x=res_x, play_res_y=res_y)

    events: List[str] = []
    if use_animation:
        try:
            events = _animated_events(transcript, track, style, settings)
        except Exception as exc:  # الحركة تحسين، لا يجوز أن تُفشل الإنتاج
            log.warning("تعذّر بناء الترجمة المتحركة (%s) — ترجمة ثابتة.", exc)
            events = []

    if not events:
        entries = _visible_segments(transcript, track, style)
        for start, end, text in entries:
            body = text.replace("\n", r"\N")
            events.append(
                f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Default,,0,0,0,,{body}"
            )

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + "\n" + "\n".join(events) + "\n", encoding="utf-8")
    log.info("كُتب ملف ASS: %s (%d حدث)", p.name, len(events))
    return p


# ============================================================ الحرق على الفيديو


def burn_subtitles(
    video_path: str | Path,
    ass_path: str | Path,
    output_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    fonts_dir: Optional[str | Path] = None,
    extra_video_filter: str = "",
    audio_filter: str = "",
    branding=None,
) -> Path:
    """يحرق ملف ASS على الفيديو عبر فلتر ``ass`` في ffmpeg.

    ``extra_video_filter``/``audio_filter`` يسمحان بدمج لمسات أخرى (مثل
    الظهور/الاختفاء الناعم) في **نفس** التمريرة بدل دورة ترميز إضافية.
    """
    settings = settings or load_settings()
    ass_file = Path(ass_path)
    if not ass_file.exists():
        raise MediaError(f"ملف ASS غير موجود: {ass_file}")

    vf = f"ass='{escape_filter_path(ass_file)}'"
    if fonts_dir:
        vf += f":fontsdir='{escape_filter_path(fonts_dir)}'"
    if extra_video_filter:
        vf = f"{vf},{extra_video_filter}"

    extra_inputs = []
    if branding is not None and getattr(branding, "active", False):
        from ..design.branding import compose_filter_graph

        vf = compose_filter_graph(branding, vf)
        extra_inputs = list(branding.extra_inputs)

    log.info("حرق الترجمة على الفيديو...")
    return apply_filters(
        video_path,
        output_path,
        video_filter=vf,
        audio_filter=audio_filter,
        extra_inputs=extra_inputs,
        crf=int(settings.get("export.crf", 20)),
        preset=str(settings.get("export.preset", "medium")),
        video_codec=str(settings.get("export.video_codec", "libx264")),
        audio_codec=str(settings.get("export.audio_codec", "aac")),
        audio_bitrate=str(settings.get("export.audio_bitrate", "160k")),
    )


# ============================================================ واجهة مجمّعة

WRITERS = {"srt": write_srt, "vtt": write_vtt, "ass": write_ass}


def write_sidecars(
    transcript: Transcript,
    base_path: str | Path,
    *,
    formats: Optional[Iterable[str]] = None,
    track: str = "ar",
    settings: Optional[Settings] = None,
    play_res_x: Optional[int] = None,
    play_res_y: Optional[int] = None,
) -> Dict[str, str]:
    """يكتب كل صيغ الترجمة المرافقة بجانب الفيديو ويرجع خريطة {الصيغة: المسار}."""
    settings = settings or load_settings()
    fmts = [f.lower() for f in (formats or settings.get("export.sidecar_formats", ["srt"]))]
    base = Path(base_path)
    written: Dict[str, str] = {}

    for fmt in fmts:
        writer = WRITERS.get(fmt)
        if writer is None:
            log.warning("صيغة ترجمة غير مدعومة، تم تجاهلها: %s", fmt)
            continue
        target = base.with_suffix(f".{fmt}")
        if fmt == "ass":
            writer(
                transcript,
                target,
                track=track,
                settings=settings,
                play_res_x=play_res_x,
                play_res_y=play_res_y,
            )
        else:
            writer(transcript, target, track=track, settings=settings)
        written[fmt] = str(target)

    return written
