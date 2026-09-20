"""اختبارات منسّق الاقتراح (core/suggest.py) — بحقن ترانسكربت جاهز.

لا شبكة ولا ffmpeg ولا نموذج: نمرّر ``transcript=`` و``source_video=`` فنختبر
منطق التنسيق والتحويل إلى ClipRequest بمعزل عن بقية المحطات.
"""

from __future__ import annotations

import json

import pytest

from core.common.config import Settings
from core.common.schemas import Segment, SourceVideo, Transcript, Word
from core.suggest import Suggestion, SuggestionSet, load_suggestions, suggest_clips


def build_transcript(n: int = 8) -> Transcript:
    rows = [
        ("So let me tell you a story about how I lost everything in 2019.", "دعني أحكي لك قصة عن كيف خسرت كل شيء في 2019."),
        ("I had about 250,000 dollars and thought I was untouchable.", "كان لديّ 250 ألف دولار وظننت أنني لا أُمَس."),
        ("But what happened next changed how I think about money.", "لكن ما حدث بعدها غيّر تفكيري في المال."),
        ("Why do most people fail at this?", "لماذا يفشل معظم الناس في هذا؟"),
        ("In my opinion the biggest mistake is believing hard work is enough.", "برأيي أكبر خطأ هو الاعتقاد أن العمل الجاد يكفي."),
        ("The secret nobody tells you is that leverage beats effort.", "السر الذي لا يخبرك به أحد أن الرافعة تتفوق على الجهد."),
        ("Anyway that is roughly the background here.", "على أي حال هذه هي الخلفية."),
        ("Let me give you three numbers: 90 percent, 7 years, 12 attempts.", "ثلاثة أرقام: 90 بالمئة، 7 سنوات، 12 محاولة."),
    ][:n]
    segments = []
    t = 0.0
    for i, (en, ar) in enumerate(rows):
        dur = 8.0
        parts = en.split()
        step = dur / len(parts)
        words = [Word(text=w, start=t + j * step, end=t + (j + 1) * step) for j, w in enumerate(parts)]
        segments.append(
            Segment(
                id=i, start=t, end=t + dur, text=en, translation=ar, words=words,
                speaker="SPEAKER_00" if i % 2 == 0 else "SPEAKER_01",
            )
        )
        t += dur + 0.6
    return Transcript(source_path="/tmp/x.mp4", language="en", segments=segments, duration=t)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        {
            "paths": {
                "work": str(tmp_path / "work"),
                "clips": str(tmp_path / "clips"),
                "registry": str(tmp_path / "registry"),
                "models": str(tmp_path / "models"),
                "cache": str(tmp_path / "cache"),
            },
            "analyze": {"llm_engine": "none", "engine": "heuristic",
                        "min_duration": 15.0, "max_duration": 60.0},
            "content_gen": {"enabled": True, "use_llm": False},
            "diarize": {"enabled": False},
            "safety": {"enabled": True},
        }
    )


def run(settings, **kw):
    video = SourceVideo(path="/tmp/x.mp4", title="demo", duration=70.0)
    params = dict(
        settings=settings,
        transcript=build_transcript(),
        source_video=video,
        analyze_engine="heuristic",
        diarize=False,
    )
    params.update(kw)
    return suggest_clips("/tmp/x.mp4", **params)


class TestSuggestClips:
    def test_returns_suggestions(self, settings):
        result = run(settings)
        assert isinstance(result, SuggestionSet)
        assert result.suggestions

    def test_indices_are_sequential(self, settings):
        result = run(settings)
        assert [s.index for s in result.suggestions] == list(range(len(result.suggestions)))

    def test_respects_max_moments(self, settings):
        result = run(settings, max_moments=2)
        assert len(result.suggestions) <= 2

    def test_each_suggestion_has_title_and_reason(self, settings):
        for s in run(settings).suggestions:
            assert s.title
            assert s.reason
            assert s.hashtags

    def test_durations_within_bounds(self, settings):
        for s in run(settings).suggestions:
            assert 15.0 - 1e-6 <= s.duration <= 60.0 + 1e-6

    def test_no_overlap_between_suggestions(self, settings):
        ordered = sorted(run(settings).suggestions, key=lambda s: s.start)
        for a, b in zip(ordered, ordered[1:]):
            assert a.end <= b.start + 1e-6

    def test_empty_transcript_yields_nothing(self, settings):
        empty = Transcript(source_path="/tmp/x.mp4", language="en", segments=[])
        result = run(settings, transcript=empty)
        assert result.suggestions == []

    def test_safety_report_present(self, settings):
        assert run(settings).safety is not None

    def test_safety_can_be_disabled(self, settings):
        assert run(settings, safety=False).safety is None

    def test_content_can_be_disabled(self, settings):
        result = run(settings, content=False)
        assert all(not s.hashtags for s in result.suggestions)

    def test_result_saved_to_registry(self, settings):
        run(settings)
        files = list(settings.path("paths.registry").glob("*.suggestions.json"))
        assert files, "يجب حفظ المقترحات للرجوع إليها"


class TestClipRequestConversion:
    def test_to_clip_requests_all(self, settings):
        result = run(settings)
        requests = result.to_clip_requests()
        assert len(requests) == len(result.suggestions)

    def test_to_clip_requests_subset(self, settings):
        result = run(settings)
        requests = result.to_clip_requests([0])
        assert len(requests) == 1
        assert requests[0].start == pytest.approx(result.suggestions[0].start)

    def test_clip_request_timing_matches(self, settings):
        result = run(settings)
        for s, r in zip(result.suggestions, result.to_clip_requests()):
            assert r.start == pytest.approx(s.start)
            assert r.end == pytest.approx(s.end)

    def test_clip_names_are_filesystem_safe(self, settings):
        forbidden = set('/\\:*?"<>|\n\t،؟')
        for s in run(settings).suggestions:
            assert s.clip_name
            assert not (set(s.clip_name) & forbidden)

    def test_clip_names_are_unique(self, settings):
        names = [s.clip_name for s in run(settings).suggestions]
        assert len(names) == len(set(names))


class TestSerialization:
    def test_round_trip(self, settings, tmp_path):
        result = run(settings)
        path = tmp_path / "out.json"
        result.save(path)
        loaded = load_suggestions(path)
        assert len(loaded.suggestions) == len(result.suggestions)
        assert loaded.suggestions[0].title == result.suggestions[0].title
        assert loaded.suggestions[0].start == pytest.approx(result.suggestions[0].start)

    def test_saved_json_is_valid_utf8(self, settings, tmp_path):
        path = tmp_path / "out.json"
        run(settings).save(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["count"] == len(data["suggestions"])
        assert "\\u" not in path.read_text(encoding="utf-8")  # العربية مقروءة كما هي

    def test_suggestion_to_dict_has_duration(self):
        s = Suggestion(index=0, start=10.0, end=40.0, score=3.0, kind="story", reason="r")
        assert s.to_dict()["duration"] == pytest.approx(30.0)
