"""كشف كلمات الحشو — أول فجوة تُغلق مقابل الأدوات التجارية."""

from __future__ import annotations

import pytest

from core.common.schemas import Segment, Transcript, Word


def make_segment(pairs, start=0.0, sid=0):
    """pairs = [(نص, مدة, فجوة بعدها)]"""
    words, t = [], start
    for text, dur, gap in pairs:
        words.append(Word(text=text, start=round(t, 3), end=round(t + dur, 3)))
        t += dur + gap
    return Segment(
        id=sid, start=start, end=round(t, 3),
        text=" ".join(w.text for w in words), words=words,
    )


def make_transcript(segments, language="ar"):
    return Transcript(source_path="x.mp4", language=language, segments=segments)


class TestNormalization:
    def test_strips_diacritics_and_tatweel(self):
        from core.silence.fillers import normalize_token

        assert normalize_token("يَعــنِي") == "يعني"

    def test_unifies_alef_forms(self):
        from core.silence.fillers import normalize_token

        assert normalize_token("أمم") == normalize_token("امم")

    def test_strips_punctuation(self):
        from core.silence.fillers import normalize_token

        assert normalize_token("يعني،") == "يعني"

    def test_empty_is_safe(self):
        from core.silence.fillers import normalize_token

        assert normalize_token("") == ""
        assert normalize_token("...") == ""


class TestArabicDetection:
    def test_pure_hesitation_always_removed(self):
        """«أممم» ليست كلمة — تُحذف دائماً ولو لم تنعزل."""
        from core.silence.fillers import detect_fillers

        seg = make_segment([("كلمة", 0.3, 0.02), ("أممم", 0.4, 0.02), ("أخرى", 0.3, 0)])
        report = detect_fillers(make_transcript([seg]))
        assert report.count == 1
        assert report.hits[0].reason == "always"

    def test_isolated_yaani_is_removed(self):
        from core.silence.fillers import detect_fillers

        seg = make_segment(
            [("الذكاء", 0.4, 0.3), ("يعني", 0.3, 0.3), ("سيغير", 0.4, 0)]
        )
        report = detect_fillers(make_transcript([seg]))
        assert any("يعني" in h.text for h in report.hits)

    def test_connective_yaani_is_kept(self):
        """«هذا يعني أن» أداة ربط حقيقية — حذفها يُفسد المعنى."""
        from core.silence.fillers import detect_fillers

        seg = make_segment(
            [("هذا", 0.3, 0.02), ("يعني", 0.3, 0.02), ("أن", 0.2, 0.02), ("النتيجة", 0.4, 0)]
        )
        assert detect_fillers(make_transcript([seg])).count == 0

    def test_long_word_is_not_filler(self):
        """كلمة تستغرق ثانيتين غالباً خطأ تفريغ لا تردد."""
        from core.silence.fillers import detect_fillers

        seg = make_segment([("بداية", 0.3, 0.3), ("يعني", 2.0, 0.3), ("نهاية", 0.3, 0)])
        assert detect_fillers(make_transcript([seg])).count == 0

    def test_normal_speech_untouched(self):
        from core.silence.fillers import detect_fillers

        seg = make_segment(
            [("الذكاء", 0.4, 0.02), ("الاصطناعي", 0.5, 0.02), ("مهم", 0.3, 0)]
        )
        assert detect_fillers(make_transcript([seg])).count == 0


class TestEnglishDetection:
    def test_uh_um_removed(self):
        from core.silence.fillers import detect_fillers

        seg = make_segment([("so", 0.2, 0.3), ("um", 0.3, 0.3), ("yes", 0.3, 0)])
        report = detect_fillers(make_transcript([seg], language="en"))
        assert any(h.text.strip() == "um" for h in report.hits)

    def test_multiword_phrase(self):
        """«you know» عبارة كاملة لا كلمتين منفصلتين."""
        from core.silence.fillers import detect_fillers

        seg = make_segment(
            [("it", 0.2, 0.3), ("you", 0.2, 0.02), ("know", 0.2, 0.3), ("works", 0.3, 0)]
        )
        report = detect_fillers(make_transcript([seg], language="en"))
        assert any(h.reason == "phrase" for h in report.hits)


class TestSafetyGuards:
    def test_high_ratio_aborts_on_long_input(self):
        """نسبة عالية = خطأ كشف؛ تمزيق الكلام أسوأ من تركه."""
        from core.silence.fillers import detect_fillers

        pairs = []
        for _ in range(30):
            pairs += [("أممم", 0.3, 0.3), ("كلمة", 0.3, 0.3)]
        report = detect_fillers(make_transcript([make_segment(pairs)]))
        assert report.count == 0, "كان يجب تخطّي الحذف عند نسبة مرتفعة"

    def test_small_sample_not_penalized(self):
        """كلمتان من سبع = 29% لكنه طبيعي في جملة قصيرة."""
        from core.silence.fillers import detect_fillers

        seg = make_segment(
            [("أممم", 0.4, 0.3), ("الذكاء", 0.4, 0.02), ("مهم", 0.3, 0)]
        )
        assert detect_fillers(make_transcript([seg])).count == 1

    def test_keep_words_respected(self):
        from copy import deepcopy

        from core.common.config import load_settings
        from core.silence.fillers import detect_fillers

        settings = deepcopy(load_settings())
        settings.data["fillers"]["keep_words"] = ["يعني"]
        seg = make_segment([("أ", 0.3, 0.3), ("يعني", 0.3, 0.3), ("ب", 0.3, 0)])
        report = detect_fillers(make_transcript([seg]), settings=settings)
        assert not any("يعني" in h.text for h in report.hits)

    def test_extra_words_honored(self):
        from copy import deepcopy

        from core.common.config import load_settings
        from core.silence.fillers import detect_fillers

        settings = deepcopy(load_settings())
        settings.data["fillers"]["extra_words"] = ["والله"]
        seg = make_segment([("أ", 0.3, 0.3), ("والله", 0.3, 0.3), ("ب", 0.3, 0)])
        report = detect_fillers(make_transcript([seg]), settings=settings)
        assert any("والله" in h.text for h in report.hits)

    def test_disabled_returns_empty(self):
        from copy import deepcopy

        from core.common.config import load_settings
        from core.silence.fillers import detect_fillers

        settings = deepcopy(load_settings())
        settings.data["fillers"]["enabled"] = False
        seg = make_segment([("أممم", 0.4, 0.3), ("كلمة", 0.3, 0)])
        assert detect_fillers(make_transcript([seg]), settings=settings).count == 0

    def test_no_word_timing_is_safe(self):
        """بلا توقيت كلمات لا يمكن الحذف — يجب ألّا ينهار."""
        from core.silence.fillers import detect_fillers

        seg = Segment(id=0, start=0, end=3, text="أممم يعني كلام", words=[])
        assert detect_fillers(make_transcript([seg])).count == 0

    def test_empty_transcript(self):
        from core.silence.fillers import detect_fillers

        assert detect_fillers(make_transcript([])).count == 0


class TestRanges:
    def test_cut_ranges_merge_adjacent(self):
        from core.silence.fillers import FillerHit, FillerReport

        report = FillerReport(
            hits=[FillerHit("a", 1.0, 1.3), FillerHit("b", 1.32, 1.6)], total_words=10
        )
        assert len(report.cut_ranges()) == 1

    def test_cut_ranges_keep_separate(self):
        from core.silence.fillers import FillerHit, FillerReport

        report = FillerReport(
            hits=[FillerHit("a", 1.0, 1.3), FillerHit("b", 5.0, 5.3)], total_words=10
        )
        assert len(report.cut_ranges()) == 2

    def test_keep_ranges_complement_cuts(self):
        from core.silence.fillers import keep_ranges_from_cuts

        keeps = keep_ranges_from_cuts([(2.0, 3.0)], duration=10.0)
        assert keeps == [(0.0, 2.0), (3.0, 10.0)]

    def test_keep_ranges_drop_slivers(self):
        """شظية 20ms لا تستحق قصّاً — تُسقط."""
        from core.silence.fillers import keep_ranges_from_cuts

        keeps = keep_ranges_from_cuts([(0.0, 1.0), (1.02, 2.0)], duration=5.0)
        assert all((b - a) >= 0.08 for a, b in keeps)

    def test_cut_at_start_and_end(self):
        from core.silence.fillers import keep_ranges_from_cuts

        keeps = keep_ranges_from_cuts([(0.0, 0.5), (9.5, 10.0)], duration=10.0)
        assert keeps == [(0.5, 9.5)]

    def test_report_metrics(self):
        from core.silence.fillers import FillerHit, FillerReport

        report = FillerReport(
            hits=[FillerHit("a", 1.0, 1.4), FillerHit("b", 2.0, 2.2)], total_words=20
        )
        assert report.count == 2
        assert report.removed_seconds == pytest.approx(0.6)
        assert report.ratio == pytest.approx(0.1)
