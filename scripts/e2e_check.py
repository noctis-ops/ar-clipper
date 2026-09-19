#!/usr/bin/env python3
"""فحص شامل لخط أنابيب المرحلة 1 دون الحاجة لتحميل نماذج ذكاء اصطناعي.

يولّد فيديو اختباري عبر ffmpeg، يحقن ترانسكربتاً جاهزاً (بدل التفريغ)، ثم
يشغّل كامل مسار الوسائط: قص ← حذف صمت ← 9:16 ← ترجمة ASS ← حرق ← تصدير.

الفائدة: التحقق من سلامة خط الأنابيب في أي بيئة (CI مثلاً) بلا إنترنت
وبلا GPU وبلا تحميل نماذج.

التشغيل:  python scripts/e2e_check.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.common.config import load_settings  # noqa: E402
from core.common.ffmpeg import ffmpeg_bin, probe, run_ffmpeg  # noqa: E402
from core.common.logging_utils import setup_logging  # noqa: E402
from core.common.schemas import ClipRequest, Segment, SourceVideo, Transcript, Word  # noqa: E402
from core.pipeline import PipelineOptions, run_pipeline  # noqa: E402

SPEECH_WINDOWS = [(3, 8), (14, 19), (24, 28)]

ARABIC_LINES = [
    "أهلاً بكم في حلقة جديدة من البودكاست",
    "اليوم نتحدث عن بناء الأدوات المجانية",
    "كل ما تحتاجه موجود مفتوح المصدر",
    "والنتيجة احترافية بدون أي اشتراك شهري",
    "شكراً لمتابعتكم حتى نهاية الحلقة",
]

ENGLISH_LINES = [
    "Welcome back to another episode of the podcast",
    "Today we talk about building free tools",
    "Everything you need is open source already",
    "And the result looks professional without any subscription",
    "Thanks for watching until the very end",
]


def build_sample_video(path: Path, duration: int = 30) -> Path:
    """ينشئ فيديو 16:9 بصوت متقطع (كلام محاكى + فترات صمت)."""
    volume_expr = "+".join(f"between(t,{a},{b})" for a, b in SPEECH_WINDOWS)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=380:duration={duration}:sample_rate=44100",
            "-filter_complex", f"[1:a]volume='if({volume_expr},0.9,0.0)':eval=frame[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ]
    )
    return path


def build_fake_transcript(video: Path) -> Transcript:
    """ترانسكربت مُحقَن يحاكي ناتج WhisperX (جُمل + كلمات موقّتة + ترجمة)."""
    segments = []
    slots = [(2.0, 6.0), (6.5, 10.0), (13.0, 17.0), (17.5, 21.0), (24.0, 28.0)]
    for i, (start, end) in enumerate(slots):
        text = ENGLISH_LINES[i]
        tokens = text.split()
        step = (end - start) / max(1, len(tokens))
        words = [
            Word(text=tok, start=start + j * step, end=start + (j + 1) * step, score=0.95)
            for j, tok in enumerate(tokens)
        ]
        segments.append(
            Segment(id=i, start=start, end=end, text=text, words=words,
                    translation=ARABIC_LINES[i])
        )
    return Transcript(
        source_path=str(video),
        language="en",
        segments=segments,
        duration=probe(video).duration,
        engine="fixture",
        model="fixture",
        translated_to="ar",
        translate_engine="fixture",
    )


def main() -> int:
    setup_logging("INFO", force=True)
    settings = load_settings()
    settings.ensure_dirs()

    workdir = Path(tempfile.mkdtemp(prefix="arclipper_e2e_"))
    print(f"مجلد العمل المؤقت: {workdir}")
    checks: list[tuple[str, bool, str]] = []

    try:
        video_path = build_sample_video(workdir / "sample_16x9.mp4")
        info = probe(video_path)
        checks.append(
            ("توليد فيديو اختباري 1280x720", info.width == 1280 and info.height == 720,
             f"{info.width}x{info.height} / {info.duration:.1f}s")
        )

        transcript = build_fake_transcript(video_path)
        source = SourceVideo(
            path=str(video_path), title="عينة-اختبار", origin="local",
            duration=info.duration, width=info.width, height=info.height, fps=info.fps,
            license_note="عينة مولَّدة محلياً للاختبار — لا حقوق طرف ثالث",
            video_id="e2etest00001",
        )

        options = PipelineOptions(
            license_note=source.license_note,
            translate=False,           # الترجمة محقونة مسبقاً
            remove_silence=True,
            reframe=True,
            subtitles=True,
            burn_subtitles=True,
            clip_name="e2e-demo",
            keep_temp=False,
        )

        results = run_pipeline(
            video_path,
            [ClipRequest(start=2.0, end=22.0, name="e2e-demo")],
            options=options,
            settings=settings,
            source_video=source,
            transcript=transcript,
            progress=lambda s, m: print(f"   • [{s}] {m}"),
        )

        checks.append(("إنتاج مقطع واحد", len(results) == 1, f"عدد النتائج: {len(results)}"))
        result = results[0]
        out = Path(result.video_path)

        checks.append(("ملف الفيديو النهائي موجود", out.exists(), str(out)))
        checks.append(("حجم الملف > 0", out.stat().st_size > 10_000,
                       f"{out.stat().st_size / 1024:.0f} KB"))
        checks.append(("المقاس النهائي 1080x1920 (9:16)",
                       result.width == 1080 and result.height == 1920,
                       f"{result.width}x{result.height}"))
        checks.append(("مدة المقطع أقصر من المدى الأصلي (حُذف الصمت)",
                       0 < result.duration < 20.0, f"{result.duration:.2f}s من أصل 20s"))

        for fmt in ("srt", "vtt", "ass"):
            p = Path(result.subtitle_files.get(fmt, ""))
            checks.append((f"ملف الترجمة .{fmt}", p.exists() and p.stat().st_size > 0,
                           p.name if p.name else "مفقود"))

        srt_text = Path(result.subtitle_files["srt"]).read_text(encoding="utf-8")
        checks.append(("نص عربي داخل SRT", any(l in srt_text for l in ARABIC_LINES[:3]),
                       f"{len(srt_text.splitlines())} سطر"))
        checks.append(("توقيت SRT يبدأ من الصفر", "00:00:0" in srt_text, "rebase صحيح"))

        meta = json.loads(Path(result.metadata_path).read_text(encoding="utf-8"))
        checks.append(("سند الترخيص مسجَّل في البيانات الوصفية",
                       bool(meta.get("source", {}).get("license_note")),
                       meta.get("source", {}).get("license_note", "")[:40]))
        expected_stages = {"cut", "silence", "reframe", "subtitles", "burn", "export"}
        checks.append(("كل المحطات نُفّذت", expected_stages.issubset(set(meta["stages"])),
                       " → ".join(meta["stages"])))

        tmp_left = list(settings.path("paths.tmp").glob("e2e-demo*"))
        checks.append(("تنظيف الملفات الوسيطة", not tmp_left, f"متبقٍ: {len(tmp_left)}"))

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n" + "=" * 72)
    print("نتائج الفحص الشامل للمرحلة 1")
    print("=" * 72)
    failed = 0
    for label, passed, detail in checks:
        mark = "✅" if passed else "❌"
        if not passed:
            failed += 1
        print(f"{mark}  {label:<45} {detail}")
    print("=" * 72)
    print(f"نجح {len(checks) - failed}/{len(checks)} فحصاً.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
