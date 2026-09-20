"""اختبارات المرحلة 2: الاستدلال، استخراج JSON، اكتشاف اللحظات، التقسيم.

كلها تعمل بلا شبكة وبلا Ollama — مسار النموذج يُختبر بعميل وهمي.
"""

from __future__ import annotations

import json

import pytest

from core.analyze.heuristics import (
    KIND_PRIORITY,
    MomentCandidate,
    find_moments,
    score_segment,
    speech_density,
)
from core.analyze.llm_client import (
    BaseLLM,
    LLMError,
    NoLLM,
    build_llm,
    extract_json,
)
from core.analyze.moments import analyze_transcript, split_long_moment
from core.common.config import Settings
from core.common.schemas import Segment, Transcript, Word


# ============================================================ أدوات مساعدة


def make_transcript(rows, gap: float = 0.5, dur: float = 6.0) -> Transcript:
    """يبني ترانسكربت من (نص, ترجمة) أو (نص, ترجمة, متحدث)."""
    segments = []
    t = 0.0
    for i, row in enumerate(rows):
        text, translation = row[0], row[1]
        speaker = row[2] if len(row) > 2 else None
        parts = text.split() or ["x"]
        step = dur / len(parts)
        words = [
            Word(text=w, start=t + j * step, end=t + (j + 1) * step)
            for j, w in enumerate(parts)
        ]
        segments.append(
            Segment(
                id=i,
                start=t,
                end=t + dur,
                text=text,
                translation=translation,
                words=words,
                speaker=speaker,
            )
        )
        t += dur + gap
    return Transcript(
        source_path="/tmp/x.mp4", language="en", segments=segments, duration=t
    )


class FakeLLM(BaseLLM):
    """عميل نموذج وهمي يرجع نصاً محدداً مسبقاً."""

    name = "fake"
    available = True

    def __init__(self, payload: str, *, fail: bool = False):
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def generate(self, prompt: str, *, system: str = "", **kwargs) -> str:
        self.calls += 1
        if self.fail:
            raise LLMError("فشل مُتعمَّد")
        return self.payload


# ============================================================ extract_json


class TestExtractJson:
    def test_plain_object(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_plain_array(self):
        assert extract_json('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]

    def test_markdown_fence(self):
        raw = 'حسناً إليك النتيجة:\n```json\n{"title": "عنوان"}\n```\nانتهى.'
        assert extract_json(raw) == {"title": "عنوان"}

    def test_trailing_comma(self):
        assert extract_json('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}

    def test_array_trailing_comma_in_fence(self):
        raw = "```\n[{'x': 1},]\n```".replace("'", '"')
        assert extract_json(raw) == [{"x": 1}]

    def test_prose_around_json(self):
        raw = 'بالتأكيد! {"ok": true} هذا هو المطلوب.'
        assert extract_json(raw) == {"ok": True}

    def test_nested_braces(self):
        raw = 'x {"a": {"b": [1, 2]}} y'
        assert extract_json(raw) == {"a": {"b": [1, 2]}}

    def test_arabic_content_preserved(self):
        raw = '{"title": "لماذا يفشل الناس؟", "tags": ["#نجاح"]}'
        out = extract_json(raw)
        assert out["title"] == "لماذا يفشل الناس؟"
        assert out["tags"] == ["#نجاح"]

    def test_invalid_raises(self):
        # العقد: الفشل صريح عبر استثناء، ليس None صامتاً
        with pytest.raises(LLMError):
            extract_json("لا يوجد هنا أي JSON إطلاقاً")

    def test_empty_raises(self):
        with pytest.raises(LLMError):
            extract_json("")


# ============================================================ محرك النموذج


class TestLLMClient:
    def test_no_llm_is_unavailable(self):
        engine = NoLLM()
        assert engine.available is False
        with pytest.raises(LLMError):
            engine.generate("أي شيء")

    def test_build_llm_none_engine(self):
        settings = Settings({"analyze": {"llm_engine": "none"}})
        assert isinstance(build_llm(settings), NoLLM)

    def test_generate_json_retries_then_fails(self):
        llm = FakeLLM("نص بلا JSON")
        with pytest.raises(LLMError):
            llm.generate_json("prompt", retries=2)
        assert llm.calls == 3  # المحاولة الأولى + إعادتان

    def test_generate_json_success(self):
        llm = FakeLLM('```json\n{"ok": 1}\n```')
        assert llm.generate_json("prompt") == {"ok": 1}


# ============================================================ الاستدلال


class TestHeuristics:
    def test_question_is_detected(self):
        tr = make_transcript([("Why do most people fail at this?", "لماذا يفشل معظمهم؟")])
        score, kind, signals = score_segment(tr, 0, avg_density=2.0)
        assert signals.get("question") == 1.0
        assert score > 0

    def test_arabic_question_mark(self):
        tr = make_transcript([("ما هو السر الحقيقي؟", "ما هو السر الحقيقي؟")])
        _, _, signals = score_segment(tr, 0, avg_density=2.0)
        assert signals.get("question") == 1.0

    def test_story_beats_surprise_on_tie(self):
        # أولوية النوع تحسم التعادل لصالح السرد
        assert KIND_PRIORITY["story"] > KIND_PRIORITY["surprise"]

    def test_story_marker_classified_as_story(self):
        tr = make_transcript(
            [("So let me tell you a story about how I lost everything.", "دعني أحكي لك قصة.")]
        )
        _, kind, _ = score_segment(tr, 0, avg_density=2.0)
        assert kind == "story"

    def test_numbers_signal(self):
        tr = make_transcript([("We grew 250 percent in 7 years.", "نمونا 250 بالمئة في 7 سنوات.")])
        _, _, signals = score_segment(tr, 0, avg_density=2.0)
        assert signals.get("numbers", 0) >= 1

    def test_speaker_change_signal(self):
        tr = make_transcript(
            [
                ("First speaker talking here.", "الأول", "SPEAKER_00"),
                ("Second speaker replies now.", "الثاني", "SPEAKER_01"),
            ]
        )
        _, _, signals = score_segment(tr, 1, avg_density=2.0)
        assert signals.get("speaker_change") == 1.0

    def test_no_speaker_change_when_same(self):
        tr = make_transcript(
            [
                ("First line.", "الأولى", "SPEAKER_00"),
                ("Same person again.", "الثانية", "SPEAKER_00"),
            ]
        )
        _, _, signals = score_segment(tr, 1, avg_density=2.0)
        assert "speaker_change" not in signals

    def test_speech_density_positive(self):
        tr = make_transcript([("one two three four five six", "ستة")])
        assert speech_density(tr.segments[0]) > 0

    def test_find_moments_respects_min_duration(self):
        tr = make_transcript(
            [
                ("Let me tell you a story about failure.", "قصة عن الفشل."),
                ("I had 250000 dollars and lost it all.", "كان لدي 250000 دولار."),
                ("Why do most people fail at this?", "لماذا يفشل معظم الناس؟"),
                ("In my opinion the secret is leverage.", "برأيي السر هو الرافعة."),
            ]
        )
        moments = find_moments(tr, max_moments=3, min_duration=15.0, max_duration=60.0)
        assert moments
        for m in moments:
            assert m.duration >= 15.0 - 1e-6
            assert m.duration <= 60.0 + 1e-6

    def test_find_moments_no_overlap(self):
        tr = make_transcript([(f"Sentence number {i} here, the secret is big.", f"جملة {i}") for i in range(12)])
        moments = find_moments(tr, max_moments=4, min_duration=12.0, max_duration=40.0)
        ordered = sorted(moments, key=lambda m: m.start)
        for a, b in zip(ordered, ordered[1:]):
            assert a.end <= b.start + 1e-6

    def test_find_moments_empty_transcript(self):
        tr = Transcript(source_path="/tmp/x.mp4", language="en", segments=[])
        assert find_moments(tr) == []

    def test_moments_sorted_by_time(self):
        tr = make_transcript([(f"The secret number {i} is that nobody tells you.", f"سر {i}") for i in range(8)])
        moments = find_moments(tr, max_moments=3, min_duration=10.0, max_duration=40.0)
        assert [m.start for m in moments] == sorted(m.start for m in moments)


# ============================================================ التقسيم


class TestSplitLongMoment:
    def _moment(self, start=0.0, end=100.0):
        return MomentCandidate(
            start=start, end=end, score=3.0, kind="story", reason="سبب", text="نص"
        )

    def test_short_moment_untouched(self):
        m = self._moment(0.0, 40.0)
        assert split_long_moment(m, max_duration=70.0) == [m]

    def test_long_moment_is_split(self):
        parts = split_long_moment(self._moment(0.0, 150.0), max_duration=70.0)
        assert len(parts) == 3
        assert all(p.duration <= 70.0 + 1e-6 for p in parts)

    def test_parts_are_contiguous_and_bounded(self):
        parts = split_long_moment(self._moment(10.0, 160.0), max_duration=70.0)
        assert parts[0].start == pytest.approx(10.0)
        assert parts[-1].end == pytest.approx(160.0)
        for a, b in zip(parts, parts[1:]):
            assert a.end == pytest.approx(b.start)

    def test_part_metadata(self):
        parts = split_long_moment(self._moment(0.0, 150.0), max_duration=70.0)
        assert parts[0].signals["part"] == 1
        assert parts[0].signals["total_parts"] == 3
        assert "جزء 1 من 3" in parts[0].reason


# ============================================================ analyze_transcript


class TestAnalyzeTranscript:
    def _tr(self):
        return make_transcript(
            [
                ("Let me tell you a story about how I lost everything.", "دعني أحكي قصة."),
                ("I had 250000 dollars and thought I was untouchable.", "كان لدي 250000 دولار."),
                ("But what happened next changed how I think.", "لكن ما حدث غيّرني."),
                ("Why do most people fail at this?", "لماذا يفشل معظم الناس؟"),
                ("In my opinion the secret is leverage not effort.", "برأيي السر هو الرافعة."),
                ("Anyway that is the background.", "على أي حال هذه الخلفية."),
            ]
        )

    def test_heuristic_mode_needs_no_llm(self):
        settings = Settings({"analyze": {"llm_engine": "none"}})
        moments = analyze_transcript(
            self._tr(), settings=settings, engine="heuristic", max_moments=3
        )
        assert 1 <= len(moments) <= 3
        assert all(isinstance(m, MomentCandidate) for m in moments)

    def test_llm_failure_falls_back_to_heuristic(self):
        """أهم اختبار: انهيار النموذج يجب ألّا يُسقط الأداة."""
        settings = Settings({"analyze": {"llm_engine": "none"}})
        moments = analyze_transcript(
            self._tr(), settings=settings, engine="hybrid", max_moments=3
        )
        assert moments, "يجب أن يتراجع إلى الاستدلال بدل الفشل"

    def test_max_moments_respected(self):
        settings = Settings({"analyze": {"llm_engine": "none"}})
        moments = analyze_transcript(
            self._tr(), settings=settings, engine="heuristic", max_moments=2
        )
        assert len(moments) <= 2

    def test_empty_transcript_returns_empty(self):
        settings = Settings({"analyze": {"llm_engine": "none"}})
        tr = Transcript(source_path="/tmp/x.mp4", language="en", segments=[])
        assert analyze_transcript(tr, settings=settings, engine="heuristic") == []

    def test_moments_carry_text_and_reason(self):
        settings = Settings({"analyze": {"llm_engine": "none"}})
        moments = analyze_transcript(self._tr(), settings=settings, engine="heuristic")
        assert all(m.reason for m in moments)
        assert any(m.text for m in moments)


# ============================================================ مسار النموذج


class TestLLMPathsNoOverlap:
    """النموذج قد يقترح مقاطع متداخلة — الضمان مفروض على مخرجاته أيضاً."""

    def _transcript(self):
        return make_transcript(
            [
                (f"The secret number {i} is that nobody tells you this fact.", f"سر رقم {i}")
                for i in range(10)
            ]
        )

    def test_hybrid_drops_overlapping_llm_output(self):
        from core.analyze.moments import _analyze_hybrid

        llm = FakeLLM(
            json.dumps(
                [
                    {"index": 0, "score": 9, "kind": "insight", "reason": "أ",
                     "trim_start_offset": 0, "trim_end_offset": 40},
                    {"index": 1, "score": 8, "kind": "insight", "reason": "ب",
                     "trim_start_offset": -40, "trim_end_offset": 0},
                ]
            )
        )
        out = _analyze_hybrid(self._transcript(), llm, max_moments=5, min_dur=15.0, max_dur=60.0)
        for a, b in zip(out, out[1:]):
            assert a.end <= b.start + 1e-6

    def test_hybrid_keeps_highest_score_on_conflict(self):
        from core.analyze.moments import _analyze_hybrid

        llm = FakeLLM(
            json.dumps(
                [
                    {"index": 0, "score": 9, "kind": "insight", "reason": "الأعلى",
                     "trim_start_offset": 0, "trim_end_offset": 40},
                    {"index": 1, "score": 2, "kind": "insight", "reason": "الأدنى",
                     "trim_start_offset": -40, "trim_end_offset": 0},
                ]
            )
        )
        out = _analyze_hybrid(self._transcript(), llm, max_moments=5, min_dur=15.0, max_dur=60.0)
        assert out[0].score == 9.0

    def test_llm_only_drops_overlaps(self):
        from core.analyze.moments import _analyze_llm_only

        llm = FakeLLM(
            json.dumps(
                [
                    {"start": 0, "end": 40, "score": 9, "kind": "story", "reason": "أ"},
                    {"start": 20, "end": 55, "score": 5, "kind": "story", "reason": "ب"},
                ]
            )
        )
        out = _analyze_llm_only(self._transcript(), llm, max_moments=5, min_dur=15.0, max_dur=60.0)
        for a, b in zip(out, out[1:]):
            assert a.end <= b.start + 1e-6

    def test_hybrid_ignores_bad_indices(self):
        from core.analyze.moments import _analyze_hybrid

        llm = FakeLLM(json.dumps([{"index": 999, "score": 9, "kind": "x", "reason": "y"}]))
        out = _analyze_hybrid(self._transcript(), llm, max_moments=3, min_dur=15.0, max_dur=60.0)
        assert out, "فهرس خاطئ يجب أن يتراجع لمرشحات الاستدلال لا أن يُفرغ النتيجة"

    def test_llm_only_skips_invalid_ranges(self):
        from core.analyze.moments import _analyze_llm_only

        llm = FakeLLM(
            json.dumps(
                [
                    {"start": 50, "end": 10, "score": 9, "kind": "x", "reason": "معكوس"},
                    {"start": 0, "end": 30, "score": 7, "kind": "story", "reason": "سليم"},
                ]
            )
        )
        out = _analyze_llm_only(self._transcript(), llm, max_moments=5, min_dur=15.0, max_dur=60.0)
        assert len(out) == 1
        assert out[0].start == pytest.approx(0.0)
