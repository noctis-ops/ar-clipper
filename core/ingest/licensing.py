"""أساس الاستخدام (Licensing Basis) — قيم جاهزة بدل النص الحر الطويل.

الفلسفة:
المبدأ 4 في الوثيقة يطلب **تسجيل** أساس الاستخدام، ولا يطلب أن يكون
التسجيل مؤلماً. لذلك هنا:

- قائمة قيم مختصرة جاهزة (``campaign``، ``owner_permission``...).
- قيمة افتراضية تُضبط مرة واحدة في الإعدادات ثم لا تُكتب يومياً.
- ثلاثة أوضاع تشغيل: ``required`` (صارم) | ``default`` (مرن) | ``off`` (معطّل).
- النص الحر ما زال مقبولاً لمن يحتاج تفصيلاً.

الأهم: مهما كان الوضع، القيمة النهائية **تُسجَّل دائماً** في
``metadata.json`` لكل مقطع. الاحتكاك يزول، وأثر التوثيق يبقى.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..common.errors import LicenseError


@dataclass(frozen=True)
class LicensePreset:
    """قيمة جاهزة لأساس الاستخدام."""

    key: str
    label_ar: str
    note: str
    publishable: bool  # هل يسمح هذا الأساس بالنشر العلني؟


#: القيم الجاهزة. الترتيب هنا هو ترتيب العرض في الواجهة.
LICENSE_PRESETS: Dict[str, LicensePreset] = {
    "campaign": LicensePreset(
        key="campaign",
        label_ar="حملة clipping مرخّصة (Whop أو مشابه)",
        note="مرخّص ضمن شروط حملة clipping معلنة",
        publishable=True,
    ),
    "owner_permission": LicensePreset(
        key="owner_permission",
        label_ar="إذن صريح من صاحب المحتوى",
        note="إذن صريح من صاحب المحتوى",
        publishable=True,
    ),
    "own_content": LicensePreset(
        key="own_content",
        label_ar="محتوى من إنتاجي",
        note="محتوى من إنتاجي — كامل الحقوق",
        publishable=True,
    ),
    "cc_by": LicensePreset(
        key="cc_by",
        label_ar="رخصة مشاع إبداعي (مع نسب المصدر)",
        note="رخصة مشاع إبداعي — يلزم نسب المصدر",
        publishable=True,
    ),
    "fair_use_edu": LicensePreset(
        key="fair_use_edu",
        label_ar="اقتباس محدود تعليمي/نقدي",
        note="اقتباس محدود لغرض تعليمي أو نقدي",
        publishable=True,
    ),
    "personal_test": LicensePreset(
        key="personal_test",
        label_ar="استخدام شخصي/تجريبي — بدون نشر",
        note="استخدام شخصي وتجريبي — غير مُعدّ للنشر العلني",
        publishable=False,
    ),
}

VALID_MODES = ("required", "default", "off")


def preset_keys() -> List[str]:
    return list(LICENSE_PRESETS)


def describe_presets() -> List[Dict[str, object]]:
    """وصف القيم الجاهزة (تستهلكه الواجهة والـ CLI)."""
    return [
        {
            "key": p.key,
            "label": p.label_ar,
            "note": p.note,
            "publishable": p.publishable,
        }
        for p in LICENSE_PRESETS.values()
    ]


def expand(value: str) -> str:
    """يوسّع قيمة جاهزة إلى نصها الكامل، ويقبل تفصيلاً بعد نقطتين.

    أمثلة::

        "campaign"                 → "مرخّص ضمن شروط حملة clipping معلنة"
        "campaign:حملة فلان"        → "مرخّص ضمن شروط حملة clipping معلنة — حملة فلان"
        "إذن واتساب من المالك"      → يبقى كما هو (نص حر)
    """
    raw = (value or "").strip()
    if not raw:
        return ""

    head, _, detail = raw.partition(":")
    preset = LICENSE_PRESETS.get(head.strip().lower())
    if preset is None:
        return raw  # نص حر — يُقبل كما هو

    detail = detail.strip()
    return f"{preset.note} — {detail}" if detail else preset.note


def preset_for(value: str) -> Optional[LicensePreset]:
    """يرجع القيمة الجاهزة المطابقة إن وُجدت (أو None للنص الحر)."""
    head = (value or "").strip().partition(":")[0].strip().lower()
    return LICENSE_PRESETS.get(head)


def is_publishable(value: str) -> bool:
    """هل يسمح هذا الأساس بالنشر العلني؟ النص الحر يُعتبر مسموحاً."""
    preset = preset_for(value)
    return True if preset is None else preset.publishable


def resolve(
    provided: str,
    *,
    mode: str = "default",
    fallback: str = "personal_test",
) -> str:
    """يحدّد القيمة النهائية لأساس الاستخدام حسب وضع التشغيل.

    - ``required`` : لا بد أن يمرّر المستخدم قيمة صراحةً.
    - ``default``  : إن لم يمرّر شيئاً تُستخدم القيمة الافتراضية.
    - ``off``      : لا اشتراط إطلاقاً (يُسجَّل أنه معطّل).
    """
    mode = (mode or "default").strip().lower()
    if mode not in VALID_MODES:
        raise LicenseError(
            f"وضع ترخيص غير معروف: '{mode}'. المتاح: {', '.join(VALID_MODES)}"
        )

    given = (provided or "").strip()
    if given:
        return expand(given)

    if mode == "required":
        options = "، ".join(preset_keys())
        raise LicenseError(
            "مطلوب تحديد أساس الاستخدام قبل المعالجة (المبدأ 4 في الوثيقة).\n\n"
            f"أسرع طريقة — اختر كلمة واحدة:\n  --license {options}\n\n"
            "مثال:  --license campaign\n"
            "أو مع تفصيل:  --license \"campaign:حملة بودكاست فلان\"\n\n"
            "لتفادي كتابته في كل مرة، اضبطه مرة واحدة في config/settings.yaml:\n"
            "  ingest:\n"
            "    license_mode: default\n"
            "    default_license: campaign"
        )

    if mode == "off":
        return "غير مسجّل (الاشتراط معطّل في الإعدادات)"

    return expand(fallback) or "استخدام شخصي وتجريبي — غير مُعدّ للنشر العلني"
