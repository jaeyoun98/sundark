"""Multi-account ChatGPT credential lifecycle tests."""

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.backends import chatgpt_auth as auth


class ChatGPTAccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(
            os.environ,
            {"SUNDARK_CHATGPT_CREDENTIALS": self.directory.name + "/auth.json"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        auth._PENDING.clear()
        auth._LOCK = asyncio.Lock()

        self.identities = {
            "id-token-first": {
                "sub": "subject-first",
                "email": "first@example.invalid",
            },
            "id-token-second": {
                "sub": "subject-second",
                "email": "second@example.invalid",
            },
        }
        self.account_requests = []

        async def request(method, url, **kwargs):
            if url == auth.TOKEN:
                data = kwargs["data"]
                if data["grant_type"] == "authorization_code":
                    self.account_requests.append(data["client_id"])
                    return {
                        "access_token": "access-" + data["client_id"],
                        "refresh_token": "refresh-" + data["client_id"],
                        "id_token": "id-token-" + data["client_id"],
                        "expires_in": 3600,
                        "token_type": "Bearer",
                        "scope": auth.SCOPE,
                    }
                raise AssertionError("unexpected token grant")
            if url == auth.RESOURCE + "/models":
                token = kwargs["headers"]["Authorization"].removeprefix("Bearer ")
                account = token.removeprefix("access-")
                return {
                    "models": [
                        {
                            "slug": "model-" + account,
                            "display_name": "Model " + account,
                            "visibility": "list",
                        }
                    ]
                }
            if url.endswith("openid-configuration"):
                return {
                    "revocation_endpoint": auth.ISSUER + "/api/accounts/oauth/revoke"
                }
            if url.endswith("/oauth/revoke"):
                return {}
            raise AssertionError("unexpected provider request: " + url)

        async def identity(token, client_id, nonce):
            return self.identities[token]

        self.request_patch = patch.object(auth, "_request", request)
        self.identity_patch = patch.object(auth, "_identity", identity)
        self.request_patch.start()
        self.identity_patch.start()
        self.addCleanup(self.request_patch.stop)
        self.addCleanup(self.identity_patch.stop)

    async def add_account(self, client_id):
        attempt = await auth.begin("http://127.0.0.1:8743/api/chatgpt/callback")
        return await auth.complete(
            "authorization-code-" + client_id,
            attempt["state"],
            client_id,
        )

    async def test_add_select_and_selectively_logout_accounts(self):
        await self.add_account("first")
        status = await self.add_account("second")

        self.assertEqual(self.account_requests, ["first", "second"])
        self.assertEqual(status["active"], "second")
        self.assertEqual(
            {account["account_id"] for account in status["accounts"]},
            {"first", "second"},
        )

        selected = await auth.select("first")
        self.assertEqual(selected["active"], "first")
        self.assertEqual(
            await auth.models(),
            [{"slug": "model-first", "display_name": "Model first"}],
        )

        signed_out = await auth.logout("first")
        self.assertFalse(signed_out["connected"])
        self.assertFalse(
            next(
                account["connected"]
                for account in signed_out["accounts"]
                if account["account_id"] == "first"
            )
        )
        self.assertTrue(
            next(
                account["connected"]
                for account in signed_out["accounts"]
                if account["account_id"] == "second"
            )
        )

        await auth.select("second")
        self.assertEqual(await auth.access_token(), "access-second")
        self.assertEqual(
            await auth.models(),
            [{"slug": "model-second", "display_name": "Model second"}],
        )

    async def test_status_never_serializes_credential_secrets(self):
        await self.add_account("first")
        raw = auth._read()["accounts"]["first"]
        status = auth.status()
        serialized = json.dumps(status)

        for field in ("access_token", "refresh_token", "id_token"):
            self.assertIn(field, raw)
            self.assertNotIn(raw[field], serialized)
        self.assertEqual(status["accounts"][0]["account_id"], "first")


if __name__ == "__main__":
    unittest.main()
