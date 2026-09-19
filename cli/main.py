"""AR-Clipper — واجهة سطر الأوامر (المرحلة 1).

الأمر الرئيسي:

    ar-clipper clip <المصدر> --start 00:12:30 --end 00:13:20 --license "إذن المالك"

أوامر مساعدة:

    ar-clipper doctor         فحص البيئة والتبعيات
    ar-clipper transcribe     تفريغ وترجمة فقط (بدون إنتاج فيديو)
    ar-clipper show           عرض ترانسكربت محفوظ مع التوقيت
    ar-clipper info           معلومات ملف وسائط
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

# السماح بالتشغيل المباشر: python cli/main.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.clip.cutter import preview_transcript
from core.common.config import load_settings
from core.common.errors import ArClipperError
from core.common.logging_utils import setup_logging
from core.common.schemas import ClipRequest, Transcript
from core.common.text_utils import human_duration, parse_timestamp
from core.pipeline import PipelineOptions, run_pipeline, stage_ingest, stage_transcript

app = typer.Typer(
    name="ar-clipper",
    help="أداة إعادة توظيف البودكاست بالذكاء الاصطناعي — محلية ومجانية بالكامل.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


# ============================================================ مساعدات العرض


def _progress(stage: str, message: str) -> None:
    icons = {
        "ingest": "📥",
        "transcribe": "📝",
        "translate": "🌐",
        "cut": "✂️",
        "silence": "🔇",
        "reframe": "📱",
        "subtitles": "💬",
        "burn": "🔥",
        "export": "📦",
    }
    console.print(f"  {icons.get(stage, '•')} [bold cyan]{stage}[/] — {message}")


def _fail(exc: Exception) -> None:
    console.print(Panel(str(exc), title="[bold red]خطأ[/]", border_style="red"))
    raise typer.Exit(code=1)


def _parse_ranges(
    start: Optional[str], end: Optional[str], ranges: Optional[List[str]]
) -> List[ClipRequest]:
    """يبني قائمة المقاطع من ‎--start/--end‎ أو من ‎--range "بداية-نهاية"‎ المتكرر."""
    requests: List[ClipRequest] = []

    for raw in ranges or []:
        text = str(raw).strip()
        sep = None
        for candidate in ("..", "-->", "~"):
            if candidate in text:
                sep = candidate
                break
        if sep is None and text.count("-") == 1 and ":" not in text:
            sep = "-"
        if sep is None:
            raise typer.BadParameter(
                f"صيغة المدى غير صحيحة: '{raw}'. استخدم مثلاً: --range 00:01:10..00:01:50"
            )
        a, b = text.split(sep, 1)
        requests.append(ClipRequest(start=parse_timestamp(a), end=parse_timestamp(b)))

    if start is not None and end is not None:
        requests.insert(0, ClipRequest(start=parse_timestamp(start), end=parse_timestamp(end)))
    elif (start is None) != (end is None):
        raise typer.BadParameter("يجب تمرير ‎--start‎ و ‎--end‎ معاً.")

    return requests


def _print_results(results) -> None:
    table = Table(title="المقاطع الناتجة", header_style="bold magenta", show_lines=True)
    table.add_column("المعرّف", style="cyan")
    table.add_column("المدة", justify="right")
    table.add_column("الأبعاد", justify="center")
    table.add_column("الملف", overflow="fold")
    table.add_column("المحطات", overflow="fold")

    for r in results:
        table.add_row(
            r.clip_id,
            human_duration(r.duration),
            f"{r.width}x{r.height}",
            r.video_path,
            " → ".join(r.stages),
        )
    console.print(table)
    for r in results:
        if r.subtitle_files:
            console.print(
                f"  [dim]ملفات الترجمة لـ {r.clip_id}: "
                f"{', '.join(sorted(r.subtitle_files))}[/]"
            )


# ============================================================ الأمر الرئيسي: clip


@app.command("clip", help="المسار الكامل: مصدر → مقطع عمودي 9:16 مترجم للعربية.")
def clip_command(
    source: str = typer.Argument(..., help="رابط فيديو (YouTube...) أو مسار ملف محلي."),
    start: Optional[str] = typer.Option(None, "--start", "-s", help="وقت البداية (00:01:30 أو 90)."),
    end: Optional[str] = typer.Option(None, "--end", "-e", help="وقت النهاية."),
    ranges: Optional[List[str]] = typer.Option(
        None, "--range", "-r", help="مدى إضافي بالصيغة: 00:01:10..00:01:50 (يمكن تكراره)."
    ),
    license_note: str = typer.Option(
        "", "--license", "-L", help="سند/مصدر الترخيص — إلزامي (المبدأ 4 في الوثيقة)."
    ),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="اسم مخصص للمقطع."),
    language: Optional[str] = typer.Option(
        None, "--language", "-l", help="لغة المصدر (en, ar...). الافتراضي: كشف تلقائي."
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="نموذج التفريغ: tiny|base|small|medium|large-v3."
    ),
    translate_engine: Optional[str] = typer.Option(
        None, "--translate-engine", help="محرك الترجمة: nllb|argos|passthrough."
    ),
    track: Optional[str] = typer.Option(
        None, "--track", help="نص الترجمة المعروض: ar|source|bilingual."
    ),
    no_translate: bool = typer.Option(False, "--no-translate", help="تعطيل الترجمة للعربية."),
    no_silence: bool = typer.Option(False, "--no-silence", help="تعطيل حذف الصمت."),
    no_reframe: bool = typer.Option(False, "--no-reframe", help="إبقاء الأبعاد الأصلية."),
    no_subtitles: bool = typer.Option(False, "--no-subtitles", help="بدون ترجمة مرئية."),
    no_burn: bool = typer.Option(
        False, "--no-burn", help="توليد ملفات الترجمة دون حرقها على الفيديو."
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="عرض الترانسكربت واختيار المدى تفاعلياً."
    ),
    fast_cut: bool = typer.Option(
        False, "--fast-cut", help="قص سريع بدون إعادة ترميز (أقل دقة على الإطار)."
    ),
    keep_temp: bool = typer.Option(False, "--keep-temp", help="الاحتفاظ بالملفات الوسيطة."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="سجلات تفصيلية."),
):
    setup_logging("DEBUG" if verbose else "INFO", force=True)
    settings = load_settings()

    options = PipelineOptions(
        license_note=license_note,
        language=language,
        transcribe_model=model,
        translate=not no_translate,
        translate_engine=translate_engine,
        remove_silence=not no_silence,
        reframe=not no_reframe,
        subtitles=not no_subtitles,
        burn_subtitles=not no_burn,
        subtitle_track=track,
        fast_cut=fast_cut,
        keep_temp=keep_temp,
        clip_name=name,
    )

    try:
        requests = _parse_ranges(start, end, ranges)

        if interactive or not requests:
            console.print(Panel("الوضع التفاعلي: سنفرّغ الفيديو أولاً ثم تختار المدى.", style="cyan"))
            video = stage_ingest(source, options, settings, _progress)
            transcript, _ = stage_transcript(video, options, settings, _progress)

            console.print(Panel("الترانسكربت (اختر المدى المطلوب)", style="green"))
            for line in preview_transcript(transcript):
                console.print(line)

            if not requests:
                s = typer.prompt("وقت البداية (مثال 00:01:30)")
                e = typer.prompt("وقت النهاية (مثال 00:02:10)")
                requests = [ClipRequest(start=parse_timestamp(s), end=parse_timestamp(e))]

            results = run_pipeline(
                source,
                requests,
                options=options,
                settings=settings,
                progress=_progress,
                transcript=transcript,
                source_video=video,
            )
        else:
            results = run_pipeline(
                source, requests, options=options, settings=settings, progress=_progress
            )

        console.print()
        _print_results(results)
        console.print("\n[bold green]✅ اكتملت المعالجة بنجاح.[/]")

    except ArClipperError as exc:
        _fail(exc)
    except typer.BadParameter:
        raise
    except KeyboardInterrupt:  # pragma: no cover
        console.print("\n[yellow]أُلغيت العملية بواسطة المستخدم.[/]")
        raise typer.Exit(code=130)


# ============================================================ transcribe


@app.command("transcribe", help="تفريغ وترجمة فقط، دون إنتاج فيديو.")
def transcribe_command(
    source: str = typer.Argument(..., help="رابط أو مسار ملف."),
    license_note: str = typer.Option("", "--license", "-L", help="سند الترخيص (إلزامي)."),
    language: Optional[str] = typer.Option(None, "--language", "-l"),
    model: Optional[str] = typer.Option(None, "--model", "-m"),
    no_translate: bool = typer.Option(False, "--no-translate"),
    translate_engine: Optional[str] = typer.Option(None, "--translate-engine"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    setup_logging("DEBUG" if verbose else "INFO", force=True)
    settings = load_settings()
    options = PipelineOptions(
        license_note=license_note,
        language=language,
        transcribe_model=model,
        translate=not no_translate,
        translate_engine=translate_engine,
    )
    try:
        video = stage_ingest(source, options, settings, _progress)
        transcript, path = stage_transcript(video, options, settings, _progress)
        console.print(
            Panel(
                f"اللغة: {transcript.language or 'غير معروفة'}\n"
                f"عدد الجمل: {len(transcript.segments)}\n"
                f"المدة: {human_duration(transcript.duration)}\n"
                f"الملف: {path}",
                title="[bold green]اكتمل التفريغ[/]",
            )
        )
    except ArClipperError as exc:
        _fail(exc)


# ============================================================ show


@app.command("show", help="عرض ترانسكربت محفوظ (JSON) مع التوقيت.")
def show_command(
    transcript_path: Path = typer.Argument(..., help="مسار ملف الترانسكربت JSON."),
    track: str = typer.Option("both", "--track", help="both|ar|source."),
    limit: Optional[int] = typer.Option(None, "--limit", help="عدد الجمل المعروضة."),
):
    try:
        transcript = Transcript.load(transcript_path)
    except Exception as exc:
        _fail(ArClipperError(f"تعذّرت قراءة الترانسكربت: {exc}"))
        return
    console.print(
        Panel(
            f"اللغة: {transcript.language} | الجمل: {len(transcript.segments)} | "
            f"المدة: {human_duration(transcript.duration)}",
            style="cyan",
        )
    )
    for line in preview_transcript(transcript, track=track, limit=limit):
        console.print(line)


# ============================================================ info


@app.command("info", help="عرض معلومات ملف وسائط.")
def info_command(path: Path = typer.Argument(..., help="مسار ملف الفيديو/الصوت.")):
    from core.common.ffmpeg import probe

    try:
        info = probe(path)
    except ArClipperError as exc:
        _fail(exc)
        return
    table = Table(header_style="bold magenta")
    table.add_column("الخاصية")
    table.add_column("القيمة")
    table.add_row("المسار", str(info.path))
    table.add_row("المدة", f"{human_duration(info.duration)} ({info.duration:.2f}s)")
    table.add_row("الأبعاد", f"{info.width}x{info.height}")
    table.add_row("معدل الإطارات", f"{info.fps:.2f}")
    table.add_row("ترميز الفيديو", info.video_codec or "—")
    table.add_row("ترميز الصوت", info.audio_codec or "—")
    console.print(table)


# ============================================================ doctor


@app.command("doctor", help="فحص البيئة والتبعيات والإعدادات.")
def doctor_command():
    setup_logging("WARNING", force=True)
    table = Table(title="فحص البيئة — AR-Clipper", header_style="bold magenta")
    table.add_column("المكوّن")
    table.add_column("الحالة", justify="center")
    table.add_column("التفاصيل", overflow="fold")

    ok, warn, bad = "[green]✅[/]", "[yellow]⚠️[/]", "[red]❌[/]"
    problems = 0

    table.add_row("Python", ok, sys.version.split()[0])

    # ffmpeg / ffprobe
    try:
        from core.common.ffmpeg import ffmpeg_bin, has_ffprobe

        table.add_row("ffmpeg", ok, ffmpeg_bin())
        table.add_row(
            "ffprobe",
            ok if has_ffprobe() else warn,
            "متوفر" if has_ffprobe() else "غير متوفر — سيُستخدم تحليل مخرجات ffmpeg كبديل",
        )
    except Exception as exc:
        problems += 1
        table.add_row("ffmpeg", bad, str(exc))

    # الحزم
    checks = [
        ("yt-dlp", "yt_dlp", True, "تحميل الفيديو من الروابط"),
        ("faster-whisper", "faster_whisper", True, "محرك التفريغ الافتراضي"),
        ("pysubs2", "pysubs2", False, "ترجمة ASS متقدمة (المرحلة 3)"),
        ("transformers", "transformers", False, "ترجمة NLLB-200"),
        ("torch", "torch", False, "مطلوب لـ NLLB / تسريع GPU"),
        ("argostranslate", "argostranslate", False, "محرك ترجمة خفيف بديل"),
        ("auto-editor", "auto_editor", False, "محرك حذف صمت بديل"),
    ]
    for label, module, required, note in checks:
        try:
            __import__(module)
            table.add_row(label, ok, note)
        except ImportError:
            if required:
                problems += 1
                table.add_row(label, bad, f"مفقود (مطلوب) — {note}")
            else:
                table.add_row(label, warn, f"غير مثبّت (اختياري) — {note}")

    # GPU
    try:
        import torch  # type: ignore

        cuda = torch.cuda.is_available()
        table.add_row(
            "GPU (CUDA)",
            ok if cuda else warn,
            torch.cuda.get_device_name(0) if cuda else "غير متوفر — ستعمل المعالجة على CPU",
        )
    except Exception:
        table.add_row("GPU (CUDA)", warn, "torch غير مثبّت — ستعمل المعالجة على CPU")

    # الإعدادات والمجلدات
    try:
        settings = load_settings()
        settings.ensure_dirs()
        table.add_row("settings.yaml", ok, str(settings.source_path))
        table.add_row(
            "مجلدات البيانات",
            ok,
            f"{settings.path('paths.clips')} (جاهزة)",
        )
        table.add_row(
            "الإعداد الحالي",
            ok,
            f"تفريغ={settings.get('transcribe.model')} | "
            f"ترجمة={settings.get('translate.engine')} | "
            f"مقاس={settings.get('reframe.width')}x{settings.get('reframe.height')}",
        )
    except Exception as exc:
        problems += 1
        table.add_row("settings.yaml", bad, str(exc))

    console.print(table)
    if problems:
        console.print(
            Panel(
                f"عدد المشاكل الحرجة: {problems}\nنفّذ: pip install -r requirements.txt",
                title="[bold red]يلزم إصلاح[/]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)
    console.print("\n[bold green]✅ البيئة جاهزة للمرحلة 1.[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
