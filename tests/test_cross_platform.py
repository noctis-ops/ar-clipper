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
