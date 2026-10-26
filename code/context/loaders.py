"""Read the dataset CSV files into typed records."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional

from .records import (
    Business,
    DailyLoad,
    Group,
    GroupMember,
    Label,
    Message,
    Reaction,
    User,
    UserBusiness,
)

DEFAULT_DATASET_DIR = Path(__file__).resolve().parents[2] / "dataset"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"


def dataset_dir() -> Path:
    """The dataset folder: $DATASET_DIR if set, otherwise <repo>/dataset."""
    return Path(os.environ.get("DATASET_DIR", DEFAULT_DATASET_DIR))


@dataclass
class Dataset:
    root: Path
    users: List[User]
    groups: List[Group]
    members: List[GroupMember]
    businesses: List[Business]
    relationships: List[UserBusiness]
    history: List[Message]
    incoming: List[Message]
    daily_load: List[DailyLoad]
    images: Dict[str, Path]
    voice_notes: Dict[str, Path]


def load_dataset(root: Optional[Path] = None) -> Dataset:
    """Load every context file. Sample labels are deliberately left out (see load_sample_labels)."""
    root = Path(root) if root else dataset_dir()
    reactions = {(r["user_id"], r["message_id"]): _reaction(r) for r in _rows(root, "message_events")}
    history = [
        _message(r, reaction=reactions.get((r["user_id"], r["message_id"])))
        for r in _rows(root, "message_history")
    ]
    incoming = [_message(r) for r in _rows(root, "messages")]
    incoming += [_message(r) for r in _rows(root, "sample_messages")]
    return Dataset(
        root=root,
        users=[_user(r) for r in _rows(root, "users")],
        groups=[_group(r) for r in _rows(root, "groups")],
        members=[_member(r) for r in _rows(root, "group_members")],
        businesses=[_business(r) for r in _rows(root, "business_accounts")],
        relationships=[_relationship(r) for r in _rows(root, "user_business_history")],
        history=history,
        incoming=incoming,
        daily_load=[_daily(r) for r in _rows(root, "daily_notification_summary")],
        images={r["image_id"]: root / r["file_path"] for r in _rows(root, "images")},
        voice_notes={r["voice_note_id"]: root / r["file_path"] for r in _rows(root, "voice_notes")},
    )


def load_sample_labels(root: Optional[Path] = None) -> Dict[str, Label]:
    """Expected outputs for sample_messages.csv. For scoring only; never pass these to an agent."""
    root = Path(root) if root else dataset_dir()
    labels = {}
    for r in _rows(root, "sample_messages"):
        evidence = r["evidence_message_ids"].strip()
        labels[r["message_id"]] = Label(
            message_id=r["message_id"],
            action=r["action"],
            message_type=r["message_type"],
            reason=r["reason"],
            confidence=float(r["confidence"]),
            evidence_message_ids=() if evidence in ("", "none") else tuple(evidence.split(";")),
        )
    return labels


def _rows(root: Path, name: str) -> List[Dict[str, str]]:
    with (root / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _text(value: str) -> Optional[str]:
    value = value.strip()
    return value or None


def _int(value: str) -> int:
    return int(value)


def _opt_int(value: str) -> Optional[int]:
    return int(value) if value.strip() else None


def _bool(value: str) -> bool:
    return value.strip() == "1"


def _timestamp(value: str) -> datetime:
    return datetime.strptime(value.strip(), TIMESTAMP_FORMAT)


def _opt_timestamp(value: str) -> Optional[datetime]:
    return _timestamp(value) if value.strip() else None


def _date(value: str) -> date:
    return date.fromisoformat(value.strip())


def _clock(value: str) -> time:
    hours, minutes = value.strip().split(":")
    return time(int(hours), int(minutes))


def _user(r: Dict[str, str]) -> User:
    start, end = r["do_not_disturb_window"].split("-")
    return User(
        user_id=r["user_id"],
        quiet_start=_clock(start),
        quiet_end=_clock(end),
        messages_opened_30d=_int(r["messages_opened_30d"]),
        messages_replied_30d=_int(r["messages_replied_30d"]),
        notifications_dismissed_30d=_int(r["notifications_dismissed_30d"]),
        messages_reported_30d=_int(r["messages_reported_30d"]),
    )


def _group(r: Dict[str, str]) -> Group:
    return Group(
        group_id=r["group_id"],
        group_name=r["group_name"],
        group_type=r["group_type"],
        member_count=_int(r["member_count"]),
        admin_count=_int(r["admin_count"]),
        created_at=_date(r["created_at"]),
        messages_30d=_int(r["messages_30d"]),
    )


def _member(r: Dict[str, str]) -> GroupMember:
    return GroupMember(
        group_id=r["group_id"],
        user_id=r["user_id"],
        role=r["role"],
        joined_at=_date(r["joined_at"]),
        messages_sent_30d=_int(r["messages_sent_30d"]),
        messages_read_30d=_int(r["messages_read_30d"]),
        replies_sent_30d=_int(r["replies_sent_30d"]),
        notifications_dismissed_30d=_int(r["notifications_dismissed_30d"]),
        muted=_bool(r["group_muted_by_user"]),
    )


def _business(r: Dict[str, str]) -> Business:
    return Business(
        business_id=r["business_id"],
        display_name=r["display_name"],
        brand_name=r["brand_name"],
        category=r["category"],
        verified=_bool(r["verified"]),
        official_domain=r["official_domain"],
        sender_domain=r["domain_used_by_sender"],
        account_age_days=_int(r["account_age_days"]),
        messages_sent_30d=_int(r["messages_sent_30d"]),
        user_reports_30d=_int(r["user_reports_30d"]),
        sender_domain_age_days=_int(r["domain_used_by_sender_age_days"]),
    )


def _relationship(r: Dict[str, str]) -> UserBusiness:
    return UserBusiness(
        user_id=r["user_id"],
        business_id=r["business_id"],
        why_user_knows_account=r["why_user_knows_account"],
        last_activity_at=_timestamp(r["last_activity_at"]),
        allows_promotions=_bool(r["allows_promotions"]),
        promotions_opted_out_at=_opt_timestamp(r["promotions_opted_out_at"]),
        activity_count_180d=_int(r["activity_count_180d"]),
        messages_opened_30d=_int(r["messages_opened_30d"]),
        messages_dismissed_30d=_int(r["messages_dismissed_30d"]),
        messages_replied_30d=_int(r["messages_replied_30d"]),
        last_reply_at=_opt_timestamp(r["last_reply_at"]),
    )


def _reaction(r: Dict[str, str]) -> Reaction:
    return Reaction(
        opened=_bool(r["message_opened"]),
        replied=_bool(r["message_replied"]),
        reaction_time_minutes=_opt_int(r["reaction_time_minutes"]),
        dismissed=_bool(r["notification_dismissed"]),
        muted_after=_bool(r["muted_after_message"]),
        reported=_bool(r["message_reported"]),
    )


def _message(r: Dict[str, str], reaction: Optional[Reaction] = None) -> Message:
    # Only input columns are read, so label columns in sample_messages.csv never reach a Message.
    return Message(
        message_id=r["message_id"],
        user_id=r["user_id"],
        conversation_type=r["conversation_type"],
        group_id=_text(r["group_id"]),
        business_id=_text(r["business_id"]),
        sender_user_id=_text(r["sender_user_id"]),
        created_at=_timestamp(r["created_at"]),
        text=r["message_text"],
        media_type=_text(r["media_type"]),
        media_id=_text(r["media_id"]),
        forwarded_count=_int(r["forwarded_count"]),
        reaction=reaction,
    )


def _daily(r: Dict[str, str]) -> DailyLoad:
    return DailyLoad(
        user_id=r["user_id"],
        day=_date(r["date"]),
        notifications_sent=_int(r["notifications_sent"]),
        notifications_dismissed=_int(r["notifications_dismissed"]),
    )
