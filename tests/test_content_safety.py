"""اختبارات توليد المحتوى، فحص السلامة، وفصل المتحدثين (منطق الإسناد).

كلها بلا شبكة وبلا نماذج: pyannote يُختبر عبر ``assign_speakers``/``friendly_names``
ببيانات مُصطنعة، لأن أوزانه gated على HuggingFace.
"""

from __future__ import annotations

import pytest

from core.analyze.heuristics import MomentCandidate
from core.common.config import Settings
from core.common.schemas import Segment, Transcript, Word
from core.content_gen.generator import (
    ClipContent,
    generate_content,
    generate_for_moments,
)
from core.diarize.speakers import SpeakerTurn, assign_speakers, friendly_names
from core.safety.checker import (
    SafetyIssue,
    SafetyReport,
    check_transcript,
    check_translation_sanity,
    check_words,
    load_word_lists,
)

NO_LLM = Settings({"analyze": {"llm_engine": "none"}})


def seg(i, start, end, text, translation=None, speaker=None):
    return Segment(
        id=i, start=start, end=end, text=text, translation=translation, speaker=speaker
    )


def moment(text="نص المقطع", translation="", kind="story"):
    return MomentCandidate(
        start=0.0, end=30.0, score=4.0, kind=kind, reason="سبب",
        text=text, translation=translation,
    )


# ============================================================ توليد المحتوى


class TestContentGeneration:
    def test_heuristic_content_has_title(self):
        c = generate_content(moment(), settings=NO_LLM, use_llm=False)
        assert isinstance(c, ClipContent)
        assert c.title
        assert c.generated_by == "heuristic"

    def test_hashtags_are_prefixed(self):
        c = generate_content(moment(), settings=NO_LLM, use_llm=False)
        assert c.hashtags
        assert all(h.startswith("#") for h in c.hashtags)

    def test_hashtags_have_no_spaces(self):
        c = generate_content(moment(), settings=NO_LLM, use_llm=False)
        assert all(" " not in h for h in c.hashtags)

    def test_prefers_arabic_translation_for_title(self):
        m = moment(text="English source sentence here.", translation="الجملة العربية المترجمة.")
        c = generate_content(m, settings=NO_LLM, use_llm=False)
        assert "الجملة" in c.title

    def test_unavailable_llm_falls_back(self):
        """النموذج المعطّل يجب ألّا يُفشل التوليد."""
        c = generate_content(moment(), settings=NO_LLM, use_llm=True)
        assert c.title
        assert c.generated_by == "heuristic"

    def test_generate_for_moments_length_matches(self):
        moments = [moment(text=f"مقطع رقم {i}") for i in range(4)]
        out = generate_for_moments(moments, settings=NO_LLM, use_llm=False)
        assert len(out) == 4
        assert all(c.title for c in out)

    def test_generate_for_moments_empty(self):
        assert generate_for_moments([], settings=NO_LLM, use_llm=False) == []

    def test_title_is_trimmed(self):
        long_text = "كلمة " * 100
        c = generate_content(moment(text=long_text), settings=NO_LLM, use_llm=False)
        assert len(c.title) <= 120


# ============================================================ فصل المتحدثين


class TestSpeakerAssignment:
    def _transcript(self):
        return Transcript(
            source_path="/tmp/x.mp4",
            language="ar",
            segments=[
                seg(0, 0.0, 5.0, "المضيف يتكلم"),
                seg(1, 5.0, 10.0, "الضيف يجيب"),
                seg(2, 10.0, 15.0, "المضيف مجدداً"),
            ],
        )

    def test_assign_by_max_overlap(self):
        turns = [
            SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=5.2),
            SpeakerTurn(speaker="SPEAKER_01", start=5.2, end=9.8),
            SpeakerTurn(speaker="SPEAKER_00", start=9.8, end=15.0),
        ]
        tr = assign_speakers(self._transcript(), turns)
        assert [s.speaker for s in tr.segments] == [
            "SPEAKER_00", "SPEAKER_01", "SPEAKER_00",
        ]

    def test_partial_overlap_picks_dominant(self):
        # الجملة 0..5 تتقاطع 1s مع 00 و4s مع 01 → يجب اختيار 01
        turns = [
            SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=1.0),
            SpeakerTurn(speaker="SPEAKER_01", start=1.0, end=5.0),
        ]
        tr = Transcript(
            source_path="/tmp/x.mp4", language="ar", segments=[seg(0, 0.0, 5.0, "جملة")]
        )
        assert assign_speakers(tr, turns).segments[0].speaker == "SPEAKER_01"

    def test_no_turns_leaves_speakers_none(self):
        tr = assign_speakers(self._transcript(), [])
        assert all(s.speaker is None for s in tr.segments)

    def test_speaker_turn_overlap_math(self):
        turn = SpeakerTurn(speaker="A", start=10.0, end=20.0)
        assert turn.overlap(15.0, 25.0) == pytest.approx(5.0)
        assert turn.overlap(0.0, 5.0) == 0.0
        assert turn.duration == pytest.approx(10.0)

    def test_friendly_names_host_is_most_talkative(self):
        tr = Transcript(
            source_path="/tmp/x.mp4",
            language="ar",
            segments=[
                seg(0, 0.0, 30.0, "كلام طويل للمضيف", speaker="SPEAKER_00"),
                seg(1, 30.0, 35.0, "رد قصير", speaker="SPEAKER_01"),
                seg(2, 35.0, 60.0, "كلام طويل آخر", speaker="SPEAKER_00"),
            ],
        )
        names = friendly_names(tr)
        assert names["SPEAKER_00"] == "المضيف"
        assert names["SPEAKER_01"] == "الضيف"

    def test_friendly_names_empty_when_no_speakers(self):
        assert friendly_names(self._transcript()) == {}


# ============================================================ السلامة


class TestSafety:
    def test_word_lists_load(self):
        lists = load_word_lists()
        assert lists
        assert any(lists.values())

    def test_clean_transcript_has_no_issues(self):
        tr = Transcript(
            source_path="/tmp/x.mp4",
            language="ar",
            segments=[seg(0, 0.0, 5.0, "حديث عادي تماماً", "حديث عادي تماماً")],
        )
        report = check_transcript(tr, settings=NO_LLM, use_llm=False)
        assert isinstance(report, SafetyReport)
        assert not report.has_critical

    def test_empty_translation_flagged(self):
        tr = Transcript(
            source_path="/tmp/x.mp4",
            language="en",
            translated_to="ar",
            segments=[seg(0, 0.0, 5.0, "This is a full English sentence.", "")],
        )
        issues = check_translation_sanity(tr)
        assert any("بلا ترجمة" in i.message for i in issues)

    def test_untranslated_latin_flagged(self):
        tr = Transcript(
            source_path="/tmp/x.mp4",
            language="en",
            translated_to="ar",
            segments=[
                seg(0, 0.0, 5.0, "This is English.", "This is still English, not Arabic.")
            ],
        )
        assert check_translation_sanity(tr), "ترجمة بلا حروف عربية يجب أن تُعلَّم"

    def test_repetition_loop_flagged(self):
        tr = Transcript(
            source_path="/tmp/x.mp4",
            language="en",
            translated_to="ar",
            segments=[
                seg(0, 0.0, 10.0, "A normal sentence about many different topics here.",
                    "نعم نعم نعم نعم نعم نعم نعم نعم نعم نعم")
            ],
        )
        assert check_translation_sanity(tr), "تكرار الكلمة الواحدة يجب أن يُعلَّم"

    def test_no_false_positive_on_good_translation(self):
        tr = Transcript(
            source_path="/tmp/x.mp4",
            language="en",
            translated_to="ar",
            segments=[
                seg(0, 0.0, 5.0, "The secret is leverage, not effort.",
                    "السر هو الرافعة المالية وليس الجهد.")
            ],
        )
        assert check_translation_sanity(tr) == []

    def test_sensitive_word_detected(self):
        lists = load_word_lists()
        # نأخذ كلمة فعلية من الملف لضمان تطابق السلوك مع الإعداد
        category = next(k for k, v in lists.items() if v)
        word = lists[category][0]
        tr = Transcript(
            source_path="/tmp/x.mp4",
            language="ar",
            segments=[seg(0, 0.0, 5.0, f"جملة تحتوي {word} داخلها", f"جملة تحتوي {word} داخلها")],
        )
        issues = check_words(tr, lists)
        assert issues
        assert issues[0].start == pytest.approx(0.0)

    def test_report_serializes(self):
        report = SafetyReport(
            issues=[SafetyIssue("warning", "translation", "تنبيه", 0, 1.0)]
        )
        d = report.to_dict()
        assert d["issues"][0]["message"] == "تنبيه"

    def test_warnings_property(self):
        report = SafetyReport(
            issues=[
                SafetyIssue("warning", "translation", "أ"),
                SafetyIssue("info", "translation", "ب"),
            ]
        )
        assert len(report.warnings) == 1
