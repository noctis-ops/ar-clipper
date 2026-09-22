"""اختبارات التوافق عبر الأنظمة — ويندوز تحديداً.

المشروع طُوِّر على لينكس، فمخاطر ويندوز لا تظهر إلا بالاختبار الصريح:
مسارات الخطوط، أسماء الملفات المحجوزة، والمحرف الفاصل في المسارات.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


class TestFontDiscovery:
    def test_finds_a_font_on_this_system(self):
        from core.common.fonts import find_font_file

        assert find_font_file() is not None, "لا خط عربي — الترجمة ستظهر مربّعات"

    def test_ass_family_is_never_empty(self):
        from core.common.fonts import default_ass_font

        assert default_ass_font().strip()

    def test_explicit_path_wins(self, tmp_path):
        from core.common.fonts import find_font_file

        fake = tmp_path / "خطي.ttf"
        fake.write_bytes(b"x")
        find_font_file.cache_clear()
        try:
            assert find_font_file(str(fake)) == fake
        finally:
            find_font_file.cache_clear()

    def test_missing_explicit_path_falls_back(self, tmp_path):
        """مسار خاطئ في الإعدادات يجب ألّا يُعطّل الإنتاج."""
        from core.common.fonts import find_font_file

        find_font_file.cache_clear()
        try:
            assert find_font_file(str(tmp_path / "لا-يوجد.ttf")) is not None
        finally:
            find_font_file.cache_clear()

    def test_windows_lookup_prefers_segoe(self, tmp_path, monkeypatch):
        """محاكاة ويندوز: Segoe UI هو أفضل خط عربي مرفق معه."""
        import core.common.fonts as fonts

        fonts_dir = tmp_path / "Windows" / "Fonts"
        fonts_dir.mkdir(parents=True)
        for name in ("segoeui.ttf", "tahoma.ttf", "arial.ttf"):
            (fonts_dir / name).write_bytes(b"x")

        monkeypatch.setattr(fonts, "IS_WINDOWS", True)
        monkeypatch.setattr(fonts, "IS_MACOS", False)
        monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
        fonts.find_font_file.cache_clear()
        fonts.default_ass_font.cache_clear()
        try:
            assert fonts.find_font_file().name == "segoeui.ttf"
            assert fonts.default_ass_font() == "Segoe UI"
        finally:
            fonts.find_font_file.cache_clear()
            fonts.default_ass_font.cache_clear()

    def test_macos_lookup(self, tmp_path, monkeypatch):
        import core.common.fonts as fonts

        monkeypatch.setattr(fonts, "IS_WINDOWS", False)
        monkeypatch.setattr(fonts, "IS_MACOS", True)
        fonts.default_ass_font.cache_clear()
        try:
            assert isinstance(fonts.font_directories(), list)
        finally:
            fonts.default_ass_font.cache_clear()

    def test_subtitle_style_uses_detected_font(self):
        """إعداد فارغ ⇒ يُملأ تلقائياً، لا يبقى فارغاً في ملف ASS."""
        from core.common.config import load_settings
        from core.subtitles.builder import build_ass_style

        section = dict(load_settings().section("subtitles"))
        section["font_name"] = ""
        style = build_ass_style(section, play_res_x=1080, play_res_y=1920)
        line = [l for l in style.splitlines() if l.startswith("Style:")][0]
        assert line.split(",")[1].strip(), "اسم الخط فارغ في ملف ASS"


class TestWindowsFilenames:
    @pytest.mark.parametrize("name", ["con", "CON", "Aux", "nul", "com1", "LPT9"])
    def test_reserved_names_are_escaped(self, name):
        """ويندوز يرفض هذه الأسماء مهما كان الامتداد."""
        from core.common.text_utils import slugify

        reserved = (
            {"CON", "PRN", "AUX", "NUL"}
            | {f"COM{i}" for i in range(1, 10)}
            | {f"LPT{i}" for i in range(1, 10)}
        )
        assert slugify(name).upper() not in reserved

    @pytest.mark.parametrize("ch", [":", "*", "?", '"', "<", ">", "|", "\\", "/"])
    def test_forbidden_characters_removed(self, ch):
        from core.common.text_utils import slugify

        assert ch not in slugify(f"اسم{ch}ملف")

    def test_no_trailing_dot_or_space(self):
        """ويندوز يحذفها صامتاً فيختلف الاسم عمّا سجّلناه."""
        from core.common.text_utils import slugify

        for candidate in ("ملف.", "ملف ", "ملف. ", "name."):
            result = slugify(candidate)
            assert not result.endswith((".", " ")), repr(result)

    def test_arabic_names_still_work(self):
        from core.common.text_utils import slugify

        assert slugify("الذكاء الاصطناعي") == "الذكاء-الاصطناعي"

    def test_empty_falls_back(self):
        from core.common.text_utils import slugify

        assert slugify("***") == "clip"


class TestPathHandling:
    def test_filter_path_escaping_handles_drive_letters(self):
        """``C:\\Users\\...`` داخل فلتر ffmpeg يحتاج هروباً خاصاً."""
        from core.common.ffmpeg import escape_filter_path

        escaped = escape_filter_path(r"C:\Users\محمد\clip.ass")
        assert "\\:" in escaped          # النقطتان مهرَّبتان
        assert "\\\\" not in escaped     # الخطوط المائلة صارت أمامية

    def test_forward_slashes_in_output(self):
        from core.common.ffmpeg import escape_filter_path

        assert "\\" not in escape_filter_path(r"C:\a\b\c.ass").replace("\\:", "")

    def test_no_hardcoded_linux_font_paths(self):
        """مسار لينكس ثابت كان يُسقط ويندوز إلى خط بلا دعم عربي."""
        source = Path("core/design/thumbnail.py").read_text(encoding="utf-8")
        assert "/usr/share/fonts" not in source

    def test_text_files_are_written_as_utf8(self):
        """ترميز ويندوز الافتراضي (cp1256) يفسد العربية."""
        import re

        for path in ("core/subtitles/builder.py", "core/common/schemas.py"):
            source = Path(path).read_text(encoding="utf-8")
            for match in re.finditer(r"\.write_text\(", source):
                tail = source[match.start() : match.start() + 400]
                assert "encoding=" in tail, f"كتابة بلا ترميز صريح في {path}"


class TestLaunchers:
    """المشغّلات في جذر المشروع — أول ما يلمسه المستخدم."""

    def test_all_three_exist(self):
        for name in ("arc.bat", "arc.ps1", "arc.sh"):
            assert Path(name).exists(), f"{name} مفقود"

    def test_bat_is_pure_ascii(self):
        """رسائل عربية داخل .bat تظهر رموزاً مشوّهة قبل تنفيذ chcp."""
        raw = Path("arc.bat").read_bytes()
        assert all(b < 128 for b in raw), "arc.bat يحتوي أحرفاً غير ASCII"

    def test_bat_sets_utf8_codepage(self):
        assert "chcp 65001" in Path("arc.bat").read_text(encoding="utf-8")

    def test_bat_passes_all_arguments(self):
        """%* لا %1 — وإلا ضاعت كل المعاملات بعد الأولى."""
        content = Path("arc.bat").read_text(encoding="utf-8")
        assert "%*" in content

    def test_bat_resolves_its_own_directory(self):
        """%~dp0 يجعله يعمل من أي مجلد، لا من جذر المشروع فقط."""
        assert "%~dp0" in Path("arc.bat").read_text(encoding="utf-8")

    def test_bat_quotes_python_path(self):
        """مسار فيه مسافات (C:\\Users\\My Name\\...) يكسر الأمر بلا اقتباس."""
        assert '"%PY%"' in Path("arc.bat").read_text(encoding="utf-8")

    def test_ps1_uses_splatting(self):
        """@args لا $args — الثاني يمرّر المصفوفة كسلسلة واحدة."""
        content = Path("arc.ps1").read_text(encoding="utf-8")
        assert "@args" in content
        assert "-m cli.main $args" not in content

    def test_ps1_propagates_exit_code(self):
        assert "$LASTEXITCODE" in Path("arc.ps1").read_text(encoding="utf-8")

    def test_sh_is_executable(self):
        import os
        import stat

        if sys.platform.startswith("win"):
            pytest.skip("أذونات التنفيذ لا معنى لها على ويندوز")
        mode = os.stat("arc.sh").st_mode
        assert mode & stat.S_IXUSR, "arc.sh غير قابل للتنفيذ"

    def test_launchers_check_for_missing_venv(self):
        """رسالة واضحة خير من ImportError غامض."""
        assert "if not exist" in Path("arc.bat").read_text(encoding="utf-8")
        assert "Test-Path" in Path("arc.ps1").read_text(encoding="utf-8")
        assert "-x " in Path("arc.sh").read_text(encoding="utf-8")


class TestGuideAccuracy:
    """الدليل يجب أن يطابق الواقع — وثيقة خاطئة أسوأ من لا وثيقة."""

    def _guide(self):
        return Path("docs/TESTING-GUIDE.md").read_text(encoding="utf-8")

    def test_no_bare_arc_command_lines(self):
        """PowerShell يرفض 'arc' بلا .\\ — كان هذا خطأ حقيقياً في الدليل.

        الاستثناء الوحيد المسموح: السطر الذي يوضّح أن `arc` تعمل بعد إضافة
        المجلد إلى PATH، وهو معلَّم بتعليق صريح على نفس السطر.
        """
        import re

        for line in self._guide().splitlines():
            if not re.match(r"^arc\s", line):
                continue
            assert "#" in line and "PATH" in self._guide(), (
                f"سطر أمر بلا .\\ ولا تفسير: {line}"
            )

    def test_explains_the_dot_slash_requirement(self):
        assert "CommandNotFoundException" in self._guide()

    def test_mentions_all_platforms(self):
        guide = self._guide()
        for token in ("PowerShell", "Command Prompt", "arc.sh"):
            assert token in guide


class TestSuggestedCommands:
    """الأوامر المقترحة في المخرجات يجب أن تكون قابلة للنسخ واللصق.

    خطأ حقيقي: ``doctor`` كان يقترح ``ar-clipper quickstart`` وهو اسم غير
    مسجَّل (لا pyproject.toml ينشئ نقطة دخول). المستخدم ينسخه فيحصل على
    ``CommandNotFoundException`` — نفس الخطأ الذي أبلغ عنه.
    """

    def test_no_unregistered_command_name_in_cli(self):
        source = Path("cli/main.py").read_text(encoding="utf-8")
        assert "ar-clipper " not in source, "اقتراح أمر غير مسجَّل في cli/main.py"

    def test_launcher_matches_platform(self, monkeypatch):
        import core.common.platform_utils as pu

        monkeypatch.setattr(pu, "is_windows", lambda: True)
        monkeypatch.setattr(pu, "in_powershell", lambda: True)
        assert pu.launcher() == ".\\arc"

        monkeypatch.setattr(pu, "in_powershell", lambda: False)
        assert pu.launcher() == "arc"

        monkeypatch.setattr(pu, "is_windows", lambda: False)
        assert pu.launcher() == "./arc.sh"

    def test_cmd_builds_full_command(self, monkeypatch):
        import core.common.platform_utils as pu

        monkeypatch.setattr(pu, "is_windows", lambda: True)
        monkeypatch.setattr(pu, "in_powershell", lambda: True)
        assert pu.cmd("quickstart") == ".\\arc quickstart"

    def test_cmd_without_args(self):
        from core.common.platform_utils import cmd, launcher

        assert cmd() == launcher()

    def test_powershell_detected_by_env(self, monkeypatch):
        import core.common.platform_utils as pu

        monkeypatch.setattr(pu, "is_windows", lambda: True)
        monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\Modules")
        assert pu.in_powershell() is True
        monkeypatch.delenv("PSModulePath", raising=False)
        assert pu.in_powershell() is False

    def test_doctor_output_uses_real_launcher(self):
        """المخرج الفعلي يجب أن يحوي مشغّلاً صالحاً لهذا النظام."""
        from typer.testing import CliRunner

        from cli.main import app
        from core.common.platform_utils import launcher

        result = CliRunner().invoke(app, ["doctor"])
        assert launcher() in result.output


class TestOptionalExtrasDoc:
    """الوثيقة تصف مفاتيح إعدادات حقيقية."""

    def test_documented_config_keys_exist(self):
        import yaml

        doc = Path("docs/OPTIONAL-EXTRAS.md").read_text(encoding="utf-8")
        config = yaml.safe_load(Path("config/settings.yaml").read_text(encoding="utf-8"))

        if "analyze:" in doc and "model:" in doc:
            assert "model" in config["analyze"], "مفتاح analyze.model غير موجود"
        if "silence.engine" in doc:
            assert "engine" in config["silence"]

    def test_auto_editor_is_truly_optional(self):
        """الوثيقة تنصح بتجاهله — تأكد أن الافتراضي ليس هو."""
        import yaml

        config = yaml.safe_load(Path("config/settings.yaml").read_text(encoding="utf-8"))
        assert config["silence"]["engine"] == "ffmpeg"
