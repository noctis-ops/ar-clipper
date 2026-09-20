"""نظام قوالب التصميم — المرحلة 3، الخطوة 3.

القالب = ملف JSON يصف الهوية البصرية كاملةً: الخط، الألوان، نمط الترجمة،
موقع الشعار. تبديل القالب يغيّر شكل المقطع كلياً بلا لمس الكود.

لماذا JSON وليس YAML؟ لأن القوالب يفترض أن يتبادلها المستخدمون ويشاركوها،
وJSON مدعوم في كل مكان بلا تبعية. أما إعدادات المشروع فتبقى YAML لأنها
تُحرَّر يدوياً وتحتاج تعليقات.

القالب يُطبَّق بالدمج فوق ``settings`` — لا يستبدلها. فأي مفتاح لا يذكره
القالب يبقى على قيمته الافتراضية.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common.config import Settings, load_settings
from ..common.errors import ArClipperError
from ..common.logging_utils import get_logger

log = get_logger(__name__)

# المفاتيح المسموح للقالب بتعديلها — حراسة تمنع قالباً من تغيير مسارات
# أو إعدادات تشغيل حسّاسة.
ALLOWED_SECTIONS = {"subtitles", "branding", "thumbnail", "reframe", "export"}


@dataclass
class Template:
    """قالب تصميم مُحمَّل."""

    key: str
    label: str = ""
    description: str = ""
    overrides: Dict[str, Any] = field(default_factory=dict)
    source_path: Optional[Path] = None

    def sections(self) -> List[str]:
        return sorted(self.overrides.keys())


def templates_dir(settings: Optional[Settings] = None) -> Path:
    settings = settings or load_settings()
    configured = settings.get("paths.templates", "config/templates")
    path = Path(configured)
    return path if path.is_absolute() else settings.root / path


def load_templates(settings: Optional[Settings] = None) -> Dict[str, Template]:
    """يحمّل كل القوالب من مجلد القوالب."""
    settings = settings or load_settings()
    directory = templates_dir(settings)
    found: Dict[str, Template] = {}
    if not directory.exists():
        return found

    for file in sorted(directory.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("قالب تالف سيُتجاهل (%s): %s", file.name, exc)
            continue

        key = str(data.get("key") or file.stem)
        overrides = {
            section: values
            for section, values in (data.get("overrides") or {}).items()
            if section in ALLOWED_SECTIONS and isinstance(values, dict)
        }
        found[key] = Template(
            key=key,
            label=str(data.get("label") or key),
            description=str(data.get("description") or ""),
            overrides=overrides,
            source_path=file,
        )
    return found


def get_template(key: str, settings: Optional[Settings] = None) -> Template:
    templates = load_templates(settings)
    if key not in templates:
        available = "، ".join(sorted(templates)) or "لا يوجد"
        raise ArClipperError(f"قالب غير معروف: {key}. المتاح: {available}")
    return templates[key]


def apply_template(template: Template, settings: Settings) -> Settings:
    """يدمج القالب فوق الإعدادات (تعديل موضعي) ويرجعها."""
    for section, values in template.overrides.items():
        target = settings.data.setdefault(section, {})
        if isinstance(target, dict):
            target.update(values)
    log.info("القالب المطبَّق: %s (%s)", template.key, ", ".join(template.sections()))
    return settings
