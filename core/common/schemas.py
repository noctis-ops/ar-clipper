"""نماذج البيانات المشتركة بين محطات خط الأنابيب (Pipeline Stages).

كل محطة تستهلك/تنتج هذه الأنواع فقط — وهذا ما يجعل استبدال أي محطة
(مثلاً محرك تفريغ آخر) ممكناً دون كسر البقية (المبدأ 5 في الوثيقة).

الصيغة المخزَّنة على القرص هي JSON بنسخة (``schema_version``) حتى نتمكن من
التطوير لاحقاً دون كسر الملفات القديمة.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1


# ============================================================ الكلمة والجملة


@dataclass
class Word:
    """كلمة واحدة مع توقيتها (Word-level timestamp)."""

    text: str
    start: float
    end: float
    score: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Word":
        return cls(
            text=d.get("text", ""),
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 0.0)),
            score=d.get("score"),
        )


@dataclass
class Segment:
    """جملة/مقطع نصي مع توقيته، ونصه المترجم إن وُجد."""

    id: int
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)
    translation: Optional[str] = None
    speaker: Optional[str] = None  # يُملأ في المرحلة 2 (Diarization)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def display_text(self, track: str = "ar") -> str:
        """النص المطلوب عرضه حسب المسار: ar | source | bilingual."""
        if track == "source":
            return self.text
        if track == "bilingual":
            if self.translation and self.translation.strip() != self.text.strip():
                return f"{self.translation}\n{self.text}"
            return self.text
        return self.translation or self.text

    def shifted(self, offset: float) -> "Segment":
        """نسخة من الجملة مع إزاحة زمنية (تُستخدم بعد القص)."""
        return Segment(
            id=self.id,
            start=self.start + offset,
            end=self.end + offset,
            text=self.text,
            words=[Word(w.text, w.start + offset, w.end + offset, w.score) for w in self.words],
            translation=self.translation,
            speaker=self.speaker,
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }
        if self.translation is not None:
            d["translation"] = self.translation
        if self.speaker is not None:
            d["speaker"] = self.speaker
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Segment":
        return cls(
            id=int(d.get("id", 0)),
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 0.0)),
            text=d.get("text", ""),
            words=[Word.from_dict(w) for w in d.get("words", [])],
            translation=d.get("translation"),
            speaker=d.get("speaker"),
        )


# ============================================================ الترانسكربت


@dataclass
class Transcript:
    """ناتج محطة التفريغ: قائمة جُمل موقّتة + بيانات وصفية."""

    source_path: str
    language: str
    segments: List[Segment] = field(default_factory=list)
    duration: float = 0.0
    engine: str = ""
    model: str = ""
    translated_to: Optional[str] = None
    translate_engine: Optional[str] = None
    schema_version: int = SCHEMA_VERSION

    # -------------------------------------------------- مساعدات
    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def full_translation(self) -> str:
        return " ".join((s.translation or "").strip() for s in self.segments if s.translation)

    def slice(self, start: float, end: float, *, rebase: bool = True) -> "Transcript":
        """يرجع ترانسكربت فرعي يغطي المدى [start, end].

        ``rebase=True`` يعيد ضبط التوقيت ليبدأ من الصفر (مطلوب لملفات
        الترجمة المرافقة للمقطع المقصوص).
        """
        picked: List[Segment] = []
        for seg in self.segments:
            if seg.end <= start or seg.start >= end:
                continue
            new = Segment(
                id=len(picked),
                start=max(seg.start, start),
                end=min(seg.end, end),
                text=seg.text,
                words=[w for w in seg.words if w.end > start and w.start < end],
                translation=seg.translation,
                speaker=seg.speaker,
            )
            if rebase:
                new = new.shifted(-start)
                new.start = max(0.0, new.start)
                new.end = max(new.start, new.end)
                new.words = [
                    Word(w.text, max(0.0, w.start), max(0.0, w.end), w.score) for w in new.words
                ]
            new.id = len(picked)
            picked.append(new)

        return Transcript(
            source_path=self.source_path,
            language=self.language,
            segments=picked,
            duration=max(0.0, end - start),
            engine=self.engine,
            model=self.model,
            translated_to=self.translated_to,
            translate_engine=self.translate_engine,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "language": self.language,
            "duration": round(self.duration, 3),
            "engine": self.engine,
            "model": self.model,
            "translated_to": self.translated_to,
            "translate_engine": self.translate_engine,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transcript":
        return cls(
            source_path=d.get("source_path", ""),
            language=d.get("language", ""),
            segments=[Segment.from_dict(s) for s in d.get("segments", [])],
            duration=float(d.get("duration", 0.0)),
            engine=d.get("engine", ""),
            model=d.get("model", ""),
            translated_to=d.get("translated_to"),
            translate_engine=d.get("translate_engine"),
            schema_version=int(d.get("schema_version", SCHEMA_VERSION)),
        )

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Transcript":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)


# ============================================================ المصدر والمقطع


@dataclass
class SourceVideo:
    """ناتج محطة الإدخال (Ingest)."""

    path: str
    title: str = ""
    origin: str = "local"  # local | url
    url: Optional[str] = None
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    license_note: str = ""  # مصدر/سند الترخيص (إلزامي — المبدأ 4)
    video_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SourceVideo":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ClipRequest:
    """طلب إنتاج مقطع: مدى زمني + خيارات تجاوز اختيارية."""

    start: float
    end: float
    name: Optional[str] = None
    title: Optional[str] = None
    # المرحلة 3: رقم الجزء ضمن سلسلة، ونص الصورة المصغّرة
    part: Optional[int] = None
    total_parts: Optional[int] = None
    hook: Optional[str] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class ClipResult:
    """ناتج خط الأنابيب لمقطع واحد: الفيديو النهائي + الملفات المرافقة."""

    clip_id: str
    video_path: str
    start: float
    end: float
    duration: float
    subtitle_files: Dict[str, str] = field(default_factory=dict)
    transcript_path: Optional[str] = None
    metadata_path: Optional[str] = None
    source: Optional[SourceVideo] = None
    stages: List[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    # المرحلة 3: الصورة المصغّرة المولَّدة تلقائياً
    thumbnail_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.source:
            d["source"] = self.source.to_dict()
        return d
