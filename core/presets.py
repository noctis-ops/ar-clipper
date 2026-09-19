"""المسارات الجاهزة (Presets) — تقليل عدد الخيارات التي يحفظها المستخدم.

بدل تمرير عشرة أعلام، يختار المستخدم مساراً واحداً (``--preset campaign``)
فيُطبَّق مجموعة خيارات متناسقة مُختبَرة.

ترتيب الأولوية (الأعلى يفوز):
    أعلام CLI الصريحة  >  المسار الجاهز  >  config/settings.yaml
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .common.config import PROJECT_ROOT, Settings
from .common.errors import ConfigError

PRESETS_PATH = PROJECT_ROOT / "config" / "presets.yaml"


@dataclass
class Preset:
    """مسار جاهز: مجموعة خيارات + تجاوزات إعدادات."""

    key: str
    label: str
    description: str = ""
    options: Dict[str, Any] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def load_presets(path: str | None = None) -> Dict[str, Preset]:
    """يحمّل المسارات الجاهزة من ``config/presets.yaml``."""
    p = Path(path) if path else PRESETS_PATH
    if not p.exists():
        return {}

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"ملف المسارات الجاهزة غير صالح ({p}): {exc}") from exc

    out: Dict[str, Preset] = {}
    for key, body in raw.items():
        if not isinstance(body, dict):
            continue
        out[key] = Preset(
            key=key,
            label=str(body.get("label", key)),
            description=str(body.get("description", "")),
            options=dict(body.get("options") or {}),
            settings=dict(body.get("settings") or {}),
        )
    return out


def preset_names() -> List[str]:
    return list(load_presets())


def get_preset(key: str) -> Preset:
    presets = load_presets()
    found = presets.get((key or "").strip().lower())
    if found is None:
        raise ConfigError(
            f"مسار جاهز غير معروف: '{key}'.\n"
            f"المتاح: {', '.join(presets) or 'لا يوجد'}\n"
            "اعرضها بالتفصيل عبر:  ar-clipper presets"
        )
    return found


def apply_preset_to_settings(preset: Preset, settings: Settings) -> None:
    """يطبّق تجاوزات المسار على كائن الإعدادات (في الذاكرة فقط)."""
    for dotted, value in preset.settings.items():
        parts = dotted.split(".")
        node: Dict[str, Any] = settings.data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
