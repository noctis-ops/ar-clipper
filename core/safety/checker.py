"""فحص السلامة (Safety Check) — كلمات حساسة وأخطاء ترجمة محتملة.

طبقتان:
1. **قوائم كلمات** (بلا نموذج): ملف قابل للتوسعة في ``config/sensitive_words.yaml``.
2. **مراجعة النموذج** (اختياري): يكشف أخطاء الترجمة الجسيمة وانقلاب المعنى.

الهدف ليس الرقابة، بل تنبيهك **قبل** النشر إلى ما قد يُسقط مقطعاً من حملة
أو يُسيء للمعنى الأصلي.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..analyze.llm_client import BaseLLM, build_llm
from ..common.config import PROJECT_ROOT, Settings, load_settings
from ..common.logging_utils import get_logger
from ..common.schemas import Transcript
from ..common.text_utils import contains_arabic, normalize_text

log = get_logger(__name__)

WORDS_PATH = PROJECT_ROOT / "config" / "sensitive_words.yaml"


@dataclass
class SafetyIssue:
    """ملاحظة واحدة على المحتوى."""

    severity: str  # info | warning | critical
    category: str
    message: str
    segment_id: Optional[int] = None
    start: Optional[float] = None
    excerpt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "segment_id": self.segment_id,
            "start": self.start,
            "excerpt": self.excerpt,
        }


@dataclass
class SafetyReport:
    """تقرير الفحص الكامل."""

    issues: List[SafetyIssue] = field(default_factory=list)
    checked_segments: int = 0

    @property
    def has_critical(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)

    @property
    def warnings(self) -> List[SafetyIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checked_segments": self.checked_segments,
            "issue_count": len(self.issues),
            "has_critical": self.has_critical,
            "issues": [i.to_dict() for i in self.issues],
        }


# ============================================================ قوائم الكلمات


def load_word_lists(path: Optional[Path] = None) -> Dict[str, List[str]]:
    """يحمّل قوائم الكلمات الحساسة القابلة للتوسعة."""
    p = path or WORDS_PATH
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.warning("ملف الكلمات الحساسة غير صالح: %s", exc)
        return {}

    out: Dict[str, List[str]] = {}
    for category, words in data.items():
        if isinstance(words, list):
            out[str(category)] = [str(w).strip().lower() for w in words if str(w).strip()]
    return out


def check_words(transcript: Transcript, lists: Dict[str, List[str]]) -> List[SafetyIssue]:
    """يبحث عن الكلمات الحساسة في النص الأصلي والمترجم."""
    issues: List[SafetyIssue] = []
    severity_map = {
        "profanity": "warning",
        "sensitive_topics": "info",
        "platform_risk": "warning",
        "claims": "info",
    }

    for seg in transcript.segments:
        haystack = f"{seg.text} {seg.translation or ''}".lower()
        for category, words in lists.items():
            for word in words:
                if word and word in haystack:
                    issues.append(
                        SafetyIssue(
                            severity=severity_map.get(category, "info"),
                            category=category,
                            message=f"وردت كلمة/عبارة ضمن قائمة '{category}': «{word}»",
                            segment_id=seg.id,
                            start=seg.start,
                            excerpt=normalize_text(seg.text)[:120],
                        )
                    )
                    break  # تنبيه واحد لكل فئة في الجملة الواحدة
    return issues


# ============================================================ فحوص الترجمة


def check_translation_sanity(transcript: Transcript) -> List[SafetyIssue]:
    """فحوص بنيوية سريعة على جودة الترجمة (بلا نموذج)."""
    issues: List[SafetyIssue] = []
    if not transcript.translated_to:
        return issues

    for seg in transcript.segments:
        src = normalize_text(seg.text)
        tgt = normalize_text(seg.translation or "")
        if not src:
            continue

        if not tgt:
            issues.append(
                SafetyIssue("warning", "translation", "جملة بلا ترجمة.", seg.id, seg.start, src[:120])
            )
            continue

        if transcript.translated_to == "ar" and not contains_arabic(tgt):
            issues.append(
                SafetyIssue(
                    "warning", "translation",
                    "الترجمة لا تحتوي حروفاً عربية — قد تكون فشلت.",
                    seg.id, seg.start, tgt[:120],
                )
            )
            continue

        # اختلاف طول جسيم يشير إلى بتر أو تكرار
        ratio = len(tgt) / max(1, len(src))
        if ratio < 0.25:
            issues.append(
                SafetyIssue(
                    "warning", "translation",
                    f"الترجمة أقصر بكثير من الأصل ({ratio:.0%}) — احتمال بتر.",
                    seg.id, seg.start, tgt[:120],
                )
            )
        elif ratio > 3.5:
            issues.append(
                SafetyIssue(
                    "info", "translation",
                    f"الترجمة أطول بكثير من الأصل ({ratio:.0%}) — احتمال تكرار.",
                    seg.id, seg.start, tgt[:120],
                )
            )

        # تكرار كلمة واحدة كثيراً = انهيار النموذج
        words = tgt.split()
        if len(words) >= 8:
            most = max(set(words), key=words.count)
            if words.count(most) / len(words) > 0.4:
                issues.append(
                    SafetyIssue(
                        "warning", "translation",
                        f"تكرار مفرط للكلمة «{most}» — احتمال خلل في الترجمة.",
                        seg.id, seg.start, tgt[:120],
                    )
                )
    return issues


# ============================================================ مراجعة النموذج


REVIEW_SYSTEM = (
    "أنت مدقّق ترجمة محترف من الإنجليزية إلى العربية. "
    "تكشف الأخطاء الجسيمة فقط (انقلاب المعنى، حذف مهم، ترجمة حرفية مضلّلة). "
    "تتجاهل اختلافات الأسلوب. تُجيب بصيغة JSON صالحة فقط."
)


def llm_review(
    transcript: Transcript, llm: BaseLLM, *, max_segments: int = 25
) -> List[SafetyIssue]:
    """مراجعة عيّنة من الترجمة عبر النموذج لكشف الأخطاء الجسيمة."""
    pairs = [
        (s.id, s.text, s.translation)
        for s in transcript.segments
        if s.translation and s.text
    ][:max_segments]
    if not pairs:
        return []

    body = "\n".join(f'{sid}. EN: {en}\n   AR: {ar}' for sid, en, ar in pairs)
    prompt = f"""راجع أزواج الترجمة التالية واكشف الأخطاء الجسيمة فقط.

{body}

أرجع JSON فقط:
[
  {{"id": 3, "severity": "warning", "message": "وصف الخطأ بالعربية"}}
]

أرجع مصفوفة فارغة [] إن كانت الترجمة سليمة. لا تُبلّغ عن اختلافات أسلوبية."""

    try:
        data = llm.generate_json(prompt, system=REVIEW_SYSTEM)
    except Exception as exc:
        log.warning("تعذّرت مراجعة الترجمة عبر النموذج: %s", exc)
        return []

    by_id = {s.id: s for s in transcript.segments}
    issues: List[SafetyIssue] = []
    for item in data if isinstance(data, list) else []:
        try:
            sid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        seg = by_id.get(sid)
        issues.append(
            SafetyIssue(
                severity=str(item.get("severity", "warning")),
                category="translation_review",
                message=str(item.get("message", "خطأ ترجمة محتمل")),
                segment_id=sid,
                start=seg.start if seg else None,
                excerpt=(seg.translation or "")[:120] if seg else "",
            )
        )
    return issues


# ============================================================ الواجهة العامة


def check_transcript(
    transcript: Transcript,
    *,
    settings: Optional[Settings] = None,
    use_llm: bool = False,
) -> SafetyReport:
    """يفحص الترانسكربت ويرجع تقريراً بكل الملاحظات."""
    settings = settings or load_settings()
    report = SafetyReport(checked_segments=len(transcript.segments))

    lists = load_word_lists()
    if lists:
        report.issues.extend(check_words(transcript, lists))

    report.issues.extend(check_translation_sanity(transcript))

    if use_llm:
        try:
            report.issues.extend(llm_review(transcript, build_llm(settings)))
        except Exception as exc:
            log.warning("تخطّي مراجعة النموذج: %s", exc)

    report.issues.sort(key=lambda i: (i.start if i.start is not None else 0.0))
    if report.issues:
        log.info(
            "فحص السلامة: %d ملاحظة (%d تحذير).",
            len(report.issues),
            len(report.warnings),
        )
    return report
