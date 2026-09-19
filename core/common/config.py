"""تحميل الإعدادات (config/settings.yaml) مع دعم التجاوز عبر متغيرات البيئة.

قاعدة التجاوز:  ``ARCLIPPER__<SECTION>__<KEY>`` (غير حساس لحالة الأحرف).
مثال: ``ARCLIPPER__TRANSCRIBE__MODEL=base`` يغيّر ``transcribe.model``.

الإعدادات تُقرأ مرة واحدة وتُخزّن (cache)؛ استخدم ``load_settings(force=True)``
لإعادة القراءة داخل الاختبارات.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml

from .errors import ConfigError

ENV_PREFIX = "ARCLIPPER__"

#: جذر المستودع (يُحسب من موقع هذا الملف: core/common/config.py → ../../)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def _coerce(value: str) -> Any:
    """يحوّل نص متغير البيئة إلى نوع بايثون مناسب (bool/int/float/None/str)."""
    low = value.strip().lower()
    if low in {"null", "none", "~", ""}:
        return None
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _apply_env_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(ENV_PREFIX):
            continue
        parts = env_key[len(ENV_PREFIX) :].split("__")
        if len(parts) < 2:
            continue
        section, *rest = [p.lower() for p in parts]
        node = data.setdefault(section, {})
        if not isinstance(node, dict):
            continue
        for key in rest[:-1]:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                break
        else:
            node[rest[-1]] = _coerce(env_val)
    return data


@dataclass
class Settings:
    """غلاف بسيط حول قاموس الإعدادات مع وصول نقطي آمن."""

    data: Dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None
    root: Path = PROJECT_ROOT

    # -------------------------------------------------- وصول عام
    def get(self, dotted: str, default: Any = None) -> Any:
        """يقرأ قيمة عبر مسار نقطي، مثال: ``settings.get("export.crf", 20)``."""
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted, _MISSING)
        if value is _MISSING:
            raise ConfigError(f"إعداد مفقود في settings.yaml: {dotted}")
        return value

    def section(self, name: str) -> Dict[str, Any]:
        value = self.data.get(name, {})
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    # -------------------------------------------------- مسارات
    def path(self, dotted: str) -> Path:
        """يرجع مساراً مطلقاً؛ المسارات النسبية تُحسب من جذر المشروع."""
        raw = self.require(dotted)
        p = Path(str(raw)).expanduser()
        return p if p.is_absolute() else (self.root / p)

    def ensure_dirs(self) -> None:
        """ينشئ كل مجلدات البيانات المعرّفة في قسم ``paths``."""
        for key in self.section("paths"):
            self.path(f"paths.{key}").mkdir(parents=True, exist_ok=True)


class _Missing:
    pass


_MISSING = _Missing()
_CACHE: Settings | None = None


def load_settings(path: str | Path | None = None, force: bool = False) -> Settings:
    """يحمّل الإعدادات من YAML ويطبّق تجاوزات البيئة."""
    global _CACHE
    if _CACHE is not None and not force and path is None:
        return _CACHE

    settings_path = Path(path) if path else DEFAULT_SETTINGS_PATH
    if not settings_path.exists():
        raise ConfigError(f"ملف الإعدادات غير موجود: {settings_path}")

    try:
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - نادر
        raise ConfigError(f"ملف الإعدادات غير صالح ({settings_path}): {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("جذر settings.yaml يجب أن يكون قاموساً (mapping).")

    raw = _apply_env_overrides(raw)
    settings = Settings(data=raw, source_path=settings_path, root=PROJECT_ROOT)
    if path is None:
        _CACHE = settings
    return settings
