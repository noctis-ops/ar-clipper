"""اختبارات محطة التفريغ — بلا تحميل أي نموذج.

الوحدة كانت عند تغطية 15%. أوزان Whisper لا يمكن تحميلها في بيئة الاختبار،
لكن منطق الضبط (الجهاز، نوع الحساب، اختيار المحرك، التحقق من المدخلات)
قابل للاختبار بالكامل — وهو مصدر الأعطال العملية الأكثر شيوعاً.
"""

from __future__ import annotations

import pytest

from core.common.config import load_settings
from core.common.errors import TranscribeError
from core.transcribe.engine import (
    ENGINES,
    resolve_compute_type,
    resolve_device,
    transcribe,
)


class TestResolveDevice:
    def test_explicit_wins(self):
        assert resolve_device("cpu") == "cpu"
        assert resolve_device("cuda") == "cuda"

    def test_auto_returns_valid_device(self):
        assert resolve_device("auto") in {"cpu", "cuda"}

    def test_empty_is_treated_as_auto(self):
        assert resolve_device("") in {"cpu", "cuda"}

    def test_no_torch_falls_back_to_cpu(self, monkeypatch):
        """غياب torch يجب ألّا يُسقط التفريغ — CPU هو التراجع."""
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("torch غير مثبّت")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        assert resolve_device("auto") == "cpu"


class TestResolveComputeType:
    def test_explicit_wins(self):
        assert resolve_compute_type("float32", "cpu") == "float32"

    def test_cpu_defaults_to_int8(self):
        assert resolve_compute_type("auto", "cpu") == "int8"

    def test_gpu_defaults_to_float16(self):
        assert resolve_compute_type("auto", "cuda") == "float16"

    def test_empty_is_auto(self):
        assert resolve_compute_type("", "cpu") == "int8"


class TestTranscribeGuards:
    """الفحوص التي تقع قبل تحميل أي نموذج."""

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(TranscribeError, match="غير موجود"):
            transcribe(tmp_path / "لا-يوجد.mp4", settings=load_settings())

    def test_unknown_engine_raises(self, tmp_path):
        media = tmp_path / "x.mp4"
        media.write_bytes(b"fake")
        with pytest.raises(TranscribeError, match="غير معروف"):
            transcribe(media, engine="محرك-وهمي", settings=load_settings())

    def test_error_message_lists_available_engines(self, tmp_path):
        media = tmp_path / "x.mp4"
        media.write_bytes(b"fake")
        with pytest.raises(TranscribeError) as exc:
            transcribe(media, engine="nope", settings=load_settings())
        for name in ENGINES:
            assert name in str(exc.value)

    def test_known_engines_registered(self):
        assert "faster_whisper" in ENGINES
