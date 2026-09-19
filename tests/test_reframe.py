"""اختبارات حسابات إعادة التأطير (لا تحتاج تشغيل ffmpeg)."""

from __future__ import annotations

import pytest

from core.reframe.center import build_reframe_filter, compute_crop, parse_aspect


class TestParseAspect:
    def test_ratio_string(self):
        assert parse_aspect("9:16") == pytest.approx(0.5625)

    def test_float_string(self):
        assert parse_aspect("1.777") == pytest.approx(1.777)


class TestComputeCrop:
    def test_landscape_to_vertical(self):
        w, h, x, y = compute_crop(1920, 1080, 9 / 16)
        assert h == 1080                      # الارتفاع كامل
        assert w == pytest.approx(608, abs=2)  # 1080 * 9/16
        assert y == 0
        assert x > 0                           # مقصوص من الجانبين

    def test_centered_by_default(self):
        w, _, x, _ = compute_crop(1920, 1080, 9 / 16)
        assert x == pytest.approx((1920 - w) // 2, abs=2)

    def test_focus_left(self):
        _, _, x, _ = compute_crop(1920, 1080, 9 / 16, focus_x=0.0)
        assert x == 0

    def test_focus_right(self):
        w, _, x, _ = compute_crop(1920, 1080, 9 / 16, focus_x=1.0)
        assert x == pytest.approx(1920 - w, abs=2)

    def test_focus_clamped(self):
        _, _, x1, _ = compute_crop(1920, 1080, 9 / 16, focus_x=5.0)
        _, _, x2, _ = compute_crop(1920, 1080, 9 / 16, focus_x=-5.0)
        assert x1 == compute_crop(1920, 1080, 9 / 16, focus_x=1.0)[2]
        assert x2 == 0

    def test_portrait_source_crops_height(self):
        w, h, x, y = compute_crop(1080, 2400, 9 / 16)
        assert w == 1080
        assert h < 2400
        assert x == 0

    def test_already_correct_aspect_unchanged(self):
        w, h, x, y = compute_crop(1080, 1920, 9 / 16)
        assert (w, h, x, y) == (1080, 1920, 0, 0)

    @pytest.mark.parametrize(
        "src", [(1920, 1080), (1280, 720), (640, 480), (1081, 1921), (999, 777)]
    )
    def test_dimensions_always_even(self, src):
        w, h, x, y = compute_crop(*src, 9 / 16)
        assert w % 2 == 0 and h % 2 == 0, "الأبعاد الفردية تكسر yuv420p"
        assert x % 2 == 0 and y % 2 == 0

    def test_crop_window_inside_source(self):
        for sw, sh in [(1920, 1080), (720, 1280), (3840, 2160)]:
            w, h, x, y = compute_crop(sw, sh, 9 / 16)
            assert x + w <= sw and y + h <= sh

    def test_invalid_dimensions(self):
        from core.common.errors import MediaError

        with pytest.raises(MediaError):
            compute_crop(0, 1080, 9 / 16)


class TestFilterString:
    def test_contains_all_stages(self):
        vf = build_reframe_filter(1920, 1080, out_w=1080, out_h=1920)
        assert vf.startswith("crop=")
        assert "scale=1080:1920" in vf
        assert "setsar=1" in vf

    def test_no_spaces(self):
        """المسافات تكسر سلسلة فلاتر ffmpeg."""
        assert " " not in build_reframe_filter(1920, 1080, out_w=1080, out_h=1920)
