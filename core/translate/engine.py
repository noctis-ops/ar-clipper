"""محطة الترجمة (Translate) — ترجمة الترانسكربت إلى العربية محلياً.

المحركات المتاحة (كلها مجانية ومفتوحة المصدر وتعمل محلياً):
- ``nllb``        : NLLB-200 من Meta عبر transformers — الافتراضي، جودة جيدة.
- ``argos``       : Argos Translate — خفيف جداً، يعمل بدون GPU.
- ``passthrough`` : بدون ترجمة (نسخ النص الأصلي) — للاختبار أو للمحتوى العربي أصلاً.

كل محرك يرث ``BaseTranslator`` ويكفي تنفيذ ``translate_batch`` لإضافة محرك جديد.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Type

from ..common.config import Settings, load_settings
from ..common.errors import DependencyError, TranslateError
from ..common.logging_utils import get_logger
from ..common.schemas import Transcript
from ..common.text_utils import contains_arabic, normalize_text

log = get_logger(__name__)


# ============================================================ خرائط اللغات

#: تحويل رمز ISO-639-1 إلى رمز NLLB (Flores-200)
NLLB_LANG_CODES: Dict[str, str] = {
    "ar": "arb_Arab",
    "en": "eng_Latn",
    "fr": "fra_Latn",
    "es": "spa_Latn",
    "de": "deu_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "ru": "rus_Cyrl",
    "tr": "tur_Latn",
    "fa": "pes_Arab",
    "ur": "urd_Arab",
    "hi": "hin_Deva",
    "id": "ind_Latn",
    "ms": "zsm_Latn",
    "nl": "nld_Latn",
    "pl": "pol_Latn",
    "sv": "swe_Latn",
    "zh": "zho_Hans",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
}


def to_nllb_code(lang: str) -> str:
    lang = (lang or "en").split("-")[0].lower()
    code = NLLB_LANG_CODES.get(lang)
    if not code:
        raise TranslateError(
            f"اللغة '{lang}' غير معرّفة في خريطة NLLB. أضفها في NLLB_LANG_CODES."
        )
    return code


# ============================================================ الواجهة المجرّدة


class BaseTranslator(ABC):
    """الواجهة الموحّدة لكل محركات الترجمة."""

    name: str = "base"

    def __init__(self, source_lang: str, target_lang: str, settings: Settings):
        self.source_lang = (source_lang or "en").split("-")[0].lower()
        self.target_lang = (target_lang or "ar").split("-")[0].lower()
        self.settings = settings

    @abstractmethod
    def translate_batch(self, texts: List[str]) -> List[str]:
        """يترجم دفعة نصوص ويرجع قائمة بنفس الطول والترتيب."""

    def translate_one(self, text: str) -> str:
        return self.translate_batch([text])[0]


# ============================================================ passthrough


class PassthroughTranslator(BaseTranslator):
    """لا يترجم — يعيد النص كما هو (مفيد للمحتوى العربي أصلاً أو للاختبار)."""

    name = "passthrough"

    def translate_batch(self, texts: List[str]) -> List[str]:
        return list(texts)


# ============================================================ NLLB-200


class NLLBTranslator(BaseTranslator):
    """NLLB-200 (Meta) عبر transformers — يعمل محلياً بدون إنترنت بعد أول تحميل."""

    name = "nllb"

    def __init__(self, source_lang: str, target_lang: str, settings: Settings):
        super().__init__(source_lang, target_lang, settings)
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore
        except ImportError as exc:
            raise DependencyError(
                "الترجمة عبر NLLB تحتاج: pip install transformers torch sentencepiece\n"
                "أو استخدم محركاً أخف: translate.engine=argos"
            ) from exc

        model_id = str(settings.get("translate.nllb_model", "facebook/nllb-200-distilled-600M"))
        self.src_code = to_nllb_code(self.source_lang)
        self.tgt_code = to_nllb_code(self.target_lang)

        log.info("تحميل نموذج الترجمة: %s (%s → %s)", model_id, self.src_code, self.tgt_code)
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_id, src_lang=self.src_code)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
        except Exception as exc:
            raise TranslateError(
                f"تعذّر تحميل نموذج NLLB '{model_id}'. تأكد من الاتصال بالإنترنت لأول "
                f"تحميل، أو استخدم translate.engine=argos.\nالتفاصيل: {exc}"
            ) from exc

        try:
            import torch  # type: ignore

            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model.to(self.device)
            self.model.eval()
            self._torch = torch
        except Exception:  # pragma: no cover
            self.device = "cpu"
            self._torch = None

    def _target_token_id(self) -> int:
        tok = self.tokenizer
        # تغيّرت واجهة transformers عبر الإصدارات — نجرّب الطرق المعروفة بالترتيب
        for getter in (
            lambda: tok.convert_tokens_to_ids(self.tgt_code),
            lambda: tok.lang_code_to_id[self.tgt_code],  # type: ignore[attr-defined]
        ):
            try:
                tid = getter()
                if tid is not None and tid >= 0:
                    return int(tid)
            except Exception:
                continue
        raise TranslateError(f"تعذّر تحديد رمز اللغة الهدف: {self.tgt_code}")

    def translate_batch(self, texts: List[str]) -> List[str]:
        if not texts:
            return []
        inputs = self.tokenizer(
            texts, return_tensors="pt", padding=True, truncation=True, max_length=512
        )
        if self._torch is not None:
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            ctx = self._torch.no_grad()
        else:  # pragma: no cover
            from contextlib import nullcontext

            ctx = nullcontext()

        with ctx:
            generated = self.model.generate(
                **inputs,
                forced_bos_token_id=self._target_token_id(),
                max_length=512,
                num_beams=4,
            )
        return [
            normalize_text(t)
            for t in self.tokenizer.batch_decode(generated, skip_special_tokens=True)
        ]


# ============================================================ Argos Translate


class ArgosTranslator(BaseTranslator):
    """Argos Translate — خفيف جداً ويعمل بدون GPU."""

    name = "argos"

    def __init__(self, source_lang: str, target_lang: str, settings: Settings):
        super().__init__(source_lang, target_lang, settings)
        try:
            import argostranslate.package as package  # type: ignore
            import argostranslate.translate as translate_mod  # type: ignore
        except ImportError as exc:
            raise DependencyError(
                "Argos غير مثبّت. نفّذ: pip install argostranslate"
            ) from exc

        self._translate_mod = translate_mod
        installed = translate_mod.get_installed_languages()
        codes = {lang.code for lang in installed}
        if self.source_lang not in codes or self.target_lang not in codes:
            log.info("تثبيت حزمة لغة Argos: %s → %s", self.source_lang, self.target_lang)
            try:
                package.update_package_index()
                available = package.get_available_packages()
                match = next(
                    (
                        p
                        for p in available
                        if p.from_code == self.source_lang and p.to_code == self.target_lang
                    ),
                    None,
                )
                if match is None:
                    raise TranslateError(
                        f"لا توجد حزمة Argos للزوج {self.source_lang}→{self.target_lang}"
                    )
                package.install_from_path(match.download())
                installed = translate_mod.get_installed_languages()
            except TranslateError:
                raise
            except Exception as exc:
                raise TranslateError(f"فشل تثبيت حزمة Argos: {exc}") from exc

        by_code = {lang.code: lang for lang in installed}
        try:
            self._translation = by_code[self.source_lang].get_translation(
                by_code[self.target_lang]
            )
        except KeyError as exc:
            raise TranslateError(
                f"حزمة Argos غير متوفرة للزوج {self.source_lang}→{self.target_lang}"
            ) from exc

    def translate_batch(self, texts: List[str]) -> List[str]:
        return [normalize_text(self._translation.translate(t)) if t.strip() else t for t in texts]


TRANSLATORS: Dict[str, Type[BaseTranslator]] = {
    "nllb": NLLBTranslator,
    "argos": ArgosTranslator,
    "passthrough": PassthroughTranslator,
}


def build_translator(
    source_lang: str,
    target_lang: str = "ar",
    *,
    engine: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> BaseTranslator:
    settings = settings or load_settings()
    name = (engine or settings.get("translate.engine", "nllb")).strip().lower()
    cls = TRANSLATORS.get(name)
    if cls is None:
        raise TranslateError(
            f"محرك ترجمة غير معروف: {name} (المتاح: {', '.join(TRANSLATORS)})"
        )
    return cls(source_lang, target_lang, settings)


# ============================================================ الواجهة العامة


def translate_transcript(
    transcript: Transcript,
    *,
    target_lang: Optional[str] = None,
    engine: Optional[str] = None,
    settings: Optional[Settings] = None,
    skip_if_same_language: bool = True,
) -> Transcript:
    """يترجم كل جملة في الترانسكربت ويملأ الحقل ``translation``.

    التوقيت الأصلي يبقى كما هو تماماً — نترجم النص فقط.
    """
    settings = settings or load_settings()
    target = (target_lang or settings.get("translate.target_lang", "ar")).lower()
    source = (transcript.language or "en").split("-")[0].lower()

    if not transcript.segments:
        log.warning("الترانسكربت فارغ — تخطّي الترجمة.")
        return transcript

    # المحتوى بنفس لغة الهدف أصلاً → لا حاجة للترجمة
    if skip_if_same_language and source == target:
        log.info("لغة المصدر هي لغة الهدف (%s) — نسخ النص كما هو.", target)
        for seg in transcript.segments:
            seg.translation = seg.text
        transcript.translated_to = target
        transcript.translate_engine = "passthrough"
        return transcript

    translator = build_translator(source, target, engine=engine, settings=settings)
    batch_size = max(1, int(settings.get("translate.batch_size", 8)))
    segments = transcript.segments
    total = len(segments)

    log.info("بدء الترجمة (%s → %s) عبر %s لعدد %d جملة...", source, target, translator.name, total)

    for i in range(0, total, batch_size):
        chunk = segments[i : i + batch_size]
        texts = [normalize_text(s.text) for s in chunk]
        try:
            results = translator.translate_batch(texts)
        except Exception as exc:
            raise TranslateError(f"فشل ترجمة الدفعة عند الجملة {i}: {exc}") from exc

        # المحرك قد يُرجع عدداً مخالفاً لعدد المدخلات (يحدث مع NLLB عند نص
        # فارغ أو طويل جداً). zip الصامت كان يترك جملاً بلا ترجمة فتُعرض
        # بالإنجليزية داخل ملف عربي — نكشف الخلل بدل ابتلاعه.
        if len(results) != len(chunk):
            log.warning(
                "المترجم أرجع %d نتيجة مقابل %d جملة (الدفعة عند %d) — "
                "سيُعاد ترجمة الناقص جملةً جملة.",
                len(results),
                len(chunk),
                i,
            )
            fixed: List[str] = list(results[: len(chunk)])
            for seg in chunk[len(fixed) :]:
                try:
                    fixed.append(translator.translate_one(normalize_text(seg.text)))
                except Exception as exc:
                    log.warning("تعذّرت ترجمة الجملة %d (%s) — يبقى نصها الأصلي.", seg.id, exc)
                    fixed.append("")
            results = fixed

        for seg, out in zip(chunk, results):
            seg.translation = out or seg.text

        done = min(i + batch_size, total)
        if done % (batch_size * 5) == 0 or done == total:
            log.info("الترجمة: %d/%d جملة (%.0f%%)", done, total, 100 * done / total)

    transcript.translated_to = target
    transcript.translate_engine = translator.name

    if target == "ar":
        arabic_ratio = sum(
            1 for s in segments if s.translation and contains_arabic(s.translation)
        ) / max(1, total)
        if arabic_ratio < 0.5:
            log.warning(
                "تحذير: %.0f%% فقط من النتائج تحتوي حروفاً عربية — تحقق من جودة الترجمة.",
                arabic_ratio * 100,
            )

    log.info("اكتملت الترجمة لـ %d جملة.", total)
    return transcript
