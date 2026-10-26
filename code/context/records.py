"""Typed records for the dataset. Field names follow the CSV columns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional, Tuple


def in_window(start: time, end: time, moment: time) -> bool:
    """True if `moment` falls in [start, end), including windows that cross midnight."""
    if start == end:
        return False
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


@dataclass(frozen=True)
class User:
    user_id: str
    quiet_start: time
    quiet_end: time
    messages_opened_30d: int
    messages_replied_30d: int
    notifications_dismissed_30d: int
    messages_reported_30d: int

    @property
    def quiet_window(self) -> str:
        return f"{self.quiet_start:%H:%M}-{self.quiet_end:%H:%M}"

    def in_quiet_hours(self, when: datetime) -> bool:
        return in_window(self.quiet_start, self.quiet_end, when.time())


@dataclass(frozen=True)
class Group:
    group_id: str
    group_name: str
    group_type: str
    member_count: int
    admin_count: int
    created_at: date
    messages_30d: int


@dataclass(frozen=True)
class GroupMember:
    group_id: str
    user_id: str
    role: str
    joined_at: date
    messages_sent_30d: int
    messages_read_30d: int
    replies_sent_30d: int
    notifications_dismissed_30d: int
    muted: bool

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class Business:
    business_id: str
    display_name: str
    brand_name: str
    category: str
    verified: bool
    official_domain: str
    sender_domain: str
    account_age_days: int
    messages_sent_30d: int
    user_reports_30d: int
    sender_domain_age_days: int

    @property
    def domain_matches_official(self) -> bool:
        return self.sender_domain.lower() == self.official_domain.lower()


@dataclass(frozen=True)
class UserBusiness:
    """A user's relationship with a business (user_business_history.csv)."""

    user_id: str
    business_id: str
    why_user_knows_account: str
    last_activity_at: datetime
    allows_promotions: bool
    promotions_opted_out_at: Optional[datetime]
    activity_count_180d: int
    messages_opened_30d: int
    messages_dismissed_30d: int
    messages_replied_30d: int
    last_reply_at: Optional[datetime]


@dataclass(frozen=True)
class Reaction:
    """How the receiving user reacted to a past message (message_events.csv)."""

    opened: bool
    replied: bool
    reaction_time_minutes: Optional[int]
    dismissed: bool
    muted_after: bool
    reported: bool

    @property
    def outcome(self) -> str:
        """One word for the user's stance: rejected, replied, opened or ignored."""
        if self.reported or self.muted_after or self.dismissed:
            return "rejected"
        if self.replied:
            return "replied"
        if self.opened:
            return "opened"
        return "ignored"

    def describe(self) -> str:
        """Short plain-English summary, e.g. 'replied after 2 min'."""
        after = f" after {self.reaction_time_minutes} min" if self.reaction_time_minutes is not None else ""
        if self.replied:
            parts = [f"replied{after}"]
        elif self.opened:
            parts = [f"opened{after}, no reply"]
        else:
            parts = ["not opened"]
        if self.dismissed:
            parts.append("dismissed the notification")
        if self.muted_after:
            parts.append("muted afterwards")
        if self.reported:
            parts.append("reported it")
        return ", ".join(parts)


@dataclass(frozen=True)
class Message:
    """An incoming or historical message. History messages carry the user's reaction."""

    message_id: str
    user_id: str
    conversation_type: str
    group_id: Optional[str]
    business_id: Optional[str]
    sender_user_id: Optional[str]
    created_at: datetime
    text: str
    media_type: Optional[str]
    media_id: Optional[str]
    forwarded_count: int
    reaction: Optional[Reaction] = None

    @property
    def sender_key(self) -> str:
        """Who sent it: a business ID for business messages, otherwise a user ID."""
        return self.business_id or self.sender_user_id or ""


@dataclass(frozen=True)
class DailyLoad:
    user_id: str
    day: date
    notifications_sent: int
    notifications_dismissed: int


@dataclass(frozen=True)
class Label:
    """A solved sample's expected output. For evaluation only; never shown to the agent."""

    message_id: str
    action: str
    message_type: str
    reason: str
    confidence: float
    evidence_message_ids: Tuple[str, ...]
