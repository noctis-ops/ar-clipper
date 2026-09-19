"""محطة حذف الصمت."""

from .remover import SilenceResult, detect_silences, remap_transcript, remove_silence

__all__ = ["remove_silence", "detect_silences", "remap_transcript", "SilenceResult"]
