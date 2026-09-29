"""اختبارات المرحلتين 4 و5: المكتبة، البحث، الطابور، الحملات، الأرباح."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.common.errors import ArClipperError


@pytest.fixture
def db(tmp_path, monkeypatch):
    """قاعدة بيانات معزولة لكل اختبار."""
    from copy import deepcopy

    import core.common.config as cfg
    from core.common.config import load_settings as real_load

    settings = deepcopy(real_load())
    settings.data.setdefault("store", {})["path"] = str(tmp_path / "test.db")
    monkeypatch.setattr(cfg, "load_settings", lambda *a, **k: settings)

    for module in ("core.store.db", "library.manager", "library.queue", "campaign.manager"):
        try:
            mod = __import__(module, fromlist=["load_settings"])
            monkeypatch.setattr(mod, "load_settings", lambda *a, **k: settings)
        except (ImportError, AttributeError):
            pass
    return settings


# ============================================================ قاعدة البيانات


class TestSchema:
    def test_all_tables_created(self, db):
        from core.store.db import table_names

        names = set(table_names(db))
        for table in ("videos", "transcripts", "clips", "campaigns", "posts", "comments", "queue"):
            assert table in names, f"جدول مفقود: {table}"

    def test_fts_table_exists(self, db):
        from core.store.db import table_names

        assert "transcript_fts" in table_names(db)

    def test_foreign_keys_enforced(self, db):
        import sqlite3

        from core.store.db import connect

        with pytest.raises(sqlite3.IntegrityError):
            with connect(db) as conn:
                conn.execute(
                    "INSERT INTO comments (post_id, text) VALUES (9999, 'x')"
                )

    def test_reconnect_is_idempotent(self, db):
        """فتح القاعدة مرتين يجب ألّا يفسد المخطط أو البيانات."""
        from campaign.manager import add_campaign, list_campaigns

        add_campaign("a", rate_per_1000_views=1.0, settings=db)
        from core.store.db import table_names

        table_names(db)
        assert len(list_campaigns(settings=db)) == 1


# ============================================================ المكتبة


def _source(key="vid1", title="عنوان"):
    from core.common.schemas import SourceVideo

    return SourceVideo(
        path=f"/tmp/{key}.mp4", title=title, origin="local",
        duration=600, width=1920, height=1080, video_id=key,
    )


def _transcript(texts, start=10.0):
    from core.common.schemas import Segment, Transcript

    segments = []
    t = start
    for i, text in enumerate(texts):
        segments.append(Segment(id=i, start=t, end=t + 8, text=text, words=[]))
        t += 20
    return Transcript(source_path="/tmp/x.mp4", language="ar", segments=segments)


class TestLibraryIndexing:
    def test_index_video(self, db):
        from library.manager import index_video, list_videos

        index_video(_source(), settings=db, workspace="vid1")
        videos = list_videos(settings=db)
        assert len(videos) == 1
        assert videos[0].video_id == "vid1"

    def test_reindex_updates_not_duplicates(self, db):
        from library.manager import index_video, list_videos

        index_video(_source(title="قديم"), settings=db, workspace="vid1")
        index_video(_source(title="جديد"), settings=db, workspace="vid1")
        videos = list_videos(settings=db)
        assert len(videos) == 1
        assert videos[0].title == "جديد"

    def test_index_transcript_counts_segments(self, db):
        from library.manager import index_transcript, index_video

        index_video(_source(), settings=db, workspace="vid1")
        n = index_transcript(
            _transcript(["جملة أولى", "جملة ثانية"]), video_key="vid1", settings=db
        )
        assert n == 2

    def test_transcript_without_video_is_safe(self, db):
        from library.manager import index_transcript

        assert index_transcript(_transcript(["نص"]), video_key="مجهول", settings=db) == 0

    def test_reindex_transcript_replaces(self, db):
        """إعادة الفهرسة يجب ألّا تُضاعف النتائج."""
        from library.manager import index_transcript, index_video, search

        index_video(_source(), settings=db, workspace="vid1")
        for _ in range(3):
            index_transcript(_transcript(["الاستثمار مهم"]), video_key="vid1", settings=db)
        assert len(search("الاستثمار", settings=db)) == 1

    def test_forget_video(self, db):
        from library.manager import forget_video, index_video, list_videos

        index_video(_source(), settings=db, workspace="vid1")
        assert forget_video("vid1", settings=db) is True
        assert list_videos(settings=db) == []

    def test_forget_missing_returns_false(self, db):
        from library.manager import forget_video

        assert forget_video("لا-يوجد", settings=db) is False


class TestSearch:
    @pytest.fixture
    def indexed(self, db):
        from library.manager import index_transcript, index_video

        index_video(_source(), settings=db, workspace="vid1")
        index_transcript(
            _transcript([
                "اليوم نتحدث عن الاستثمار في الأسهم",
                "الذكاء الاصطناعي سيغير كل شيء",
                "عدت للاستثمار بعد خسارة كبيرة",
            ]),
            video_key="vid1", settings=db,
        )
        return db

    def test_finds_exact_word(self, indexed):
        from library.manager import search

        assert len(search("الاستثمار", settings=indexed)) >= 1

    def test_morphological_expansion(self, indexed):
        """العربية لصقية: «استثمار» يجب أن تجد «الاستثمار» و«للاستثمار»."""
        from library.manager import search

        assert len(search("استثمار", settings=indexed)) >= 2

    def test_diacritics_ignored(self, indexed):
        from library.manager import search

        assert len(search("الذَّكاء", settings=indexed)) >= 1

    def test_alef_forms_unified(self, indexed):
        from library.manager import search

        assert len(search("الاسهم", settings=indexed)) >= 1

    def test_exact_mode_disables_expansion(self, indexed):
        from library.manager import search

        assert len(search("استثمار", settings=indexed, exact=True)) == 0

    def test_no_results_for_absent_term(self, indexed):
        from library.manager import search

        assert search("الفيزياء الكمية", settings=indexed) == []

    def test_empty_query_is_safe(self, indexed):
        from library.manager import search

        assert search("", settings=indexed) == []
        assert search("   ", settings=indexed) == []

    def test_fts_operators_are_neutralized(self, indexed):
        """رموز FTS5 الخاصة يجب ألّا تُفشل الاستعلام."""
        from library.manager import search

        for query in ['"', "*", "(الاستثمار", "NEAR/", "a OR b"]:
            search(query, settings=indexed)  # لا يرمي

    def test_hit_has_timestamp(self, indexed):
        from library.manager import search

        hit = search("الذكاء", settings=indexed)[0]
        assert ":" in hit.to_dict()["timestamp"]

    def test_limit_respected(self, indexed):
        from library.manager import search

        assert len(search("الاستثمار", settings=indexed, limit=1)) <= 1


class TestLibraryClips:
    def test_index_and_list_clip(self, db):
        from core.common.schemas import ClipResult
        from library.manager import index_clip, index_video, list_clips

        index_video(_source(), settings=db, workspace="vid1")
        result = ClipResult(
            clip_id="c1", video_path="/tmp/c1.mp4", start=0, end=30,
            duration=30, width=1080, height=1920,
        )
        index_clip(result, video_key="vid1", settings=db, title="عنوان")
        clips = list_clips(settings=db)
        assert len(clips) == 1
        assert clips[0]["clip_id"] == "c1"

    def test_stats_reflect_content(self, db):
        from core.common.schemas import ClipResult
        from library.manager import index_clip, index_video, stats

        index_video(_source(), settings=db, workspace="vid1")
        index_clip(
            ClipResult(clip_id="c1", video_path="/x.mp4", start=0, end=20, duration=20),
            video_key="vid1", settings=db,
        )
        data = stats(settings=db)
        assert data["videos"] == 1
        assert data["clips"] == 1
        assert data["total_clip_seconds"] == pytest.approx(20)


# ============================================================ الطابور


class TestQueue:
    def test_enqueue_and_list(self, db):
        from library.queue import enqueue, list_items

        enqueue("/a.mp4", settings=db)
        enqueue("/b.mp4", settings=db)
        assert len(list_items(settings=db)) == 2

    def test_priority_ordering(self, db):
        from library.queue import enqueue, next_pending

        enqueue("/low.mp4", priority=0, settings=db)
        enqueue("/high.mp4", priority=10, settings=db)
        assert next_pending(settings=db).source == "/high.mp4"

    def test_options_round_trip(self, db):
        from library.queue import enqueue, list_items

        enqueue("/a.mp4", options={"count": 5, "preset": "fast"}, settings=db)
        assert list_items(settings=db)[0].options["count"] == 5

    def test_run_processes_all(self, db):
        from library.queue import enqueue, run_queue

        for i in range(3):
            enqueue(f"/v{i}.mp4", settings=db)
        run = run_queue(settings=db, worker=lambda item: ["clip"])
        assert run.processed == 3
        assert run.succeeded == 3

    def test_failure_does_not_stop_queue(self, db):
        """مهمة فاشلة يجب ألّا تُلغي ليلة معالجة كاملة."""
        from library.queue import enqueue, run_queue

        for i in range(4):
            enqueue(f"/v{i}.mp4", settings=db)

        def worker(item):
            if "v1" in item.source:
                raise RuntimeError("تالف")
            return ["c"]

        run = run_queue(settings=db, worker=worker)
        assert run.processed == 4
        assert run.succeeded == 3
        assert run.failed == 1

    def test_stop_on_error_halts(self, db):
        from library.queue import enqueue, run_queue

        for i in range(4):
            enqueue(f"/v{i}.mp4", settings=db)

        def worker(item):
            raise RuntimeError("فشل")

        run = run_queue(settings=db, worker=worker, continue_on_error=False)
        assert run.processed == 1

    def test_limit_respected(self, db):
        from library.queue import enqueue, run_queue

        for i in range(5):
            enqueue(f"/v{i}.mp4", settings=db)
        run = run_queue(settings=db, worker=lambda i: ["c"], limit=2)
        assert run.processed == 2

    def test_stop_event_interrupts(self, db):
        import threading

        from library.queue import enqueue, run_queue

        for i in range(5):
            enqueue(f"/v{i}.mp4", settings=db)
        stop = threading.Event()

        def worker(item):
            stop.set()
            return ["c"]

        run = run_queue(settings=db, worker=worker, stop_event=stop)
        assert run.processed == 1

    def test_error_is_recorded(self, db):
        from library.queue import FAILED, enqueue, list_items, run_queue

        enqueue("/bad.mp4", settings=db)
        run_queue(settings=db, worker=lambda i: (_ for _ in ()).throw(ValueError("سبب")))
        item = list_items(settings=db)[0]
        assert item.status == FAILED
        assert "سبب" in item.error

    def test_cancel_pending(self, db):
        from library.queue import CANCELLED, cancel, enqueue, list_items

        item_id = enqueue("/a.mp4", settings=db)
        assert cancel(item_id, settings=db) is True
        assert list_items(settings=db)[0].status == CANCELLED

    def test_cancel_done_fails(self, db):
        from library.queue import DONE, cancel, enqueue, update_status

        item_id = enqueue("/a.mp4", settings=db)
        update_status(item_id, DONE, settings=db)
        assert cancel(item_id, settings=db) is False

    def test_reset_stuck(self, db):
        from library.queue import PENDING, RUNNING, enqueue, list_items, reset_stuck, update_status

        item_id = enqueue("/a.mp4", settings=db)
        update_status(item_id, RUNNING, settings=db)
        assert reset_stuck(settings=db) == 1
        assert list_items(settings=db)[0].status == PENDING

    def test_clear_finished_only(self, db):
        from library.queue import DONE, clear, enqueue, list_items, update_status

        done_id = enqueue("/done.mp4", settings=db)
        enqueue("/pending.mp4", settings=db)
        update_status(done_id, DONE, settings=db)
        clear(settings=db)
        remaining = list_items(settings=db)
        assert len(remaining) == 1
        assert remaining[0].source == "/pending.mp4"

    def test_summary_counts(self, db):
        from library.queue import enqueue, summary

        enqueue("/a.mp4", settings=db)
        enqueue("/b.mp4", settings=db)
        assert summary(settings=db)["pending"] == 2

    def test_empty_queue_run_is_noop(self, db):
        from library.queue import run_queue

        assert run_queue(settings=db, worker=lambda i: []).processed == 0


# ============================================================ الحملات


class TestCampaigns:
    def test_add_and_get(self, db):
        from campaign.manager import add_campaign, get_campaign

        add_campaign("c1", rate_per_1000_views=1.5, budget=100, settings=db)
        campaign = get_campaign("c1", settings=db)
        assert campaign.rate_per_1000_views == 1.5

    def test_duplicate_rejected(self, db):
        from campaign.manager import add_campaign

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        with pytest.raises(ArClipperError):
            add_campaign("c1", rate_per_1000_views=2.0, settings=db)

    def test_empty_name_rejected(self, db):
        from campaign.manager import add_campaign

        with pytest.raises(ArClipperError):
            add_campaign("   ", rate_per_1000_views=1.0, settings=db)

    def test_negative_rate_rejected(self, db):
        from campaign.manager import add_campaign

        with pytest.raises(ArClipperError):
            add_campaign("c", rate_per_1000_views=-1, settings=db)

    def test_update_fields(self, db):
        from campaign.manager import add_campaign, get_campaign, update_campaign

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        update_campaign("c1", rate_per_1000_views=3.0, settings=db)
        assert get_campaign("c1", settings=db).rate_per_1000_views == 3.0

    def test_invalid_status_rejected(self, db):
        from campaign.manager import add_campaign, update_campaign

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        with pytest.raises(ArClipperError):
            update_campaign("c1", status="مجهول", settings=db)

    def test_unknown_fields_ignored(self, db):
        """حماية: حقل غير مسموح لا يُحقن في SQL."""
        from campaign.manager import add_campaign, update_campaign

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        assert update_campaign("c1", id=999, settings=db) is False

    def test_delete(self, db):
        from campaign.manager import add_campaign, delete_campaign, list_campaigns

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        assert delete_campaign("c1", settings=db) is True
        assert list_campaigns(settings=db) == []


class TestPosts:
    @pytest.fixture
    def ready(self, db):
        from campaign.manager import add_campaign

        add_campaign("c1", rate_per_1000_views=2.0, budget=100, settings=db)
        return db

    def test_add_post(self, ready):
        from campaign.manager import add_post, list_posts

        add_post("clip1", platform="tiktok", views=1000, campaign="c1", settings=ready)
        assert len(list_posts(settings=ready)) == 1

    def test_unknown_platform_rejected(self, ready):
        from campaign.manager import add_post

        with pytest.raises(ArClipperError):
            add_post("c", platform="myspace", campaign="c1", settings=ready)

    def test_unknown_campaign_rejected(self, ready):
        from campaign.manager import add_post

        with pytest.raises(ArClipperError):
            add_post("c", platform="tiktok", campaign="لا-توجد", settings=ready)

    def test_negative_views_rejected(self, ready):
        from campaign.manager import add_post

        with pytest.raises(ArClipperError):
            add_post("c", platform="tiktok", views=-5, campaign="c1", settings=ready)

    def test_update_views(self, ready):
        from campaign.manager import add_post, list_posts, update_post

        post_id = add_post("c", platform="tiktok", views=100, campaign="c1", settings=ready)
        update_post(post_id, views=5000, settings=ready)
        assert list_posts(settings=ready)[0]["views_count"] == 5000

    def test_earning_computed_in_listing(self, ready):
        from campaign.manager import add_post, list_posts

        add_post("c", platform="tiktok", views=10_000, campaign="c1", settings=ready)
        assert list_posts(settings=ready)[0]["earning"] == pytest.approx(20.0)

    def test_delete_post(self, ready):
        from campaign.manager import add_post, delete_post, list_posts

        post_id = add_post("c", platform="x", campaign="c1", settings=ready)
        assert delete_post(post_id, settings=ready) is True
        assert list_posts(settings=ready) == []


class TestEarnings:
    def test_formula(self):
        from campaign.manager import calculate_earning

        assert calculate_earning(125_000, 1.5) == pytest.approx(187.5)
        assert calculate_earning(1000, 2.0) == pytest.approx(2.0)

    def test_zero_cases(self):
        from campaign.manager import calculate_earning

        assert calculate_earning(0, 5.0) == 0.0
        assert calculate_earning(1000, 0) == 0.0
        assert calculate_earning(-10, 5.0) == 0.0

    def test_report_aggregates(self, db):
        from campaign.manager import add_campaign, add_post, earnings_report

        add_campaign("c1", rate_per_1000_views=1.0, budget=100, settings=db)
        add_post("a", platform="tiktok", views=50_000, campaign="c1", settings=db)
        add_post("b", platform="youtube", views=30_000, campaign="c1", settings=db)
        row = earnings_report(settings=db)[0]
        assert row.views == 80_000
        assert row.earning == pytest.approx(80.0)

    def test_accepted_only_filter(self, db):
        from campaign.manager import add_campaign, add_post, earnings_report

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        add_post("a", platform="tiktok", views=50_000, status="accepted", campaign="c1", settings=db)
        add_post("b", platform="x", views=50_000, status="rejected", campaign="c1", settings=db)
        assert earnings_report(settings=db, only_accepted=True)[0].earning == pytest.approx(50.0)

    def test_budget_percentage(self, db):
        from campaign.manager import add_campaign, add_post, earnings_report

        add_campaign("c1", rate_per_1000_views=1.0, budget=100, settings=db)
        add_post("a", platform="tiktok", views=25_000, campaign="c1", settings=db)
        assert earnings_report(settings=db)[0].budget_used_pct == pytest.approx(25.0)

    def test_orphan_posts_listed(self, db):
        from campaign.manager import add_post, earnings_report

        add_post("a", platform="tiktok", views=1000, settings=db)
        assert any(r.campaign == "(بلا حملة)" for r in earnings_report(settings=db))

    def test_totals_by_currency(self, db):
        from campaign.manager import add_campaign, add_post, totals

        add_campaign("usd", rate_per_1000_views=1.0, currency="USD", settings=db)
        add_campaign("eur", rate_per_1000_views=2.0, currency="EUR", settings=db)
        add_post("a", platform="tiktok", views=10_000, campaign="usd", settings=db)
        add_post("b", platform="tiktok", views=10_000, campaign="eur", settings=db)
        earnings = totals(settings=db)["earnings_by_currency"]
        assert earnings["USD"] == pytest.approx(10.0)
        assert earnings["EUR"] == pytest.approx(20.0)


class TestComments:
    def test_add_and_list(self, db):
        from campaign.manager import add_campaign, add_comment, add_post, list_comments

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        post_id = add_post("clip", platform="tiktok", campaign="c1", settings=db)
        add_comment(post_id, "تعليق رائع", likes=10, settings=db)
        assert len(list_comments(post_id=post_id, settings=db)) == 1

    def test_comment_on_missing_post_rejected(self, db):
        from campaign.manager import add_comment

        with pytest.raises(ArClipperError):
            add_comment(9999, "نص", settings=db)

    def test_empty_comment_rejected(self, db):
        from campaign.manager import add_campaign, add_comment, add_post

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        post_id = add_post("clip", platform="tiktok", campaign="c1", settings=db)
        with pytest.raises(ArClipperError):
            add_comment(post_id, "   ", settings=db)

    def test_followups_prefer_questions(self, db):
        from campaign.manager import add_campaign, add_comment, add_post, suggest_followups

        add_campaign("c1", rate_per_1000_views=1.0, settings=db)
        post_id = add_post("clip", platform="tiktok", campaign="c1", settings=db)
        add_comment(post_id, "محتوى جميل جداً ومفيد", settings=db)
        add_comment(post_id, "كيف أبدأ الاستثمار بمبلغ صغير؟", settings=db)
        ideas = suggest_followups(post_id=post_id, settings=db)
        assert ideas
        assert any("كيف أبدأ" in idea for idea in ideas)

    def test_no_comments_returns_empty(self, db):
        from campaign.manager import suggest_followups

        assert suggest_followups(post_id=1, settings=db) == []


# ============================================================ كشف التكرار


@dataclass
class FakeSuggestion:
    start: float
    end: float
    text: str
    score: float = 5.0
    title: str = ""
    hook: str = ""


class TestDedupe:
    def test_identical_text_detected(self):
        from core.analyze.dedupe import deduplicate

        items = [
            FakeSuggestion(10, 40, "الذكاء الاصطناعي يغير سوق العمل جذرياً", score=8),
            FakeSuggestion(500, 530, "الذكاء الاصطناعي يغير سوق العمل جذرياً", score=6),
        ]
        assert deduplicate(items).removed == 1

    def test_keeps_higher_score(self):
        from core.analyze.dedupe import deduplicate

        items = [
            FakeSuggestion(10, 40, "نص متطابق تماماً هنا للاختبار", score=3),
            FakeSuggestion(500, 530, "نص متطابق تماماً هنا للاختبار", score=9),
        ]
        assert deduplicate(items).kept[0].score == 9

    def test_time_overlap_detected(self):
        from core.analyze.dedupe import deduplicate

        items = [
            FakeSuggestion(100, 140, "موضوع أول مختلف تماماً", score=8),
            FakeSuggestion(105, 138, "كلام آخر بعيد لا صلة له", score=5),
        ]
        assert deduplicate(items).removed == 1

    def test_distinct_items_kept(self):
        from core.analyze.dedupe import deduplicate

        items = [
            FakeSuggestion(10, 40, "الاستثمار في العقارات يحتاج دراسة", score=8),
            FakeSuggestion(500, 530, "تعلّم البرمجة يفتح فرصاً كثيرة", score=7),
            FakeSuggestion(900, 930, "الرياضة تحسّن الصحة النفسية", score=6),
        ]
        assert deduplicate(items).removed == 0

    def test_original_order_preserved(self):
        from core.analyze.dedupe import deduplicate

        items = [
            FakeSuggestion(10, 40, "موضوع الأول عن السفر والمغامرة", score=3),
            FakeSuggestion(500, 530, "موضوع الثاني عن الطبخ والمطاعم", score=9),
        ]
        kept = deduplicate(items).kept
        assert kept[0].start == 10  # الترتيب الزمني محفوظ رغم اختلاف الدرجات

    def test_disabled_keeps_all(self):
        from copy import deepcopy

        from core.analyze.dedupe import deduplicate
        from core.common.config import load_settings

        settings = deepcopy(load_settings())
        settings.data.setdefault("dedupe", {})["enabled"] = False
        items = [FakeSuggestion(1, 2, "نفس النص"), FakeSuggestion(1, 2, "نفس النص")]
        assert deduplicate(items, settings=settings).removed == 0

    def test_empty_input(self):
        from core.analyze.dedupe import deduplicate

        assert deduplicate([]).kept == []

    def test_single_item(self):
        from core.analyze.dedupe import deduplicate

        assert len(deduplicate([FakeSuggestion(1, 5, "نص")]).kept) == 1

    def test_jaccard_bounds(self):
        from core.analyze.dedupe import jaccard

        assert jaccard(set(), set()) == 0.0
        assert jaccard({"a"}, {"a"}) == 1.0
        assert 0 < jaccard({"a", "b"}, {"b", "c"}) < 1

    def test_stopwords_excluded(self):
        from core.analyze.dedupe import content_words

        words = content_words("هذا هو الذكاء الاصطناعي في المستقبل")
        assert "هذا" not in words
        assert "الذكاء" in words

    def test_time_overlap_math(self):
        from core.analyze.dedupe import time_overlap

        assert time_overlap(0, 10, 20, 30) == 0.0
        assert time_overlap(0, 10, 0, 10) == pytest.approx(1.0)
        assert time_overlap(0, 10, 5, 15) == pytest.approx(0.5)


# ============================================================ تحسين الصوت


class TestVoiceEnhancement:
    def _settings(self, **overrides):
        from copy import deepcopy

        from core.common.config import load_settings

        settings = deepcopy(load_settings())
        settings.data["polish"].update(overrides)
        return settings

    def test_disabled_by_default(self):
        from core.common.config import load_settings

        assert load_settings().get("polish.enhance_voice") is False

    def test_adds_filters_when_enabled(self):
        from core.polish.finisher import build_audio_filter

        chain = build_audio_filter(self._settings(enhance_voice=True))
        for expected in ("highpass", "lowpass", "afftdn", "acompressor"):
            assert expected in chain

    def test_order_enhancement_before_loudnorm(self):
        """التطبيع يجب أن يعمل على صوت نظيف."""
        from core.polish.finisher import build_audio_filter

        chain = build_audio_filter(self._settings(enhance_voice=True))
        assert chain.index("afftdn") < chain.index("loudnorm")

    def test_denoise_strength_zero_skips_filter(self):
        from core.polish.finisher import build_audio_filter

        chain = build_audio_filter(self._settings(enhance_voice=True, denoise_strength=0))
        assert "afftdn" not in chain

    def test_compressor_can_be_disabled(self):
        from core.polish.finisher import build_audio_filter

        chain = build_audio_filter(self._settings(enhance_voice=True, compress_voice=False))
        assert "acompressor" not in chain


# ============================================================ أوامر CLI


class TestNewCommands:
    def _help(self, *args):
        from typer.testing import CliRunner

        from cli.main import app

        return CliRunner().invoke(app, list(args)).output

    def test_groups_registered(self):
        output = self._help("--help")
        for group in ("library", "queue", "campaign"):
            assert group in output

    def test_library_subcommands(self):
        output = self._help("library", "--help")
        for sub in ("list", "clips", "search", "stats"):
            assert sub in output

    def test_queue_subcommands(self):
        output = self._help("queue", "--help")
        for sub in ("add", "list", "run", "clear", "cancel"):
            assert sub in output

    def test_campaign_subcommands(self):
        output = self._help("campaign", "--help")
        for sub in ("add", "list", "post", "views", "report", "ideas"):
            assert sub in output


# ============================================================ واجهة الويب


class TestWebEndpoints:
    @pytest.fixture
    @staticmethod
    def client():
        from fastapi.testclient import TestClient

        from ui.server import app

        return TestClient(app)

    @pytest.mark.parametrize(
        "path",
        [
            "/api/library/videos",
            "/api/library/stats",
            "/api/queue",
            "/api/campaigns",
            "/api/earnings",
        ],
    )
    def test_endpoints_respond(self, client, path):
        assert client.get(path).status_code == 200

    def test_search_endpoint(self, client):
        data = client.get("/api/library/search", params={"q": "أي-شيء"}).json()
        assert "hits" in data and "count" in data

    def test_campaign_validation_returns_400(self, client):
        response = client.post(
            "/api/campaigns", json={"name": "", "rate_per_1000_views": 1.0}
        )
        assert response.status_code == 400

    def test_post_unknown_platform_returns_400(self, client):
        response = client.post(
            "/api/posts", json={"clip_id": "x", "platform": "myspace"}
        )
        assert response.status_code == 400

    def test_queue_run_with_empty_queue(self, client):
        data = client.post("/api/queue/run").json()
        assert "started" in data


class TestWebUiMarkup:
    def _html(self):
        return Path("ui/static/index.html").read_text(encoding="utf-8")

    def test_all_tabs_present(self):
        html = self._html()
        for tab in ("tab-produce", "tab-library", "tab-queue", "tab-money"):
            assert f'id="{tab}"' in html

    def test_js_functions_defined(self):
        html = self._html()
        for fn in ("loadLibrary", "loadQueue", "loadMoney", "runSearch"):
            assert f"function {fn}" in html

    def test_html_is_escaped(self):
        """بيانات المستخدم (العناوين، المصادر) تُحقن في DOM — لا بد من الهروب."""
        html = self._html()
        assert "const esc =" in html
        assert "replace(/</g, '&lt;')" in html

    def test_single_script_block(self):
        html = self._html()
        assert html.count("<script>") == html.count("</script>") == 1

    def test_div_tags_balanced(self):
        html = self._html()
        assert html.count("<div") == html.count("</div>")


class TestBrandingUpload:
    """رفع الشعار من الواجهة — كان يتطلب تحرير YAML يدوياً."""

    @pytest.fixture
    @staticmethod
    def client():
        from fastapi.testclient import TestClient

        from ui.server import app

        return TestClient(app)

    def _png(self, size=(64, 64)):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGBA", size, (255, 120, 20, 255)).save(buf, "PNG")
        buf.seek(0)
        return buf

    def test_get_branding_state(self, client):
        data = client.get("/api/branding").json()
        for key in ("enabled", "logo_path", "has_logo", "handle", "corner"):
            assert key in data

    def test_rejects_non_image_extension(self, client):
        response = client.post(
            "/api/branding/logo", files={"file": ("x.txt", b"hello", "text/plain")}
        )
        assert response.status_code == 400

    def test_rejects_fake_image(self, client):
        """امتداد صحيح لا يكفي — نتحقق أنها صورة فعلاً."""
        response = client.post(
            "/api/branding/logo",
            files={"file": ("fake.png", b"not-an-image", "image/png")},
        )
        assert response.status_code == 400

    def test_rejects_empty_file(self, client):
        response = client.post(
            "/api/branding/logo", files={"file": ("e.png", b"", "image/png")}
        )
        assert response.status_code == 400

    def test_rejects_unknown_corner(self, client):
        assert client.patch("/api/branding", json={"corner": "مجهول"}).status_code == 400

    def test_accepts_valid_corner(self, client):
        assert client.patch("/api/branding", json={"corner": "top_left"}).status_code == 200


class TestSettingsPersistence:
    """الكتابة في settings.yaml يجب ألّا تمحو التعليقات."""

    def test_comments_preserved(self, tmp_path, monkeypatch):
        import core.common.config as cfg
        from ui.server import _persist_setting

        original = Path("config/settings.yaml").read_text(encoding="utf-8")
        target = tmp_path / "settings.yaml"
        target.write_text(original, encoding="utf-8")

        settings = cfg.load_settings()
        monkeypatch.setattr(settings, "source_path", target)
        monkeypatch.setattr(cfg, "load_settings", lambda *a, **k: settings)
        import ui.server as srv

        monkeypatch.setattr(srv, "load_settings", lambda *a, **k: settings)

        before = original.count("#")
        _persist_setting("branding.handle", "@x")
        after = target.read_text(encoding="utf-8")
        assert after.count("#") == before, "التعليقات فُقدت عند الحفظ"
        assert '@x' in after

    def test_only_target_line_changes(self, tmp_path, monkeypatch):
        import core.common.config as cfg
        import ui.server as srv
        from ui.server import _persist_setting

        original = Path("config/settings.yaml").read_text(encoding="utf-8")
        target = tmp_path / "settings.yaml"
        target.write_text(original, encoding="utf-8")

        settings = cfg.load_settings()
        monkeypatch.setattr(settings, "source_path", target)
        monkeypatch.setattr(srv, "load_settings", lambda *a, **k: settings)

        _persist_setting("branding.handle", "@only")
        changed = [
            (a, b)
            for a, b in zip(original.splitlines(), target.read_text(encoding="utf-8").splitlines())
            if a != b
        ]
        assert len(changed) == 1
