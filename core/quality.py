"""تقرير جودة المقطع — فحص تلقائي قبل النشر.

المشكلة: المقطع قد يخرج سليماً تقنياً لكن غير صالح للنشر — صوت خافت،
صورة مظلمة، ترجمة مقطوعة، أو مدة خارج حدود المنصة. اكتشاف ذلك بعد النشر
مكلف.

كل الفحوص محلية بلا نماذج: قياسات ffmpeg وOpenCV على الملف الناتج.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common.config import Settings, load_settings
from .common.ffmpeg import ffmpeg_bin, probe, run
from .common.logging_utils import get_logger

log = get_logger(__name__)

OK = "ok"
WARN = "warn"
FAIL = "fail"

# حدود المنصات (بالثواني) — من وثائقها الرسمية
PLATFORM_LIMITS = {
    "tiktok": (3, 600),
    "youtube_shorts": (3, 180),
    "instagram_reels": (3, 90),
    "x": (1, 140),
}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "status": self.status,
            "detail": self.detail, "value": self.value,
        }


@dataclass
class QualityReport:
    checks: List[Check] = field(default_factory=list)
    clip_path: str = ""

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> List[Check]:
        return [c for c in self.checks if c.status == WARN]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def score(self) -> int:
        """درجة من 100: كل إخفاق -25، كل تحذير -8."""
        return max(0, 100 - len(self.failures) * 25 - len(self.warnings) * 8)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip": self.clip_path,
            "score": self.score,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }


def measure_loudness(path: Path) -> Optional[float]:
    """يقيس الجهارة المتكاملة بـEBU R128."""
    try:
        proc = run(
            [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(path),
             "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
            check=False,
        )
        matches = re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS", proc.stderr or "")
        return float(matches[-1]) if matches else None
    except Exception as exc:
        log.debug("تعذّر قياس الجهارة: %s", exc)
        return None


def measure_brightness(path: Path, samples: int = 5) -> Optional[float]:
    """متوسط السطوع (0-255) من عيّنات موزّعة."""
    try:
        import cv2

        cap = cv2.VideoCapture(str(path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            cap.release()
            return None
        values = []
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
            ok, frame = cap.read()
            if ok:
                values.append(float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()))
        cap.release()
        return sum(values) / len(values) if values else None
    except Exception as exc:
        log.debug("تعذّر قياس السطوع: %s", exc)
        return None


def check_clip(
    clip_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    subtitle_path: Optional[str | Path] = None,
    platform: str = "",
) -> QualityReport:
    """يفحص مقطعاً ناتجاً ويرجع تقرير جودة."""
    settings = settings or load_settings()
    path = Path(clip_path)
    report = QualityReport(clip_path=str(path))

    if not path.exists():
        report.checks.append(Check("وجود الملف", FAIL, "الملف غير موجود"))
        return report

    size_mb = path.stat().st_size / 1_048_576
    report.checks.append(
        Check("حجم الملف", OK if size_mb > 0.05 else FAIL, f"{size_mb:.1f} MB", size_mb)
    )

    try:
        info = probe(path)
    except Exception as exc:
        report.checks.append(Check("قراءة الملف", FAIL, str(exc)[:100]))
        return report

    # المدة
    duration = info.duration
    min_d = float(settings.get("quality.min_duration", 5.0))
    max_d = float(settings.get("quality.max_duration", 180.0))
    if duration < min_d:
        status, detail = FAIL, f"{duration:.1f}s — أقصر من {min_d}s"
    elif duration > max_d:
        status, detail = WARN, f"{duration:.1f}s — أطول من {max_d}s"
    else:
        status, detail = OK, f"{duration:.1f}s"
    report.checks.append(Check("المدة", status, detail, duration))

    # حدود المنصة
    if platform and platform in PLATFORM_LIMITS:
        low, high = PLATFORM_LIMITS[platform]
        fits = low <= duration <= high
        report.checks.append(
            Check(
                f"حدود {platform}", OK if fits else FAIL,
                f"{duration:.1f}s (المسموح {low}-{high}s)", duration,
            )
        )

    # الأبعاد
    if info.width and info.height:
        ratio = info.width / info.height
        vertical = ratio < 0.85
        report.checks.append(
            Check(
                "المقاس", OK if info.width >= 720 else WARN,
                f"{info.width}x{info.height}" + (" (عمودي)" if vertical else ""),
                float(info.width),
            )
        )

    # الصوت
    if not info.has_audio:
        report.checks.append(Check("الصوت", FAIL, "لا مسار صوتي"))
    else:
        loudness = measure_loudness(path)
        if loudness is None:
            report.checks.append(Check("الجهارة", WARN, "تعذّر القياس"))
        else:
            target = float(settings.get("polish.loudness_target", -16.0))
            drift = abs(loudness - target)
            if loudness < -30:
                status, detail = FAIL, f"{loudness:.1f} LUFS — صوت شبه صامت"
            elif drift > 4:
                status, detail = WARN, f"{loudness:.1f} LUFS (الهدف {target})"
            else:
                status, detail = OK, f"{loudness:.1f} LUFS"
            report.checks.append(Check("الجهارة", status, detail, loudness))

    # السطوع
    brightness = measure_brightness(path)
    if brightness is not None:
        if brightness < 25:
            status, detail = FAIL, f"{brightness:.0f}/255 — مظلم جداً"
        elif brightness < 45 or brightness > 225:
            status, detail = WARN, f"{brightness:.0f}/255"
        else:
            status, detail = OK, f"{brightness:.0f}/255"
        report.checks.append(Check("السطوع", status, detail, brightness))

    # الترجمة
    if subtitle_path:
        sub = Path(subtitle_path)
        if not sub.exists():
            report.checks.append(Check("الترجمة", FAIL, "الملف غير موجود"))
        else:
            text = sub.read_text(encoding="utf-8", errors="replace")
            lines = [l for l in text.splitlines() if l.strip() and "-->" not in l]
            arabic = sum(1 for l in lines if re.search(r"[\u0600-\u06FF]", l))
            if not lines:
                report.checks.append(Check("الترجمة", FAIL, "ملف فارغ"))
            elif arabic == 0:
                report.checks.append(
                    Check("الترجمة", WARN, f"{len(lines)} سطر بلا نص عربي")
                )
            else:
                report.checks.append(
                    Check("الترجمة", OK, f"{arabic} سطر عربي", float(arabic))
                )

    failed = len(report.failures)
    if failed:
        log.warning("تقرير الجودة: %d إخفاق (الدرجة %d).", failed, report.score)
    return report


def check_many(
    clips: List[str | Path], *, settings: Optional[Settings] = None
) -> List[QualityReport]:
    return [check_clip(c, settings=settings) for c in clips]
