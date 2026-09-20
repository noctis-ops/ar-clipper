"""اختبارات واجهة الويب المحلية (FastAPI)."""

from __future__ import annotations

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient", reason="fastapi غير مثبّت")
from fastapi.testclient import TestClient  # noqa: E402

from ui.server import app  # noqa: E402




class _DummyThread:
    """يمنع تشغيل خيط المعالجة الحقيقي أثناء الاختبار."""

    def __init__(self, target=None, args=(), daemon=False, **kwargs):
        self.target, self.args = target, args

    def start(self):  # لا ينفّذ شيئاً عمداً
        return None


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


# ============================================================ المرحلة 2


class TestSuggestApi:
    """مسارات الاقتراح التلقائي — بلا تشغيل فعلي لخط الأنابيب."""

    def test_suggest_requires_source(self, client):
        assert client.post("/api/suggest", json={"source": "   "}).status_code == 400

    def test_suggest_returns_job_id(self, client, monkeypatch):
        import ui.server as server

        monkeypatch.setattr(server.threading, "Thread", _DummyThread)
        r = client.post("/api/suggest", json={"source": "/tmp/x.mp4", "count": 3})
        assert r.status_code == 200
        assert r.json()["job_id"]

    def test_suggest_defaults(self, client, monkeypatch):
        import ui.server as server

        captured = {}

        def fake_thread(target, args, daemon):
            captured["payload"] = args[1]
            return _DummyThread(target=target, args=args, daemon=daemon)

        monkeypatch.setattr(server.threading, "Thread", fake_thread)
        client.post("/api/suggest", json={"source": "/tmp/x.mp4"})
        payload = captured["payload"]
        assert payload.count == 6
        assert payload.diarize is True
        assert payload.engine == ""

    def test_produce_unknown_analysis_is_404(self, client):
        r = client.post("/api/produce", json={"analysis_id": "nope", "indices": [0]})
        assert r.status_code == 404

    def test_produce_accepts_known_analysis(self, client, monkeypatch):
        import ui.server as server
        from core.common.schemas import SourceVideo
        from core.suggest import Suggestion, SuggestionSet

        analysis = SuggestionSet(
            source=SourceVideo(path="/tmp/x.mp4", title="t"),
            suggestions=[Suggestion(0, 0.0, 30.0, 3.0, "story", "سبب", title="عنوان")],
        )
        server.ANALYSES["test-analysis"] = analysis
        monkeypatch.setattr(server.threading, "Thread", _DummyThread)
        r = client.post(
            "/api/produce", json={"analysis_id": "test-analysis", "indices": [0]}
        )
        assert r.status_code == 200
        assert r.json()["job_id"]
        server.ANALYSES.pop("test-analysis", None)


class TestSuggestUi:
    def test_page_has_both_modes(self, client):
        text = client.get("/").text
        assert "modeManual" in text
        assert "modeAuto" in text

    def test_page_calls_suggest_endpoints(self, client):
        text = client.get("/").text
        assert "/api/suggest" in text
        assert "/api/produce" in text

    def test_engine_choices_present(self, client):
        text = client.get("/").text
        for engine in ("heuristic", "hybrid", "llm"):
            assert engine in text


class TestMemoryBounds:
    """الذاكرة لا تنمو بلا حد في الجلسات الطويلة."""

    def test_jobs_are_capped(self):
        import ui.server as server

        server.JOBS.clear()
        for i in range(server.MAX_JOBS + 25):
            server.JOBS[f"job{i}"] = server.Job(id=f"job{i}")
            server._prune(server.JOBS, server.MAX_JOBS)
        assert len(server.JOBS) == server.MAX_JOBS

    def test_newest_job_survives_pruning(self):
        import ui.server as server

        server.JOBS.clear()
        for i in range(server.MAX_JOBS + 5):
            server.JOBS[f"job{i}"] = server.Job(id=f"job{i}")
            server._prune(server.JOBS, server.MAX_JOBS)
        last = f"job{server.MAX_JOBS + 4}"
        assert last in server.JOBS
        assert "job0" not in server.JOBS

    def test_analyses_are_capped(self):
        import ui.server as server

        server.ANALYSES.clear()
        for i in range(server.MAX_ANALYSES + 15):
            server.ANALYSES[f"a{i}"] = object()
            server._prune(server.ANALYSES, server.MAX_ANALYSES)
        assert len(server.ANALYSES) == server.MAX_ANALYSES
