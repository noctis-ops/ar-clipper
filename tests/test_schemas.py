"""اختبارات نماذج البيانات المشتركة (Transcript / Segment / Word)."""

from __future__ import annotations

import json

import pytest

from core.common.schemas import Segment, SourceVideo, Transcript, Word


def build_transcript() -> Transcript:
    segments = [
        Segment(
            id=0,
            start=0.0,
            end=5.0,
            text="hello world",
            words=[Word("hello", 0.0, 2.0, 0.9), Word("world", 2.0, 5.0, 0.95)],
            translation="مرحبا بالعالم",
        ),
        Segment(id=1, start=5.0, end=10.0, text="second one", translation="الجملة الثانية"),
        Segment(id=2, start=10.0, end=15.0, text="third one", translation="الجملة الثالثة"),
    ]
    return Transcript(
        source_path="/tmp/x.mp4",
        language="en",
        segments=segments,
        duration=15.0,
        engine="test",
        model="tiny",
        translated_to="ar",
    )


class TestSegment:
    def test_duration(self):
        assert Segment(0, 1.0, 4.5, "x").duration == pytest.approx(3.5)

    def test_display_text_tracks(self):
        seg = Segment(0, 0, 1, "hello", translation="مرحبا")
        assert seg.display_text("ar") == "مرحبا"
        assert seg.display_text("source") == "hello"
        assert seg.display_text("bilingual") == "مرحبا\nhello"

    def test_display_falls_back_to_source(self):
        assert Segment(0, 0, 1, "hello").display_text("ar") == "hello"

    def test_shifted(self):
        seg = Segment(0, 10.0, 12.0, "x", words=[Word("x", 10.0, 12.0)])
        moved = seg.shifted(-10.0)
        assert moved.start == pytest.approx(0.0)
        assert moved.words[0].start == pytest.approx(0.0)


class TestTranscriptSlice:
    def test_picks_overlapping_only(self):
        sub = build_transcript().slice(5.0, 10.0)
        assert len(sub.segments) == 1
        assert sub.segments[0].text == "second one"

    def test_rebases_to_zero(self):
        sub = build_transcript().slice(5.0, 15.0)
        assert sub.segments[0].start == pytest.approx(0.0)
        assert sub.segments[-1].end == pytest.approx(10.0)

    def test_no_rebase(self):
        sub = build_transcript().slice(5.0, 15.0, rebase=False)
        assert sub.segments[0].start == pytest.approx(5.0)

    def test_preserves_translation(self):
        sub = build_transcript().slice(0.0, 6.0)
        assert sub.segments[0].translation == "مرحبا بالعالم"

    def test_no_negative_times(self):
        sub = build_transcript().slice(2.0, 8.0)
        assert all(s.start >= 0 and s.end >= s.start for s in sub.segments)
        assert all(w.start >= 0 for s in sub.segments for w in s.words)

    def test_empty_range(self):
        assert build_transcript().slice(100.0, 200.0).segments == []

    def test_ids_are_sequential(self):
        sub = build_transcript().slice(0.0, 15.0)
        assert [s.id for s in sub.segments] == list(range(len(sub.segments)))


class TestSerialization:
    def test_roundtrip_in_memory(self):
        original = build_transcript()
        restored = Transcript.from_dict(original.to_dict())
        assert restored.language == original.language
        assert len(restored.segments) == len(original.segments)
        assert restored.segments[0].words[0].text == "hello"
        assert restored.segments[0].translation == "مرحبا بالعالم"

    def test_roundtrip_on_disk(self, tmp_path):
        path = tmp_path / "t.json"
        build_transcript().save(path)
        loaded = Transcript.load(path)
        assert len(loaded.segments) == 3
        # يجب أن يُحفظ بعربية مقروءة لا برموز \uXXXX
        assert "مرحبا" in path.read_text(encoding="utf-8")

    def test_json_is_valid(self, tmp_path):
        path = tmp_path / "t.json"
        build_transcript().save(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["translated_to"] == "ar"

    def test_full_text(self):
        t = build_transcript()
        assert t.full_text == "hello world second one third one"
        assert "مرحبا بالعالم" in t.full_translation


class TestSourceVideo:
    def test_roundtrip(self):
        v = SourceVideo(path="/a.mp4", title="t", license_note="إذن المالك")
        assert SourceVideo.from_dict(v.to_dict()).license_note == "إذن المالك"

    def test_ignores_unknown_keys(self):
        v = SourceVideo.from_dict({"path": "/a.mp4", "unknown_field": 1})
        assert v.path == "/a.mp4"
