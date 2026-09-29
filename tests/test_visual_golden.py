"""اختبار بصري ذهبي — يمنع الانحدار البصري الصامت.

المشكلة التي يحلّها: كل الاختبارات الأخرى تفحص **البنية** (هل الفلتر
صحيح؟ هل الأبعاد صحيحة؟) لكن لا شيء يفحص أن الصورة الناتجة تبدو كما
ينبغي. تغيير في ترتيب الفلاتر قد يمرّ صامتاً ويُفسد المخرج.

المنهج: ننتج مقطعاً بإعدادات ثابتة، ونقارن إطاراً منه ببصمة محفوظة
(متوسط السطوع والتباين لشبكة 4×4 + نسبة البكسلات الفاتحة). بصمة رقمية
صغيرة بدل صورة كاملة — تُودَع في git بلا تضخيم المستودع.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden" / "reframe_signature.json"
TOLERANCE = 0.08  # 8% انحراف مسموح (فروق ترميز بين الإصدارات)


def _signature(frame) -> dict:
    """بصمة رقمية مختصرة لإطار — ثابتة عبر إعادة الترميز."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (4, 4), interpolation=cv2.INTER_AREA)
    return {
        "grid": [round(float(v) / 255.0, 4) for v in small.flatten()],
        "mean": round(float(gray.mean()) / 255.0, 4),
        "std": round(float(gray.std()) / 255.0, 4),
        "bright_ratio": round(float((gray > 128).sum()) / gray.size, 4),
    }


def _distance(a: dict, b: dict) -> float:
    """أقصى فارق بين بصمتين."""
    diffs = [abs(a["mean"] - b["mean"]), abs(a["std"] - b["std"]),
             abs(a["bright_ratio"] - b["bright_ratio"])]
    diffs += [abs(x - y) for x, y in zip(a["grid"], b["grid"])]
    return max(diffs)


@pytest.mark.slow
class TestVisualRegression:
    @pytest.fixture(scope="class")
    @staticmethod
    def produced(tmp_path_factory):
        """ينتج مقطعاً بإعدادات ثابتة تماماً."""
        cv2 = pytest.importorskip("cv2")
        from copy import deepcopy

        from core.common.config import load_settings
        from core.reframe.center import reframe

        sample = Path("data/samples/speaker.mp4")
        if not sample.exists():
            pytest.skip("العيّنة غير مولَّدة — شغّل scripts/make_sample.py")

        settings = deepcopy(load_settings())
        # إعدادات مثبّتة: أي تغيير هنا يُبطل البصمة عمداً
        settings.data["reframe"].update(
            {"mode": "center", "width": 1080, "height": 1920, "aspect": "9:16"}
        )
        settings.data["polish"]["enhance_voice"] = False

        out = tmp_path_factory.mktemp("golden") / "out.mp4"
        reframe(str(sample), str(out), settings=settings)
        return out

    def test_output_exists(self, produced):
        assert produced.exists() and produced.stat().st_size > 10_000

    def test_dimensions_exact(self, produced):
        from core.common.ffmpeg import probe

        info = probe(produced)
        assert (info.width, info.height) == (1080, 1920)

    def test_matches_golden_signature(self, produced):
        """المقارنة الحقيقية — تكشف أي انحراف بصري."""
        import cv2

        cap = cv2.VideoCapture(str(produced))
        cap.set(cv2.CAP_PROP_POS_MSEC, 5000)
        ok, frame = cap.read()
        cap.release()
        assert ok, "تعذّرت قراءة الإطار"

        current = _signature(frame)

        if not GOLDEN.exists():
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(
                json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            pytest.skip("أُنشئت البصمة المرجعية — أعد التشغيل للمقارنة")

        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        distance = _distance(current, golden)
        assert distance <= TOLERANCE, (
            f"انحراف بصري {distance:.3f} يتجاوز {TOLERANCE}.\n"
            f"إن كان التغيير مقصوداً احذف {GOLDEN} وأعد التشغيل."
        )

    def test_signature_is_deterministic(self, produced):
        """البصمة نفسها من قراءتين متتاليتين."""
        import cv2

        signatures = []
        for _ in range(2):
            cap = cv2.VideoCapture(str(produced))
            cap.set(cv2.CAP_PROP_POS_MSEC, 5000)
            ok, frame = cap.read()
            cap.release()
            signatures.append(_signature(frame))
        assert _distance(signatures[0], signatures[1]) < 0.001


class TestSignatureMath:
    """اختبارات البصمة نفسها — سريعة بلا ترميز."""

    def _frame(self, value):
        import numpy as np

        return np.full((100, 100, 3), value, dtype=np.uint8)

    def test_identical_frames_zero_distance(self):
        pytest.importorskip("cv2")
        a = _signature(self._frame(120))
        assert _distance(a, a) == 0.0

    def test_different_frames_detected(self):
        pytest.importorskip("cv2")
        dark = _signature(self._frame(30))
        bright = _signature(self._frame(220))
        assert _distance(dark, bright) > 0.5

    def test_signature_is_json_serializable(self):
        pytest.importorskip("cv2")
        json.dumps(_signature(self._frame(100)))

    def test_grid_has_16_cells(self):
        pytest.importorskip("cv2")
        assert len(_signature(self._frame(100))["grid"]) == 16
