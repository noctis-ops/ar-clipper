"""استهلاك المعالج وتجربة الاستخدام — بلاغ مستخدم حقيقي.

المستخدم أبلغ: «استخدم قدر كبير من وحدة المعالجة المركزية وأنا لم أنزّل
الموديل». القياس أكّد 192% استغلال على آلة بنواتين — ffmpeg كان يلتهم
كل الأنوية بلا أي حدّ.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


class TestCpuBudget:
    def test_default_leaves_a_core_free(self):
        """السلوك الافتراضي يجب أن يُبقي الجهاز مستجيباً."""
        import os

        from core.common.ffmpeg import cpu_budget

        budget = cpu_budget()
        total = os.cpu_count() or 2
        assert budget == max(1, total - 1)

    def test_never_returns_zero_on_auto(self):
        """صفر يعني 'بلا قيد' في ffmpeg — لا نريده في الوضع التلقائي."""
        from core.common.ffmpeg import cpu_budget

        assert cpu_budget() >= 1

    def test_explicit_value_is_respected(self):
        from copy import deepcopy

        from core.common.config import load_settings
        from core.common.ffmpeg import cpu_budget

        settings = deepcopy(load_settings())
        settings.data["performance"]["threads"] = 3
        assert cpu_budget(settings) == 3

    def test_negative_one_means_unlimited(self):
        from copy import deepcopy

        from core.common.config import load_settings
        from core.common.ffmpeg import cpu_budget

        settings = deepcopy(load_settings())
        settings.data["performance"]["threads"] = -1
        assert cpu_budget(settings) == 0  # 0 = تلقائي بلا قيد في ffmpeg

    def test_threads_flag_reaches_ffmpeg(self, monkeypatch):
        captured = {}

        from core.common import ffmpeg as mod

        def fake_run(args, **kwargs):
            captured["args"] = list(args)

            class R:
                returncode = 0
                stdout = stderr = ""

            return R()

        monkeypatch.setattr(mod, "run", fake_run)
        mod.run_ffmpeg(["-i", "x.mp4", "out.mp4"])
        assert "-threads" in captured["args"]

    def test_explicit_threads_override(self, monkeypatch):
        captured = {}

        from core.common import ffmpeg as mod

        def fake_run(args, **kwargs):
            captured["args"] = list(args)

            class R:
                returncode = 0
                stdout = stderr = ""

            return R()

        monkeypatch.setattr(mod, "run", fake_run)
        mod.run_ffmpeg(["-i", "x.mp4", "out.mp4"], threads=2)
        idx = captured["args"].index("-threads")
        assert captured["args"][idx + 1] == "2"

    def test_config_section_exists(self):
        from core.common.config import load_settings

        settings = load_settings()
        assert settings.get("performance.threads") is not None
        assert settings.get("performance.low_priority") is not None


class TestLowPriority:
    """الأهم لاستجابة الجهاز — أهم من حدّ الخيوط نفسه."""

    def test_returns_kwargs_by_default(self):
        from core.common.ffmpeg import _low_priority_kwargs

        assert _low_priority_kwargs() != {}

    def test_disabled_via_settings(self, monkeypatch):
        from copy import deepcopy

        import core.common.config as cfg
        from core.common.ffmpeg import _low_priority_kwargs

        settings = deepcopy(cfg.load_settings())
        settings.data["performance"]["low_priority"] = False
        monkeypatch.setattr(cfg, "load_settings", lambda *a, **k: settings)
        assert _low_priority_kwargs() == {}

    @pytest.mark.skipif(sys.platform.startswith("win"), reason="يونكس فقط")
    def test_unix_uses_nice(self):
        from core.common.ffmpeg import _low_priority_kwargs

        assert "preexec_fn" in _low_priority_kwargs()

    def test_windows_branch_is_present(self):
        """لا يمكن تنفيذه هنا، لكن الفرع يجب أن يوجد."""
        import inspect

        from core.common.ffmpeg import _low_priority_kwargs

        source = inspect.getsource(_low_priority_kwargs)
        assert "BELOW_NORMAL_PRIORITY_CLASS" in source


class TestPreviewEndpoint:
    """معاينة اللحظة قبل إنتاجها — الخطوة الناقصة مقابل الأدوات التجارية."""

    @pytest.fixture
    @staticmethod
    def client():
        from fastapi.testclient import TestClient

        from ui.server import app

        return TestClient(app)

    def test_endpoint_registered(self, client):
        from ui.server import app

        paths = {r.path for r in app.routes}
        assert "/api/preview" in paths

    def test_missing_source_returns_404(self, client):
        r = client.get("/api/preview", params={"source": "/لا/يوجد.mp4", "start": 0, "end": 5})
        assert r.status_code == 404

    @pytest.mark.slow
    def test_returns_playable_video(self, client):
        sample = Path("data/samples/speaker.mp4")
        if not sample.exists():
            pytest.skip("العيّنة غير مولَّدة — شغّل scripts/make_sample.py")
        r = client.get(
            "/api/preview",
            params={"source": str(sample.resolve()), "start": 2, "end": 8},
        )
        assert r.status_code == 200
        assert len(r.content) > 5_000

    def test_duration_is_capped(self):
        """معاينة لا تتحول إلى ترميز ملف كامل."""
        import inspect

        from ui.server import preview_moment

        assert "90.0" in inspect.getsource(preview_moment)

    def test_uses_stream_copy_first(self):
        """النسخ شبه فوري؛ إعادة الترميز احتياطي فقط."""
        import inspect

        source = inspect.getsource(__import__("ui.server", fromlist=["x"]).preview_moment)
        copy_at = source.index('"-c", "copy"')
        encode_at = source.index("libx264")
        assert copy_at < encode_at


class TestSuggestionUx:
    """عرض الاقتراحات بأسلوب يفهمه غير التقني."""

    def _html(self):
        return Path("ui/static/index.html").read_text(encoding="utf-8")

    def test_score_is_displayed(self):
        """score كانت تُحسب ولا تُعرض إطلاقاً."""
        html = self._html()
        assert "scoreBox" in html
        assert "(s.score || 0) * 10" in html

    def test_percentage_is_clamped(self):
        assert "Math.max(0, Math.min(100" in self._html()

    def test_three_visual_tiers(self):
        html = self._html()
        for tier in ("hi", "mid", "low"):
            assert f".scoreBox.{tier}{{" in html

    def test_sorted_by_score_descending(self):
        assert "sort((a, b) => (b.score || 0) - (a.score || 0))" in self._html()

    def test_top_three_preselected(self):
        assert "rank < 3 ? 'checked' : ''" in self._html()

    def test_preview_button_exists(self):
        html = self._html()
        assert 'class="prevBtn"' in html
        assert "function playPreview" in html

    def test_hook_and_transcript_shown(self):
        html = self._html()
        assert "hookLine" in html
        assert "النص المنطوق" in html
