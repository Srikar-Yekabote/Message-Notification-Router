"""Tests for the Groq client, using a fake transport (no network)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from llm import GroqClient, GroqError, RateLimitError, load_env_file

CHAT_REPLY = {"choices": [{"message": {"content": "hello"}}]}


def ok(payload, headers=None):
    return 200, headers or {}, json.dumps(payload).encode("utf-8")


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class GroqClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, transport, key="test-key"):
        return GroqClient(api_key=key, cache_dir=self.cache, transport=transport, sleep=self.sleeps.append)

    def chat(self, client):
        return client.chat("some-model", [{"role": "user", "content": "hi"}])

    def test_chat_request_shape(self):
        transport = FakeTransport(ok(CHAT_REPLY))
        self.chat(self.client(transport))
        call = transport.calls[0]
        self.assertTrue(call["url"].endswith("/chat/completions"))
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        body = json.loads(call["body"])
        self.assertEqual((body["model"], body["temperature"], body["seed"]), ("some-model", 0.0, 7))

    def test_chat_is_cached(self):
        transport = FakeTransport(ok(CHAT_REPLY))
        client = self.client(transport)
        first, second = self.chat(client), self.chat(client)
        self.assertEqual(first, second)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(GroqClient.message_text(second), "hello")

    def test_cached_reply_needs_no_api_key(self):
        self.chat(self.client(FakeTransport(ok(CHAT_REPLY))))
        transport = FakeTransport()
        self.assertEqual(self.chat(self.client(transport, key="")), CHAT_REPLY)
        self.assertEqual(transport.calls, [])

    def test_missing_key_fails_before_any_request(self):
        transport = FakeTransport()
        with self.assertRaises(GroqError):
            self.chat(self.client(transport, key=""))
        self.assertEqual(transport.calls, [])

    def test_rate_limit_waits_for_retry_after(self):
        limited = (429, {"retry-after": "1.5"}, b'{"error": {"message": "slow down"}}')
        transport = FakeTransport(limited, ok(CHAT_REPLY))
        self.assertEqual(self.chat(self.client(transport)), CHAT_REPLY)
        self.assertEqual(self.sleeps, [1.5])

    def test_request_too_large_is_not_retried(self):
        too_large = (429, {}, b'{"error": {"message": "Request too large for model on output tokens per minute"}}')
        transport = FakeTransport(too_large)
        with self.assertRaises(GroqError):
            self.chat(self.client(transport))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_network_errors_are_retried(self):
        transport = FakeTransport(OSError("connection reset"), ok(CHAT_REPLY))
        self.assertEqual(self.chat(self.client(transport)), CHAT_REPLY)
        self.assertEqual(len(transport.calls), 2)

    def test_client_errors_are_not_retried_and_hide_the_key(self):
        transport = FakeTransport((400, {}, b'{"error": {"message": "bad request"}}'))
        with self.assertRaises(GroqError) as caught:
            self.chat(self.client(transport))
        self.assertEqual(len(transport.calls), 1)
        self.assertIn("bad request", str(caught.exception))
        self.assertNotIn("test-key", str(caught.exception))

    def test_failed_calls_are_not_cached(self):
        transport = FakeTransport((400, {}, b"{}"), ok(CHAT_REPLY))
        client = self.client(transport)
        with self.assertRaises(GroqError):
            self.chat(client)
        self.assertEqual(self.chat(client), CHAT_REPLY)

    def test_tool_calling_options_are_sent(self):
        transport = FakeTransport(ok(CHAT_REPLY))
        tools = [{"type": "function", "function": {"name": "gather", "parameters": {"type": "object"}}}]
        self.client(transport).chat("m", [{"role": "user", "content": "hi"}], tools=tools, tool_choice="auto",
                                    reasoning_effort="low", max_tokens=50)
        body = json.loads(transport.calls[0]["body"])
        self.assertEqual((body["tools"], body["tool_choice"], body["reasoning_effort"], body["max_tokens"]),
                         (tools, "auto", "low", 50))

    def test_cache_hits_are_reported(self):
        client = self.client(FakeTransport(ok(CHAT_REPLY)))
        self.chat(client)
        self.assertFalse(client.last_from_cache)
        self.chat(client)
        self.assertTrue(client.last_from_cache)
        self.assertEqual((client.live_calls, client.cache_hits), (1, 1))

    def test_long_rate_limit_waits_are_not_slept_through(self):
        limited = (429, {"retry-after": "3600"}, b'{"error": {"message": "slow down"}}')
        transport = FakeTransport(limited)
        with self.assertRaises(RateLimitError) as caught:
            self.chat(self.client(transport))
        self.assertEqual(caught.exception.retry_after, 3600.0)
        self.assertEqual(self.sleeps, [])

    def test_daily_quota_is_not_retried(self):
        spent = (429, {}, b'{"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 200000"}}')
        transport = FakeTransport(spent)
        with self.assertRaises(RateLimitError):
            self.chat(self.client(transport))
        self.assertEqual((len(transport.calls), self.sleeps), (1, []))

    def test_transcribe_sends_multipart_and_caches(self):
        audio = self.cache / "clip.mp3"
        audio.write_bytes(b"ID3-fake-audio")
        transport = FakeTransport(ok({"text": "hi there"}))
        client = self.client(transport)
        first = client.transcribe(audio, model="whisper-large-v3")
        second = client.transcribe(audio, model="whisper-large-v3")
        self.assertEqual(first, second)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertTrue(call["url"].endswith("/audio/transcriptions"))
        self.assertTrue(call["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="model"\r\n\r\nwhisper-large-v3', call["body"])
        self.assertIn(b"ID3-fake-audio", call["body"])


class EnvFileTest(unittest.TestCase):
    def test_loads_values_without_overriding(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text('# comment\nROUTER_TEST_A=from-file\nROUTER_TEST_B="two"\n', encoding="utf-8")
            os.environ["ROUTER_TEST_A"] = "already-set"
            try:
                load_env_file(env)
                self.assertEqual(os.environ["ROUTER_TEST_A"], "already-set")
                self.assertEqual(os.environ["ROUTER_TEST_B"], "two")
            finally:
                os.environ.pop("ROUTER_TEST_A", None)
                os.environ.pop("ROUTER_TEST_B", None)


if __name__ == "__main__":
    unittest.main()
