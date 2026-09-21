"""تتبّع الوجه وتوسيط المتحدث تلقائياً — المرحلة 3، الخطوة 1.

المشكلة التي يحلّها: القص المركزي الثابت (المرحلة 1) يقطع رأس المتحدث كلما جلس
خارج منتصف الكادر — وهذا هو الحال الغالب في البودكاست (متحدثان على الجانبين).

الحل: نأخذ عيّنات من إطارات الفيديو، نكشف الوجه في كل عيّنة، ثم نبني مساراً
أفقياً سلساً يتبع الوجه. يُحوَّل المسار إلى تعبير ``crop`` ديناميكي في ffmpeg،
فيبقى القص تمريرة ترميز واحدة — بلا استخراج إطارات إلى القرص وبلا تمريرة ثانية.

المحركات (بالترتيب، مع تراجع آمن في كل خطوة):
1. ``opencv``   — Haar Cascade المرفق داخل حزمة opencv نفسها. الافتراضي: لا
   تنزيل نماذج ولا اتصال بالشبكة إطلاقاً.
2. ``mediapipe``— أدق، لكنه يتطلب تنزيل ملف نموذج لأول مرة، فهو اختياري.
3. ``center``   — إن فشل كل شيء، نرجع للقص المركزي بلا أي خطأ للمستخدم.

كل هذا مجاني ومفتوح المصدر ويعمل محلياً بلا حساب ولا مفتاح API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..common.config import Settings, load_settings
from ..common.logging_utils import get_logger

log = get_logger(__name__)


@dataclass
class FaceSample:
    """موضع الوجه في لحظة زمنية محددة."""

    time: float
    center_x: float  # 0..1 نسبةً لعرض الإطار
    center_y: float  # 0..1 نسبةً لارتفاع الإطار
    size: float = 0.0  # عرض الوجه نسبةً لعرض الإطار (مؤشر ثقة/قرب)
    # كل الوجوه في هذا الإطار: [(x, y, size)] — لاكتشاف الحوار الثنائي
    all_faces: List[tuple] = field(default_factory=list)


@dataclass
class TrackResult:
    """حصيلة التتبّع: مسار زمني + إحصاءات تشخيصية."""

    samples: List[FaceSample] = field(default_factory=list)
    frames_scanned: int = 0
    engine: str = "none"

    @property
    def detection_rate(self) -> float:
        if self.frames_scanned <= 0:
            return 0.0
        return len(self.samples) / self.frames_scanned

    @property
    def found(self) -> bool:
        return bool(self.samples)


# ============================================================ الكشف


def _detect_opencv(frames: Sequence[Tuple[float, "object"]], min_size_ratio: float):
    """يكشف الوجوه عبر Haar Cascade المرفق مع opencv (بلا تنزيل)."""
    import cv2  # استيراد كسول: الوحدة تعمل بدونه في وضع center

    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    profile_path = Path(cv2.data.haarcascades) / "haarcascade_profileface.xml"
    cascade = cv2.CascadeClassifier(str(cascade_path))
    profile = cv2.CascadeClassifier(str(profile_path))
    if cascade.empty():
        raise RuntimeError("تعذّر تحميل مصنّف Haar المرفق مع opencv.")

    samples: List[FaceSample] = []
    for timestamp, frame in frames:
        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        min_side = max(24, int(width * min_size_ratio))

        # مهم: لا نبدأ بـ``equalizeHist``. قياسٌ فعلي أظهر أنه يمحو وجوهاً
        # كانت تُكتشف بلا معالجة (1 → 0) في المشاهد المتجانسة الإضاءة، بينما
        # لا يضيف شيئاً على الوجوه العادية. نجرّب الصورة الخام أولاً، ونلجأ
        # إلى معادلة الهيستوغرام فقط حين يفشل الكشف — فتنفع الإضاءة السيئة
        # بلا أن تضرّ الحالة الطبيعية.
        def _detect(image):
            found = cascade.detectMultiScale(
                image, scaleFactor=1.1, minNeighbors=5, minSize=(min_side, min_side)
            )
            if len(found) == 0 and not profile.empty():
                # المتحدث ملتفت للجانب — شائع جداً في حوار بين شخصين
                found = profile.detectMultiScale(
                    image, scaleFactor=1.1, minNeighbors=5, minSize=(min_side, min_side)
                )
            return found

        faces = _detect(gray)
        if len(faces) == 0:
            faces = _detect(cv2.equalizeHist(gray))
        if len(faces) == 0:
            continue

        # الوجه الأكبر = الأقرب للكاميرا = المتحدث المقصود غالباً
        x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        samples.append(
            FaceSample(
                time=timestamp,
                center_x=(x + w / 2) / width,
                center_y=(y + h / 2) / height,
                size=w / width,
                # كل الوجوه المكتشفة — تُستخدم لتخطيط الشاشة المنقسمة
                all_faces=[
                    ((fx + fw / 2) / width, (fy + fh / 2) / height, fw / width)
                    for fx, fy, fw, fh in faces
                ],
            )
        )
    return samples


def _detect_mediapipe(frames: Sequence[Tuple[float, "object"]], min_size_ratio: float):
    """محرك اختياري أدق — يتطلب ملف نموذج محلي، وإلا يرمي ويتراجع المستدعي."""
    import mediapipe as mp  # noqa: F401

    raise RuntimeError(
        "محرك mediapipe يتطلب تنزيل ملف نموذج (blaze_face_short_range.tflite). "
        "المحرك الافتراضي opencv يعمل بلا تنزيل."
    )


def track_faces(
    video_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    engine: Optional[str] = None,
    sample_fps: Optional[float] = None,
    max_samples: int = 400,
) -> TrackResult:
    """يمسح الفيديو ويرجع مسار الوجه عبر الزمن.

    نأخذ عيّنة كل ``1/sample_fps`` ثانية بدل كل إطار: الوجه لا يقفز 25 مرة في
    الثانية، والمسح الكامل يكلّف أضعاف زمن الترميز بلا فائدة.
    """
    settings = settings or load_settings()
    result = TrackResult()

    # مسار التتبّع مكلف (~2.6s) ويتكرّر حرفياً عند إعادة إنتاج نفس المقطع
    # بقالب مختلف (--variants). التخزين المؤقت يُسقط هذه الكلفة تماماً.
    cache_file = _cache_path(video_path, settings)
    cached = _load_cache(cache_file, video_path)
    if cached is not None:
        log.info("مسار الوجه من الذاكرة المؤقتة (%d عيّنة).", len(cached.samples))
        return cached

    try:
        import cv2
    except ImportError:
        log.warning("opencv غير مثبت — تتبّع الوجه معطّل، سيُستخدم القص المركزي.")
        return result

    engine = (engine or settings.get("reframe.face_engine", "opencv")).lower()
    sample_fps = float(sample_fps or settings.get("reframe.sample_fps", 3.0))
    min_size_ratio = float(settings.get("reframe.min_face_ratio", 0.05))

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        log.warning("تعذّر فتح الفيديو للتتبّع: %s", video_path)
        return result

    try:
        native_fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, int(round(native_fps / max(0.1, sample_fps))))
        if total > 0 and (total // step) > max_samples:
            step = max(step, total // max_samples)

        frames: List[Tuple[float, object]] = []
        index = 0
        while True:
            ok = capture.grab()
            if not ok:
                break
            if index % step == 0:
                ok, frame = capture.retrieve()
                if ok and frame is not None:
                    frames.append((index / native_fps, frame))
            index += 1
    finally:
        capture.release()

    result.frames_scanned = len(frames)
    if not frames:
        return result

    for candidate in ([engine] if engine != "auto" else ["opencv"]):
        try:
            detector = {
                "opencv": _detect_opencv,
                "mediapipe": _detect_mediapipe,
            }.get(candidate)
            if detector is None:
                log.warning("محرك تتبّع غير معروف: %s — سيُستخدم opencv.", candidate)
                detector = _detect_opencv
                candidate = "opencv"
            result.samples = detector(frames, min_size_ratio)
            result.engine = candidate
            break
        except Exception as exc:
            log.warning("فشل محرك التتبّع %s: %s", candidate, exc)

    log.info(
        "تتبّع الوجه (%s): %d وجه في %d عيّنة (%.0f%%).",
        result.engine,
        len(result.samples),
        result.frames_scanned,
        result.detection_rate * 100,
    )
    _save_cache(cache_file, video_path, result)
    return result


# ============================================================ التخزين المؤقت


def _cache_path(video_path: str | Path, settings: Settings) -> Optional[Path]:
    """مسار ملف الكاش المبني على بصمة الفيديو."""
    if not settings.get("reframe.cache_tracks", True):
        return None
    try:
        source = Path(video_path)
        stat = source.stat()
        import hashlib

        key = hashlib.sha1(
            f"{source.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|"
            f"{settings.get('reframe.sample_fps')}|{settings.get('reframe.face_engine')}"
            "".encode("utf-8")
        ).hexdigest()[:16]
        directory = settings.path("paths.tmp") / "face_tracks"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{key}.json"
    except (OSError, ValueError):
        return None


def _load_cache(path: Optional[Path], video_path: str | Path) -> Optional[TrackResult]:
    if not path or not path.exists():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        result = TrackResult(
            frames_scanned=int(data["frames_scanned"]),
            engine=str(data["engine"]),
        )
        result.samples = [
            FaceSample(
                time=float(s["t"]),
                center_x=float(s["x"]),
                center_y=float(s["y"]),
                size=float(s.get("s", 0.0)),
                all_faces=[tuple(f) for f in s.get("all", [])],
            )
            for s in data["samples"]
        ]
        return result
    except (OSError, ValueError, KeyError):
        return None  # كاش تالف يُتجاهل بصمت ويُعاد التتبّع


def _save_cache(path: Optional[Path], video_path: str | Path, result: TrackResult) -> None:
    if not path or not result.found:
        return
    try:
        import json

        path.write_text(
            json.dumps(
                {
                    "engine": result.engine,
                    "frames_scanned": result.frames_scanned,
                    "samples": [
                        {
                            "t": round(s.time, 3),
                            "x": round(s.center_x, 5),
                            "y": round(s.center_y, 5),
                            "s": round(s.size, 5),
                            "all": [[round(v, 5) for v in f] for f in s.all_faces],
                        }
                        for s in result.samples
                    ],
                }
            ),
            encoding="utf-8",
        )
    except OSError:  # pragma: no cover
        pass


# ============================================================ التنعيم


def smooth_track(
    samples: Sequence[FaceSample],
    *,
    window: int = 5,
    max_step: float = 0.04,
) -> List[FaceSample]:
    """ينعّم المسار لتفادي اهتزاز الكاميرا الافتراضية.

    خطوتان: متوسط متحرك يزيل الرجفة، ثم حدّ أقصى لسرعة الحركة بين عيّنتين
    يمنع القفزات المفاجئة (مثلاً عند كشف خاطئ في إطار واحد).
    """
    if not samples:
        return []
    ordered = sorted(samples, key=lambda s: s.time)
    if len(ordered) == 1:
        return list(ordered)

    window = max(1, int(window))
    half = window // 2
    averaged: List[FaceSample] = []
    for i, sample in enumerate(ordered):
        lo = max(0, i - half)
        hi = min(len(ordered), i + half + 1)
        chunk = ordered[lo:hi]
        averaged.append(
            FaceSample(
                time=sample.time,
                center_x=sum(s.center_x for s in chunk) / len(chunk),
                center_y=sum(s.center_y for s in chunk) / len(chunk),
                size=sum(s.size for s in chunk) / len(chunk),
            )
        )

    limited = [averaged[0]]
    for sample in averaged[1:]:
        previous = limited[-1]
        delta = sample.center_x - previous.center_x
        if abs(delta) > max_step:
            delta = max_step if delta > 0 else -max_step
        limited.append(
            FaceSample(
                time=sample.time,
                center_x=previous.center_x + delta,
                center_y=sample.center_y,
                size=sample.size,
            )
        )
    return limited


def average_focus(samples: Sequence[FaceSample]) -> Tuple[float, float]:
    """مركز ثقل المسار — يُستخدم كـ focus ثابت حين لا يتحرك المتحدث."""
    if not samples:
        return 0.5, 0.5
    return (
        sum(s.center_x for s in samples) / len(samples),
        sum(s.center_y for s in samples) / len(samples),
    )


def track_spread(samples: Sequence[FaceSample]) -> float:
    """أقصى إزاحة أفقية في المسار — تقرّر: قص ثابت أم متحرك؟"""
    if len(samples) < 2:
        return 0.0
    xs = [s.center_x for s in samples]
    return max(xs) - min(xs)


# ============================================================ بناء الفلتر


def build_dynamic_crop(
    samples: Sequence[FaceSample],
    *,
    src_w: int,
    src_h: int,
    crop_w: int,
    crop_h: int,
) -> str:
    """يحوّل المسار إلى تعبير ``crop`` متحرك يفهمه ffmpeg.

    نبني دالة خطّية متعددة القطع بصيغة ``if(lt(t,t1), x0, if(lt(t,t2), x1, ...))``
    فينزلق القص بين المواضع بدل القفز. هذا يُنفَّذ داخل تمريرة الترميز نفسها.
    """
    if not samples:
        x = max(0, (src_w - crop_w) // 2)
        y = max(0, (src_h - crop_h) // 2)
        return f"crop={crop_w}:{crop_h}:{x}:{y}"

    max_x = max(0, src_w - crop_w)
    max_y = max(0, src_h - crop_h)

    points: List[Tuple[float, int]] = []
    for sample in samples:
        # مركز الوجه يصبح مركز نافذة القص، مع تقييدها داخل حدود الإطار
        x = int(round(sample.center_x * src_w - crop_w / 2))
        x = max(0, min(max_x, x))
        if points and points[-1][1] == x:
            continue  # نقاط مكرّرة تضخّم التعبير بلا فائدة
        points.append((sample.time, x))

    if len(points) == 1:
        y = max(0, min(max_y, int(round(samples[0].center_y * src_h - crop_h / 2))))
        return f"crop={crop_w}:{crop_h}:{points[0][1]}:{y}"

    # تعبير طويل جداً يُبطئ ffmpeg — نكتفي بعدد معقول من نقاط الانعطاف
    if len(points) > 120:
        stride = len(points) // 120 + 1
        points = points[::stride] + [points[-1]]

    expr = str(points[-1][1])
    for (t0, x0), (t1, x1) in zip(reversed(points[:-1]), reversed(points[1:])):
        span = max(1e-3, t1 - t0)
        # استيفاء خطّي بين النقطتين
        segment = f"({x0}+({x1 - x0})*(t-{t0:.3f})/{span:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{segment},{expr})"

    y_center = sum(s.center_y for s in samples) / len(samples)
    y = max(0, min(max_y, int(round(y_center * src_h - crop_h / 2))))
    return f"crop={crop_w}:{crop_h}:'{expr}':{y}"


# ============================================================ الحوار الثنائي


@dataclass
class DialogueLayout:
    """تخطيط حوار بين متحدثَين: نافذتا قص مكدّستان رأسياً."""

    top: Tuple[float, float] = (0.0, 0.0)  # (مركز أفقي، مركز رأسي) للوجه العلوي
    bottom: Tuple[float, float] = (0.0, 0.0)
    confidence: float = 0.0
    # متوسط عرض الوجه نسبةً لعرض الإطار — يحدّد قوة التقريب
    face_size: float = 0.0

    @property
    def usable(self) -> bool:
        return self.confidence >= 0.5


def detect_dialogue(
    samples: Sequence[FaceSample], *, min_ratio: float = 0.4, min_gap: float = 0.25
) -> DialogueLayout:
    """يكتشف وجود متحدثَين ثابتين على جانبَي الكادر.

    الحالة الأصعب في البودكاست: شخصان على اليمين واليسار. تتبّع وجه واحد يعني
    قطع الآخر تماماً، والقص المركزي يقطع الاثنين. الحل: شاشة منقسمة رأسياً.

    ``min_ratio`` = النسبة الدنيا من العيّنات التي يجب أن تُظهر وجهَين.
    ``min_gap``   = المسافة الأفقية الدنيا بينهما (وإلا فهما وجه واحد مكرّر).
    """
    layout = DialogueLayout()
    if not samples:
        return layout

    pairs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    sizes: List[float] = []
    for sample in samples:
        faces = sorted(sample.all_faces or [], key=lambda f: -f[2])[:2]
        if len(faces) < 2:
            continue
        left, right = sorted(faces[:2], key=lambda f: f[0])
        if (right[0] - left[0]) < min_gap:
            continue
        pairs.append(((left[0], left[1]), (right[0], right[1])))
        sizes.append((left[2] + right[2]) / 2)

    layout.confidence = len(pairs) / len(samples)
    if not pairs or layout.confidence < min_ratio:
        return layout

    layout.top = (
        sum(p[0][0] for p in pairs) / len(pairs),
        sum(p[0][1] for p in pairs) / len(pairs),
    )
    layout.bottom = (
        sum(p[1][0] for p in pairs) / len(pairs),
        sum(p[1][1] for p in pairs) / len(pairs),
    )
    layout.face_size = sum(sizes) / len(sizes) if sizes else 0.0
    log.info(
        "حوار ثنائي مكتشف (ثقة %.0f%%): وجهان عند %.2f و%.2f.",
        layout.confidence * 100,
        layout.top[0],
        layout.bottom[0],
    )
    return layout


def build_split_screen_filter(
    layout: DialogueLayout,
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    zoom: float = 3.2,
) -> str:
    """يبني فلتر شاشة منقسمة: كل متحدث في نصف رأسي.

    **حجم النافذة يتبع حجم الوجه، لا ارتفاع المصدر.** لو أخذنا الارتفاع كاملاً
    لخرجت نافذة عريضة جداً يظهر فيها الوجه صغيراً وسط فراغ. نضبط عرض النافذة
    ليساوي ``zoom`` أضعاف عرض الوجه، فيملأ الوجه النصف بشكل طبيعي.
    """
    half_h = out_h // 2
    pane_aspect = out_w / half_h

    face_w = max(0.04, layout.face_size or 0.18)
    crop_w = int(round(face_w * zoom * src_w))
    crop_h = int(round(crop_w / pane_aspect))

    # التقييد داخل حدود المصدر مع الحفاظ على النسبة
    if crop_h > src_h:
        crop_h = src_h
        crop_w = int(round(crop_h * pane_aspect))
    if crop_w > src_w:
        crop_w = src_w
        crop_h = int(round(crop_w / pane_aspect))

    crop_w = max(16, crop_w - (crop_w % 2))
    crop_h = max(16, crop_h - (crop_h % 2))

    def _pane(center: Tuple[float, float], label: str) -> str:
        x = int(round(center[0] * src_w - crop_w / 2))
        x = max(0, min(src_w - crop_w, x))
        x -= x % 2
        y = int(round(center[1] * src_h - crop_h / 2))
        y = max(0, min(src_h - crop_h, y))
        y -= y % 2
        return (
            f"[0:v]crop={crop_w}:{crop_h}:{x}:{y},"
            f"scale={out_w}:{half_h}:flags=lanczos,setsar=1[{label}]"
        )

    return (
        f"{_pane(layout.top, 'sp0')};"
        f"{_pane(layout.bottom, 'sp1')};"
        f"[sp0][sp1]vstack=inputs=2"
    )
