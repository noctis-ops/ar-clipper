"""اكتشاف الخطوط العربية عبر الأنظمة — ويندوز وماك ولينكس.

المشكلة التي تحلّها هذه الوحدة: المشروع كان يبحث عن الخطوط في مسارات لينكس
فقط (``/usr/share/fonts/...``). على ويندوز لا وجود لهذا المسار، ولا يأتي
``DejaVu Sans`` مثبّتاً افتراضياً — فكانت الصورة المصغّرة تتراجع إلى خط Pillow
الافتراضي الذي **لا يرسم الحروف العربية إطلاقاً** (مربّعات فارغة)، وكان
libass يتراجع لخط بديل قد لا يدعم العربية.

ويندوز يأتي بخطوط عربية ممتازة جاهزة (Segoe UI، Tahoma، Arial)، وماك كذلك
(Geeza Pro، Al Bayan). المطلوب فقط البحث في المكان الصحيح لكل نظام.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from .logging_utils import get_logger

log = get_logger(__name__)

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"


# الخطوط مرتّبة بالأفضلية: الأول = الأفضل للعربية على ذلك النظام.
# كل عنصر (اسم العائلة كما يعرفه libass، اسم الملف).
_WINDOWS_FONTS = [
    ("Segoe UI", "segoeui.ttf"),
    ("Segoe UI Bold", "segoeuib.ttf"),
    ("Tahoma", "tahoma.ttf"),
    ("Arial", "arial.ttf"),
    ("Arial Bold", "arialbd.ttf"),
    ("Times New Roman", "times.ttf"),
]

_MACOS_FONTS = [
    ("Geeza Pro", "GeezaPro.ttc"),
    ("Al Bayan", "AlBayan.ttc"),
    ("Arial", "Arial.ttf"),
    ("Helvetica", "Helvetica.ttc"),
]

_LINUX_FONTS = [
    ("Noto Naskh Arabic", "NotoNaskhArabic-Regular.ttf"),
    ("Amiri", "amiri-regular.ttf"),
    ("DejaVu Sans", "DejaVuSans-Bold.ttf"),
    ("DejaVu Sans", "DejaVuSans.ttf"),
    ("Liberation Sans", "LiberationSans-Regular.ttf"),
]


def font_directories() -> List[Path]:
    """مجلدات الخطوط المحتملة على النظام الحالي."""
    if IS_WINDOWS:
        import os

        roots = [
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
            # خطوط مثبّتة للمستخدم الحالي فقط (شائع على ويندوز 10/11)
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
        ]
        return [p for p in roots if str(p) and p.exists()]

    if IS_MACOS:
        roots = [
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts",
        ]
        return [p for p in roots if p.exists()]

    roots = [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path.home() / ".local" / "share" / "fonts",
    ]
    return [p for p in roots if p.exists()]


def _candidates() -> List[tuple]:
    if IS_WINDOWS:
        return _WINDOWS_FONTS
    if IS_MACOS:
        return _MACOS_FONTS
    return _LINUX_FONTS


@lru_cache(maxsize=8)
def find_font_file(preferred: str = "") -> Optional[Path]:
    """يعيد مسار أول خط متاح يدعم العربية، أو ``None``.

    ``preferred`` مسار صريح من الإعدادات — يُجرَّب أولاً دائماً.
    """
    if preferred:
        path = Path(preferred)
        if path.exists():
            return path
        log.warning("الخط المحدد غير موجود: %s — سيُبحث عن بديل.", preferred)

    directories = font_directories()
    for _family, filename in _candidates():
        for directory in directories:
            # البحث المباشر أسرع بكثير من rglob على مجلد ضخم
            direct = directory / filename
            if direct.exists():
                return direct
        # لينكس يوزّع الخطوط في مجلدات فرعية (dejavu/, truetype/...)
        if not IS_WINDOWS:
            for directory in directories:
                try:
                    found = next(directory.rglob(filename), None)
                except OSError:  # pragma: no cover - أذونات
                    continue
                if found:
                    return found
    return None


@lru_cache(maxsize=2)
def default_ass_font() -> str:
    """اسم عائلة الخط الذي يجب أن يُكتب في ملف ASS.

    libass يبحث عن الخط بالاسم عبر fontconfig (أو DirectWrite على ويندوز)،
    فنعطيه اسماً موجوداً فعلاً على هذا النظام بدل اسم ثابت قد لا يوجد.
    """
    directories = font_directories()
    for family, filename in _candidates():
        for directory in directories:
            if (directory / filename).exists():
                return family
        if not IS_WINDOWS:
            for directory in directories:
                try:
                    if next(directory.rglob(filename), None):
                        return family
                except OSError:  # pragma: no cover
                    continue
    # تراجع أخير: أسماء عامة يفهمها fontconfig على أي نظام
    return "Arial" if IS_WINDOWS else "DejaVu Sans"


def describe() -> dict:
    """تشخيص للعرض في ``doctor``."""
    found = find_font_file()
    return {
        "platform": "windows" if IS_WINDOWS else ("macos" if IS_MACOS else "linux"),
        "directories": [str(p) for p in font_directories()],
        "font_file": str(found) if found else None,
        "ass_family": default_ass_font(),
    }
