"""Tests for the context store. Run from the repository root:

    python3 -m unittest discover -s code/tests -t code -v
"""

import json
import unittest
from dataclasses import fields
from datetime import datetime, time

from context import load_sample_labels, load_store
from context.loaders import TIMESTAMP_FORMAT
from context.records import Message, Reaction, in_window


def _parse(value):
    return datetime.strptime(value, TIMESTAMP_FORMAT)


class LoadingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_store()

    def test_counts_match_the_dataset(self):
        data = self.store.data
        self.assertEqual(len(data.incoming), 110 + 30)
        self.assertEqual(len(data.history), 412)
        self.assertEqual(len(data.users), 54)
        self.assertEqual(len(data.images), 20)
        self.assertEqual(len(data.voice_notes), 13)

    def test_every_history_message_has_a_reaction(self):
        self.assertTrue(all(m.reaction is not None for m in self.store.data.history))

    def test_dataset_is_consistent(self):
        self.assertEqual(self.store.validate(), [])

    def test_sample_labels_never_reach_incoming_messages(self):
        message_fields = {f.name for f in fields(Message)}
        for label_column in ("action", "message_type", "reason", "confidence", "evidence_message_ids"):
            self.assertNotIn(label_column, message_fields)
        self.assertIn("sample_msg_001", self.store.incoming)

    def test_sample_labels_load_separately(self):
        labels = load_sample_labels()
        self.assertEqual(len(labels), 30)
        self.assertEqual(labels["sample_msg_013"].evidence_message_ids, ("message_0013", "message_0014"))
        self.assertEqual(labels["sample_msg_049"].evidence_message_ids, ())


class QuietHoursTest(unittest.TestCase):
    def test_window_crossing_midnight(self):
        start, end = time(22, 0), time(7, 0)
        self.assertTrue(in_window(start, end, time(23, 30)))
        self.assertTrue(in_window(start, end, time(6, 59)))
        self.assertFalse(in_window(start, end, time(7, 0)))
        self.assertFalse(in_window(start, end, time(12, 0)))

    def test_window_starting_at_midnight(self):
        start, end = time(0, 0), time(6, 30)
        self.assertTrue(in_window(start, end, time(0, 0)))
        self.assertFalse(in_window(start, end, time(6, 30)))
        self.assertFalse(in_window(start, end, time(23, 59)))


class ReactionTest(unittest.TestCase):
    def test_rejection_outranks_opening(self):
        r = Reaction(opened=True, replied=False, reaction_time_minutes=9,
                     dismissed=True, muted_after=False, reported=False)
        self.assertEqual(r.outcome, "rejected")
        self.assertEqual(r.describe(), "opened after 9 min, no reply, dismissed the notification")

    def test_reply(self):
        r = Reaction(opened=True, replied=True, reaction_time_minutes=2,
                     dismissed=False, muted_after=False, reported=False)
        self.assertEqual(r.outcome, "replied")
        self.assertEqual(r.describe(), "replied after 2 min")


class BundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_store()
        cls.bundles = {mid: cls.store.bundle(mid) for mid in cls.store.incoming}

    def test_bundles_are_json_serialisable(self):
        for bundle in self.bundles.values():
            json.dumps(bundle)

    def test_history_never_includes_later_messages(self):
        for mid, bundle in self.bundles.items():
            sent = _parse(bundle["message"]["created_at"])
            for section in ("same_sender", "same_group"):
                for past in bundle["history"].get(section, {}).get("messages", []):
                    self.assertLess(_parse(past["created_at"]), sent, mid)

    def test_user_history_respects_cutoff(self):
        user_id = "u_001"
        everything = self.store.user_history(user_id)
        cutoff = everything[len(everything) // 2].created_at
        earlier = self.store.user_history(user_id, before=cutoff)
        self.assertTrue(0 < len(earlier) < len(everything))
        self.assertTrue(all(m.created_at < cutoff for m in earlier))

    def test_same_sender_history_matches_sender(self):
        for mid, bundle in self.bundles.items():
            sender = bundle["message"]["from"]
            for past in bundle["history"]["same_sender"]["messages"]:
                self.assertEqual(past["from"], sender, mid)

    def test_business_domain_flag(self):
        for mid, bundle in self.bundles.items():
            sender = bundle["sender"]
            if sender["kind"] == "business":
                expected = sender["official_domain"].lower() == sender["sender_domain"].lower()
                self.assertEqual(sender["domain_matches_official"], expected, mid)

    def test_group_roles_and_mute_state(self):
        for mid, bundle in self.bundles.items():
            message = self.store.message(mid)
            if not message.group_id:
                self.assertIsNone(bundle["group"], mid)
                continue
            recipient = self.store.membership(message.group_id, message.user_id)
            self.assertEqual(bundle["group"]["recipient_muted_group"], recipient.muted, mid)
            sender = self.store.membership(message.group_id, message.sender_user_id)
            if sender:
                self.assertEqual(bundle["sender"]["group_role"], sender.role, mid)

    def test_recent_load_only_uses_days_before_the_message(self):
        for mid, bundle in self.bundles.items():
            load = bundle["recipient"]["recent_load"]
            if load is None:
                continue
            self.assertLess(load["last_day"], bundle["message"]["created_at"][:10], mid)
            self.assertLessEqual(load["days_on_record"], 7, mid)

    def test_media_messages_point_at_existing_files(self):
        for mid, bundle in self.bundles.items():
            if bundle["message"]["media"]:
                self.assertIn("media_path", bundle["message"], mid)


if __name__ == "__main__":
    unittest.main()
