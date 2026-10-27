"""Profile tools: facts about the recipient, the sender, the group and the business relationship."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional

from context import ContextStore
from context.records import Business, GroupMember

from .base import ToolContext, clip, fmt, not_applicable, past_message

MENTION = re.compile(r"@(\w+)")
RECENT_FROM_SENDER = 3


def user_profile(ctx: ToolContext) -> Dict[str, Any]:
    """The recipient: quiet hours, recent activity and how they usually react."""
    user = ctx.store.users[ctx.message.user_id]
    load = ctx.store.recent_load(user.user_id, ctx.now.date())
    if load:
        # The daily summary can end days before the message, so say how stale it is.
        load["data_ends_days_before_message"] = (ctx.now.date() - date.fromisoformat(load["last_day"])).days
    return {
        "user_id": user.user_id,
        "message_time": f"{ctx.now:%Y-%m-%d %H:%M} ({ctx.now:%A})",
        "quiet_hours": user.quiet_window,
        "message_in_quiet_hours": user.in_quiet_hours(ctx.now),
        "activity_30d": {
            "opened": user.messages_opened_30d,
            "replied": user.messages_replied_30d,
            "dismissed": user.notifications_dismissed_30d,
            "reported": user.messages_reported_30d,
        },
        "reactions_to_all_past_messages": ContextStore.reaction_summary(ctx.history()),
        "recent_notification_load": load,
    }


def sender_profile(ctx: ToolContext) -> Dict[str, Any]:
    """Who sent the message, and how this user has reacted to them before."""
    m = ctx.message
    past = ctx.history(sender_key=m.sender_key)
    by_conversation: Dict[str, int] = {}
    for p in past:
        key = p.group_id or p.conversation_type
        by_conversation[key] = by_conversation.get(key, 0) + 1
    replies = [p.created_at for p in past if p.reaction and p.reaction.replied]
    history = {
        "past_messages_to_user": {"total": len(past), "by_conversation": by_conversation},
        "user_reactions": ContextStore.reaction_summary(past),
        "last_user_reply_at": fmt(max(replies)) if replies else None,
        "most_recent": [past_message(p, ctx) for p in reversed(past[-RECENT_FROM_SENDER:])],
    }
    if m.business_id:
        return {"kind": "business", **business_facts(ctx.store.businesses[m.business_id]), **history}

    profile: Dict[str, Any] = {
        "kind": "person",
        "user_id": m.sender_user_id,
        "registered_user": m.sender_user_id in ctx.store.users,
    }
    if m.group_id:
        member = ctx.store.membership(m.group_id, m.sender_user_id)
        profile["group_role"] = member.role if member else "not a member"
        profile["group_activity_30d"] = member_activity(member)
    return {**profile, **history}


def group_context(ctx: ToolContext) -> Dict[str, Any]:
    """The group the message was posted in, and the recipient's place in it."""
    m = ctx.message
    if not m.group_id:
        return not_applicable("This is not a group message.")
    g = ctx.store.groups[m.group_id]
    recipient = ctx.store.membership(m.group_id, m.user_id)
    sender = ctx.store.membership(m.group_id, m.sender_user_id)
    mentions = list(dict.fromkeys(clip(name, 30) for name in MENTION.findall(m.text)))[:5]
    return {
        "group_id": g.group_id,
        "name": g.group_name,
        "type": g.group_type,
        "member_count": g.member_count,
        "admin_count": g.admin_count,
        "messages_30d": g.messages_30d,
        "recipient_role": recipient.role if recipient else "not a member",
        "recipient_muted_group": recipient.muted if recipient else None,
        "recipient_activity_30d": member_activity(recipient),
        "recipient_reactions_in_group": ContextStore.reaction_summary(ctx.history(group_id=m.group_id)),
        "sender_role": sender.role if sender else "not a member",
        "mentions": mentions,
        "mentions_recipient": m.user_id in mentions,
    }


def business_relationship(ctx: ToolContext) -> Dict[str, Any]:
    """Whether and how the recipient knows the business that sent the message."""
    m = ctx.message
    if not m.business_id:
        return not_applicable("This is not a business message.")
    b = ctx.store.businesses[m.business_id]
    r = ctx.store.relationship(m.user_id, m.business_id)
    facts: Dict[str, Any] = {
        "business_id": b.business_id,
        "brand_name": b.brand_name,
        "relationship_on_record": r is not None,
    }
    if r is None:
        return facts
    opted_out = r.promotions_opted_out_at
    return {
        **facts,
        "why_user_knows_account": r.why_user_knows_account,
        "last_activity_at": fmt(r.last_activity_at),
        "days_since_last_activity": (ctx.now - r.last_activity_at).days,
        "allows_promotions": r.allows_promotions,
        "promotions_opted_out_at": fmt(opted_out),
        "opted_out_before_message": bool(opted_out and opted_out <= ctx.now),
        "activity_count_180d": r.activity_count_180d,
        "opened_30d": r.messages_opened_30d,
        "dismissed_30d": r.messages_dismissed_30d,
        "replied_30d": r.messages_replied_30d,
        "last_reply_at": fmt(r.last_reply_at),
    }


def business_facts(b: Business) -> Dict[str, Any]:
    """A business account's facts. A missing official domain is reported as null, not as a mismatch."""
    official = b.official_domain or None
    return {
        "business_id": b.business_id,
        "display_name": b.display_name,
        "brand_name": b.brand_name,
        "category": b.category,
        "verified": b.verified,
        "official_domain": official,
        "sender_domain": b.sender_domain or None,
        "sender_domain_matches_official": b.domain_matches_official if official else None,
        "sender_domain_age_days": b.sender_domain_age_days,
        "account_age_days": b.account_age_days,
        "messages_sent_30d": b.messages_sent_30d,
        "user_reports_30d": b.user_reports_30d,
    }


def member_activity(member: Optional[GroupMember]) -> Optional[Dict[str, int]]:
    if member is None:
        return None
    return {
        "sent": member.messages_sent_30d,
        "read": member.messages_read_30d,
        "replied": member.replies_sent_30d,
        "dismissed": member.notifications_dismissed_30d,
    }
