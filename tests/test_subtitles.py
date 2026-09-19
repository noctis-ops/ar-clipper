"""اختبارات توليد ملفات الترجمة (SRT / VTT / ASS)."""

from __future__ import annotations

import pytest

from core.common.config import load_settings
from core.common.schemas import Segment, Transcript
from core.common.text_utils import RLM
from core.subtitles.builder import (
    _ass_timestamp,
    build_ass_style,
    hex_to_ass_color,
    write_ass,
    write_sidecars,
    write_srt,
    write_vtt,
)


@pytest.fixture
def transcript() -> Transcript:
    return Transcript(
        source_path="/tmp/x.mp4",
        language="en",
        duration=12.0,
        segments=[
            Segment(0, 0.0, 3.0, "first line", translation="السطر الأول"),
            Segment(1, 3.5, 7.0, "second line", translation="السطر الثاني"),
            Segment(2, 7.5, 12.0, "third line", translation="السطر الثالث"),
        ],
    )


@pytest.fixture
def settings():
    return load_settings()


class TestColors:
    def test_white(self):
        assert hex_to_ass_color("#FFFFFF") == "&H00FFFFFF"

    def test_bgr_order(self):
        # أحمر #FF0000 → BGR يعطي 0000FF
        assert hex_to_ass_color("#FF0000") == "&H000000FF"

    def test_alpha_inverted(self):
        # ASS: 00 = معتم تماماً، لذا #FF... (ألفا كاملة) → &H00
        assert hex_to_ass_color("#FF000000") == "&H00000000"

    def test_passthrough_native(self):
        assert hex_to_ass_color("&H00ABCDEF") == "&H00ABCDEF"

    def test_invalid_uses_default(self):
        assert hex_to_ass_color("nonsense", "&H00FFFFFF") == "&H00FFFFFF"


class TestAssTimestamp:
    @pytest.mark.parametrize(
        "sec,expected",
        [(0, "0:00:00.00"), (1.5, "0:00:01.50"), (90.25, "0:01:30.25"), (3661.0, "1:01:01.00")],
    )
    def test_format(self, sec, expected):
        assert _ass_timestamp(sec) == expected


class TestSrt:
    def test_structure(self, transcript, settings, tmp_path):
        path = write_srt(transcript, tmp_path / "a.srt", settings=settings)
        content = path.read_text(encoding="utf-8")
        assert content.startswith("1\n")
        assert "-->" in content
        assert "00:00:00,000" in content

    def test_arabic_track(self, transcript, settings, tmp_path):
        content = write_srt(
            transcript, tmp_path / "a.srt", track="ar", settings=settings
        ).read_text(encoding="utf-8")
        assert "السطر الأول" in content
        assert "first line" not in content

    def test_source_track(self, transcript, settings, tmp_path):
        content = write_srt(
            transcript, tmp_path / "a.srt", track="source", settings=settings
        ).read_text(encoding="utf-8")
        assert "first line" in content

    def test_bilingual_track(self, transcript, settings, tmp_path):
        content = write_srt(
            transcript, tmp_path / "a.srt", track="bilingual", settings=settings
        ).read_text(encoding="utf-8")
        assert "السطر الأول" in content and "first line" in content

    def test_rtl_mark_present(self, transcript, settings, tmp_path):
        content = write_srt(transcript, tmp_path / "a.srt", settings=settings).read_text(
            encoding="utf-8"
        )
        assert RLM in content

    def test_skips_empty_segments(self, settings, tmp_path):
        t = Transcript(
            source_path="x",
            language="en",
            segments=[Segment(0, 0, 1, "", translation=""), Segment(1, 1, 2, "ok")],
        )
        content = write_srt(t, tmp_path / "a.srt", settings=settings).read_text(encoding="utf-8")
        assert content.count("-->") == 1


class TestVtt:
    def test_header_and_dot_separator(self, transcript, settings, tmp_path):
        content = write_vtt(transcript, tmp_path / "a.vtt", settings=settings).read_text(
            encoding="utf-8"
        )
        assert content.startswith("WEBVTT")
        assert "00:00:00.000" in content
        assert ",".join([]) == ""  # الفاصلة ليست فاصل توقيت في VTT
        assert "00:00:00,000" not in content


class TestAss:
    def test_sections(self, transcript, settings, tmp_path):
        content = write_ass(transcript, tmp_path / "a.ass", settings=settings).read_text(
            encoding="utf-8"
        )
        for section in ("[Script Info]", "[V4+ Styles]", "[Events]"):
            assert section in content

    def test_playres_matches_output(self, transcript, settings, tmp_path):
        content = write_ass(
            transcript, tmp_path / "a.ass", settings=settings, play_res_x=1080, play_res_y=1920
        ).read_text(encoding="utf-8")
        assert "PlayResX: 1080" in content and "PlayResY: 1920" in content

    def test_dialogue_count(self, transcript, settings, tmp_path):
        content = write_ass(transcript, tmp_path / "a.ass", settings=settings).read_text(
            encoding="utf-8"
        )
        assert content.count("Dialogue:") == 3

    def test_newlines_become_N(self, settings, tmp_path):
        t = Transcript(
            source_path="x",
            language="en",
            segments=[Segment(0, 0, 3, "a", translation="سطر طويل جداً " * 6)],
        )
        content = write_ass(t, tmp_path / "a.ass", settings=settings).read_text(encoding="utf-8")
        assert r"\N" in content
        assert "\nDialogue" not in content.split("[Events]")[1].strip().split("\n", 1)[1][:5]

    def test_style_header_is_wellformed(self, settings):
        header = build_ass_style(settings.section("subtitles"), play_res_x=1080, play_res_y=1920)
        style_line = [l for l in header.split("\n") if l.startswith("Style: ")][0]
        fmt_line = [l for l in header.split("\n") if l.startswith("Format: Name")][0]
        n_fields = len(fmt_line.split(":", 1)[1].split(","))
        assert len(style_line.split(":", 1)[1].split(",")) == n_fields


class TestOverlapPrevention:
    def test_no_overlapping_cues(self, settings, tmp_path):
        t = Transcript(
            source_path="x",
            language="en",
            segments=[
                Segment(0, 0.0, 5.0, "a", translation="أ"),
                Segment(1, 3.0, 8.0, "b", translation="ب"),  # متداخلة عمداً
            ],
        )
        content = write_srt(t, tmp_path / "a.srt", settings=settings).read_text(encoding="utf-8")
        stamps = [l for l in content.split("\n") if "-->" in l]
        first_end = stamps[0].split("-->")[1].strip()
        second_start = stamps[1].split("-->")[0].strip()
        assert first_end <= second_start


class TestSidecars:
    def test_writes_all_formats(self, transcript, settings, tmp_path):
        out = write_sidecars(
            transcript, tmp_path / "clip", formats=["srt", "vtt", "ass"], settings=settings
        )
        assert set(out) == {"srt", "vtt", "ass"}
        for path in out.values():
            from pathlib import Path

            assert Path(path).exists()

    def test_ignores_unknown_format(self, transcript, settings, tmp_path):
        out = write_sidecars(transcript, tmp_path / "clip", formats=["srt", "xyz"], settings=settings)
        assert set(out) == {"srt"}
