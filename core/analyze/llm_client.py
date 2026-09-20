"""عميل نموذج اللغة المحلي (LLM) — واجهة مجرّدة قابلة للاستبدال.

المحركات:
- ``ollama`` : الافتراضي — نموذج محلي عبر Ollama (مجاني، بلا إنترنت بعد التحميل).
- ``none``   : بلا نموذج إطلاقاً — يجعل المحطات تعتمد على الاستدلال فقط.
- ``api``    : (opt-in) خدمة متوافقة مع OpenAI بمفتاح المستخدم. ليست افتراضية
               أبداً، والنظام يعمل بكامل وظائفه بدونها (المبدأ 3).

أهم دالة هنا هي ``generate_json`` — النماذج الصغيرة كثيراً ما تحيط الـJSON
بشرح أو تغلّفه بسياج Markdown، فنحن نستخرجه ونصلحه بدل أن نفشل.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..common.config import Settings, load_settings
from ..common.errors import DependencyError, PipelineError
from ..common.logging_utils import get_logger

log = get_logger(__name__)

DEFAULT_TIMEOUT = 300


class LLMError(PipelineError):
    """فشل في الاتصال بنموذج اللغة أو في تفسير مخرجاته."""


# ============================================================ استخراج JSON


def extract_json(text: str) -> Any:
    """يستخرج أول كائن/مصفوفة JSON صالحة من نص حرّ.

    يتعامل مع الحالات الشائعة في مخرجات النماذج الصغيرة:
    - سياج Markdown: ```json ... ```
    - شرح قبل أو بعد الـJSON
    - فواصل زائدة قبل ] أو }
    """
    if not text or not text.strip():
        raise LLMError("النموذج أرجع نصاً فارغاً.")

    cleaned = text.strip()

    # إزالة سياج Markdown إن وُجد
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()

    # محاولة مباشرة
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # البحث عن أول بنية متوازنة الأقواس.
    # مهم: نبدأ بالقوس الذي يظهر أولاً في النص، وإلا فإن كائناً مثل
    # {"a": {"b": [1, 2]}} سيُقتطع منه [1, 2] فقط لأن "[" جُرّب أولاً.
    pairs = [("[", "]"), ("{", "}")]
    pairs.sort(
        key=lambda pc: cleaned.find(pc[0]) if cleaned.find(pc[0]) != -1 else len(cleaned) + 1
    )
    for opener, closer in pairs:
        start = cleaned.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    # إصلاح الفواصل الزائدة
                    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    raise LLMError(f"تعذّر استخراج JSON من مخرجات النموذج:\n{text[:400]}")


# ============================================================ الواجهة المجرّدة


class BaseLLM(ABC):
    """الواجهة الموحّدة لكل محركات نماذج اللغة."""

    name = "base"
    available = True

    @abstractmethod
    def generate(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        """يرجع نصاً حرّاً من النموذج."""

    def generate_json(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.1,
        retries: int = 2,
    ) -> Any:
        """يطلب JSON ويحاول مرة إضافية بصيغة أصرم عند الفشل."""
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                text = self.generate(prompt, system=system, temperature=temperature)
                return extract_json(text)
            except LLMError as exc:
                last_error = exc
                if attempt < retries:
                    log.warning("مخرجات غير صالحة (محاولة %d) — إعادة المحاولة.", attempt + 1)
                    prompt = (
                        prompt
                        + "\n\nمهم جداً: أرجع JSON صالحاً فقط، بلا أي شرح أو نص إضافي."
                    )
        raise LLMError(f"فشل الحصول على JSON بعد {retries + 1} محاولات: {last_error}")


# ============================================================ Ollama


class OllamaLLM(BaseLLM):
    """نموذج محلي عبر Ollama — الافتراضي."""

    name = "ollama"

    def __init__(self, settings: Settings):
        self.host = str(settings.get("analyze.ollama_host", "http://127.0.0.1:11434")).rstrip("/")
        self.model = str(settings.get("analyze.model", "qwen2.5:7b"))
        self.timeout = int(settings.get("analyze.timeout", DEFAULT_TIMEOUT))
        self.num_ctx = int(settings.get("analyze.num_ctx", 8192))

        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise DependencyError("مكتبة httpx مفقودة. نفّذ: pip install httpx") from exc

    # -------------------------------------------------- فحص التوفّر
    def health(self) -> tuple[bool, str]:
        """يفحص أن Ollama يعمل وأن النموذج محمّل."""
        import httpx

        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=5)
            r.raise_for_status()
        except Exception:
            return False, (
                f"Ollama لا يعمل على {self.host}.\n"
                "ثبّته من https://ollama.com ثم شغّله:  ollama serve"
            )

        models = [m.get("name", "") for m in r.json().get("models", [])]
        base = self.model.split(":")[0]
        if not any(m == self.model or m.startswith(base + ":") for m in models):
            return False, (
                f"النموذج '{self.model}' غير محمّل.\n"
                f"نزّله عبر:  ollama pull {self.model}\n"
                f"المتاح حالياً: {', '.join(models) or 'لا شيء'}"
            )
        return True, f"Ollama جاهز — النموذج {self.model}"

    def generate(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        import httpx

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
        }
        if system:
            payload["system"] = system

        try:
            r = httpx.post(f"{self.host}/api/generate", json=payload, timeout=self.timeout)
            r.raise_for_status()
        except Exception as exc:
            ok, msg = self.health()
            raise LLMError(msg if not ok else f"فشل الاتصال بـ Ollama: {exc}") from exc

        return str(r.json().get("response", "")).strip()


# ============================================================ بلا نموذج


class NoLLM(BaseLLM):
    """محرك فارغ — يجعل المحطات تسقط تلقائياً إلى الاستدلال."""

    name = "none"
    available = False

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings

    def generate(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        raise LLMError(
            "لا يوجد نموذج لغة مفعّل.\n"
            "فعّله بتثبيت Ollama:  ollama pull qwen2.5:7b\n"
            "أو استخدم الوضع الاستدلالي:  analyze.engine: heuristic"
        )


# ============================================================ API خارجي (opt-in)


class OpenAICompatLLM(BaseLLM):
    """خدمة متوافقة مع OpenAI — اختيارية بالكامل وبمفتاح المستخدم.

    ⚠️ ليست افتراضية ولا مطلوبة. النظام يعمل بكامل وظائفه بدونها.
    """

    name = "api"

    def __init__(self, settings: Settings):
        import os

        self.base_url = str(
            settings.get("analyze.api_base_url", "https://api.openai.com/v1")
        ).rstrip("/")
        self.model = str(settings.get("analyze.api_model", "gpt-4o-mini"))
        self.timeout = int(settings.get("analyze.timeout", DEFAULT_TIMEOUT))
        env_var = str(settings.get("analyze.api_key_env", "AR_CLIPPER_API_KEY"))
        self.api_key = os.getenv(env_var, "")
        if not self.api_key:
            raise LLMError(
                f"محرك API مفعّل لكن المفتاح غير موجود في متغير البيئة {env_var}.\n"
                "هذا الخيار اختياري تماماً — استخدم analyze.engine=ollama للعمل محلياً مجاناً."
            )

    def generate(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        import httpx

        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages, "temperature": temperature},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except Exception as exc:
            raise LLMError(f"فشل الاتصال بخدمة API: {exc}") from exc

        return str(r.json()["choices"][0]["message"]["content"]).strip()


ENGINES = {"ollama": OllamaLLM, "none": NoLLM, "api": OpenAICompatLLM}


def build_llm(
    settings: Optional[Settings] = None, *, engine: Optional[str] = None
) -> BaseLLM:
    """يبني عميل النموذج حسب الإعدادات."""
    settings = settings or load_settings()
    name = (engine or settings.get("analyze.llm_engine", "ollama")).strip().lower()
    cls = ENGINES.get(name)
    if cls is None:
        raise LLMError(f"محرك نموذج غير معروف: {name} (المتاح: {', '.join(ENGINES)})")
    return cls(settings)


def llm_available(settings: Optional[Settings] = None) -> tuple[bool, str]:
    """يفحص جاهزية النموذج دون رمي استثناء."""
    try:
        client = build_llm(settings)
    except Exception as exc:
        return False, str(exc)
    if isinstance(client, OllamaLLM):
        return client.health()
    return client.available, f"المحرك {client.name} جاهز"
