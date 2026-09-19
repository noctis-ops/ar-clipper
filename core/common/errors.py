"""أخطاء المشروع الموحّدة (Project-wide exception hierarchy).

كل وحدة ترمي خطأً مشتقاً من ``ArClipperError`` حتى تستطيع طبقة الـ CLI
التقاطها وعرض رسالة عربية واضحة بدل Traceback مربك.
"""

from __future__ import annotations


class ArClipperError(Exception):
    """الخطأ الأساسي لكل أخطاء AR-Clipper."""


class ConfigError(ArClipperError):
    """خطأ في ملف الإعدادات أو متغيرات البيئة."""


class DependencyError(ArClipperError):
    """أداة خارجية مفقودة (ffmpeg، yt-dlp، نموذج غير مثبّت...)."""


class IngestError(ArClipperError):
    """فشل في تحميل/قراءة الفيديو المصدر."""


class LicenseError(ArClipperError):
    """لم يتم تأكيد حقوق استخدام الفيديو (المبدأ 4: الالتزام بالحقوق)."""


class TranscribeError(ArClipperError):
    """فشل في مرحلة التفريغ الصوتي."""


class TranslateError(ArClipperError):
    """فشل في مرحلة الترجمة."""


class MediaError(ArClipperError):
    """فشل في أمر ffmpeg/ffprobe أو في معالجة الوسائط."""


class PipelineError(ArClipperError):
    """فشل عام في تنسيق خط الأنابيب."""
