"""اختبارات أساس الاستخدام (Licensing) والمسارات الجاهزة (Presets)."""

from __future__ import annotations

import pytest

from core.common.errors import ConfigError, LicenseError
from core.ingest.licensing import (
    LICENSE_PRESETS,
    describe_presets,
    expand,
    is_publishable,
    preset_for,
    preset_keys,
    resolve,
)
from core.presets import apply_preset_to_settings, get_preset, load_presets


class TestExpand:
    def test_preset_expands_to_note(self):
        assert expand("campaign") == LICENSE_PRESETS["campaign"].note

    def test_case_insensitive(self):
        assert expand("CAMPAIGN") == expand("campaign")

    def test_detail_appended(self):
        out = expand("campaign:حملة فلان")
        assert out.startswith(LICENSE_PRESETS["campaign"].note)
        assert "حملة فلان" in out

    def test_free_text_passthrough(self):
        assert expand("إذن واتساب من المالك") == "إذن واتساب من المالك"

    def test_empty(self):
        assert expand("") == ""

    def test_all_presets_expand(self):
        for key in preset_keys():
            assert expand(key) and expand(key) != key


class TestResolve:
    def test_default_mode_uses_fallback(self):
        out = resolve("", mode="default", fallback="personal_test")
        assert out == LICENSE_PRESETS["personal_test"].note

    def test_default_mode_respects_explicit(self):
        assert resolve("campaign", mode="default") == LICENSE_PRESETS["campaign"].note

    def test_required_mode_blocks_empty(self):
        with pytest.raises(LicenseError):
            resolve("", mode="required")

    def test_required_mode_accepts_value(self):
        assert resolve("own_content", mode="required") == LICENSE_PRESETS["own_content"].note

    def test_off_mode_never_blocks(self):
        assert "معطّل" in resolve("", mode="off")

    def test_off_mode_still_records_given_value(self):
        assert resolve("campaign", mode="off") == LICENSE_PRESETS["campaign"].note

    def test_invalid_mode(self):
        with pytest.raises(LicenseError):
            resolve("campaign", mode="nonsense")

    def test_never_returns_empty(self):
        """مهما كان الوضع، لا بد من تسجيل قيمة ما (المبدأ 4)."""
        for mode in ("default", "off"):
            assert resolve("", mode=mode).strip()


class TestPublishable:
    def test_personal_test_not_publishable(self):
        assert is_publishable("personal_test") is False

    def test_campaign_publishable(self):
        assert is_publishable("campaign") is True

    def test_free_text_assumed_publishable(self):
        assert is_publishable("إذن خاص من المالك") is True

    def test_detail_does_not_change_flag(self):
        assert is_publishable("personal_test:تجربة") is False


class TestPresetMetadata:
    def test_describe_shape(self):
        for item in describe_presets():
            assert {"key", "label", "note", "publishable"} <= set(item)

    def test_preset_for_returns_none_on_free_text(self):
        assert preset_for("نص حر تماماً") is None

    def test_campaign_exists(self):
        """حملات Whop هي حالة الاستخدام الأساسية — يجب أن تكون حاضرة."""
        assert "campaign" in LICENSE_PRESETS


class TestPipelinePresets:
    def test_loads_from_yaml(self):
        assert load_presets(), "ملف presets.yaml يجب أن يُحمَّل"

    def test_expected_presets_exist(self):
        keys = set(load_presets())
        assert {"campaign", "fast", "quality"} <= keys

    def test_get_unknown_raises(self):
        with pytest.raises(ConfigError):
            get_preset("nope")

    def test_campaign_burns_arabic(self):
        p = get_preset("campaign")
        assert p.options["burn_subtitles"] is True
        assert p.options["subtitle_track"] == "ar"

    def test_no_subtitles_skips_transcription(self):
        """يجب ألّا يحتاج هذا المسار أي نموذج تفريغ."""
        p = get_preset("no_subtitles")
        assert p.options["subtitles"] is False
        assert p.options.get("snap_to_speech") is False

    def test_fast_is_actually_faster(self):
        fast, quality = get_preset("fast"), get_preset("quality")
        assert fast.settings["export.crf"] > quality.settings["export.crf"]

    def test_apply_overrides_settings(self):
        from core.common.config import load_settings

        settings = load_settings(force=True)
        apply_preset_to_settings(get_preset("quality"), settings)
        assert settings.get("export.crf") == 18
        assert settings.get("transcribe.model") == "medium"
        load_settings(force=True)  # استعادة

    def test_options_map_to_real_fields(self):
        """كل مفتاح في presets.yaml يجب أن يقابل حقلاً حقيقياً."""
        from core.pipeline import PipelineOptions

        for key, preset in load_presets().items():
            for opt in preset.options:
                assert hasattr(PipelineOptions(), opt), f"{key}: حقل غير موجود '{opt}'"
