"""أدوات خاصة بالنظام — صيغة الأوامر المعروضة للمستخدم.

المشكلة التي تحلّها: الوثائق والرسائل كانت تقترح ``ar-clipper <أمر>``، وهو
اسم غير مسجَّل كأمر نظام (لا يوجد pyproject.toml ينشئ نقطة دخول). المستخدم
ينسخ الاقتراح فيحصل على ``CommandNotFoundException``.

الصيغة الصحيحة تختلف بالنظام وبالصَدَفة:
  - PowerShell : .\\arc       (لا يشغّل من المجلد الحالي بلا مسار صريح)
  - cmd        : arc
  - لينكس/ماك  : ./arc.sh
"""

from __future__ import annotations

import os
import sys


def is_windows() -> bool:
    return sys.platform.startswith("win")


def in_powershell() -> bool:
    """يكتشف PowerShell عبر متغيّرات البيئة التي يضبطها حصراً."""
    if not is_windows():
        return False
    return bool(os.environ.get("PSModulePath"))


def launcher() -> str:
    """الصيغة التي يكتبها المستخدم فعلاً لتشغيل الأداة على نظامه."""
    if is_windows():
        return ".\\arc" if in_powershell() else "arc"
    return "./arc.sh"


def cmd(args: str = "") -> str:
    """يبني أمراً كاملاً جاهزاً للنسخ، مثل ``.\\arc quickstart``."""
    base = launcher()
    return f"{base} {args}".rstrip()
