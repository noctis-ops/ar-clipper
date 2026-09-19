"""اختبارات الأدوات النصية — لا تحتاج ffmpeg ولا نماذج."""

from __future__ import annotations

import pytest

from core.common.text_utils import (
    RLM,
    apply_rtl_marks,
    contains_arabic,
    format_timestamp,
    human_duration,
    is_rtl_text,
    normalize_text,
    parse_timestamp,
    prepare_subtitle_text,
    slugify,
    strip_tatweel,
    wrap_text,
)


class TestArabicDetection:
    def test_contains_arabic(self):
        assert contains_arabic("مرحبا")
        assert contains_arabic("hello مرحبا")
        assert not contains_arabic("hello world")
        assert not contains_arabic("12345")

    def test_is_rtl_text(self):
        assert is_rtl_text("هذه جملة عربية كاملة")
        assert not is_rtl_text("this is a full english sentence")
        # جملة مختلطة فيها نسبة عربية معتبرة
        assert is_rtl_text("مرحبا hello")

    def test_strip_tatweel(self):
        assert strip_tatweel("مرحبـــا") == "مرحبا"


class TestNormalize:
    def test_collapses_whitespace(self):
        assert normalize_text("  hello    world  ") == "hello world"

    def test_removes_zero_width(self):
        assert normalize_text("a\u200bb") == "ab"

    def test_empty(self):
        assert normalize_text("") == ""
        assert normalize_text(None or "") == ""


class TestWrap:
    def test_short_text_unchanged(self):
        assert wrap_text("قصير", max_chars=40) == "قصير"

    def test_wraps_long_text(self):
        text = "كلمة " * 20
        out = wrap_text(text, max_chars=30, max_lines=2)
        assert "\n" in out
        assert len(out.split("\n")) <= 2

    def test_no_word_loss(self):
        text = "واحد اثنان ثلاثة اربعة خمسة ستة سبعة ثمانية تسعة عشرة"
        out = wrap_text(text, max_chars=20, max_lines=2)
        assert set(text.split()) == set(out.replace("\n", " ").split())


class TestRtlMarks:
    def test_adds_rlm_to_arabic(self):
        assert apply_rtl_marks("مرحبا").startswith(RLM)

    def test_skips_english(self):
        assert apply_rtl_marks("hello") == "hello"

    def test_no_double_mark(self):
        once = apply_rtl_marks("مرحبا")
        assert apply_rtl_marks(once).count(RLM) == 1

    def test_disabled(self):
        assert apply_rtl_marks("مرحبا", enabled=False) == "مرحبا"


class TestPrepareSubtitle:
    def test_full_pipeline(self):
        out = prepare_subtitle_text("هذه جملة عربية طويلة جداً لاختبار اللف", max_chars=20)
        assert RLM in out
        assert "\n" in out

    def test_preserves_logical_order(self):
        """حرج: يجب ألّا نعكس النص — libass يتولى ذلك."""
        text = "مرحبا بالعالم"
        out = prepare_subtitle_text(text)
        assert out.replace(RLM, "") == text


class TestTimestamps:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (90, 90.0),
            (90.5, 90.5),
            ("90", 90.0),
            ("1:30", 90.0),
            ("01:30", 90.0),
            ("00:01:30", 90.0),
            ("00:01:30.500", 90.5),
            ("00:01:30,500", 90.5),
            ("1:00:00", 3600.0),
        ],
    )
    def test_parse(self, raw, expected):
        assert parse_timestamp(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "-5"])
    def test_parse_invalid(self, bad):
        with pytest.raises(ValueError):
            parse_timestamp(bad)

    def test_format_srt(self):
        assert format_timestamp(90.5, sep=",") == "00:01:30,500"
        assert format_timestamp(3661.25, sep=",") == "01:01:01,250"
        assert format_timestamp(0) == "00:00:00,000"

    def test_format_vtt(self):
        assert format_timestamp(90.5, sep=".") == "00:01:30.500"

    def test_roundtrip(self):
        for v in (0.0, 1.5, 90.25, 3661.125):
            assert parse_timestamp(format_timestamp(v).replace(",", ".")) == pytest.approx(v, abs=0.002)

    def test_human_duration(self):
        assert human_duration(90) == "1:30"
        assert human_duration(3661) == "1:01:01"
        assert human_duration(0) == "0:00"


class TestSlugify:
    def test_keeps_arabic(self):
        assert "مرحبا" in slugify("مرحبا بالعالم")

    def test_removes_unsafe_chars(self):
        out = slugify("a/b\\c:d*e?f")
        for ch in "/\\:*?":
            assert ch not in out

    def test_fallback(self):
        assert slugify("!!!", fallback="clip") == "clip"

    def test_max_len(self):
        assert len(slugify("a" * 200, max_len=20)) <= 20
