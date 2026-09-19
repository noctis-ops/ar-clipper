"""اختبارات واجهة الويب المحلية (FastAPI)."""

from __future__ import annotations

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient", reason="fastapi غير مثبّت")
from fastapi.testclient import TestClient  # noqa: E402

from ui.server import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


class TestBasics:
    def test_health(self, client):
        assert client.get("/api/health").json() == {"status": "ok"}

    def test_index_is_rtl_arabic(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert 'dir="rtl"' in r.text
        assert 'lang="ar"' in r.text

    def test_index_has_no_external_calls(self, client):
        """محلي أولاً: لا CDN ولا خطوط خارجية (المبدأ 2)."""
        text = client.get("/").text
        for bad in ("http://", "https://cdn", "googleapis", "unpkg", "jsdelivr"):
            assert bad not in text.replace("https://youtube.com/watch?v=...", "")


class TestOptions:
    def test_returns_presets_and_licenses(self, client):
        data = client.get("/api/options").json()
        assert data["presets"] and data["licenses"]

    def test_campaign_preset_present(self, client):
        keys = {p["key"] for p in client.get("/api/options").json()["presets"]}
        assert "campaign" in keys

    def test_license_flags_exposed(self, client):
        licenses = client.get("/api/options").json()["licenses"]
        personal = next(l for l in licenses if l["key"] == "personal_test")
        assert personal["publishable"] is False


class TestProbe:
    def test_url_detected(self, client):
        r = client.post("/api/probe", json={"source": "https://youtube.com/watch?v=x"})
        assert r.json()["kind"] == "url"

    def test_missing_file(self, client):
        r = client.post("/api/probe", json={"source": "/nope/missing.mp4"})
        assert r.status_code == 404


class TestClipValidation:
    def test_empty_source_rejected(self, client):
        assert client.post("/api/clip", json={"source": "   "}).status_code == 400

    def test_unknown_job(self, client):
        assert client.get("/api/jobs/doesnotexist").status_code == 404


class TestFileSecurity:
    def test_path_traversal_blocked(self, client):
        """حرج: منع تسريب أي ملف خارج مجلد المخرجات."""
        for attack in ("/etc/passwd", "../../../../etc/passwd", "/home/user/.ssh/id_rsa"):
            r = client.get("/api/file", params={"path": attack})
            assert r.status_code in (403, 404), f"لم يُحجب: {attack}"

    def test_missing_allowed_file(self, client):
        from core.common.config import load_settings

        target = load_settings().path("paths.clips") / "nope" / "missing.mp4"
        assert client.get("/api/file", params={"path": str(target)}).status_code == 404
