"""In-memory lookups over the dataset, and the per-message context bundle.

Everything here is plain fact-finding. Nothing in this module decides how a
message should be routed; that is the agent's job.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .loaders import TIMESTAMP_FORMAT, Dataset, load_dataset
from .records import Business, GroupMember, Message, UserBusiness

OUTCOMES = ("replied", "opened", "rejected", "ignored")


def load_store(root: Optional[Path] = None) -> "ContextStore":
    return ContextStore(load_dataset(root))


class ContextStore:
    def __init__(self, data: Dataset):
        self.data = data
        self.users = {u.user_id: u for u in data.users}
        self.groups = {g.group_id: g for g in data.groups}
        self.members = {(m.group_id, m.user_id): m for m in data.members}
        self.businesses = {b.business_id: b for b in data.businesses}
        self.relationships = {(r.user_id, r.business_id): r for r in data.relationships}
        self.incoming = {m.message_id: m for m in data.incoming}
        self.history = {m.message_id: m for m in data.history}

        self._history_by_user: Dict[str, List[Message]] = defaultdict(list)
        for m in sorted(data.history, key=lambda m: (m.created_at, m.message_id)):
            self._history_by_user[m.user_id].append(m)

        self._daily_by_user = defaultdict(list)
        for d in sorted(data.daily_load, key=lambda d: d.day):
            self._daily_by_user[d.user_id].append(d)

    # ---- lookups -------------------------------------------------------

    def message(self, message_id: str) -> Message:
        """An incoming message from messages.csv or sample_messages.csv."""
        return self.incoming[message_id]

    def membership(self, group_id: Optional[str], user_id: Optional[str]) -> Optional[GroupMember]:
        if not group_id or not user_id:
            return None
        return self.members.get((group_id, user_id))

    def relationship(self, user_id: str, business_id: Optional[str]) -> Optional[UserBusiness]:
        return self.relationships.get((user_id, business_id)) if business_id else None

    def media_path(self, message: Message) -> Optional[Path]:
        if message.media_type == "image":
            return self.data.images.get(message.media_id or "")
        if message.media_type == "voice":
            return self.data.voice_notes.get(message.media_id or "")
        return None

    def user_history(
        self,
        user_id: str,
        before: Optional[datetime] = None,
        sender_key: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> List[Message]:
        """This user's past messages, oldest first, optionally filtered.

        Pass `before` (the incoming message's time) so nothing from the future leaks in.
        """
        result = []
        for m in self._history_by_user.get(user_id, []):
            if before is not None and m.created_at >= before:
                continue
            if sender_key is not None and m.sender_key != sender_key:
                continue
            if group_id is not None and m.group_id != group_id:
                continue
            result.append(m)
        return result

    @staticmethod
    def reaction_summary(messages: Iterable[Message]) -> Dict[str, int]:
        """Counts of the user's reactions: replied, opened, rejected, ignored, total."""
        counts = Counter(m.reaction.outcome for m in messages if m.reaction)
        summary = {outcome: counts.get(outcome, 0) for outcome in OUTCOMES}
        summary["total"] = sum(counts.values())
        return summary

    def recent_load(self, user_id: str, before: date, days: int = 7) -> Optional[Dict[str, Any]]:
        """Notification load over the most recent `days` days on record before `before`.

        The summary file can stop well before the message date, so `last_day` is
        returned to show how current the numbers are.
        """
        rows = [d for d in self._daily_by_user.get(user_id, []) if d.day < before][-days:]
        if not rows:
            return None
        sent = sum(d.notifications_sent for d in rows)
        dismissed = sum(d.notifications_dismissed for d in rows)
        return {
            "days_on_record": len(rows),
            "first_day": rows[0].day.isoformat(),
            "last_day": rows[-1].day.isoformat(),
            "avg_sent_per_day": round(sent / len(rows), 1),
            "avg_dismissed_per_day": round(dismissed / len(rows), 1),
            "dismiss_rate": round(dismissed / sent, 2) if sent else None,
        }

    # ---- the context bundle --------------------------------------------

    def bundle(self, message_id: str, history_limit: int = 10) -> Dict[str, Any]:
        """Everything the dataset says about one incoming message, as plain JSON-ready data."""
        m = self.message(message_id)
        user = self.users[m.user_id]
        before = m.created_at

        recipient = {
            "user_id": user.user_id,
            "quiet_window": user.quiet_window,
            "in_quiet_hours": user.in_quiet_hours(m.created_at),
            "activity_30d": {
                "opened": user.messages_opened_30d,
                "replied": user.messages_replied_30d,
                "dismissed": user.notifications_dismissed_30d,
                "reported": user.messages_reported_30d,
            },
            "recent_load": self.recent_load(user.user_id, before.date()),
        }

        same_sender = self.user_history(m.user_id, before=before, sender_key=m.sender_key)
        history: Dict[str, Any] = {
            "total_before_message": len(self.user_history(m.user_id, before=before)),
            "same_sender": self._history_block(same_sender, history_limit),
        }
        if m.group_id:
            same_group = self.user_history(m.user_id, before=before, group_id=m.group_id)
            history["same_group"] = self._history_block(same_group, history_limit)

        return {
            "message": _message_dict(m, self.media_path(m)),
            "recipient": recipient,
            "sender": self._sender_dict(m, known=bool(same_sender)),
            "group": self._group_dict(m),
            "business_relationship": _relationship_dict(self.relationship(m.user_id, m.business_id)),
            "history": history,
        }

    def _sender_dict(self, m: Message, known: bool) -> Dict[str, Any]:
        if m.business_id:
            return {"kind": "business", **_business_dict(self.businesses[m.business_id])}
        sender = {"kind": "person", "user_id": m.sender_user_id, "has_messaged_user_before": known}
        member = self.membership(m.group_id, m.sender_user_id)
        if member:
            sender["group_role"] = member.role
            sender["group_activity_30d"] = _member_activity(member)
        return sender

    def _group_dict(self, m: Message) -> Optional[Dict[str, Any]]:
        if not m.group_id:
            return None
        g = self.groups[m.group_id]
        member = self.membership(m.group_id, m.user_id)
        return {
            "group_id": g.group_id,
            "name": g.group_name,
            "type": g.group_type,
            "member_count": g.member_count,
            "admin_count": g.admin_count,
            "messages_30d": g.messages_30d,
            "recipient_role": member.role if member else None,
            "recipient_muted_group": member.muted if member else None,
            "recipient_activity_30d": _member_activity(member) if member else None,
        }

    @staticmethod
    def _history_block(messages: List[Message], limit: int) -> Dict[str, Any]:
        recent = messages[-limit:] if limit else []
        return {
            "summary": ContextStore.reaction_summary(messages),
            "messages": [_message_dict(h) for h in reversed(recent)],
        }

    # ---- integrity -----------------------------------------------------

    def validate(self) -> List[str]:
        """Problems that would make lookups unreliable. An empty list means the data is consistent."""
        problems = []
        overlap = set(self.incoming) & set(self.history)
        if overlap:
            problems.append(f"{len(overlap)} incoming message IDs also appear in history")
        problems += [f"{m.message_id}: no reaction recorded" for m in self.data.history if m.reaction is None]
        for m in list(self.data.incoming) + list(self.data.history):
            problems += [f"{m.message_id}: {p}" for p in self._reference_problems(m)]
        for media_id, path in {**self.data.images, **self.data.voice_notes}.items():
            if not path.exists():
                problems.append(f"{media_id}: file missing at {path}")
        return problems

    def _reference_problems(self, m: Message) -> List[str]:
        problems = []
        if m.user_id not in self.users:
            problems.append(f"unknown recipient {m.user_id}")
        if m.sender_user_id and m.sender_user_id not in self.users:
            problems.append(f"unknown sender {m.sender_user_id}")
        if m.group_id and m.group_id not in self.groups:
            problems.append(f"unknown group {m.group_id}")
        if m.group_id and not self.membership(m.group_id, m.user_id):
            problems.append(f"recipient not a member of {m.group_id}")
        if m.business_id and m.business_id not in self.businesses:
            problems.append(f"unknown business {m.business_id}")
        if m.media_type and self.media_path(m) is None:
            problems.append(f"unknown {m.media_type} {m.media_id}")
        return problems


def _fmt(when: Optional[datetime]) -> Optional[str]:
    return when.strftime(TIMESTAMP_FORMAT) if when else None


def _message_dict(m: Message, media_path: Optional[Path] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "message_id": m.message_id,
        "created_at": _fmt(m.created_at),
        "conversation_type": m.conversation_type,
        "from": m.sender_key,
        "group_id": m.group_id,
        "text": m.text,
        "media": f"{m.media_type}:{m.media_id}" if m.media_type else None,
        "forwarded_count": m.forwarded_count,
    }
    if media_path is not None:
        d["media_path"] = str(media_path)
    if m.reaction is not None:
        d["user_reaction"] = m.reaction.describe()
        d["outcome"] = m.reaction.outcome
    return d


def _business_dict(b: Business) -> Dict[str, Any]:
    return {
        "business_id": b.business_id,
        "display_name": b.display_name,
        "brand_name": b.brand_name,
        "category": b.category,
        "verified": b.verified,
        "official_domain": b.official_domain,
        "sender_domain": b.sender_domain,
        "domain_matches_official": b.domain_matches_official,
        "sender_domain_age_days": b.sender_domain_age_days,
        "account_age_days": b.account_age_days,
        "messages_sent_30d": b.messages_sent_30d,
        "user_reports_30d": b.user_reports_30d,
    }


def _relationship_dict(r: Optional[UserBusiness]) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    return {
        "why_user_knows_account": r.why_user_knows_account,
        "last_activity_at": _fmt(r.last_activity_at),
        "allows_promotions": r.allows_promotions,
        "promotions_opted_out_at": _fmt(r.promotions_opted_out_at),
        "activity_count_180d": r.activity_count_180d,
        "opened_30d": r.messages_opened_30d,
        "dismissed_30d": r.messages_dismissed_30d,
        "replied_30d": r.messages_replied_30d,
        "last_reply_at": _fmt(r.last_reply_at),
    }


def _member_activity(member: GroupMember) -> Dict[str, Any]:
    return {
        "sent": member.messages_sent_30d,
        "read": member.messages_read_30d,
        "replied": member.replies_sent_30d,
        "dismissed": member.notifications_dismissed_30d,
    }
