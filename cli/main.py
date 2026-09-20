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
from core.ingest.licensing import describe_presets
from core.pipeline import PipelineOptions, run_pipeline, stage_ingest, stage_transcript
from core.presets import apply_preset_to_settings, get_preset, load_presets
from core.suggest import suggest_clips

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
    preset: Optional[str] = typer.Option(
        None, "--preset", "-p",
        help="مسار جاهز: campaign|fast|quality|arabic_source|subtitles_only|no_subtitles.",
    ),
    license_note: str = typer.Option(
        "", "--license", "-L",
        help="أساس الاستخدام: campaign|owner_permission|own_content|cc_by|"
             "fair_use_edu|personal_test (أو نص حر).",
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

    # المسار الجاهز يُطبَّق أولاً، ثم تتجاوزه أعلام CLI الصريحة
    options = PipelineOptions()
    if preset:
        try:
            chosen = get_preset(preset)
        except ArClipperError as exc:
            _fail(exc)
            return
        apply_preset_to_settings(chosen, settings)
        for key, value in chosen.options.items():
            if hasattr(options, key):
                setattr(options, key, value)
        console.print(f"[dim]المسار الجاهز: [bold]{chosen.key}[/] — {chosen.label}[/]")

    options.license_note = license_note
    options.clip_name = name
    options.keep_temp = keep_temp or options.keep_temp
    if language is not None:
        options.language = language
    if model is not None:
        options.transcribe_model = model
    if translate_engine is not None:
        options.translate_engine = translate_engine
    if track is not None:
        options.subtitle_track = track
    if no_translate:
        options.translate = False
    if no_silence:
        options.remove_silence = False
    if no_reframe:
        options.reframe = False
    if no_subtitles:
        options.subtitles = False
    if no_burn:
        options.burn_subtitles = False
    if fast_cut:
        options.fast_cut = True

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


# ============================================================ suggest


def _print_suggestions(result) -> None:
    """يعرض المقترحات في جدول مقروء."""
    table = Table(
        title=f"المقاطع المقترحة ({len(result.suggestions)})",
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("#", style="cyan", justify="center")
    table.add_column("التوقيت", justify="center")
    table.add_column("المدة", justify="center")
    table.add_column("النوع", justify="center")
    table.add_column("العنوان والسبب", overflow="fold")

    kinds = {
        "story": "📖 قصة", "opinion": "💭 رأي", "question": "❓ سؤال",
        "surprise": "⚡ مفاجأة", "number": "📊 رقم", "insight": "💡 فكرة",
        "dense": "🔥 ذروة", "general": "• عام",
    }

    for s in result.suggestions:
        part = f" [dim](جزء {s.part}/{s.total_parts})[/]" if s.part else ""
        speaker = f"\n[dim]المتحدث: {s.speaker}[/]" if s.speaker else ""
        hook = f"\n[dim]Hook: {s.hook}[/]" if s.hook else ""
        tags = f"\n[dim]{' '.join(s.hashtags[:5])}[/]" if s.hashtags else ""
        table.add_row(
            str(s.index),
            f"{human_duration(s.start)}\n{human_duration(s.end)}",
            human_duration(s.duration),
            kinds.get(s.kind, s.kind),
            f"[bold]{s.title or '(بلا عنوان)'}[/]{part}\n[dim]{s.reason}[/]{speaker}{hook}{tags}",
        )
    console.print(table)

    if result.speakers:
        console.print(
            f"[dim]المتحدثون: {'، '.join(f'{v}' for v in result.speakers.values())}[/]"
        )

    if result.safety and result.safety.issues:
        warns = result.safety.warnings
        if warns:
            panel = "\n".join(
                f"• [{human_duration(i.start or 0)}] {i.message}" for i in warns[:8]
            )
            console.print(
                Panel(panel, title="[bold yellow]ملاحظات قبل النشر[/]", border_style="yellow")
            )


@app.command("suggest", help="🤖 حلّل فيديو واقترح أفضل المقاطع تلقائياً (المرحلة 2).")
def suggest_command(
    source: str = typer.Argument(..., help="رابط فيديو أو مسار ملف محلي."),
    license_note: str = typer.Option("", "--license", "-L", help="أساس الاستخدام."),
    count: Optional[int] = typer.Option(None, "--count", "-c", help="عدد المقاطع المقترحة."),
    engine: Optional[str] = typer.Option(
        None, "--engine", help="محرك التحليل: heuristic|llm|hybrid."
    ),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="لغة المصدر."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="نموذج التفريغ."),
    no_translate: bool = typer.Option(False, "--no-translate", help="بدون ترجمة للعربية."),
    no_diarize: bool = typer.Option(False, "--no-diarize", help="بدون فصل متحدثين."),
    no_content: bool = typer.Option(False, "--no-content", help="بدون عناوين وهاشتاغات."),
    no_safety: bool = typer.Option(False, "--no-safety", help="بدون فحص السلامة."),
    produce: bool = typer.Option(
        False, "--produce", help="أنتج المقاطع مباشرةً بعد التحليل."
    ),
    pick: Optional[str] = typer.Option(
        None, "--pick", help="أنتج أرقاماً محددة فقط، مثال: 0,2,5"
    ),
    preset: str = typer.Option("campaign", "--preset", "-p", help="المسار الجاهز للإنتاج."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    setup_logging("DEBUG" if verbose else "INFO", force=True)
    settings = load_settings()

    options = PipelineOptions(
        license_note=license_note,
        language=language,
        transcribe_model=model,
        translate=not no_translate,
    )

    try:
        result = suggest_clips(
            source,
            options=options,
            settings=settings,
            progress=_progress,
            max_moments=count,
            analyze_engine=engine,
            diarize=False if no_diarize else None,
            content=False if no_content else None,
            safety=False if no_safety else None,
        )
    except ArClipperError as exc:
        _fail(exc)
        return

    if not result.suggestions:
        console.print(
            Panel(
                "لم يُعثر على مقاطع مقترحة.\n"
                "جرّب: [cyan]--engine heuristic[/] أو خفّض [cyan]analyze.min_duration[/].",
                border_style="yellow",
            )
        )
        raise typer.Exit(code=0)

    console.print()
    _print_suggestions(result)

    indices = None
    if pick:
        try:
            indices = [int(x.strip()) for x in pick.split(",") if x.strip()]
        except ValueError:
            _fail(ArClipperError(f"صيغة --pick غير صالحة: {pick} (مثال صحيح: 0,2,5)"))
            return

    if not produce and indices is None:
        console.print(
            Panel(
                "لإنتاج المقاطع:\n"
                f"  [cyan]ar-clipper suggest \"{source}\" --produce[/]           ← الكل\n"
                f"  [cyan]ar-clipper suggest \"{source}\" --pick 0,2[/]          ← مختارة\n\n"
                "[dim]التفريغ محفوظ، فلن يُعاد — الإنتاج سيبدأ مباشرةً.[/]",
                title="[bold green]الخطوة التالية[/]",
                border_style="green",
            )
        )
        raise typer.Exit(code=0)

    # ---- الإنتاج
    requests = result.to_clip_requests(indices)
    if not requests:
        _fail(ArClipperError("لم يُطابق أي مقترح الأرقام المحددة."))
        return

    console.print(f"\n[bold]إنتاج {len(requests)} مقطعاً...[/]\n")
    try:
        chosen = get_preset(preset)
        apply_preset_to_settings(chosen, settings)
        prod_options = PipelineOptions(license_note=license_note)
        for key, value in chosen.options.items():
            if hasattr(prod_options, key):
                setattr(prod_options, key, value)

        results = run_pipeline(
            source,
            requests,
            options=prod_options,
            settings=settings,
            progress=_progress,
            source_video=result.source,
            transcript=result.transcript,  # لا تُعِد التفريغ — تم في التحليل
        )
    except ArClipperError as exc:
        _fail(exc)
        return

    console.print()
    _print_results(results)
    console.print("\n[bold green]✅ اكتمل الإنتاج.[/]")


# ============================================================ quickstart


@app.command(
    "quickstart",
    help="⭐ ابدأ من هنا — يسألك 3 أسئلة فقط وينتج أول مقطع.",
)
def quickstart_command():
    """أقصر طريق لأول مقطع ناجح: بلا حفظ أعلام وبلا قراءة توثيق."""
    setup_logging("INFO", force=True)
    settings = load_settings()

    console.print(
        Panel(
            "سنصنع أول مقطع لك الآن.\n"
            "ثلاثة أسئلة فقط، وكل سؤال له إجابة افتراضية بين قوسين — "
            "اضغط Enter لقبولها.",
            title="[bold cyan]AR-Clipper — البداية السريعة[/]",
            border_style="cyan",
        )
    )

    # --- 1) المصدر
    source = typer.prompt("\n1) رابط الفيديو أو مسار ملف محلي").strip().strip('"\'')
    if not source:
        console.print("[red]لم تُدخل مصدراً.[/]")
        raise typer.Exit(code=1)

    # --- 2) المدى الزمني
    console.print("\n2) أي جزء تريد؟  (مثال: 00:12:30 إلى 00:13:20)")
    start_raw = typer.prompt("   من", default="00:00:00")
    end_raw = typer.prompt("   إلى", default="00:00:45")

    # --- 3) أساس الاستخدام
    console.print("\n3) ما أساس استخدامك لهذا الفيديو؟")
    entries = describe_presets()
    for i, item in enumerate(entries, start=1):
        flag = "" if item["publishable"] else "  [yellow](بدون نشر علني)[/]"
        console.print(f"   {i}) [cyan]{item['key']}[/] — {item['label']}{flag}")
    choice = typer.prompt("   اختر رقماً", default="1")
    try:
        license_key = entries[int(choice) - 1]["key"]
    except (ValueError, IndexError):
        license_key = str(choice).strip() or "personal_test"

    # --- التنفيذ بالمسار الموصى به
    chosen = get_preset("campaign")
    apply_preset_to_settings(chosen, settings)
    options = PipelineOptions(license_note=license_key)
    for key, value in chosen.options.items():
        if hasattr(options, key):
            setattr(options, key, value)

    console.print(
        Panel(
            f"المصدر: {source}\n"
            f"المدى: {start_raw} → {end_raw}\n"
            f"أساس الاستخدام: {license_key}\n"
            f"المسار: campaign (جودة عالية + ترجمة عربية + 9:16)",
            title="[bold]سيتم التنفيذ الآن[/]",
            border_style="green",
        )
    )
    console.print(
        "[dim]أول تشغيل قد يستغرق دقائق إضافية لتحميل نموذج التفريغ مرة واحدة.[/]\n"
    )

    try:
        requests = [
            ClipRequest(start=parse_timestamp(start_raw), end=parse_timestamp(end_raw))
        ]
        results = run_pipeline(
            source, requests, options=options, settings=settings, progress=_progress
        )
    except ArClipperError as exc:
        _fail(exc)
        return
    except ValueError as exc:
        _fail(ArClipperError(f"صيغة توقيت غير صالحة: {exc}"))
        return

    console.print()
    _print_results(results)
    console.print(
        Panel(
            "المقطع جاهز ✅\n\n"
            "لتكرار نفس النتيجة مباشرةً في المرة القادمة:\n"
            f"  [cyan]ar-clipper clip \"{source}\" --preset campaign "
            f"-s {start_raw} -e {end_raw} -L {license_key}[/]",
            title="[bold green]تم[/]",
            border_style="green",
        )
    )


# ============================================================ presets


@app.command("presets", help="عرض المسارات الجاهزة وأساسات الاستخدام المتاحة.")
def presets_command():
    table = Table(title="المسارات الجاهزة (--preset)", header_style="bold magenta", show_lines=True)
    table.add_column("المفتاح", style="cyan")
    table.add_column("الوصف", overflow="fold")
    for key, preset in load_presets().items():
        table.add_row(key, f"[bold]{preset.label}[/]\n{preset.description}")
    console.print(table)

    lic = Table(title="أساسات الاستخدام (--license)", header_style="bold magenta")
    lic.add_column("المفتاح", style="cyan")
    lic.add_column("المعنى", overflow="fold")
    lic.add_column("قابل للنشر", justify="center")
    for item in describe_presets():
        lic.add_row(item["key"], item["label"], "✅" if item["publishable"] else "—")
    console.print(lic)

    console.print(
        Panel(
            "أمثلة:\n"
            "  [cyan]ar-clipper quickstart[/]                                  ← الأسهل للبداية\n"
            "  [cyan]ar-clipper clip v.mp4 -p campaign -s 60 -e 105[/]         ← حملة clipping\n"
            "  [cyan]ar-clipper clip v.mp4 -p fast -s 60 -e 105[/]             ← معاينة سريعة\n"
            "  [cyan]ar-clipper clip v.mp4 -p campaign -i[/]                   ← اختيار المدى تفاعلياً",
            title="[bold]كيف تستخدمها[/]",
            border_style="cyan",
        )
    )


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


# ============================================================ serve


@app.command("serve", help="🌐 تشغيل الواجهة المحلية في المتصفح.")
def serve_command(
    host: str = typer.Option("127.0.0.1", "--host", help="عنوان الاستماع."),
    port: int = typer.Option(8000, "--port", help="المنفذ."),
):
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        _fail(
            ArClipperError(
                "الواجهة تحتاج حزمتين إضافيتين:\n\n"
                "    pip install fastapi \"uvicorn[standard]\"\n\n"
                "أو استخدم الطرفية مباشرةً:  ar-clipper quickstart"
            )
        )
        return

    console.print(
        Panel(
            f"الواجهة تعمل على:  [bold cyan]http://{host}:{port}[/]\n\n"
            "كل المعالجة تجري على جهازك — لا شيء يُرفع لأي خادم خارجي.\n"
            "[dim]للإيقاف: Ctrl+C[/]",
            title="[bold green]AR-Clipper — الواجهة المحلية[/]",
            border_style="green",
        )
    )

    import uvicorn

    uvicorn.run("ui.server:app", host=host, port=port, log_level="warning")


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

    # ---- المرحلة 2: الذكاء في الاختيار
    try:
        from core.analyze.llm_client import llm_available

        settings_now = load_settings()
        engine_name = settings_now.get("analyze.llm_engine", "ollama")
        model_name = settings_now.get("analyze.model", "qwen2.5:7b")
        ready, message = llm_available(settings_now)
        table.add_row(
            "نموذج اللغة (اقتراح المقاطع)",
            ok if ready else warn,
            f"{engine_name}/{model_name} — جاهز"
            if ready
            else f"غير جاهز — ستعمل الأداة بالاستدلال.\n{message.splitlines()[0]}",
        )
    except Exception as exc:
        table.add_row("نموذج اللغة (اقتراح المقاطع)", warn, f"تعذّر الفحص: {exc}")

    try:
        from core.diarize.speakers import is_available as diarize_available

        ready, message = diarize_available(load_settings())
        table.add_row(
            "فصل المتحدثين (مضيف/ضيف)",
            ok if ready else warn,
            "جاهز" if ready else message.splitlines()[0],
        )
    except Exception as exc:
        table.add_row("فصل المتحدثين (مضيف/ضيف)", warn, f"تعذّر الفحص: {exc}")

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
                "انسخ هذا السطر ونفّذه:\n\n"
                "    [cyan]pip install -r requirements.txt[/]\n\n"
                "وإن كان ffmpeg هو الناقص:\n"
                "    Debian/Ubuntu:  [cyan]sudo apt install ffmpeg[/]\n"
                "    macOS:          [cyan]brew install ffmpeg[/]\n"
                "    Windows:        [cyan]winget install Gyan.FFmpeg[/]\n\n"
                f"عدد المشاكل الحرجة: {problems}",
                title="[bold red]يلزم إصلاح قبل الاستخدام[/]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    # اقتراحات اختيارية مفيدة — لا تمنع الاستخدام
    missing_optional = []
    try:
        __import__("transformers")
    except ImportError:
        try:
            __import__("argostranslate")
        except ImportError:
            missing_optional.append(
                "الترجمة للعربية غير مفعّلة بعد:\n"
                "    خفيف وسريع:  [cyan]pip install argostranslate[/]\n"
                "    أفضل جودة:   [cyan]pip install transformers torch sentencepiece[/]"
            )
    try:
        __import__("fastapi")
    except ImportError:
        missing_optional.append(
            "الواجهة الرسومية غير مثبّتة:\n"
            "    [cyan]pip install fastapi \"uvicorn[standard]\"[/]  ثم:  [cyan]ar-clipper serve[/]"
        )

    try:
        from core.analyze.llm_client import llm_available as _llm_ok

        if not _llm_ok(load_settings())[0]:
            missing_optional.append(
                "اقتراح المقاطع تلقائياً (المرحلة 2) يعمل أفضل بنموذج محلي مجاني:\n"
                "    1) ثبّت Ollama من https://ollama.com\n"
                "    2) [cyan]ollama pull qwen2.5:7b[/]\n"
                "    بدونه تعمل الأداة بالاستدلال: [cyan]ar-clipper suggest <مصدر> --engine heuristic[/]"
            )
    except Exception:
        pass

    if missing_optional:
        console.print(
            Panel("\n\n".join(missing_optional), title="[bold yellow]إضافات مقترحة[/]",
                  border_style="yellow")
        )

    console.print(
        Panel(
            "البيئة جاهزة ✅\n\n"
            "ابدأ من هنا:\n"
            "    [cyan]ar-clipper quickstart[/]     ← 3 أسئلة وينتج أول مقطع\n"
            "    [cyan]ar-clipper serve[/]          ← واجهة في المتصفح\n"
            "    [cyan]ar-clipper presets[/]        ← المسارات الجاهزة",
            title="[bold green]جاهز[/]",
            border_style="green",
        )
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
