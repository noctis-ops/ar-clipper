"""اختبارات الإعدادات، التحقق من مدَيات القص، والمحاذاة مع الكلام."""

from __future__ import annotations

import pytest

from core.clip.cutter import preview_transcript, snap_to_speech, validate_range
from core.common.config import load_settings
from core.common.errors import ConfigError, LicenseError, MediaError
from core.common.schemas import Segment, Transcript
from core.ingest.downloader import ingest_local, is_url, make_video_id


class TestSettings:
    def test_loads_defaults(self):
        s = load_settings()
        assert s.get("reframe.width") == 1080
        assert s.get("reframe.height") == 1920
        assert s.get("transcribe.engine") == "faster_whisper"

    def test_dotted_missing_returns_default(self):
        assert load_settings().get("nope.nothing", "fallback") == "fallback"

    def test_require_raises(self):
        with pytest.raises(ConfigError):
            load_settings().require("nope.nothing")

    def test_paths_absolute(self):
        assert load_settings().path("paths.clips").is_absolute()

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("ARCLIPPER__EXPORT__CRF", "31")
        assert load_settings(force=True).get("export.crf") == 31
        monkeypatch.delenv("ARCLIPPER__EXPORT__CRF")
        load_settings(force=True)

    def test_env_override_types(self, monkeypatch):
        monkeypatch.setenv("ARCLIPPER__SILENCE__ENABLED", "false")
        monkeypatch.setenv("ARCLIPPER__SILENCE__THRESHOLD_DB", "-40.5")
        s = load_settings(force=True)
        assert s.get("silence.enabled") is False
        assert s.get("silence.threshold_db") == -40.5
        monkeypatch.delenv("ARCLIPPER__SILENCE__ENABLED")
        monkeypatch.delenv("ARCLIPPER__SILENCE__THRESHOLD_DB")
        load_settings(force=True)

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError):
            load_settings(tmp_path / "nope.yaml")


class TestValidateRange:
    def test_valid(self):
        assert validate_range(10.0, 20.0, 100.0) == (10.0, 20.0)

    def test_end_before_start(self):
        with pytest.raises(MediaError):
            validate_range(20.0, 10.0, 100.0)

    def test_equal_times(self):
        with pytest.raises(MediaError):
            validate_range(10.0, 10.0, 100.0)

    def test_negative_start(self):
        with pytest.raises(MediaError):
            validate_range(-1.0, 10.0, 100.0)

    def test_start_beyond_duration(self):
        with pytest.raises(MediaError):
            validate_range(200.0, 210.0, 100.0)

    def test_end_clamped_to_duration(self):
        assert validate_range(50.0, 200.0, 100.0) == (50.0, 100.0)

    def test_unknown_duration_allowed(self):
        assert validate_range(10.0, 20.0, 0.0) == (10.0, 20.0)


class TestSnapToSpeech:
    def transcript(self):
        return Transcript(
            source_path="x",
            language="en",
            segments=[
                Segment(0, 10.0, 15.0, "a"),
                Segment(1, 15.5, 20.0, "b"),
                Segment(2, 25.0, 30.0, "c"),
            ],
        )

    def test_snaps_near_boundary(self):
        start, end = snap_to_speech(10.4, 19.8, self.transcript())
        assert start < 10.4 and end >= 20.0

    def test_ignores_far_boundary(self):
        start, _ = snap_to_speech(50.0, 60.0, self.transcript(), max_shift=1.5)
        assert start == 50.0

    def test_no_transcript_is_identity(self):
        assert snap_to_speech(5.0, 9.0, None) == (5.0, 9.0)

    def test_empty_transcript_is_identity(self):
        empty = Transcript(source_path="x", language="en", segments=[])
        assert snap_to_speech(5.0, 9.0, empty) == (5.0, 9.0)

    def test_never_negative(self):
        t = Transcript(source_path="x", language="en", segments=[Segment(0, 0.0, 5.0, "a")])
        start, _ = snap_to_speech(0.1, 4.9, t)
        assert start >= 0.0


class TestIngestGuards:
    def test_is_url(self):
        assert is_url("https://youtube.com/watch?v=x")
        assert is_url("http://a.com/v.mp4")
        assert not is_url("/home/user/video.mp4")
        assert not is_url("video.mp4")

    def test_video_id_stable_and_unique(self):
        assert make_video_id("https://a.com/1") == make_video_id("https://a.com/1")
        assert make_video_id("https://a.com/1") != make_video_id("https://a.com/2")
        assert len(make_video_id("x")) == 12

    def test_license_required(self, tmp_path):
        """المبدأ 4: ممنوع المعالجة دون تسجيل سند الترخيص."""
        f = tmp_path / "v.mp4"
        f.write_bytes(b"\x00" * 100)
        with pytest.raises(LicenseError):
            ingest_local(f, license_note="")

    def test_missing_file(self, tmp_path):
        from core.common.errors import IngestError

        with pytest.raises(IngestError):
            ingest_local(tmp_path / "nope.mp4", license_note="مرخّص")


class TestPreviewTranscript:
    def test_shows_timestamps_and_both_texts(self):
        t = Transcript(
            source_path="x",
            language="en",
            segments=[Segment(0, 65.0, 70.0, "hello", translation="مرحبا")],
        )
        line = preview_transcript(t)[0]
        assert "1:05" in line and "hello" in line and "مرحبا" in line

    def test_limit(self):
        t = Transcript(
            source_path="x",
            language="en",
            segments=[Segment(i, i, i + 1, f"s{i}") for i in range(10)],
        )
        assert len(preview_transcript(t, limit=3)) == 3
