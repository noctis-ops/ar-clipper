"""تقرير الجودة وإعادة الصياغة — آخر البنود المؤجّلة."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestQualityChecks:
    def test_missing_file_fails(self, tmp_path):
        from core.quality import check_clip

        report = check_clip(tmp_path / "لا-يوجد.mp4")
        assert not report.passed
        assert report.score < 100

    def test_score_math(self):
        from core.quality import FAIL, WARN, Check, QualityReport

        report = QualityReport(checks=[Check("a", FAIL), Check("b", WARN)])
        assert report.score == 100 - 25 - 8

    def test_score_never_negative(self):
        from core.quality import FAIL, Check, QualityReport

        report = QualityReport(checks=[Check(str(i), FAIL) for i in range(10)])
        assert report.score == 0

    def test_passed_ignores_warnings(self):
        from core.quality import WARN, Check, QualityReport

        assert QualityReport(checks=[Check("a", WARN)]).passed is True

    def test_platform_limits_defined(self):
        from core.quality import PLATFORM_LIMITS

        for platform in ("tiktok", "youtube_shorts", "instagram_reels", "x"):
            low, high = PLATFORM_LIMITS[platform]
            assert 0 < low < high

    def test_report_serializable(self):
        import json

        from core.quality import OK, Check, QualityReport

        json.dumps(QualityReport(checks=[Check("a", OK, "x", 1.0)]).to_dict())

    @pytest.mark.slow
    def test_real_clip_checks(self):
        import glob

        from core.quality import check_clip

        clips = glob.glob("data/clips/**/*.mp4", recursive=True)
        if not clips:
            pytest.skip("لا مقاطع منتَجة")
        report = check_clip(clips[0])
        names = {c.name for c in report.checks}
        assert "المدة" in names
        assert "المقاس" in names

    @pytest.mark.slow
    def test_platform_check_applied(self):
        import glob

        from core.quality import check_clip

        clips = glob.glob("data/clips/**/*.mp4", recursive=True)
        if not clips:
            pytest.skip("لا مقاطع منتَجة")
        report = check_clip(clips[0], platform="instagram_reels")
        assert any("instagram" in c.name for c in report.checks)

    def test_subtitle_check_detects_empty(self, tmp_path):
        from core.quality import check_clip

        sub = tmp_path / "empty.srt"
        sub.write_text("", encoding="utf-8")
        report = check_clip(tmp_path / "x.mp4", subtitle_path=sub)
        assert not report.passed

    def test_cli_command_registered(self):
        from typer.testing import CliRunner

        from cli.main import app

        assert "check" in CliRunner().invoke(app, ["--help"]).output


class TestRewrite:
    def test_produces_variants(self):
        from core.content_gen.rewrite import rewrite_title

        result = rewrite_title("كيف بدأت مشروعي من الصفر", use_llm=False)
        assert len(result.variants) >= 3

    def test_question_variant_ends_with_mark(self):
        from core.content_gen.rewrite import rewrite_title

        result = rewrite_title("بدأت مشروعي من الصفر", use_llm=False)
        assert result.variants["question"].endswith("؟")

    def test_existing_question_kept(self):
        from core.content_gen.rewrite import rewrite_title

        result = rewrite_title("هل تعرف كيف يعمل هذا؟", use_llm=False)
        assert result.variants["question"].endswith("؟")

    def test_number_variant_only_when_numeric(self):
        from core.content_gen.rewrite import rewrite_title

        with_number = rewrite_title("3 طرق لبدء مشروعك", use_llm=False)
        without = rewrite_title("طرق لبدء مشروعك", use_llm=False)
        assert "number" in with_number.variants
        assert "number" not in without.variants

    def test_best_picks_reasonable_length(self):
        from core.content_gen.rewrite import rewrite_title

        result = rewrite_title("عنوان تجريبي للاختبار هنا", use_llm=False)
        assert 10 <= len(result.best) <= 120

    def test_empty_title_is_safe(self):
        from core.content_gen.rewrite import rewrite_title

        result = rewrite_title("", use_llm=False)
        assert result.variants == {}
        assert result.best == ""

    def test_falls_back_to_hook(self):
        from core.content_gen.rewrite import rewrite_title

        result = rewrite_title("", hook="لم أتوقع هذا أبداً", use_llm=False)
        assert result.variants

    def test_clean_strips_numbering(self):
        from core.content_gen.rewrite import _clean

        assert _clean('1. "عنوان مقتبس"') == "عنوان مقتبس"
        assert _clean("- عنوان") == "عنوان"

    def test_clean_takes_first_line(self):
        """النموذج قد يضيف شرحاً بعد العنوان."""
        from core.content_gen.rewrite import _clean

        assert _clean("العنوان الجيد\n\nهذا شرح إضافي") == "العنوان الجيد"

    def test_variants_length_bounded(self):
        from core.content_gen.rewrite import rewrite_title

        long_title = "عنوان " * 40
        result = rewrite_title(long_title, use_llm=False)
        assert all(len(v) <= 120 for v in result.variants.values())

    def test_styles_filter(self):
        from core.content_gen.rewrite import rewrite_title

        result = rewrite_title("عنوان تجريبي هنا", styles=["question"], use_llm=False)
        assert set(result.variants) <= {"question"}

    def test_rewrite_many(self):
        from core.content_gen.rewrite import rewrite_many

        results = rewrite_many(
            [{"title": "عنوان أول هنا"}, {"title": "عنوان ثانٍ هنا"}]
        )
        assert len(results) == 2

    def test_serializable(self):
        import json

        from core.content_gen.rewrite import rewrite_title

        json.dumps(rewrite_title("عنوان", use_llm=False).to_dict(), ensure_ascii=False)
