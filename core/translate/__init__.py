"""محطة الترجمة إلى العربية."""

from .engine import TRANSLATORS, build_translator, translate_transcript

__all__ = ["translate_transcript", "build_translator", "TRANSLATORS"]
