"""إعادة صياغة العناوين والأوصاف — تحسين مؤجّل من المرحلة 2.

العنوان الأول الذي يولّده النظام صالح، لكن قد لا يكون الأقوى. هذه الوحدة
تعيد صياغته بأساليب مختلفة فتختار الأنسب لجمهورك بدل قبول أول اقتراح.

تعمل بالنموذج المحلي (Ollama) إن توفّر، وإلا تتراجع إلى قوالب عربية
محلية — فالميزة لا تتعطّل بلا نموذج.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..common.config import Settings, load_settings
from ..common.logging_utils import get_logger

log = get_logger(__name__)

# أساليب إعادة الصياغة — كل أسلوب يخدم جمهوراً مختلفاً
STYLES: Dict[str, str] = {
    "question": "سؤال يثير الفضول",
    "bold": "تصريح جريء ومباشر",
    "number": "قائمة أو رقم محدّد",
    "curiosity": "فجوة معرفية تدفع للمشاهدة",
    "benefit": "فائدة صريحة للمشاهد",
}

_PROMPTS = {
    "question": "حوّله إلى سؤال يثير فضول المشاهد",
    "bold": "اجعله تصريحاً جريئاً مباشراً بلا مبالغة كاذبة",
    "number": "أضف رقماً أو صيغة قائمة إن كان المحتوى يسمح",
    "curiosity": "اترك فجوة معرفية تدفع لإكمال المشاهدة",
    "benefit": "أبرز الفائدة الصريحة التي سيكسبها المشاهد",
}


@dataclass
class RewriteResult:
    original: str = ""
    variants: Dict[str, str] = field(default_factory=dict)
    generated_by: str = "template"

    @property
    def best(self) -> str:
        """أفضل بديل تقديرياً: الأقرب للطول المثالي (35-60 محرفاً)."""
        candidates = [v for v in self.variants.values() if v.strip()]
        if not candidates:
            return self.original
        return min(candidates, key=lambda t: abs(len(t) - 48))

    def to_dict(self) -> Dict[str, object]:
        return {
            "original": self.original,
            "variants": self.variants,
            "best": self.best,
            "generated_by": self.generated_by,
        }


def _clean(text: str) -> str:
    """ينظّف مخرج النموذج من الترقيم والاقتباس الزائد.

    حذر مقصود: نحذف ترقيم القوائم (``1.`` و``2)``) لا الأرقام المعنوية.
    «3 طرق لبدء مشروعك» عنوان مشروع والرقم جزء منه — النسخة الأولى كانت
    تمحوه فتُفقد أقوى صيغة عنوان.
    """
    text = (text or "").strip()
    # ترقيم قائمة = رقم متبوع بنقطة أو قوس، أو رمز تعداد
    text = re.sub(r"^\s*(?:\d+\s*[.)\]]|[-–—•*])\s+", "", text)
    text = text.strip('"\u201c\u201d\u00ab\u00bb\' ')
    # النموذج قد يضيف شرحاً بعد سطر فارغ
    return text.split("\n")[0].strip()[:120]


def _template_variants(title: str, *, hook: str = "") -> Dict[str, str]:
    """بدائل محلية بلا نموذج — تراجع آمن يعمل دائماً."""
    core = _clean(title) or _clean(hook)
    if not core:
        return {}

    stripped = re.sub(r"^(كيف|لماذا|ماذا|متى|أين)\s+", "", core).strip()
    variants = {
        "question": core if core.endswith("؟") else f"لماذا {stripped}؟",
        "bold": f"{stripped} — وهذا ما لا يقوله أحد",
        "curiosity": f"لن تصدّق ما حدث بعد {stripped[:40]}",
        "benefit": f"ما ستتعلّمه: {stripped}",
    }
    numbers = re.findall(r"\d+", core)
    if numbers:
        variants["number"] = core
    return {k: v for k, v in variants.items() if v and len(v) <= 120}


def rewrite_title(
    title: str,
    *,
    text: str = "",
    hook: str = "",
    settings: Optional[Settings] = None,
    styles: Optional[List[str]] = None,
    use_llm: bool = True,
) -> RewriteResult:
    """يعيد صياغة عنوان بأساليب متعددة."""
    settings = settings or load_settings()
    wanted = [s for s in (styles or list(STYLES)) if s in STYLES]
    result = RewriteResult(original=_clean(title))

    if use_llm and settings.get("content_gen.use_llm", True):
        try:
            from ..analyze.llm_client import build_llm, llm_available

            ready, _reason = llm_available(settings)
            if ready:
                llm = build_llm(settings)
                context = (text or "")[:600]
                instructions = "\n".join(
                    f"{i + 1}. {_PROMPTS[s]}" for i, s in enumerate(wanted)
                )
                prompt = (
                    "أعد صياغة عنوان مقطع قصير بالعربية الفصحى المبسّطة.\n"
                    f"العنوان الحالي: {result.original}\n"
                    + (f"سياق المقطع: {context}\n" if context else "")
                    + f"\nأعطني {len(wanted)} صيغ، واحدة لكل أسلوب:\n{instructions}\n\n"
                    "اكتب صيغة واحدة في كل سطر، بالترتيب نفسه، بلا ترقيم ولا شرح.\n"
                    "كل صيغة أقل من 60 حرفاً، بلا مبالغة كاذبة."
                )
                reply = llm.generate(prompt, temperature=0.7)
                lines = [_clean(l) for l in (reply or "").splitlines()]
                lines = [l for l in lines if len(l) > 8]
                if lines:
                    result.variants = {
                        style: line for style, line in zip(wanted, lines)
                    }
                    result.generated_by = "llm"
                    log.info("أُعيدت صياغة العنوان بـ%d بديل (نموذج).", len(result.variants))
                    return result
        except Exception as exc:
            log.debug("تعذّرت إعادة الصياغة بالنموذج: %s", exc)

    result.variants = {
        k: v for k, v in _template_variants(title, hook=hook).items() if k in wanted
    }
    if result.variants:
        log.info("أُعيدت صياغة العنوان بـ%d بديل (قوالب).", len(result.variants))
    return result


def rewrite_many(
    items: List[Dict[str, str]], *, settings: Optional[Settings] = None
) -> List[RewriteResult]:
    """يعيد صياغة عناوين عدة مقترحات."""
    return [
        rewrite_title(
            item.get("title", ""), text=item.get("text", ""),
            hook=item.get("hook", ""), settings=settings,
        )
        for item in items
    ]
