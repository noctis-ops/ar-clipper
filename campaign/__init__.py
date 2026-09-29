"""إدارة الحملات والأرباح — المرحلة 4."""

from .manager import (
    add_campaign,
    add_comment,
    add_post,
    calculate_earning,
    earnings_report,
    get_campaign,
    link_clip,
    list_campaigns,
    list_posts,
    suggest_followups,
    totals,
    update_post,
)

__all__ = [
    "add_campaign", "add_comment", "add_post", "calculate_earning",
    "earnings_report", "get_campaign", "link_clip", "list_campaigns",
    "list_posts", "suggest_followups", "totals", "update_post",
]
