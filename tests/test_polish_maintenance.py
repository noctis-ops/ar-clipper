"""اختبارات اللمسات النهائية (polish) والصيانة (maintenance).

كل اختبار يثبت وحدة بمعزل — بلا شبكة وبلا نماذج.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core.common.config import load_settings as _load_settings


def load_settings():
    """نسخة معزولة: ``_load_settings`` مُخزَّن بكاش ويرجع نفس الكائن،
    فتعديل ``settings.data`` في اختبار يتسرّب إلى بقية الاختبارات."""
    import copy

    base = _load_settings()
    return copy.deepcopy(base)
from core.maintenance import SweepResult, clip_exists, disk_report, sweep_tmp
from core.polish.finisher import build_audio_filter, build_video_filter, is_enabled


# ============================================================ polish


class TestPolishFilters:
    """بناء سلاسل فلاتر ffmpeg للتطبيع والظهور الناعم."""

    def test_audio_filter_includes_loudnorm_by_default(self):
        settings = load_settings()
        f = build_audio_filter(settings, duration=20.0)
        assert "loudnorm" in f
        assert "I=-16" in f.replace(" ", "") or "I=-16.0" in f.replace(" ", "")

    def test_audio_filter_can_exclude_loudnorm(self):
        """حرج: التطبيع مرتين يُفقد الديناميكية، فيجب أن يكون الاستثناء ممكناً."""
        settings = load_settings()
        f = build_audio_filter(settings, duration=20.0, include_loudnorm=False)
        assert "loudnorm" not in f
        assert "afade" in f  # الـfade يبقى

    def test_audio_filter_has_both_fades(self):
        settings = load_settings()
        f = build_audio_filter(settings, duration=20.0, include_loudnorm=False)
        assert "afade=t=in" in f.replace(" ", "")
        assert "afade=t=out" in f.replace(" ", "")

    def test_video_filter_fades_in_and_out(self):
        settings = load_settings()
        f = build_video_filter(settings, duration=20.0)
        assert "fade=t=in" in f.replace(" ", "")
        assert "fade=t=out" in f.replace(" ", "")

    def test_fade_shortened_for_very_short_clip(self):
        """مقطع 1s لا يحتمل fade بـ0.4s على كل طرف — يجب أن يُقصَّر."""
        settings = load_settings()
        f = build_video_filter(settings, duration=1.0)
        durations = [
            float(part.split("d=")[1].split(":")[0])
            for part in f.split(",")
            if "d=" in part
        ]
        assert durations, "لم يُبنَ أي fade"
        assert all(d <= 0.25 for d in durations), f"fade طويل جداً: {durations}"

    def test_disabled_when_fade_is_zero(self):
        """التطبيع وحده مدمج في reframe، فلا يبرّر تمريرة ترميز مستقلة."""
        settings = load_settings()
        settings.data["polish"]["fade_duration"] = 0.0
        assert is_enabled(settings) is False

    def test_disabled_when_section_off(self):
        settings = load_settings()
        settings.data["polish"]["enabled"] = False
        assert is_enabled(settings) is False

    def test_enabled_by_default(self):
        assert is_enabled(load_settings()) is True

    def test_zero_duration_is_safe(self):
        """مدة صفر يجب ألّا ترمي استثناءً ولا تنتج فلتراً معطوباً."""
        settings = load_settings()
        assert build_video_filter(settings, duration=0.0) == ""


class TestPolishIntegration:
    """اللمسات مدموجة في تمريرات قائمة — لا تمريرة ترميز خامسة."""

    def test_reframe_accepts_extra_filters(self):
        import inspect

        from core.reframe.center import reframe

        params = inspect.signature(reframe).parameters
        assert "extra_video_filter" in params
        assert "extra_audio_filter" in params

    def test_burn_subtitles_accepts_extra_filters(self):
        import inspect

        from core.subtitles.builder import burn_subtitles

        params = inspect.signature(burn_subtitles).parameters
        assert "extra_video_filter" in params
        assert "audio_filter" in params

    def test_pipeline_option_exists_and_defaults_on(self):
        from core.pipeline import PipelineOptions

        assert PipelineOptions().polish is True


# ============================================================ maintenance


class TestSweepTmp:
    """كنس الملفات المؤقتة المتروكة بعد انقطاع المعالجة."""

    def _settings_with_tmp(self, tmp_path: Path):
        settings = load_settings()
        settings.data["paths"]["tmp"] = str(tmp_path)
        return settings

    def test_sweep_removes_old_files(self, tmp_path):
        old = tmp_path / "old__01_cut.mp4"
        old.write_bytes(b"x" * 2048)
        past = time.time() - (48 * 3600)
        import os

        os.utime(old, (past, past))

        result = sweep_tmp(self._settings_with_tmp(tmp_path), older_than_hours=24)
        assert result.removed == 1
        assert result.freed_bytes == 2048
        assert not old.exists()

    def test_sweep_keeps_recent_files(self):
        """ملفات المعالجة الجارية تعيش في نفس المجلد — لا يجوز حذفها."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp_path = Path(d)
            fresh = tmp_path / "running__01_cut.mp4"
            fresh.write_bytes(b"x" * 100)
            result = sweep_tmp(self._settings_with_tmp(tmp_path), older_than_hours=24)
            assert result.removed == 0
            assert fresh.exists()

    def test_dry_run_deletes_nothing(self, tmp_path):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x" * 500)
        result = sweep_tmp(
            self._settings_with_tmp(tmp_path), older_than_hours=0, dry_run=True
        )
        assert result.removed == 1
        assert f.exists(), "الجفاف حذف ملفاً فعلياً!"

    def test_missing_dir_is_not_an_error(self, tmp_path):
        result = sweep_tmp(self._settings_with_tmp(tmp_path / "لا-يوجد"))
        assert result.removed == 0

    def test_freed_mb_conversion(self):
        assert SweepResult(freed_bytes=1048576).freed_mb == pytest.approx(1.0)


class TestClipExists:
    """أساس الاستئناف: كشف المقاطع المنتَجة مسبقاً."""

    def _settings_with_clips(self, tmp_path: Path):
        settings = load_settings()
        settings.data["paths"]["clips"] = str(tmp_path)
        return settings

    def test_detects_existing_clip(self, tmp_path):
        d = tmp_path / "ws" / "myclip"
        d.mkdir(parents=True)
        (d / "myclip.mp4").write_bytes(b"data")
        assert clip_exists("ws", "myclip", self._settings_with_clips(tmp_path))

    def test_missing_clip_returns_none(self, tmp_path):
        assert clip_exists("ws", "nope", self._settings_with_clips(tmp_path)) is None

    def test_empty_file_is_not_complete(self, tmp_path):
        """ملف بحجم صفر = إنتاج انقطع في منتصفه، فيجب إعادة إنتاجه."""
        d = tmp_path / "ws" / "broken"
        d.mkdir(parents=True)
        (d / "broken.mp4").touch()
        assert clip_exists("ws", "broken", self._settings_with_clips(tmp_path)) is None

    def test_name_is_slugified_like_pipeline(self, tmp_path):
        """الاسم يمر بنفس تطبيع خط الأنابيب وإلا لن يتطابق الاستئناف أبداً."""
        from core.common.text_utils import slugify

        raw = "مقطع تجريبي"
        d = tmp_path / "ws" / slugify(raw)
        d.mkdir(parents=True)
        (d / f"{slugify(raw)}.mp4").write_bytes(b"data")
        assert clip_exists("ws", raw, self._settings_with_clips(tmp_path))


class TestDiskReport:
    def test_returns_rows_for_all_known_dirs(self):
        rows = disk_report(load_settings())
        assert len(rows) >= 4
        for label, path, size, count in rows:
            assert isinstance(label, str) and label
            assert size >= 0 and count >= 0


# ============================================================ CLI


class TestCliAdditions:
    """الأعلام الجديدة موجودة فعلاً في الواجهة."""

    def _help(self, *args):
        from typer.testing import CliRunner

        from cli.main import app

        return CliRunner().invoke(app, list(args)).output

    def test_clip_has_new_flags(self):
        out = self._help("clip", "--help")
        for flag in ("--dry-run", "--resume", "--no-polish"):
            assert flag in out, f"العلم {flag} مفقود"

    def test_suggest_has_json_and_resume(self):
        out = self._help("suggest", "--help")
        assert "--json" in out
        assert "--resume" in out

    def test_clean_command_registered(self):
        assert "clean" in self._help("--help")

    def test_dry_run_produces_no_files(self, tmp_path):
        """--dry-run يجب ألّا يلمس القرص إطلاقاً."""
        from typer.testing import CliRunner

        from cli.main import app

        src = tmp_path / "fake.mp4"
        src.write_bytes(b"not a real video")
        before = set(tmp_path.rglob("*"))
        result = CliRunner().invoke(
            app, ["clip", str(src), "-s", "0", "-e", "20", "--dry-run"]
        )
        assert result.exit_code == 0
        assert "خطة التنفيذ" in result.output
        assert set(tmp_path.rglob("*")) == before

    def test_dry_run_requires_a_range(self, tmp_path):
        from typer.testing import CliRunner

        from cli.main import app

        src = tmp_path / "fake.mp4"
        src.write_bytes(b"x")
        result = CliRunner().invoke(app, ["clip", str(src), "--dry-run"])
        assert result.exit_code != 0


class TestSuggestionsPayload:
    """مخرج --json يجب أن يبقى مستقر الشكل — سكربتات المستخدمين تعتمد عليه."""

    def _sample(self):
        from core.common.schemas import SourceVideo
        from core.suggest import Suggestion, SuggestionSet

        return SuggestionSet(
            source=SourceVideo(
                path="/tmp/x.mp4", title="عينة", origin="local", duration=70.0
            ),
            suggestions=[
                Suggestion(
                    index=0,
                    start=5.0,
                    end=35.0,
                    score=0.82,
                    kind="story",
                    reason="قصة",
                    title="عنوان",
                    hashtags=["#تجربة"],
                    text="نص",
                )
            ],
            elapsed=4.2,
        )

    def test_payload_is_json_serializable(self):
        from cli.main import _suggestions_payload

        payload = _suggestions_payload("/tmp/x.mp4", self._sample())
        restored = json.loads(json.dumps(payload, ensure_ascii=False))
        assert restored["count"] == 1
        assert restored["suggestions"][0]["kind"] == "story"
        assert restored["suggestions"][0]["duration"] == 30.0

    def test_payload_has_stable_top_level_keys(self):
        from cli.main import _suggestions_payload

        payload = _suggestions_payload("/tmp/x.mp4", self._sample())
        for key in ("source", "count", "suggestions", "duration", "safety"):
            assert key in payload

    def test_arabic_is_not_escaped(self):
        from cli.main import _suggestions_payload

        dumped = json.dumps(
            _suggestions_payload("/tmp/x.mp4", self._sample()), ensure_ascii=False
        )
        assert "عنوان" in dumped
