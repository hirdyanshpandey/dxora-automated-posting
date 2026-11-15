import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.main import app


CONFIG = dict(
    BLUESKY_HANDLE="demo.example", BLUESKY_APP_PASSWORD="test",
    MASTODON_ORIGIN="https://mastodon.example", MASTODON_TOKEN="test",
    SLACK_BOT_TOKEN="test", SLACK_CHANNEL_ID="C123",
    DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/123/test",
    DISCORD_GUILD_ID="123", TELEGRAM_BOT_TOKEN="test", TELEGRAM_CHAT_ID="@demo",
)


class PostingTests(unittest.TestCase):
    def test_five_integrations_and_partial_failure(self):
        for mastodon_status in (200, 429):
            with self.subTest(mastodon_status=mastodon_status):
                self.exercise_all_providers(mastodon_status)

    def exercise_all_providers(self, mastodon_status):
        message = "🚀 Launch https://example.com"

        def respond(req):
            path = req.url.path
            data = json.loads(req.content) if req.content else {}
            if path == "/v1/responses":
                self.assertEqual(data["input"], message)
                self.assertFalse(data["store"])
                return httpx.Response(500 if mastodon_status == 429 else 200, json={
                    "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": "Explain what readers will find."}
                    ]}]
                })
            if path.endswith("createSession"):
                self.assertEqual(data["identifier"], CONFIG["BLUESKY_HANDLE"])
                return httpx.Response(200, json={"did": "did:plc:demo", "accessJwt": "test"})
            if path.endswith("createRecord"):
                facet = data["record"]["facets"][0]
                self.assertEqual(facet["index"]["byteStart"], len("🚀 Launch ".encode()))
                self.assertEqual(facet["features"][0]["uri"], "https://example.com")
                return httpx.Response(200, json={"uri": "at://did:plc:demo/app.bsky.feed.post/123"})
            if path.endswith("/statuses"):
                self.assertEqual(data, {"status": message, "visibility": "public"})
                return httpx.Response(mastodon_status, json={"url": "https://mastodon.example/@demo/123", "visibility": "public"})
            if path.endswith("chat.postMessage"):
                self.assertEqual(data["channel"], "C123")
                return httpx.Response(200, json={"ok": True, "channel": "C123", "ts": "123.456"})
            if path.endswith("chat.getPermalink"):
                self.assertEqual(req.url.params["message_ts"], "123.456")
                return httpx.Response(200, json={"ok": True, "permalink": "https://demo.slack.com/archives/C123/p123456"})
            if req.url.host == "discord.com":
                self.assertEqual(req.url.params["wait"], "true")
                self.assertEqual(data["allowed_mentions"], {"parse": []})
                return httpx.Response(200, json={"id": "456", "channel_id": "789"})
            if path.endswith("sendMessage"):
                self.assertEqual(data["chat_id"], "@demo")
                return httpx.Response(200, json={"ok": True, "result": {"message_id": 2, "chat": {"type": "channel", "username": "demo"}}})
            self.fail(f"Unexpected route: {path}")

        with patch.dict(os.environ, {**CONFIG, "OPENAI_REVIEW_MODEL": "test-model", "OPENAI_API_KEY": "test"}, clear=True), patch(
            "app.main.make_client",
            return_value=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ), TestClient(app) as client:
            response = client.post("/posting", json={"message": message})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["llm_review"]["status"], "failed" if mastodon_status == 429 else "success")
        expected = {
            "Bluesky": ("https://bsky.app/profile/did:plc:demo/post/123", True),
            "Mastodon": ("https://mastodon.example/@demo/123", True),
            "Slack": ("https://demo.slack.com/archives/C123/p123456", False),
            "Discord": ("https://discord.com/channels/123/789/456", False),
            "Telegram": ("https://t.me/demo/2", True),
        }
        self.assertEqual({entry["platform"] for entry in payload["results"]}, set(expected))
        for entry in payload["results"]:
            if mastodon_status == 429 and entry["platform"] == "Mastodon":
                self.assertEqual(entry["status"], "failed")
                self.assertIn("429", entry["error"])
                self.assertIsNone(entry["posted_url"])
            else:
                self.assertEqual(entry["status"], "success")
                self.assertEqual((entry["posted_url"], entry["public_access"]), expected[entry["platform"]])
                self.assertRegex(entry["date"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertRegex(entry["time"], r"^\d{2}:\d{2}:\d{2}$")

    def test_invalid_requests_never_contact_providers(self):
        bad = [
            {}, {"message": "No link"}, {"message": "http://"},
            {"message": "https://example.com https://example.org"},
            {"message": "ftp://example.com"}, {"message": "https://name:password@example.com"},
            {"message": "x" * 201 + " https://example.com"},
            {"message": "https://example.com", "platform": "Slack"},
        ]
        with TestClient(app) as client, patch("app.main.make_client") as factory:
            for payload in bad:
                with self.subTest(payload=payload):
                    self.assertEqual(client.post("/posting", json=payload).status_code, 422)
            factory.assert_not_called()

    def test_unconfigured_targets_are_reported(self):
        with patch.dict(os.environ, {}, clear=True), TestClient(app) as client:
            response = client.post("/posting", json={"message": "See https://example.com."})
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["llm_review"]["status"], "disabled")
        self.assertEqual(len(payload["results"]), 5)
        self.assertTrue(all(entry["status"] == "failed" for entry in payload["results"]))
