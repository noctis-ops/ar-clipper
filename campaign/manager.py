"""إدارة الحملات والأرباح — المرحلة 4 كاملة.

منفصلة تماماً عن معالجة الفيديو (كما تنصّ الوثيقة): تتبّع حملات Whop
والمنصات المشابهة، وربط كل مقطع بحملته، وحساب الأرباح المتوقعة.

**الإدخال يدوي بالتصميم.** سحب المشاهدات تلقائياً من TikTok/Instagram
يتطلب APIs مدفوعة أو Scraping يخالف شروط الاستخدام — وكلاهما مرفوض بمبادئ
المشروع. الأتمتة الوحيدة المقبولة مستقبلاً هي يوتيوب API الرسمي المجاني.

الأرباح = (المشاهدات ÷ 1000) × سعر الألف مشاهدة.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.common.config import Settings, load_settings
from core.common.errors import ArClipperError
from core.common.logging_utils import get_logger
from core.store.db import connect, rows_to_dicts

log = get_logger(__name__)

PLATFORMS = ("tiktok", "youtube", "instagram", "x", "facebook", "linkedin", "other")
POST_STATUSES = ("pending", "accepted", "rejected", "paid")
CAMPAIGN_STATUSES = ("active", "paused", "ended")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ============================================================ النماذج


@dataclass
class Campaign:
    id: int
    name: str
    platform_link: str = ""
    budget: float = 0.0
    rate_per_1000_views: float = 0.0
    currency: str = "USD"
    rules_notes: str = ""
    start_date: str = ""
    end_date: str = ""
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "platform_link": self.platform_link,
            "budget": self.budget, "rate_per_1000_views": self.rate_per_1000_views,
            "currency": self.currency, "rules_notes": self.rules_notes,
            "start_date": self.start_date, "end_date": self.end_date,
            "status": self.status,
        }


@dataclass
class EarningsRow:
    """سطر في تقرير الأرباح."""

    campaign: str
    posts: int
    views: int
    earning: float
    currency: str = "USD"
    budget: float = 0.0
    accepted: int = 0
    pending: int = 0
    rejected: int = 0

    @property
    def budget_used_pct(self) -> float:
        return (self.earning / self.budget * 100) if self.budget else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "campaign": self.campaign, "posts": self.posts, "views": self.views,
            "earning": round(self.earning, 2), "currency": self.currency,
            "budget": self.budget, "budget_used_pct": round(self.budget_used_pct, 1),
            "accepted": self.accepted, "pending": self.pending, "rejected": self.rejected,
        }


# ============================================================ الحملات


def add_campaign(
    name: str,
    *,
    rate_per_1000_views: float,
    platform_link: str = "",
    budget: float = 0.0,
    currency: str = "USD",
    rules_notes: str = "",
    start_date: str = "",
    end_date: str = "",
    settings: Optional[Settings] = None,
) -> int:
    """يضيف حملة جديدة."""
    settings = settings or load_settings()
    name = (name or "").strip()
    if not name:
        raise ArClipperError("اسم الحملة مطلوب.")
    if rate_per_1000_views < 0 or budget < 0:
        raise ArClipperError("السعر والميزانية لا يمكن أن يكونا سالبين.")

    with connect(settings) as conn:
        exists = conn.execute("SELECT id FROM campaigns WHERE name = ?", (name,)).fetchone()
        if exists:
            raise ArClipperError(f"الحملة موجودة مسبقاً: {name}")
        cur = conn.execute(
            """
            INSERT INTO campaigns (name, platform_link, budget, rate_per_1000_views,
                                   currency, rules_notes, start_date, end_date)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (name, platform_link, float(budget), float(rate_per_1000_views),
             currency, rules_notes, start_date or _today(), end_date),
        )
        campaign_id = int(cur.lastrowid)
    log.info("أُضيفت الحملة «%s» بسعر %.2f/1000 مشاهدة.", name, rate_per_1000_views)
    return campaign_id


def get_campaign(name: str, *, settings: Optional[Settings] = None) -> Optional[Campaign]:
    settings = settings or load_settings()
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    return Campaign(
        id=row["id"], name=row["name"], platform_link=row["platform_link"],
        budget=row["budget"], rate_per_1000_views=row["rate_per_1000_views"],
        currency=row["currency"], rules_notes=row["rules_notes"],
        start_date=row["start_date"], end_date=row["end_date"], status=row["status"],
    )


def list_campaigns(
    *, settings: Optional[Settings] = None, status: str = ""
) -> List[Campaign]:
    settings = settings or load_settings()
    sql = "SELECT * FROM campaigns"
    params: List[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC, id DESC"
    with connect(settings) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Campaign(
            id=r["id"], name=r["name"], platform_link=r["platform_link"],
            budget=r["budget"], rate_per_1000_views=r["rate_per_1000_views"],
            currency=r["currency"], rules_notes=r["rules_notes"],
            start_date=r["start_date"], end_date=r["end_date"], status=r["status"],
        )
        for r in rows
    ]


def update_campaign(
    name: str, *, settings: Optional[Settings] = None, **fields: Any
) -> bool:
    """يعدّل حقول حملة. الحقول المسموحة فقط."""
    allowed = {
        "platform_link", "budget", "rate_per_1000_views", "currency",
        "rules_notes", "start_date", "end_date", "status",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    if "status" in updates and updates["status"] not in CAMPAIGN_STATUSES:
        raise ArClipperError(f"حالة غير صالحة. المتاح: {'، '.join(CAMPAIGN_STATUSES)}")

    settings = settings or load_settings()
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with connect(settings) as conn:
        cur = conn.execute(
            f"UPDATE campaigns SET {assignments} WHERE name = ?",
            [*updates.values(), name],
        )
        return cur.rowcount > 0


def delete_campaign(name: str, *, settings: Optional[Settings] = None) -> bool:
    settings = settings or load_settings()
    with connect(settings) as conn:
        cur = conn.execute("DELETE FROM campaigns WHERE name = ?", (name,))
        return cur.rowcount > 0


def link_clip(
    clip_id: str, campaign: str, *, settings: Optional[Settings] = None
) -> bool:
    """يربط مقطعاً مُنتجاً بحملة."""
    settings = settings or load_settings()
    with connect(settings) as conn:
        crow = conn.execute("SELECT id FROM campaigns WHERE name = ?", (campaign,)).fetchone()
        if not crow:
            raise ArClipperError(f"حملة غير موجودة: {campaign}")
        cur = conn.execute(
            "UPDATE clips SET campaign_id = ? WHERE clip_id = ?", (crow["id"], clip_id)
        )
        return cur.rowcount > 0


# ============================================================ المنشورات


def add_post(
    clip_id: str,
    *,
    platform: str,
    post_link: str = "",
    views: int = 0,
    status: str = "pending",
    campaign: str = "",
    notes: str = "",
    settings: Optional[Settings] = None,
) -> int:
    """يسجّل منشوراً لمقطع بعد نشره."""
    settings = settings or load_settings()
    platform = (platform or "other").strip().lower()
    if platform not in PLATFORMS:
        raise ArClipperError(f"منصة غير معروفة: {platform}. المتاح: {'، '.join(PLATFORMS)}")
    if status not in POST_STATUSES:
        raise ArClipperError(f"حالة غير صالحة: {status}. المتاح: {'، '.join(POST_STATUSES)}")
    if views < 0:
        raise ArClipperError("عدد المشاهدات لا يمكن أن يكون سالباً.")

    with connect(settings) as conn:
        crow = conn.execute(
            "SELECT id, campaign_id FROM clips WHERE clip_id = ? ORDER BY id DESC LIMIT 1",
            (clip_id,),
        ).fetchone()
        clip_key = int(crow["id"]) if crow else None
        campaign_id = crow["campaign_id"] if crow else None

        if campaign:
            cam = conn.execute("SELECT id FROM campaigns WHERE name = ?", (campaign,)).fetchone()
            if not cam:
                raise ArClipperError(f"حملة غير موجودة: {campaign}")
            campaign_id = int(cam["id"])

        cur = conn.execute(
            """
            INSERT INTO posts (clip_id, campaign_id, platform, post_link,
                               views_count, status, posted_at, notes)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (clip_key, campaign_id, platform, post_link, int(views), status, _today(), notes),
        )
        post_id = int(cur.lastrowid)
    log.info("سُجّل منشور #%d على %s للمقطع %s.", post_id, platform, clip_id)
    return post_id


def update_post(
    post_id: int,
    *,
    views: Optional[int] = None,
    status: Optional[str] = None,
    post_link: Optional[str] = None,
    notes: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> bool:
    """يحدّث منشوراً (المشاهدات غالباً — تُدخَل يدوياً دورياً)."""
    settings = settings or load_settings()
    updates: Dict[str, Any] = {}
    if views is not None:
        if views < 0:
            raise ArClipperError("عدد المشاهدات لا يمكن أن يكون سالباً.")
        updates["views_count"] = int(views)
    if status is not None:
        if status not in POST_STATUSES:
            raise ArClipperError(f"حالة غير صالحة: {status}")
        updates["status"] = status
    if post_link is not None:
        updates["post_link"] = post_link
    if notes is not None:
        updates["notes"] = notes
    if not updates:
        return False

    updates["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with connect(settings) as conn:
        cur = conn.execute(
            f"UPDATE posts SET {assignments} WHERE id = ?", [*updates.values(), post_id]
        )
        return cur.rowcount > 0


def list_posts(
    *, campaign: str = "", platform: str = "", settings: Optional[Settings] = None
) -> List[Dict[str, Any]]:
    settings = settings or load_settings()
    sql = [
        """
        SELECT p.*, c.clip_id AS clip_key, c.title AS clip_title,
               cam.name AS campaign_name, cam.rate_per_1000_views AS rate,
               cam.currency AS currency
        FROM posts p
        LEFT JOIN clips c ON c.id = p.clip_id
        LEFT JOIN campaigns cam ON cam.id = p.campaign_id
        """
    ]
    params: List[Any] = []
    conditions = []
    if campaign:
        conditions.append("cam.name = ?")
        params.append(campaign)
    if platform:
        conditions.append("p.platform = ?")
        params.append(platform.lower())
    if conditions:
        sql.append("WHERE " + " AND ".join(conditions))
    sql.append("ORDER BY p.id DESC")

    with connect(settings) as conn:
        rows = conn.execute(" ".join(sql), params).fetchall()

    out = rows_to_dicts(rows)
    for row in out:
        row["earning"] = calculate_earning(
            row.get("views_count", 0), row.get("rate") or 0.0
        )
    return out


def delete_post(post_id: int, *, settings: Optional[Settings] = None) -> bool:
    settings = settings or load_settings()
    with connect(settings) as conn:
        cur = conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        return cur.rowcount > 0


# ============================================================ الأرباح


def calculate_earning(views: int, rate_per_1000: float) -> float:
    """الصيغة المعتمدة: (المشاهدات ÷ 1000) × السعر."""
    if views <= 0 or rate_per_1000 <= 0:
        return 0.0
    return round((float(views) / 1000.0) * float(rate_per_1000), 2)


def earnings_report(
    *, settings: Optional[Settings] = None, only_accepted: bool = False
) -> List[EarningsRow]:
    """تقرير الأرباح لكل حملة.

    ``only_accepted`` يحسب المنشورات المقبولة فقط — أقرب للواقع لأن
    المرفوضة لا تُدفع.
    """
    settings = settings or load_settings()
    with connect(settings) as conn:
        campaigns = conn.execute("SELECT * FROM campaigns ORDER BY name").fetchall()
        report: List[EarningsRow] = []

        for cam in campaigns:
            rows = conn.execute(
                "SELECT views_count, status FROM posts WHERE campaign_id = ?", (cam["id"],)
            ).fetchall()

            counted = [
                r for r in rows
                if not only_accepted or r["status"] in ("accepted", "paid")
            ]
            views = sum(int(r["views_count"]) for r in counted)
            report.append(
                EarningsRow(
                    campaign=cam["name"],
                    posts=len(rows),
                    views=views,
                    earning=calculate_earning(views, cam["rate_per_1000_views"]),
                    currency=cam["currency"],
                    budget=cam["budget"],
                    accepted=sum(1 for r in rows if r["status"] in ("accepted", "paid")),
                    pending=sum(1 for r in rows if r["status"] == "pending"),
                    rejected=sum(1 for r in rows if r["status"] == "rejected"),
                )
            )

        # منشورات بلا حملة
        orphan = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(views_count),0) AS v "
            "FROM posts WHERE campaign_id IS NULL"
        ).fetchone()
        if orphan and int(orphan["n"]):
            report.append(
                EarningsRow(
                    campaign="(بلا حملة)", posts=int(orphan["n"]),
                    views=int(orphan["v"]), earning=0.0,
                )
            )
    return report


def totals(*, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """الإجمالي عبر كل الحملات."""
    rows = earnings_report(settings=settings)
    by_currency: Dict[str, float] = {}
    for row in rows:
        if row.earning:
            by_currency[row.currency] = by_currency.get(row.currency, 0.0) + row.earning
    return {
        "campaigns": len([r for r in rows if r.campaign != "(بلا حملة)"]),
        "posts": sum(r.posts for r in rows),
        "views": sum(r.views for r in rows),
        "earnings_by_currency": {k: round(v, 2) for k, v in by_currency.items()},
        "accepted": sum(r.accepted for r in rows),
        "pending": sum(r.pending for r in rows),
        "rejected": sum(r.rejected for r in rows),
    }


# ============================================================ التعليقات


def add_comment(
    post_id: int,
    text: str,
    *,
    author: str = "",
    likes: int = 0,
    settings: Optional[Settings] = None,
) -> int:
    settings = settings or load_settings()
    if not (text or "").strip():
        raise ArClipperError("نص التعليق مطلوب.")
    with connect(settings) as conn:
        exists = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not exists:
            raise ArClipperError(f"منشور غير موجود: {post_id}")
        cur = conn.execute(
            "INSERT INTO comments (post_id, author, text, likes) VALUES (?,?,?,?)",
            (post_id, author, text.strip(), int(likes)),
        )
        return int(cur.lastrowid)


def list_comments(
    *, post_id: int = 0, settings: Optional[Settings] = None, limit: int = 100
) -> List[Dict[str, Any]]:
    settings = settings or load_settings()
    sql = "SELECT * FROM comments"
    params: List[Any] = []
    if post_id:
        sql += " WHERE post_id = ?"
        params.append(post_id)
    sql += " ORDER BY likes DESC, id DESC LIMIT ?"
    params.append(int(limit))
    with connect(settings) as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


def suggest_followups(
    *, post_id: int = 0, settings: Optional[Settings] = None, max_ideas: int = 5
) -> List[str]:
    """يقترح أفكار «جزء ثانٍ» بناءً على التعليقات.

    يستخدم النموذج المحلي إن توفّر، وإلا يتراجع إلى استخلاص محلي بحت:
    التعليقات التي تحوي أسئلة هي أفضل مصدر لأفكار المتابعة.
    """
    comments = list_comments(post_id=post_id, settings=settings, limit=50)
    if not comments:
        return []

    texts = [c["text"] for c in comments if (c.get("text") or "").strip()]
    if not texts:
        return []

    try:
        from core.analyze.llm_client import build_llm, llm_available

        ready, _reason = llm_available(settings or load_settings())
        if ready:
            llm = build_llm(settings or load_settings())
            joined = "\n".join(f"- {t}" for t in texts[:30])
            prompt = (
                "هذه تعليقات جمهور على مقطع قصير. اقترح "
                f"{max_ideas} أفكار لمقاطع متابعة («جزء ثاني») تجيب عن أكثر ما "
                "يشغل الجمهور. أجب بالعربية، فكرة واحدة في كل سطر، بلا ترقيم.\n\n"
                f"{joined}"
            )
            reply = llm.generate(prompt)
            ideas = [
                line.strip(" -•\t") for line in (reply or "").splitlines()
                if len(line.strip(" -•\t")) > 8
            ]
            if ideas:
                return ideas[:max_ideas]
    except Exception as exc:
        log.debug("تعذّر استخدام النموذج لاقتراح المتابعات: %s", exc)

    # تراجع محلي: الأسئلة أولاً ثم الأكثر إعجاباً
    questions = [t for t in texts if "؟" in t or "?" in t]
    ranked = questions or texts
    return [f"أجب عن: {t[:110]}" for t in ranked[:max_ideas]]
