"""اختبارات المرحلة 3: تتبّع الوجه، الكاريوكي، الهوية البصرية، الصورة المصغّرة.

كلها بلا شبكة وبلا نماذج خارجية — تعتمد على منطق محلي وملفات مُولَّدة.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.common.config import load_settings as _load_settings


def load_settings():
    """نسخة معزولة (``_load_settings`` مُخزَّن بكاش ويرجع نفس الكائن)."""
    import copy

    return copy.deepcopy(_load_settings())


# ============================================================ تتبّع الوجه


class TestFaceTrackSmoothing:
    """التنعيم هو ما يمنع اهتزاز الكاميرا الافتراضية."""

    def _samples(self, xs, dt=0.33):
        from core.reframe.face_track import FaceSample

        return [FaceSample(time=i * dt, center_x=x, center_y=0.4) for i, x in enumerate(xs)]

    def test_removes_jitter(self):
        from core.reframe.face_track import smooth_track

        noisy = self._samples([0.3, 0.5, 0.3, 0.5, 0.3, 0.5, 0.3])
        smoothed = smooth_track(noisy)
        spread_before = max(s.center_x for s in noisy) - min(s.center_x for s in noisy)
        spread_after = max(s.center_x for s in smoothed) - min(
            s.center_x for s in smoothed
        )
        assert spread_after < spread_before / 2

    def test_enforces_max_step(self):
        """قفزة مفاجئة (كشف خاطئ) يجب ألّا تحرّك الكاميرا دفعةً واحدة."""
        from core.reframe.face_track import smooth_track

        jumpy = self._samples([0.2, 0.2, 0.9, 0.2, 0.2])
        smoothed = smooth_track(jumpy, window=1, max_step=0.04)
        steps = [
            abs(smoothed[i + 1].center_x - smoothed[i].center_x)
            for i in range(len(smoothed) - 1)
        ]
        assert max(steps) <= 0.0401, f"قفزة تجاوزت الحد: {max(steps)}"

    def test_preserves_real_movement(self):
        """الحركة الحقيقية يجب أن تُتبَع، لا أن تُلغى."""
        from core.reframe.face_track import smooth_track, track_spread

        moving = self._samples([0.2 + i * 0.02 for i in range(20)])
        smoothed = smooth_track(moving)
        assert track_spread(smoothed) > 0.25

    def test_empty_input_is_safe(self):
        from core.reframe.face_track import average_focus, smooth_track, track_spread

        assert smooth_track([]) == []
        assert track_spread([]) == 0.0
        assert average_focus([]) == (0.5, 0.5)

    def test_single_sample_passthrough(self):
        from core.reframe.face_track import smooth_track

        one = self._samples([0.7])
        assert len(smooth_track(one)) == 1


class TestDynamicCrop:
    """تعبير crop المتحرك الذي يُنفَّذ داخل ffmpeg."""

    def _samples(self, xs):
        from core.reframe.face_track import FaceSample

        return [FaceSample(time=i * 0.5, center_x=x, center_y=0.4) for i, x in enumerate(xs)]

    def test_returns_static_crop_when_no_samples(self):
        from core.reframe.face_track import build_dynamic_crop

        expr = build_dynamic_crop([], src_w=1280, src_h=720, crop_w=406, crop_h=720)
        assert expr.startswith("crop=406:720:")
        assert "if(" not in expr

    def test_builds_interpolated_expression(self):
        from core.reframe.face_track import build_dynamic_crop

        expr = build_dynamic_crop(
            self._samples([0.2, 0.5, 0.8]), src_w=1280, src_h=720, crop_w=406, crop_h=720
        )
        assert "if(lt(t," in expr
        assert expr.startswith("crop=406:720:'")

    def test_x_never_exceeds_bounds(self):
        """قص خارج حدود الإطار يُفشل ffmpeg — يجب التقييد دائماً."""
        import re

        from core.reframe.face_track import build_dynamic_crop

        expr = build_dynamic_crop(
            self._samples([0.0, 0.5, 1.0]), src_w=1280, src_h=720, crop_w=406, crop_h=720
        )
        max_x = 1280 - 406
        for value in re.findall(r"\((\d+)\+\(", expr):
            assert 0 <= int(value) <= max_x

    def test_expression_is_bounded_in_size(self):
        """تعبير ضخم يُبطئ ffmpeg — يجب تقليص النقاط."""
        from core.reframe.face_track import build_dynamic_crop

        many = self._samples([0.2 + (i % 50) * 0.01 for i in range(600)])
        expr = build_dynamic_crop(many, src_w=1280, src_h=720, crop_w=406, crop_h=720)
        assert expr.count("if(") <= 130


@pytest.mark.slow
class TestFaceTrackOnRealVideo:
    """تتبّع حقيقي على فيديو مُولَّد — يثبت أن opencv يعمل بلا تنزيل."""

    @pytest.fixture(scope="class")
    @staticmethod
    def video(tmp_path_factory):
        cv2 = pytest.importorskip("cv2")
        import numpy as np

        path = tmp_path_factory.mktemp("ft") / "moving.mp4"
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (640, 360)
        )
        # وجه تخطيطي بسيط يكفي Haar للكشف عنه بثبات
        for i in range(25 * 6):
            frame = np.full((360, 640, 3), 30, np.uint8)
            cx = int((0.25 + 0.5 * (i / (25 * 6))) * 640)
            cv2.circle(frame, (cx, 150), 60, (200, 190, 180), -1)
            cv2.circle(frame, (cx - 22, 135), 8, (20, 20, 20), -1)
            cv2.circle(frame, (cx + 22, 135), 8, (20, 20, 20), -1)
            cv2.ellipse(frame, (cx, 175), (25, 12), 0, 0, 180, (20, 20, 20), 3)
            writer.write(frame)
        writer.release()
        return path

    def test_track_runs_without_network(self, video):
        from core.reframe.face_track import track_faces

        result = track_faces(video, settings=load_settings())
        assert result.frames_scanned > 0
        assert result.engine in ("opencv", "none")

    def test_missing_file_is_handled(self, tmp_path):
        from core.reframe.face_track import track_faces

        result = track_faces(tmp_path / "لا-يوجد.mp4", settings=load_settings())
        assert result.found is False


class TestReframeModes:
    def test_face_track_is_accepted(self):
        """كان يرمي تحذير 'يأتي في المرحلة 3' — يجب أن يُقبل الآن."""
        import inspect

        from core.reframe.center import reframe

        source = inspect.getsource(reframe)
        assert "يأتي في المرحلة 3" not in source

    def test_unknown_mode_still_rejected(self, tmp_path):
        from core.common.errors import MediaError
        from core.reframe.center import reframe

        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        with pytest.raises(MediaError):
            reframe(video, tmp_path / "o.mp4", settings=load_settings(), mode="سحري")


# ============================================================ الكاريوكي


class TestKaraokeSubtitles:
    def _segment(self, words, start=0.0):
        from core.common.schemas import Segment, Word

        items = []
        t = start
        for w in words:
            items.append(Word(text=w, start=t, end=t + 0.4))
            t += 0.4
        return Segment(id=0, start=start, end=t, text=" ".join(words), words=items)

    def test_every_word_gets_a_k_tag(self):
        from core.subtitles.animated import build_karaoke_text

        text = build_karaoke_text(self._segment(["واحد", "اثنان", "ثلاثة"]))
        assert text.count("\\k") == 3

    def test_k_durations_match_segment_length(self):
        """مجموع مدد الكاريوكي يجب أن يساوي مدة الجملة، وإلا انزاح التلوين."""
        import re

        from core.subtitles.animated import build_karaoke_text

        segment = self._segment(["كلمة"] * 5)
        text = build_karaoke_text(segment)
        total_cs = sum(int(v) for v in re.findall(r"\\k(\d+)", text))
        assert total_cs == pytest.approx(segment.duration * 100, abs=6)

    def test_falls_back_when_no_word_timing(self):
        """بلا توقيت كلمات نوزّع المدة بالتساوي بدل الفشل."""
        from core.common.schemas import Segment
        from core.subtitles.animated import build_karaoke_text

        segment = Segment(id=0, start=0, end=3, text="نص بلا توقيت كلمات", words=[])
        text = build_karaoke_text(segment)
        assert "\\k" in text

    def test_empty_segment_returns_empty(self):
        from core.common.schemas import Segment
        from core.subtitles.animated import build_karaoke_text

        assert build_karaoke_text(Segment(id=0, start=0, end=1, text="", words=[])) == ""

    def test_keyword_gets_highlight_color(self):
        from core.subtitles.animated import build_karaoke_text

        text = build_karaoke_text(
            self._segment(["عادي", "مميزة"]),
            keywords={"مميزة"},
            highlight_color="&H0000D7FF",
            base_color="&H00FFFFFF",
        )
        assert "&H0000D7FF" in text
        assert "&H00FFFFFF" in text  # يعود للون الأساسي بعدها


class TestKeywordSelection:
    def test_stopwords_are_never_picked(self):
        from core.subtitles.animated import pick_keywords

        picked = pick_keywords(["في", "من", "على", "الذي", "هذا"])
        assert picked == set()

    def test_numbers_are_always_prioritized(self):
        from core.subtitles.animated import pick_keywords

        picked = pick_keywords(["كلمة", "أخرى", "2026", "شيء", "آخر", "نص"])
        assert "2026" in picked

    def test_ratio_limits_the_count(self):
        from core.subtitles.animated import pick_keywords

        words = [f"كلمةرقم{i}" for i in range(20)]
        picked = pick_keywords(words, max_ratio=0.25)
        assert len(picked) <= 6

    def test_normalize_strips_diacritics_and_punctuation(self):
        from core.subtitles.animated import normalize_word

        assert normalize_word("الذَّكاء،") == "الذكاء"
        assert normalize_word("...") == ""


class TestAnimatedAssOutput:
    def _transcript(self):
        from core.common.schemas import Segment, Transcript, Word

        segments = []
        for index in range(2):
            start = index * 4.0
            words = [
                Word(text=w, start=start + i * 0.5, end=start + (i + 1) * 0.5)
                for i, w in enumerate(["الذكاء", "الاصطناعي", "2026"])
            ]
            segments.append(
                Segment(
                    id=index,
                    start=start,
                    end=start + 1.5,
                    text="الذكاء الاصطناعي 2026",
                    words=words,
                )
            )
        return Transcript(source_path="x.mp4", language="ar", segments=segments)

    def test_animated_file_contains_karaoke(self, tmp_path):
        from core.subtitles.builder import write_ass

        path = write_ass(
            self._transcript(),
            tmp_path / "a.ass",
            track="source",
            settings=load_settings(),
            animated=True,
        )
        content = path.read_text(encoding="utf-8")
        assert "\\k" in content
        assert "\\fad(" in content

    def test_static_mode_has_no_karaoke(self, tmp_path):
        from core.subtitles.builder import write_ass

        path = write_ass(
            self._transcript(),
            tmp_path / "b.ass",
            track="source",
            settings=load_settings(),
            animated=False,
        )
        assert "\\k" not in path.read_text(encoding="utf-8")

    def test_secondary_colour_is_not_default_red(self):
        """اللون الثانوي الافتراضي في ASS أحمر صارخ — يشوّه الكاريوكي."""
        from core.subtitles.builder import build_ass_style

        style = build_ass_style(
            load_settings().section("subtitles"), play_res_x=1080, play_res_y=1920
        )
        assert "&H000000FF" not in style

    def test_events_do_not_overlap(self, tmp_path):
        from core.subtitles.builder import write_ass

        path = write_ass(
            self._transcript(),
            tmp_path / "c.ass",
            track="source",
            settings=load_settings(),
            animated=True,
        )
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")]
        assert len(lines) == 2


# ============================================================ الهوية البصرية


class TestBranding:
    def test_disabled_by_default(self):
        from core.design.branding import build_branding

        assert build_branding(settings=load_settings()).active is False

    def test_missing_logo_is_skipped_not_fatal(self, tmp_path):
        from core.design.branding import build_branding

        settings = load_settings()
        settings.data["branding"] = {
            "enabled": True,
            "logo_path": str(tmp_path / "لا-يوجد.png"),
            "handle": "",
        }
        plan = build_branding(settings=settings, work_dir=tmp_path)
        assert plan.extra_inputs == []

    def test_handle_creates_ass_overlay(self, tmp_path):
        """النص يُرسم بـASS لا drawtext — الأخير غير مُجمَّع ولا يشكّل العربية."""
        from core.design.branding import build_branding

        settings = load_settings()
        settings.data["branding"] = {"enabled": True, "logo_path": "", "handle": "@test"}
        plan = build_branding(settings=settings, work_dir=tmp_path)
        assert plan.active
        assert plan.text_ass and plan.text_ass.exists()
        assert "drawtext" not in " ".join(plan.filters)

    def test_part_badge_only_for_series(self, tmp_path):
        from core.design.branding import build_overlay_ass

        path = build_overlay_ass([("Badge", "الجزء 1/2")], tmp_path / "b.ass")
        assert "الجزء 1/2" in path.read_text(encoding="utf-8")

    def test_logo_filter_uses_named_output(self):
        from core.design.branding import BrandingPlan, build_logo_filter, compose_filter_graph

        plan = BrandingPlan(
            filters=[build_logo_filter("/x/logo.png", video_width=1080)],
            extra_inputs=["/x/logo.png"],
        )
        graph = compose_filter_graph(plan, "scale=1080:1920")
        assert graph.endswith("[vout]")
        assert "[0:v]" in graph

    def test_text_only_uses_simple_chain(self):
        """بلا شعار لا حاجة لـfilter_complex — سلسلة -vf أبسط وأسرع."""
        from core.design.branding import BrandingPlan, compose_filter_graph

        plan = BrandingPlan(filters=["ass='/x/b.ass'"])
        graph = compose_filter_graph(plan, "scale=1080:1920")
        assert "[vout]" not in graph
        assert graph == "scale=1080:1920,ass='/x/b.ass'"

    def test_inactive_plan_passes_base_through(self):
        from core.design.branding import BrandingPlan, compose_filter_graph

        assert compose_filter_graph(BrandingPlan(), "crop=1:1:0:0") == "crop=1:1:0:0"

    @pytest.mark.parametrize(
        "corner", ["top_left", "top_right", "bottom_left", "bottom_right", "bottom_center"]
    )
    def test_all_corners_produce_valid_expressions(self, corner):
        from core.design.branding import build_logo_filter

        f = build_logo_filter("/x.png", video_width=1080, corner=corner)
        assert "overlay=" in f


# ============================================================ الصورة المصغّرة


class TestArabicShaping:
    """Pillow لا يصل الحروف العربية — نتكفّل بذلك بلا تبعية."""

    def test_letters_are_connected(self):
        from core.design.thumbnail import shape_arabic

        shaped = shape_arabic("بسم")
        assert "ب" not in shaped  # استُبدل بشكل الاتصال
        assert len(shaped) == 3

    def test_lam_alef_becomes_one_glyph(self):
        from core.design.thumbnail import shape_arabic

        assert len(shape_arabic("لا")) == 1

    def test_digits_keep_their_order(self):
        """عكس الأرقام يقلب 2026 إلى 6202 — خطأ فادح في صورة مصغّرة."""
        from core.design.thumbnail import shape_arabic

        assert "2026" in shape_arabic("سنة 2026")

    def test_latin_is_not_reversed(self):
        from core.design.thumbnail import shape_arabic

        assert "AI" in shape_arabic("تقنية AI")

    def test_empty_and_plain_text(self):
        from core.design.thumbnail import shape_arabic

        assert shape_arabic("") == ""
        assert shape_arabic("hello") == "hello"

    def test_diacritics_are_stripped(self):
        from core.design.thumbnail import shape_arabic

        assert len(shape_arabic("مُحَمَّد")) < len("مُحَمَّد")


class TestFrameScoring:
    def test_dark_frame_is_penalized(self):
        from core.design.thumbnail import FrameScore

        bright = FrameScore(time=1, sharpness=300, brightness=120, face_area=0.2)
        dark = FrameScore(time=2, sharpness=300, brightness=10, face_area=0.2)
        assert bright.total > dark.total

    def test_face_beats_sharpness(self):
        """الوجه أهم من الحدّة لجذب النقر."""
        from core.design.thumbnail import FrameScore

        with_face = FrameScore(time=1, sharpness=100, brightness=120, face_area=0.25)
        no_face = FrameScore(time=2, sharpness=400, brightness=120, face_area=0.0)
        assert with_face.total > no_face.total

    def test_missing_video_returns_none(self, tmp_path):
        from core.design.thumbnail import pick_best_frame

        assert pick_best_frame(tmp_path / "لا-يوجد.mp4") is None


class TestPipelineWiring:
    """الخيارات الجديدة موصولة فعلاً بخط الأنابيب."""

    def test_options_exist_and_default_on(self):
        from core.pipeline import PipelineOptions

        options = PipelineOptions()
        assert options.branding is True
        assert options.thumbnail is True

    def test_clip_request_carries_part_and_hook(self):
        from core.common.schemas import ClipRequest

        request = ClipRequest(start=0, end=10, part=1, total_parts=3, hook="نص")
        assert request.part == 1
        assert request.hook == "نص"

    def test_clip_result_has_thumbnail_field(self):
        from core.common.schemas import ClipResult

        assert ClipResult(clip_id="a", video_path="b", start=0, end=1, duration=1).thumbnail_path is None

    def test_reframe_and_burn_accept_branding(self):
        import inspect

        from core.reframe.center import reframe
        from core.subtitles.builder import burn_subtitles

        assert "branding" in inspect.signature(reframe).parameters
        assert "branding" in inspect.signature(burn_subtitles).parameters

    def test_apply_filters_supports_extra_inputs(self):
        import inspect

        from core.common.ffmpeg import apply_filters

        assert "extra_inputs" in inspect.signature(apply_filters).parameters


# ============================================================ القوالب


class TestTemplates:
    """نظام القوالب — المرحلة 3، الخطوة 3."""

    def test_default_templates_load(self):
        from core.design.templates import load_templates

        found = load_templates(load_settings())
        assert len(found) >= 3, "يفترض وجود 3-5 قوالب افتراضية"
        assert "classic" in found

    def test_template_has_label_and_description(self):
        from core.design.templates import get_template

        tpl = get_template("classic", load_settings())
        assert tpl.label
        assert tpl.description

    def test_unknown_template_raises_with_hint(self):
        from core.common.errors import ArClipperError
        from core.design.templates import get_template

        with pytest.raises(ArClipperError) as exc:
            get_template("لا-يوجد", load_settings())
        assert "المتاح" in str(exc.value)

    def test_apply_merges_not_replaces(self):
        """القالب يعدّل ما يذكره فقط — الباقي يبقى على افتراضه."""
        from core.design.templates import apply_template, get_template

        settings = load_settings()
        settings.data["subtitles"]["rtl"] = True
        apply_template(get_template("bold_yellow", settings), settings)
        assert settings.get("subtitles.rtl") is True  # لم يمسّه القالب
        assert settings.get("subtitles.primary_color") == "#FFE81F"  # غيّره

    def test_disallowed_sections_are_ignored(self, tmp_path):
        """قالب يحاول تغيير المسارات أو الترخيص يجب أن يُرفض جزئياً."""
        import json

        from core.design.templates import load_templates

        directory = tmp_path / "templates"
        directory.mkdir()
        (directory / "evil.json").write_text(
            json.dumps(
                {
                    "key": "evil",
                    "overrides": {
                        "paths": {"clips": "/tmp/سرقة"},
                        "subtitles": {"font_size": 99},
                    },
                }
            ),
            encoding="utf-8",
        )
        settings = load_settings()
        settings.data["paths"]["templates"] = str(directory)
        tpl = load_templates(settings)["evil"]
        assert "paths" not in tpl.overrides
        assert tpl.overrides["subtitles"]["font_size"] == 99

    def test_corrupt_template_is_skipped_not_fatal(self, tmp_path):
        from core.design.templates import load_templates

        directory = tmp_path / "templates"
        directory.mkdir()
        (directory / "broken.json").write_text("{ليس JSON", encoding="utf-8")
        (directory / "fine.json").write_text('{"key":"fine"}', encoding="utf-8")
        settings = load_settings()
        settings.data["paths"]["templates"] = str(directory)
        found = load_templates(settings)
        assert "fine" in found
        assert "broken" not in found

    def test_missing_dir_returns_empty(self, tmp_path):
        from core.design.templates import load_templates

        settings = load_settings()
        settings.data["paths"]["templates"] = str(tmp_path / "لا-يوجد")
        assert load_templates(settings) == {}

    @pytest.mark.parametrize(
        "key", ["classic", "bold_yellow", "karaoke_pop", "minimal", "news"]
    )
    def test_each_template_is_valid(self, key):
        from core.design.templates import apply_template, get_template

        settings = load_settings()
        apply_template(get_template(key, settings), settings)
        assert int(settings.get("subtitles.font_size", 0)) > 0

    def test_templates_command_registered(self):
        from typer.testing import CliRunner

        from cli.main import app

        assert "templates" in CliRunner().invoke(app, ["--help"]).output


class TestVariantsAndCliFlags:
    def _help(self, *args):
        from typer.testing import CliRunner

        from cli.main import app

        return CliRunner().invoke(app, list(args)).output

    def test_phase3_flags_exist(self):
        out = self._help("clip", "--help")
        for flag in (
            "--face-track",
            "--animated-subs",
            "--template",
            "--variants",
            "--hook",
            "--part",
            "--no-thumbnail",
            "--no-branding",
        ):
            assert flag in out, f"العلم {flag} مفقود"

    def test_variants_requires_a_range(self, tmp_path):
        from typer.testing import CliRunner

        from cli.main import app

        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        result = CliRunner().invoke(app, ["clip", str(video), "--variants", "classic"])
        assert result.exit_code != 0

    def test_dry_run_shows_template(self, tmp_path):
        from typer.testing import CliRunner

        from cli.main import app

        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        result = CliRunner().invoke(
            app,
            ["clip", str(video), "-s", "0", "-e", "10", "--dry-run", "-T", "karaoke_pop"],
        )
        assert result.exit_code == 0
        assert "karaoke_pop" in result.output

    def test_no_subtitles_skips_transcription(self):
        """--no-subtitles يجب ألّا يجبر تحميل نموذج التفريغ."""
        import inspect

        from core.pipeline import run_pipeline

        source = inspect.getsource(run_pipeline)
        assert "wants_subtitles" in source


class TestSettingsIsolation:
    """``load_settings`` مُخزَّن بالكاش — تعديله مباشرةً يسرّب عالمياً.

    هذا ليس تفصيلاً: في ``ar-clipper serve`` مهمة بقالب "أصفر جريء" كانت
    ستفرض لونها على كل المهام التالية.
    """

    def test_clone_is_independent(self):
        from core.common.config import load_settings as real_load

        original = real_load()
        copy_ = original.clone()
        copy_.data["subtitles"]["font_size"] = 999
        assert real_load().get("subtitles.font_size") != 999

    def test_clone_is_deep(self):
        """نسخة سطحية لا تكفي: الأقسام قواميس متداخلة."""
        from core.common.config import load_settings as real_load

        copy_ = real_load().clone()
        assert copy_.data["subtitles"] is not real_load().data["subtitles"]

    def test_applying_template_does_not_leak(self):
        from core.common.config import load_settings as real_load
        from core.design.templates import apply_template, get_template

        before = real_load().get("subtitles.primary_color")
        scoped = real_load().clone()
        apply_template(get_template("bold_yellow", scoped), scoped)
        assert real_load().get("subtitles.primary_color") == before

    def test_cli_clones_before_mutating(self):
        import inspect

        from cli.main import clip_command

        assert "load_settings().clone()" in inspect.getsource(clip_command)

    def test_web_server_clones_per_job(self):
        from pathlib import Path

        source = Path("ui/server.py").read_text(encoding="utf-8")
        assert source.count("load_settings().clone()") >= 3


class TestWebUiPhase3:
    """ميزات المرحلة 3 متاحة في الواجهة، لا في CLI وحده."""

    @pytest.fixture
    @staticmethod
    def client():
        from fastapi.testclient import TestClient

        from ui.server import app

        return TestClient(app)

    def test_options_expose_templates(self, client):
        data = client.get("/api/options").json()
        assert "templates" in data
        keys = {t["key"] for t in data["templates"]}
        assert "karaoke_pop" in keys

    def test_clip_payload_accepts_phase3_fields(self):
        from ui.server import ClipPayload

        payload = ClipPayload(
            source="/x.mp4",
            template="news",
            face_track=True,
            animated_subs=True,
            hook="نص",
        )
        assert payload.template == "news"
        assert payload.face_track is True

    def test_payload_defaults_are_off(self):
        """الميزات الجديدة لا تُفرض على من لم يطلبها."""
        from ui.server import ClipPayload

        payload = ClipPayload(source="/x.mp4")
        assert payload.face_track is False
        assert payload.animated_subs is False
        assert payload.template == ""

    def test_html_exposes_phase3_controls(self):
        from pathlib import Path

        html = Path("ui/static/index.html").read_text(encoding="utf-8")
        for element in ("faceTrack", "animatedSubs", "template", "hook"):
            assert f'id="{element}"' in html, f"عنصر {element} مفقود من الواجهة"

    def test_results_include_thumbnail(self):
        from pathlib import Path

        source = Path("ui/server.py").read_text(encoding="utf-8")
        assert source.count('"thumbnail_path": r.thumbnail_path') >= 2
