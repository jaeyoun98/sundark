import logging
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main
from app.errors import AIError


class ChatGPTApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8743")
        self.origin = {"Origin": "http://127.0.0.1:8743"}

    def test_login_requires_local_same_origin(self):
        with patch.object(main.chatgpt_auth, "begin", new=AsyncMock()) as begin:
            for headers in ({}, {"Origin": "https://example.org"}):
                response = self.client.post(
                    "/api/chatgpt/login", json={}, headers=headers
                )
                self.assertEqual(response.status_code, 403)
            begin.assert_not_awaited()
            begin.return_value = {"url": "https://auth.openai.com/authorize"}
            response = self.client.post(
                "/api/chatgpt/login", json={}, headers=self.origin
            )
            self.assertEqual(response.status_code, 200)
            begin.assert_awaited_once_with(
                "http://127.0.0.1:8743/api/chatgpt/callback",
                None,
                new_account=False,
            )
            response = self.client.post(
                "/api/chatgpt/login",
                json={"new_account": True},
                headers=self.origin,
            )
            self.assertEqual(response.status_code, 200)
            begin.assert_awaited_with(
                "http://127.0.0.1:8743/api/chatgpt/callback",
                None,
                new_account=True,
            )
            response = self.client.post(
                "/api/chatgpt/login",
                json={"new_account": "false"},
                headers=self.origin,
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(begin.await_count, 2)

    def test_account_selection_is_awaited_and_protected(self):
        with (
            patch.object(main.chatgpt_auth, "select", new=AsyncMock()) as select,
            patch.object(main.chatgpt_auth, "status", return_value={"ready": True}),
        ):
            response = self.client.post(
                "/api/chatgpt/select",
                json={"account_id": "account"},
                headers=self.origin,
            )
            self.assertEqual(response.json(), {"ready": True})
            select.assert_awaited_once_with("account")
            self.assertEqual(
                self.client.post("/api/chatgpt/logout", json={}).status_code, 403
            )

    def test_callback_never_renders_provider_or_credentials(self):
        with patch.object(
            main.chatgpt_auth, "complete", new=AsyncMock(side_effect=AIError("private"))
        ):
            response = self.client.get(
                "/api/chatgpt/callback?code=secret&state=transaction"
            )
            self.assertEqual(response.status_code, 200)
            for value in ("private", "secret", "transaction"):
                self.assertNotIn(value, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn(
                "default-src 'none'", response.headers["content-security-policy"]
            )

    def test_uvicorn_access_log_redacts_callback_query(self):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            0,
            '%s - "%s %s HTTP/%s" %d',
            (
                "127.0.0.1",
                "GET",
                "/api/chatgpt/callback?code=secret&state=private",
                "1.1",
                200,
            ),
            None,
        )
        main._ChatGPTCallbackLogFilter().filter(record)
        self.assertNotIn("secret", record.getMessage())
        self.assertNotIn("private", record.getMessage())
        self.assertIn("/api/chatgpt/callback", record.getMessage())

    def test_account_get_routes_reject_non_loopback_host(self):
        for path in ("/api/chatgpt/status", "/api/chatgpt/models"):
            response = self.client.get(path, headers={"Host": "attacker.example"})
            self.assertEqual(response.status_code, 400)
        with patch.object(
            main.chatgpt_auth, "status", return_value={"connected": False}
        ):
            response = self.client.get("/api/chatgpt/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"connected": False})
