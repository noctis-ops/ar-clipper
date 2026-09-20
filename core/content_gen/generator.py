"""توليد المحتوى النصي المرافق (Content Generation).

لكل مقطع مقترح نولّد:
- عنوان عربي جذاب (≤ 70 حرفاً — يناسب كل المنصات)
- وصف قصير
- هاشتاغات عربية وإنجليزية
- Hook: الجملة التي تُعرض في أول 3 ثوانٍ

يعمل بوضعين: عبر LLM محلي، أو استدلالياً بلا نموذج (مخرجات أبسط لكنها
قابلة للاستخدام فوراً).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..analyze.heuristics import MomentCandidate
from ..analyze.llm_client import BaseLLM, LLMError, build_llm, llm_available
from ..common.config import Settings, load_settings
from ..common.logging_utils import get_logger
from ..common.text_utils import contains_arabic, normalize_text

log = get_logger(__name__)

SYSTEM_PROMPT = (
    "أنت كاتب محتوى عربي متخصص في المقاطع القصيرة الفيروسية. "
    "تكتب عناوين تُوقف التمرير، بلغة عربية فصيحة مبسّطة بلا مبالغة رخيصة "
    "ولا عناوين مضلّلة. تُجيب بصيغة JSON صالحة فقط."
)

DEFAULT_HASHTAGS_AR = ["#بودكاست", "#مقاطع_مفيدة", "#تطوير_الذات"]
DEFAULT_HASHTAGS_EN = ["#podcast", "#shorts", "#viral"]


@dataclass
class ClipContent:
    """المحتوى النصي المرافق لمقطع واحد."""

    title: str = ""
    description: str = ""
    hashtags: List[str] = field(default_factory=list)
    hook: str = ""
    generated_by: str = "heuristic"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================ الوضع الاستدلالي


def _first_sentence(text: str, max_len: int = 90) -> str:
    text = normalize_text(text)
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?؟।])\s+", text)
    out = parts[0] if parts else text
    if len(out) > max_len:
        out = out[:max_len].rsplit(" ", 1)[0] + "…"
    return out.strip()


def _heuristic_content(moment: MomentCandidate) -> ClipContent:
    """توليد بلا نموذج — يعتمد على نص المقطع نفسه."""
    body = moment.translation or moment.text or ""
    hook = _first_sentence(body, 90)

    kind_titles = {
        "story": "قصة قد تغيّر نظرتك",
        "opinion": "رأي صريح قد لا يعجب الجميع",
        "question": "سؤال يستحق التفكير",
        "surprise": "معلومة مفاجئة",
        "number": "رقم سيصدمك",
        "insight": "فكرة تستحق التوقف",
        "dense": "لحظة مكثّفة",
    }
    title = _first_sentence(body, 62) or kind_titles.get(moment.kind, "مقطع مختار")

    return ClipContent(
        title=title,
        description=_first_sentence(body, 150),
        hashtags=DEFAULT_HASHTAGS_AR + DEFAULT_HASHTAGS_EN,
        hook=hook,
        generated_by="heuristic",
    )


# ============================================================ الوضع عبر النموذج


def _prompt(moment: MomentCandidate) -> str:
    body = (moment.translation or moment.text or "")[:2500]
    return f"""هذا نص مقطع من بودكاست (مدته {moment.duration:.0f} ثانية، نوعه: {moment.kind}):

\"\"\"{body}\"\"\"

اكتب له محتوى نشر بالعربية.

أرجع JSON فقط:
{{
  "title": "عنوان جذاب لا يتجاوز 70 حرفاً",
  "description": "وصف من سطر إلى سطرين",
  "hashtags": ["#وسم_عربي", "#وسم_آخر", "#english_tag"],
  "hook": "الجملة التي تُعرض في أول 3 ثوانٍ لإيقاف التمرير"
}}

شروط:
- العنوان والوصف والـ hook بالعربية الفصيحة المبسّطة.
- من 5 إلى 8 هاشتاغات، اخلط العربية والإنجليزية.
- لا تُبالغ ولا تَعِد بما ليس في المقطع (لا عناوين مضلّلة).
- الـ hook يجب أن يكون مأخوذاً من روح المقطع لا مخترعاً."""


def _llm_content(moment: MomentCandidate, llm: BaseLLM) -> ClipContent:
    data = llm.generate_json(_prompt(moment), system=SYSTEM_PROMPT, temperature=0.4)
    if not isinstance(data, dict):
        raise LLMError("النموذج لم يُرجع كائن JSON.")

    tags = data.get("hashtags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split() if t.strip()]
    clean_tags = []
    for t in tags:
        t = str(t).strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t.replace(" ", "_")
        clean_tags.append(t)

    title = normalize_text(str(data.get("title", "")))[:100]
    return ClipContent(
        title=title or _first_sentence(moment.translation or moment.text, 62),
        description=normalize_text(str(data.get("description", "")))[:300],
        hashtags=clean_tags[:10] or DEFAULT_HASHTAGS_AR + DEFAULT_HASHTAGS_EN,
        hook=normalize_text(str(data.get("hook", "")))[:200],
        generated_by=llm.name,
    )


# ============================================================ الواجهة العامة


def generate_content(
    moment: MomentCandidate,
    *,
    settings: Optional[Settings] = None,
    llm: Optional[BaseLLM] = None,
    use_llm: bool = True,
) -> ClipContent:
    """يولّد المحتوى النصي لمقطع واحد، مع تراجع آمن إلى الاستدلال."""
    settings = settings or load_settings()
    if not use_llm:
        return _heuristic_content(moment)

    try:
        client = llm or build_llm(settings)
        return _llm_content(moment, client)
    except Exception as exc:
        log.warning("تعذّر توليد المحتوى عبر النموذج (%s) — استخدام الاستدلال.", exc)
        return _heuristic_content(moment)


def generate_for_moments(
    moments: List[MomentCandidate],
    *,
    settings: Optional[Settings] = None,
    use_llm: bool = True,
) -> List[ClipContent]:
    """يولّد المحتوى لكل المقاطع، بإعادة استخدام نفس عميل النموذج."""
    settings = settings or load_settings()
    if not moments:
        return []

    client: Optional[BaseLLM] = None
    if use_llm:
        # فحص واحد للجاهزية بدل محاولة فاشلة (وإعادة محاولات) لكل مقطع
        ok, message = llm_available(settings)
        if ok:
            try:
                client = build_llm(settings)
            except Exception as exc:  # pragma: no cover - نادر
                log.warning("تعذّر بناء عميل النموذج (%s).", exc)
        else:
            log.info("النموذج غير متاح — توليد استدلالي للجميع.\n%s", message)

    out: List[ClipContent] = []
    for i, moment in enumerate(moments, start=1):
        log.info("توليد المحتوى للمقطع %d/%d...", i, len(moments))
        out.append(
            generate_content(
                moment, settings=settings, llm=client, use_llm=bool(client)
            )
        )
    return out
