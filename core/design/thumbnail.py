"""الصورة المصغّرة التلقائية — المرحلة 3، الخطوة 5.

تختار أفضل إطار من المقطع ثم تضع عليه نصاً جذاباً (الـHook من المرحلة 2).

**كيف يُختار "أفضل" إطار؟** بثلاثة معايير محلية بلا أي نموذج:
1. وجود وجه واضح وكبير — الوجوه ترفع نسبة النقر.
2. حدّة الصورة (تباين لابلاسيان) — يستبعد الإطارات المهزوزة أو المموّهة.
3. السطوع المعقول — يستبعد الإطارات السوداء بين اللقطات.

النص يُرسم بـPillow، وتُشكَّل العربية يدوياً لأن Pillow لا يصل الحروف: نستخدم
جدول أشكال الحروف العربية (Unicode Presentation Forms) المدمج في الوحدة، فلا
نحتاج arabic-reshaper ولا python-bidi.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..common.config import Settings, load_settings
from ..common.errors import MediaError
from ..common.logging_utils import get_logger

log = get_logger(__name__)


# ============================================================ تشكيل العربية
# كل حرف: (منعزل، بداية، وسط، نهاية). None = الحرف لا يتصل بما بعده.
_FORMS = {
    "ا": ("ﺍ", None, None, "ﺎ"), "ب": ("ﺏ", "ﺑ", "ﺒ", "ﺐ"),
    "ت": ("ﺕ", "ﺗ", "ﺘ", "ﺖ"), "ث": ("ﺙ", "ﺛ", "ﺜ", "ﺚ"),
    "ج": ("ﺝ", "ﺟ", "ﺠ", "ﺞ"), "ح": ("ﺡ", "ﺣ", "ﺤ", "ﺢ"),
    "خ": ("ﺥ", "ﺧ", "ﺨ", "ﺦ"), "د": ("ﺩ", None, None, "ﺪ"),
    "ذ": ("ﺫ", None, None, "ﺬ"), "ر": ("ﺭ", None, None, "ﺮ"),
    "ز": ("ﺯ", None, None, "ﺰ"), "س": ("ﺱ", "ﺳ", "ﺴ", "ﺲ"),
    "ش": ("ﺵ", "ﺷ", "ﺸ", "ﺶ"), "ص": ("ﺹ", "ﺻ", "ﺼ", "ﺺ"),
    "ض": ("ﺽ", "ﺿ", "ﻀ", "ﺾ"), "ط": ("ﻁ", "ﻃ", "ﻄ", "ﻂ"),
    "ظ": ("ﻅ", "ﻇ", "ﻈ", "ﻆ"), "ع": ("ﻉ", "ﻋ", "ﻌ", "ﻊ"),
    "غ": ("ﻍ", "ﻏ", "ﻐ", "ﻎ"), "ف": ("ﻑ", "ﻓ", "ﻔ", "ﻒ"),
    "ق": ("ﻕ", "ﻗ", "ﻘ", "ﻖ"), "ك": ("ﻙ", "ﻛ", "ﻜ", "ﻚ"),
    "ل": ("ﻝ", "ﻟ", "ﻠ", "ﻞ"), "م": ("ﻡ", "ﻣ", "ﻤ", "ﻢ"),
    "ن": ("ﻥ", "ﻧ", "ﻨ", "ﻦ"), "ه": ("ﻩ", "ﻫ", "ﻬ", "ﻪ"),
    "و": ("ﻭ", None, None, "ﻮ"), "ي": ("ﻱ", "ﻳ", "ﻴ", "ﻲ"),
    "ى": ("ﻯ", None, None, "ﻰ"), "ة": ("ﺓ", None, None, "ﺔ"),
    "أ": ("ﺃ", None, None, "ﺄ"), "إ": ("ﺇ", None, None, "ﺈ"),
    "آ": ("ﺁ", None, None, "ﺂ"), "ؤ": ("ﺅ", None, None, "ﺆ"),
    "ئ": ("ﺉ", "ﺋ", "ﺌ", "ﺊ"), "ء": ("ﺀ", None, None, None),
    "پ": ("ﭖ", "ﭘ", "ﭙ", "ﭗ"), "چ": ("ﭺ", "ﭼ", "ﭽ", "ﭻ"),
    "ژ": ("ﮊ", None, None, "ﮋ"), "گ": ("ﮒ", "ﮔ", "ﮕ", "ﮓ"),
}
# لام + ألف لها شكل مدمج إلزامي
_LAM_ALEF = {"ﻻ": ("لا", "ﻻ", "ﻼ"), "ﻷ": ("لأ", "ﻷ", "ﻸ"),
             "ﻹ": ("لإ", "ﻹ", "ﻺ"), "ﻵ": ("لآ", "ﻵ", "ﻶ")}
_DIACRITIC_RANGE = ("\u064B", "\u0655")


def _is_arabic(ch: str) -> bool:
    return ch in _FORMS


def _connects_forward(ch: str) -> bool:
    """هل يتصل هذا الحرف بما بعده؟"""
    forms = _FORMS.get(ch)
    return bool(forms and forms[1])


def shape_arabic(text: str) -> str:
    """يحوّل نصاً عربياً إلى أشكال العرض الموصولة ويعكسه للرسم من اليمين.

    Pillow يرسم النص كما هو حرفاً حرفاً بلا تشكيل ولا اتجاه، فنقوم بالعملين
    هنا. النتيجة سلسلة جاهزة للرسم مباشرةً بترتيب بصري.
    """
    if not text:
        return ""

    # نزيل التشكيل: Pillow يضعه في مواضع خاطئة مع أشكال العرض
    stripped = "".join(
        ch for ch in text if not (_DIACRITIC_RANGE[0] <= ch <= _DIACRITIC_RANGE[1])
    )

    # دمج لام+ألف
    for combined, (pair, _, _) in _LAM_ALEF.items():
        stripped = stripped.replace(pair, combined)

    out: List[str] = []
    for i, ch in enumerate(stripped):
        if ch in _LAM_ALEF:
            prev = stripped[i - 1] if i > 0 else ""
            out.append(_LAM_ALEF[ch][2] if _connects_forward(prev) else _LAM_ALEF[ch][1])
            continue
        if not _is_arabic(ch):
            out.append(ch)
            continue

        prev = stripped[i - 1] if i > 0 else ""
        nxt = stripped[i + 1] if i + 1 < len(stripped) else ""
        joined_before = _connects_forward(prev)
        joined_after = _is_arabic(nxt) or nxt in _LAM_ALEF

        isolated, initial, medial, final = _FORMS[ch]
        if joined_before and joined_after and medial:
            out.append(medial)
        elif joined_before and final:
            out.append(final)
        elif joined_after and initial:
            out.append(initial)
        else:
            out.append(isolated)

    return _apply_bidi("".join(out))


def _apply_bidi(text: str) -> str:
    """ترتيب بصري مبسّط: يعكس النص مع إبقاء الأرقام واللاتينية بترتيبها."""
    tokens: List[Tuple[bool, str]] = []  # (هل هو مقطع LTR؟، النص)
    buffer = ""
    buffer_ltr: Optional[bool] = None

    for ch in text:
        if ch.isspace():
            is_ltr = buffer_ltr
        elif ch.isdigit() or ("a" <= ch.lower() <= "z"):
            is_ltr = True
        elif unicodedata.category(ch).startswith("P") or unicodedata.category(ch) == "Sm":
            is_ltr = buffer_ltr
        else:
            is_ltr = False

        if buffer_ltr is None:
            buffer_ltr = is_ltr if is_ltr is not None else False
        if is_ltr is not None and is_ltr != buffer_ltr:
            tokens.append((buffer_ltr, buffer))
            buffer, buffer_ltr = ch, is_ltr
        else:
            buffer += ch
    if buffer:
        tokens.append((bool(buffer_ltr), buffer))

    result = ""
    for is_ltr, chunk in tokens:
        result = (chunk if is_ltr else chunk[::-1]) + result
    return result


# ============================================================ اختيار الإطار


@dataclass
class FrameScore:
    """تقييم إطار مرشّح للصورة المصغّرة."""

    time: float
    sharpness: float = 0.0
    brightness: float = 0.0
    face_area: float = 0.0

    @property
    def total(self) -> float:
        # الوجه هو العامل الأقوى، ثم الحدّة، والسطوع شرط سلبي فقط
        penalty = 0.0
        if self.brightness < 35 or self.brightness > 225:
            penalty = 0.6
        # الحدّة تُسقَّف عند 1.0 (ما بعدها ليس "أحدّ" بصرياً)، والوجه يُوزن
        # أعلى لأنه العامل الأقوى في جذب النقر. الجمع لا الضرب حتى لا يُلغي
        # غياب أحدهما الآخر تماماً.
        sharpness_score = min(1.0, self.sharpness / 400.0)
        return (self.face_area * 4.0 + sharpness_score * 0.8) * (1 - penalty)


def pick_best_frame(
    video_path: str | Path, *, samples: int = 24, settings: Optional[Settings] = None
) -> Optional[FrameScore]:
    """يمسح عيّنات من الفيديو ويرجع تقييم أفضل إطار."""
    try:
        import cv2
    except ImportError:
        log.warning("opencv غير مثبت — تعذّر اختيار أفضل إطار.")
        return None

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return None

    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            return None

        cascade = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        step = max(1, total // max(1, samples))
        best: Optional[FrameScore] = None

        # القراءة التسلسلية مع grab أسرع بكثير من البحث العشوائي (set POS_FRAMES)،
        # لأن الأخير يجبر فك ترميز من أقرب keyframe في كل مرة.
        index = -1
        while True:
            if not capture.grab():
                break
            index += 1
            if index % step:
                continue
            ok, frame = capture.retrieve()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            # التقييم على نسخة مصغّرة: الحدّة والوجه يُقاسان بدقة كافية وأسرع كثيراً
            if width > 480:
                scale = 480 / width
                frame = cv2.resize(frame, (480, max(1, int(height * scale))))
                height, width = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            score = FrameScore(time=index / fps)
            score.sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            score.brightness = float(gray.mean())
            if not cascade.empty():
                faces = cascade.detectMultiScale(
                    cv2.equalizeHist(gray), 1.1, 5, minSize=(int(width * 0.06),) * 2
                )
                if len(faces):
                    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                    score.face_area = (w * h) / (width * height)

            if best is None or score.total > best.total:
                best = score
    finally:
        capture.release()

    if best:
        log.info(
            "أفضل إطار عند %.1fs (وجه %.1f%%، حدّة %.0f).",
            best.time,
            best.face_area * 100,
            best.sharpness,
        )
    return best


# ============================================================ التوليد


def generate_thumbnail(
    video_path: str | Path,
    output_path: str | Path,
    *,
    text: str = "",
    settings: Optional[Settings] = None,
    at_time: Optional[float] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Path:
    """ينتج صورة مصغّرة من أفضل إطار مع نص جذاب فوقه."""
    settings = settings or load_settings()
    try:
        import cv2
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise MediaError(f"الصورة المصغّرة تحتاج opencv وPillow: {exc}") from exc

    out_w = int(width or settings.get("thumbnail.width", 1080))
    out_h = int(height or settings.get("thumbnail.height", 1920))

    timestamp = at_time
    if timestamp is None:
        best = pick_best_frame(video_path, settings=settings)
        timestamp = best.time if best else 0.0

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise MediaError(f"تعذّر فتح الفيديو: {video_path}")
    capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        raise MediaError(f"تعذّر قراءة إطار عند {timestamp:.1f}s")

    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    image = _cover_resize(image, out_w, out_h)

    if text:
        image = _draw_hook(image, text, settings)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, quality=int(settings.get("thumbnail.quality", 88)))
    log.info("الصورة المصغّرة: %s (من %.1fs)", out.name, timestamp)
    return out


def _cover_resize(image, target_w: int, target_h: int):
    """يملأ المقاس المطلوب بلا تشويه (قص من الأطول)."""
    from PIL import Image

    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    new_size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
    resized = image.resize(new_size, Image.LANCZOS)
    left = (new_size[0] - target_w) // 2
    top = (new_size[1] - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _load_font(settings: Settings, size: int):
    """يحمّل خطاً يدعم العربية، مع تراجع لخط Pillow الافتراضي.

    البحث يمرّ عبر ``core.common.fonts`` الذي يعرف مجلدات ويندوز وماك ولينكس.
    الاعتماد على مسار لينكس ثابت كان يُسقط ويندوز إلى خط Pillow الافتراضي،
    وهو **لا يرسم العربية إطلاقاً** (مربّعات فارغة).
    """
    from PIL import ImageFont

    from ..common.fonts import find_font_file

    found = find_font_file(str(settings.get("thumbnail.font_file", "") or ""))
    if found:
        try:
            return ImageFont.truetype(str(found), size)
        except OSError:
            pass
    try:  # الخط المرفق مع matplotlib/Pillow إن وُجد
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        log.warning(
            "لم يُعثر على خط يدعم العربية — قد يظهر النص مربّعات فارغة. "
            "حدّد خطاً في settings.yaml تحت thumbnail.font_file."
        )
        return ImageFont.load_default()


def _draw_hook(image, text: str, settings: Settings):
    """يرسم النص الجذاب أسفل الصورة فوق تدرّج معتم يضمن القراءة."""
    from PIL import Image, ImageDraw

    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")

    font_size = max(28, int(width * float(settings.get("thumbnail.font_scale", 0.075))))
    font = _load_font(settings, font_size)

    # لفّ النص يدوياً بعدد أحرف معقول ثم تشكيل كل سطر
    max_chars = int(settings.get("thumbnail.max_line_chars", 22))
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = lines[: int(settings.get("thumbnail.max_lines", 3))]
    shaped = [shape_arabic(line) for line in lines]

    line_height = int(font_size * 1.35)
    block_height = line_height * len(shaped)
    margin = int(height * 0.07)
    top = height - block_height - margin

    # تدرّج معتم خلف النص (شرائح شفافية متدرجة = تدرّج بلا تبعية)
    band_top = max(0, top - int(font_size * 1.6))
    steps = 40
    band_height = height - band_top
    for i in range(steps):
        y0 = band_top + int(band_height * i / steps)
        y1 = band_top + int(band_height * (i + 1) / steps) + 1
        # تدرّج من شفاف تماماً إلى شبه معتم — يضمن قراءة النص على أي خلفية
        alpha = int(235 * (i / steps) ** 0.55)
        draw.rectangle([0, y0, width, y1], fill=(0, 0, 0, alpha))

    for index, line in enumerate(shaped):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (width - text_w) // 2
        y = top + index * line_height
        # حدّ أسود حول النص لضمان التباين على أي خلفية
        for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 230))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

    return image.convert("RGB")
