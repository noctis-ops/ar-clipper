"""اختبارات تكامل حقيقية — تشغّل ffmpeg فعلياً على فيديو مولَّد.

لا تحتاج إنترنت ولا نماذج ذكاء اصطناعي: الترانسكربت يُحقن مباشرة، وهو
ممكن لأن ``run_pipeline`` تقبل ``transcript`` و ``source_video`` جاهزين.

للتخطي السريع:  pytest -m "not slow"
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.common.config import load_settings
from core.common.errors import MediaError
from core.common.ffmpeg import cut, extract_audio, probe, run_ffmpeg
from core.common.schemas import ClipRequest, Segment, SourceVideo, Transcript, Word
from core.pipeline import PipelineOptions, run_pipeline
from core.reframe.center import reframe
from core.silence.remover import detect_silences, remove_silence
from core.subtitles.builder import burn_subtitles, write_ass

pytestmark = pytest.mark.slow

SPEECH = [(2, 6), (10, 14)]
DURATION = 16


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory) -> Path:
    """فيديو 16:9 مدته 16 ثانية، فيه نافذتا كلام وفترات صمت بينهما."""
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    vol = "+".join(f"between(t,{a},{b})" for a, b in SPEECH)
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={DURATION}",
            "-f", "lavfi", "-i", f"sine=frequency=400:duration={DURATION}:sample_rate=44100",
            "-filter_complex", f"[1:a]volume='if({vol},0.9,0.0)':eval=frame[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ]
    )
    return path


@pytest.fixture(scope="module")
def transcript(sample_video) -> Transcript:
    rows = [
        (2.0, 6.0, "first spoken part", "الجزء المنطوق الأول"),
        (10.0, 14.0, "second spoken part", "الجزء المنطوق الثاني"),
    ]
    segs = []
    for i, (s, e, en, ar) in enumerate(rows):
        toks = en.split()
        step = (e - s) / len(toks)
        segs.append(
            Segment(
                id=i, start=s, end=e, text=en, translation=ar,
                words=[Word(t, s + j * step, s + (j + 1) * step, 0.9) for j, t in enumerate(toks)],
            )
        )
    return Transcript(
        source_path=str(sample_video), language="en", segments=segs,
        duration=float(DURATION), engine="fixture", model="fixture", translated_to="ar",
    )


@pytest.fixture(scope="module")
def source(sample_video) -> SourceVideo:
    info = probe(sample_video)
    return SourceVideo(
        path=str(sample_video), title="عينة", origin="local", duration=info.duration,
        width=info.width, height=info.height, fps=info.fps,
        license_note="عينة مولَّدة محلياً للاختبار", video_id="testvid00001",
    )


# ============================================================ أدوات ffmpeg


class TestFfmpegHelpers:
    def test_probe(self, sample_video):
        info = probe(sample_video)
        assert info.has_video and info.has_audio
        assert (info.width, info.height) == (1280, 720)
        assert info.duration == pytest.approx(DURATION, abs=0.5)

    def test_probe_missing_file(self, tmp_path):
        with pytest.raises(MediaError):
            probe(tmp_path / "nope.mp4")

    def test_extract_audio_16k_mono(self, sample_video, tmp_path):
        wav = extract_audio(sample_video, tmp_path / "a.wav")
        assert wav.exists() and wav.stat().st_size > 1000
        assert probe(wav).has_audio

    def test_cut_accurate(self, sample_video, tmp_path):
        out = cut(sample_video, tmp_path / "c.mp4", 2.0, 7.0, preset="ultrafast")
        assert probe(out).duration == pytest.approx(5.0, abs=0.4)

    def test_cut_invalid_range(self, sample_video, tmp_path):
        with pytest.raises(MediaError):
            cut(sample_video, tmp_path / "c.mp4", 7.0, 2.0)


# ============================================================ المحطات منفردة


class TestReframeStage:
    def test_outputs_9x16(self, sample_video, tmp_path):
        out = reframe(sample_video, tmp_path / "v.mp4", settings=load_settings())
        info = probe(out)
        assert (info.width, info.height) == (1080, 1920)

    def test_preserves_audio(self, sample_video, tmp_path):
        out = reframe(sample_video, tmp_path / "v.mp4", settings=load_settings())
        assert probe(out).has_audio


class TestSilenceStage:
    def test_detects_silence(self, sample_video):
        silences = detect_silences(sample_video, threshold_db=-34.0, min_silence_ms=500)
        assert len(silences) >= 2, "يجب كشف الفراغات بين نافذتي الكلام"

    def test_removes_and_shortens(self, sample_video, tmp_path):
        result = remove_silence(sample_video, tmp_path / "ns.mp4", settings=load_settings())
        assert result.applied
        assert result.removed_seconds > 1.0
        assert probe(result.output_path).duration < DURATION - 1.0

    def test_kept_ranges_cover_speech(self, sample_video, tmp_path):
        result = remove_silence(sample_video, tmp_path / "ns.mp4", settings=load_settings())
        for s_start, s_end in SPEECH:
            mid = (s_start + s_end) / 2
            assert any(s <= mid <= e for s, e in result.kept_ranges), f"فُقد الكلام عند {mid}s"


class TestBurnStage:
    def test_burns_without_error(self, sample_video, transcript, tmp_path):
        settings = load_settings()
        ass = write_ass(transcript, tmp_path / "s.ass", settings=settings)
        out = burn_subtitles(sample_video, ass, tmp_path / "burned.mp4", settings=settings)
        assert out.exists()
        assert probe(out).duration == pytest.approx(DURATION, abs=0.6)

    def test_missing_ass_raises(self, sample_video, tmp_path):
        with pytest.raises(MediaError):
            burn_subtitles(sample_video, tmp_path / "nope.ass", tmp_path / "o.mp4")


# ============================================================ خط الأنابيب الكامل


class TestFullPipeline:
    @pytest.fixture(scope="module")
    def result(self, sample_video, transcript, source):
        settings = load_settings()
        settings.ensure_dirs()
        results = run_pipeline(
            sample_video,
            [ClipRequest(start=1.0, end=15.0, name="itest")],
            options=PipelineOptions(
                license_note=source.license_note, translate=False, clip_name="itest"
            ),
            settings=settings,
            source_video=source,
            transcript=transcript,
        )
        return results[0]

    def test_produces_one_clip(self, result):
        assert Path(result.video_path).exists()

    def test_vertical_output(self, result):
        assert (result.width, result.height) == (1080, 1920)

    def test_silence_was_removed(self, result):
        assert result.duration < 14.0

    def test_all_stages_recorded(self, result):
        assert {"cut", "silence", "reframe", "subtitles", "burn", "export"} <= set(result.stages)

    def test_sidecar_subtitles_written(self, result):
        assert {"srt", "vtt", "ass"} <= set(result.subtitle_files)
        for path in result.subtitle_files.values():
            assert Path(path).stat().st_size > 0

    def test_subtitles_are_arabic(self, result):
        text = Path(result.subtitle_files["srt"]).read_text(encoding="utf-8")
        assert "الجزء المنطوق الأول" in text

    def test_subtitle_timing_rebased(self, result):
        text = Path(result.subtitle_files["srt"]).read_text(encoding="utf-8")
        first_start = text.split("\n")[1].split("-->")[0].strip()
        assert first_start.startswith("00:00:0")

    def test_metadata_written(self, result):
        meta = json.loads(Path(result.metadata_path).read_text(encoding="utf-8"))
        assert meta["clip_id"] == "itest"
        # يصير 3 حين تُنتج الصورة المصغّرة (المرحلة 3)، و1 بدونها
        assert meta["phase"] in (1, 3)
        assert meta["output"]["width"] == 1080

    def test_thumbnail_generated(self, result):
        """المرحلة 3: صورة مصغّرة تلقائية بجانب كل مقطع."""
        assert result.thumbnail_path, "لم تُولَّد صورة مصغّرة"
        thumb = Path(result.thumbnail_path)
        assert thumb.exists() and thumb.stat().st_size > 5_000

    def test_license_recorded(self, result):
        """المبدأ 4: سند الترخيص يجب أن يُحفظ مع كل مقطع."""
        meta = json.loads(Path(result.metadata_path).read_text(encoding="utf-8"))
        assert meta["source"]["license_note"]

    def test_clip_transcript_saved(self, result):
        t = Transcript.load(result.transcript_path)
        assert t.segments and all(s.translation for s in t.segments)

    def test_output_is_playable(self, result):
        info = probe(result.video_path)
        assert info.has_video and info.has_audio and info.duration > 0


class TestPipelineToggles:
    def test_no_reframe_keeps_dimensions(self, sample_video, transcript, source):
        settings = load_settings()
        results = run_pipeline(
            sample_video,
            [ClipRequest(start=2.0, end=6.0, name="noreframe")],
            options=PipelineOptions(
                license_note=source.license_note, translate=False, reframe=False,
                remove_silence=False, subtitles=False, clip_name="noreframe",
            ),
            settings=settings, source_video=source, transcript=transcript,
        )
        assert (results[0].width, results[0].height) == (1280, 720)

    def test_multiple_clips(self, sample_video, transcript, source):
        settings = load_settings()
        results = run_pipeline(
            sample_video,
            [ClipRequest(start=2.0, end=6.0), ClipRequest(start=10.0, end=14.0)],
            options=PipelineOptions(
                license_note=source.license_note, translate=False,
                remove_silence=False, subtitles=False,
            ),
            settings=settings, source_video=source, transcript=transcript,
        )
        assert len(results) == 2
        assert len({r.clip_id for r in results}) == 2
