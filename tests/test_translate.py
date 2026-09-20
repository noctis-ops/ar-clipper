"""اختبارات محطة الترجمة — بمترجمين وهميين، بلا تحميل NLLB.

الوحدة كانت عند تغطية 21% رغم أنها تحدّد جودة المخرج النهائي مباشرةً.
الاختبارات هنا تغطي المنطق (الدفعات، المحاذاة، اللغة نفسها، الفشل الجزئي)
دون الحاجة لأي نموذج أو إنترنت.
"""

from __future__ import annotations

import pytest

from core.common.config import Settings, load_settings
from core.common.errors import TranslateError
from core.common.schemas import Segment, Transcript
from core.translate import engine as E
from core.translate.engine import (
    BaseTranslator,
    PassthroughTranslator,
    to_nllb_code,
    translate_transcript,
)


class FakeTranslator(BaseTranslator):
    """يترجم بإضافة بادئة — يسمح بتتبّع المحاذاة بدقة."""

    name = "fake"

    def __init__(self, source_lang="en", target_lang="ar", settings=None, *, drop=0, fail=False):
        self.source_lang, self.target_lang = source_lang, target_lang
        self.settings = settings
        self.drop = drop
        self.fail = fail
        self.batches = []

    def translate_batch(self, texts):
        if self.fail:
            raise RuntimeError("فشل مُتعمَّد")
        self.batches.append(list(texts))
        out = [f"ع:{t}" for t in texts]
        return out[: len(out) - self.drop] if self.drop else out


def make_transcript(n=6, language="en"):
    return Transcript(
        source_path="/tmp/x.mp4",
        language=language,
        segments=[Segment(i, i * 5.0, i * 5.0 + 5.0, f"sentence {i}") for i in range(n)],
    )


@pytest.fixture
def patch_builder(monkeypatch):
    """يستبدل بناء المترجم بمترجم وهمي يمكن التحكم به."""

    def _apply(translator):
        monkeypatch.setattr(
            E, "build_translator", lambda s, t, engine=None, settings=None: translator
        )
        return translator

    return _apply


# ============================================================ رموز اللغات


class TestLanguageCodes:
    def test_arabic(self):
        assert to_nllb_code("ar").startswith("arb")

    def test_english(self):
        assert to_nllb_code("en").startswith("eng")

    def test_unknown_language_fails_loudly(self):
        # العقد: لغة غير معرّفة تُرفع كخطأ واضح بدل ترجمة خاطئة صامتة
        with pytest.raises(TranslateError, match="zz"):
            to_nllb_code("zz")

    def test_region_subtag_is_stripped(self):
        assert to_nllb_code("en-US") == to_nllb_code("en")


# ============================================================ المرور المباشر


class TestPassthrough:
    def test_returns_input_unchanged(self):
        t = PassthroughTranslator("en", "ar", load_settings())
        assert t.translate_batch(["a", "b"]) == ["a", "b"]


# ============================================================ الترجمة الكاملة


class TestTranslateTranscript:
    def test_fills_every_segment(self, patch_builder):
        patch_builder(FakeTranslator())
        out = translate_transcript(make_transcript(6), settings=load_settings())
        assert all(s.translation for s in out.segments)

    def test_alignment_is_preserved(self, patch_builder):
        """أهم ضمان: ترجمة الجملة i تخصّ الجملة i لا غيرها."""
        patch_builder(FakeTranslator())
        out = translate_transcript(make_transcript(9), settings=load_settings())
        for s in out.segments:
            assert s.translation == f"ع:sentence {s.id}"

    def test_timing_never_changes(self, patch_builder):
        patch_builder(FakeTranslator())
        before = [(s.start, s.end) for s in make_transcript(5).segments]
        out = translate_transcript(make_transcript(5), settings=load_settings())
        assert [(s.start, s.end) for s in out.segments] == before

    def test_marks_engine_and_target(self, patch_builder):
        patch_builder(FakeTranslator())
        out = translate_transcript(make_transcript(3), settings=load_settings())
        assert out.translated_to == "ar"
        assert out.translate_engine == "fake"

    def test_same_language_is_copied_not_translated(self, patch_builder):
        t = patch_builder(FakeTranslator())
        out = translate_transcript(make_transcript(4, language="ar"), settings=load_settings())
        assert out.translate_engine == "passthrough"
        assert not t.batches, "لا يجب استدعاء المترجم إطلاقاً"
        assert all(s.translation == s.text for s in out.segments)

    def test_empty_transcript_is_safe(self, patch_builder):
        patch_builder(FakeTranslator())
        empty = Transcript(source_path="/tmp/x.mp4", language="en", segments=[])
        assert translate_transcript(empty, settings=load_settings()).segments == []

    def test_batching_respects_size(self, patch_builder):
        t = patch_builder(FakeTranslator())
        settings = load_settings()
        settings.data.setdefault("translate", {})["batch_size"] = 3
        translate_transcript(make_transcript(7), settings=settings)
        assert [len(b) for b in t.batches] == [3, 3, 1]

    def test_engine_failure_raises_clear_error(self, patch_builder):
        patch_builder(FakeTranslator(fail=True))
        with pytest.raises(TranslateError):
            translate_transcript(make_transcript(3), settings=load_settings())


class TestPartialResults:
    """المحرك قد يُرجع نتائج أقل من المدخلات — لا يجوز ابتلاع ذلك صامتاً."""

    def test_missing_results_do_not_leave_none(self, patch_builder, caplog):
        patch_builder(FakeTranslator(drop=2))
        out = translate_transcript(make_transcript(5), settings=load_settings())
        assert all(s.translation for s in out.segments), "لا جملة بلا ترجمة"

    def test_mismatch_is_logged(self, patch_builder, caplog):
        patch_builder(FakeTranslator(drop=1))
        with caplog.at_level("WARNING"):
            translate_transcript(make_transcript(4), settings=load_settings())
        assert any("أرجع" in r.message or "نتيجة" in r.message for r in caplog.records)

    def test_untranslated_falls_back_to_source_text(self, patch_builder):
        patch_builder(FakeTranslator(drop=1))
        out = translate_transcript(make_transcript(4), settings=load_settings())
        # الأخيرة تعذّرت ترجمتها → تبقى بنصها الأصلي لا فارغة
        assert out.segments[-1].translation == "sentence 3"
