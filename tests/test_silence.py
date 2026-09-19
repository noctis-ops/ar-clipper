"""اختبارات منطق حذف الصمت وإعادة تعيين التوقيت (منطق صرف، بلا ffmpeg)."""

from __future__ import annotations

import pytest

from core.common.schemas import Segment, Transcript, Word
from core.silence.remover import invert_ranges, remap_time, remap_transcript


class TestInvertRanges:
    def test_no_silence_keeps_everything(self):
        assert invert_ranges([], 10.0) == [(0.0, 10.0)]

    def test_single_silence_in_middle(self):
        kept = invert_ranges([(4.0, 6.0)], 10.0, padding=0.0)
        assert kept == [(0.0, 4.0), (6.0, 10.0)]

    def test_padding_expands_speech(self):
        kept = invert_ranges([(4.0, 6.0)], 10.0, padding=0.2)
        assert kept[0][1] == pytest.approx(4.2)
        assert kept[1][0] == pytest.approx(5.8)

    def test_leading_silence(self):
        kept = invert_ranges([(0.0, 3.0)], 10.0, padding=0.0)
        assert kept == [(3.0, 10.0)]

    def test_trailing_silence(self):
        kept = invert_ranges([(7.0, 10.0)], 10.0, padding=0.0)
        assert kept == [(0.0, 7.0)]

    def test_multiple_silences(self):
        kept = invert_ranges([(2.0, 3.0), (5.0, 6.0), (8.0, 9.0)], 10.0, padding=0.0)
        assert len(kept) == 4

    def test_kept_ranges_sorted_and_disjoint(self):
        kept = invert_ranges([(2.0, 3.0), (5.0, 6.0)], 10.0, padding=0.1)
        for i in range(len(kept) - 1):
            assert kept[i][1] <= kept[i + 1][0]

    def test_zero_duration(self):
        assert invert_ranges([(1.0, 2.0)], 0.0) == []

    def test_drops_slivers(self):
        kept = invert_ranges([(0.0, 4.99), (5.0, 10.0)], 10.0, padding=0.0)
        assert all(e - s > 0.08 for s, e in kept)


class TestRemapTime:
    RANGES = [(0.0, 5.0), (8.0, 12.0)]  # حُذفت 5→8

    def test_before_cut_unchanged(self):
        assert remap_time(3.0, self.RANGES) == pytest.approx(3.0)

    def test_after_cut_shifted(self):
        # 9.0 تقع في الجزء الثاني: 5 ثوانٍ محفوظة + (9-8) = 6
        assert remap_time(9.0, self.RANGES) == pytest.approx(6.0)

    def test_inside_removed_gap_snaps_forward(self):
        assert remap_time(6.5, self.RANGES) == pytest.approx(5.0)

    def test_monotonic_non_decreasing(self):
        prev = -1.0
        for t in [i * 0.5 for i in range(30)]:
            cur = remap_time(t, self.RANGES)
            assert cur >= prev - 1e-9
            prev = cur

    def test_empty_ranges_identity(self):
        assert remap_time(7.0, []) == pytest.approx(7.0)

    def test_total_never_exceeds_kept_duration(self):
        total = sum(e - s for s, e in self.RANGES)
        assert remap_time(1000.0, self.RANGES) == pytest.approx(total)


class TestRemapTranscript:
    def make(self) -> Transcript:
        return Transcript(
            source_path="x",
            language="en",
            duration=12.0,
            segments=[
                Segment(0, 0.0, 4.0, "a", words=[Word("a", 0.0, 4.0)], translation="أ"),
                Segment(1, 5.5, 7.5, "b", words=[Word("b", 5.5, 7.5)], translation="ب"),  # محذوفة
                Segment(2, 8.5, 11.0, "c", words=[Word("c", 8.5, 11.0)], translation="ج"),
            ],
        )

    def test_drops_fully_removed_segments(self):
        out = remap_transcript(self.make(), [(0.0, 5.0), (8.0, 12.0)])
        assert [s.text for s in out.segments] == ["a", "c"]

    def test_shifts_surviving_segments(self):
        out = remap_transcript(self.make(), [(0.0, 5.0), (8.0, 12.0)])
        assert out.segments[1].start == pytest.approx(5.5)  # 5 + (8.5-8)

    def test_preserves_translation(self):
        out = remap_transcript(self.make(), [(0.0, 5.0), (8.0, 12.0)])
        assert out.segments[0].translation == "أ"

    def test_ids_resequenced(self):
        out = remap_transcript(self.make(), [(0.0, 5.0), (8.0, 12.0)])
        assert [s.id for s in out.segments] == [0, 1]

    def test_words_remapped(self):
        out = remap_transcript(self.make(), [(0.0, 5.0), (8.0, 12.0)])
        for seg in out.segments:
            for w in seg.words:
                assert seg.start - 0.01 <= w.start <= seg.end + 0.01

    def test_no_ranges_is_identity(self):
        original = self.make()
        assert len(remap_transcript(original, []).segments) == 3

    def test_duration_matches_kept_total(self):
        ranges = [(0.0, 5.0), (8.0, 12.0)]
        out = remap_transcript(self.make(), ranges)
        assert out.duration == pytest.approx(sum(e - s for s, e in ranges))

    def test_times_remain_ordered(self):
        out = remap_transcript(self.make(), [(0.0, 5.0), (8.0, 12.0)])
        for i in range(len(out.segments) - 1):
            assert out.segments[i].end <= out.segments[i + 1].start + 0.01
