"""Tests for media perception, using a fake client (no network)."""

import json
import tempfile
import unittest
from pathlib import Path

from context import load_store
from perception.media import (
    load_media_entry,
    normalise_image_analysis,
    parse_json_reply,
    pending_media,
    perceive_all,
)

IMAGE_REPLY = json.dumps({
    "kind": "poster", "ocr_text": "50% OFF", "summary": "A sale poster.", "brands": ["Shop"],
    "urls": [], "qr_present": False, "asks_for": ["visit_link"], "deadlines": ["today"], "language": "en",
})


class FakeClient:
    def __init__(self, image_reply=IMAGE_REPLY):
        self.image_reply = image_reply
        self.chat_calls = 0
        self.transcribe_calls = 0

    def chat(self, model, messages, **kwargs):
        self.chat_calls += 1
        return {"choices": [{"message": {"content": self.image_reply}}]}

    @staticmethod
    def message_text(response):
        return response["choices"][0]["message"]["content"]

    def transcribe(self, path, model, **kwargs):
        self.transcribe_calls += 1
        return {"text": "  Call me when you can.  ", "language": "english", "duration": 3.5787}


class ParsingTest(unittest.TestCase):
    def test_parse_ignores_reasoning_and_fences(self):
        reply = '<think>let me look</think>\n```json\n{"kind": "photo"}\n```\nDone.'
        self.assertEqual(parse_json_reply(reply), {"kind": "photo"})

    def test_parse_rejects_replies_without_json(self):
        with self.assertRaises(ValueError):
            parse_json_reply("I cannot read this image.")

    def test_normalise_coerces_and_filters(self):
        result = normalise_image_analysis({
            "kind": "Meme", "brands": "Bank", "qr_present": "yes",
            "asks_for": ["OTP", "none", "dance"], "urls": None,
        })
        self.assertEqual(result["kind"], "other")
        self.assertEqual(result["brands"], ["Bank"])
        self.assertTrue(result["qr_present"])
        self.assertEqual(result["asks_for"], ["otp"])
        self.assertEqual(result["urls"], [])
        self.assertEqual(result["ocr_text"], "")

    def test_normalise_defaults_asks_for_to_none(self):
        self.assertEqual(normalise_image_analysis({})["asks_for"], ["none"])


class PerceiveAllTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_store()
        cls.total = len(cls.store.data.images) + len(cls.store.data.voice_notes)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_all(self, client, **kwargs):
        return perceive_all(self.store, client, cache_dir=self.cache, log=lambda _: None, **kwargs)

    def test_first_run_caches_everything_and_second_run_skips(self):
        client = FakeClient()
        result = self.run_all(client)
        self.assertEqual(len(result["analysed"]), self.total)
        self.assertEqual(pending_media(self.store, cache_dir=self.cache), [])
        self.run_all(client)
        self.assertEqual(client.chat_calls + client.transcribe_calls, self.total)

    def test_force_redoes_everything(self):
        self.run_all(FakeClient())
        self.assertEqual(len(pending_media(self.store, cache_dir=self.cache, force=True)), self.total)

    def test_only_limits_the_run(self):
        result = self.run_all(FakeClient(), only=["img_026", "vn_001"])
        self.assertEqual(sorted(result["analysed"]), ["img_026", "vn_001"])

    def test_entries_record_provenance(self):
        self.run_all(FakeClient(), only=["img_026", "vn_001"])
        image = load_media_entry("img_026", self.cache)
        self.assertEqual(image["file"], "media/images/img_026.jpg")
        self.assertEqual(image["analysis"]["asks_for"], ["visit_link"])
        self.assertEqual(len(image["file_sha256"]), 64)
        voice = load_media_entry("vn_001", self.cache)
        self.assertEqual(voice["analysis"]["transcript"], "Call me when you can.")
        self.assertEqual(voice["analysis"]["duration_seconds"], 3.58)
        self.assertIsNone(voice["analysis"]["tone"])

    def test_invalid_json_is_retried_once_then_reported(self):
        client = FakeClient(image_reply="sorry, no JSON here")
        result = self.run_all(client, only=["img_026"])
        self.assertEqual(result["failed"], ["img_026"])
        self.assertEqual(client.chat_calls, 2)
        self.assertIsNone(load_media_entry("img_026", self.cache))


if __name__ == "__main__":
    unittest.main()
