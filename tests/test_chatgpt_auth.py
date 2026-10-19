"""ChatGPT auth tests use isolated storage and generated cryptographic fixtures."""

import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.backends import chatgpt_auth as auth
from app.errors import AIError


class ChatGPTAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(
            "os.environ",
            {"SUNDARK_CHATGPT_CREDENTIALS": self.directory.name + "/auth.json"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        auth._PENDING.clear()
        auth._LOCK = asyncio.Lock()

    def patch_request(self, request):
        mocked = patch.object(auth, "_request", request)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_pkce_host_and_storage(self):
        async def scenario():
            a = await auth.begin("http://127.0.0.1:8743/api/chatgpt/callback")
            b = await auth.begin("http://127.0.0.1:1234/api/chatgpt/callback")
            first, second = (parse_qs(urlsplit(item["url"]).query) for item in (a, b))
            assert first["client_id"] == ["dynamic_agent_client"]
            assert first["ext_agent_host_id"] == second["ext_agent_host_id"]
            assert first["state"] != second["state"]
            assert first["nonce"] != second["nonce"]
            assert first["code_challenge_method"] == ["S256"]
            if os.name == "posix":
                assert auth._path().stat().st_mode & 0o777 == 0o600
            assert auth.status()["ready"] is False
            with self.assertRaises(AIError):
                await auth.begin("http://localhost:8743/api/chatgpt/callback")
            with self.assertRaises(AIError):
                await auth.begin("http://127.0.0.1:8743/other")

        asyncio.run(scenario())

    def test_exchange_verifies_and_preserves_accounts(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        public["kid"] = "generated-test-key"
        nonce = None
        requests = []

        async def request(method, url, **kwargs):
            requests.append((url, kwargs))
            if url.endswith("jwks.json"):
                return {"keys": [public]}
            token = jwt.encode(
                {
                    "iss": auth.ISSUER,
                    "aud": "issued-client",
                    "sub": "generated-subject",
                    "exp": time.time() + 300,
                    "nonce": nonce,
                    "email": "test@example.invalid",
                },
                private,
                algorithm="RS256",
                headers={"kid": public["kid"]},
            )
            return {
                "access_token": jwt.encode(
                    {"fixture": True}, private, algorithm="RS256"
                ),
                "refresh_token": jwt.encode(
                    {"refresh": True}, private, algorithm="RS256"
                ),
                "id_token": token,
                "expires_in": 300,
                "token_type": "Bearer",
                "scope": auth.SCOPE,
            }

        self.patch_request(request)

        async def scenario():
            nonlocal nonce
            result = await auth.begin("http://127.0.0.1:8743/api/chatgpt/callback")
            nonce = auth._PENDING[result["state"]]["nonce"]
            with self.assertRaises(AIError):
                await auth.complete("code", "unrecognized")
            done = await auth.complete(
                "generated-code", result["state"], "issued-client"
            )
            assert done["ready"]
            assert requests[0][1]["data"]["client_id"] == "issued-client"
            assert done["accounts"][0]["account_id"] == "issued-client"
            first_identity = auth.validation_identity()
            returning = await auth.begin(
                "http://127.0.0.1:8743/api/chatgpt/callback", "issued-client"
            )
            query = parse_qs(urlsplit(returning["url"]).query)
            assert query["client_id"] == ["issued-client"]
            assert "agent_name_hint" not in query
            assert "id_token_hint" in query
            with self.assertRaises(AIError):
                await auth.complete(
                    "generated-code", returning["state"], "other-client"
                )
            assert auth.status()["ready"]
            reconnect = await auth.begin(
                "http://127.0.0.1:8743/api/chatgpt/callback", "issued-client"
            )
            nonce = auth._PENDING[reconnect["state"]]["nonce"]
            await auth.complete("generated-code", reconnect["state"], "issued-client")
            assert auth.status()["active"] == "issued-client"
            assert auth.validation_identity() != first_identity

        asyncio.run(scenario())

    def test_id_token_validation(self):
        bad = "nonce"
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        public["kid"] = "generated-test-key"
        claims = {
            "iss": auth.ISSUER,
            "aud": "issued-client",
            "sub": "subject",
            "exp": time.time() + 300,
            "nonce": "expected",
        }
        claims[bad] = 0 if bad == "exp" else "wrong"
        token = jwt.encode(
            claims, private, algorithm="RS256", headers={"kid": public["kid"]}
        )

        async def request(*args, **kwargs):
            return {"keys": [public]}

        self.patch_request(request)
        with self.assertRaises(AIError):
            asyncio.run(auth._identity(token, "issued-client", "expected"))

    def test_refresh_serializes_and_logout_retains_registration(self):
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {
                    "issued": {
                        "client_id": "issued",
                        "subject": "subject",
                        "email": "test@example.invalid",
                        "access_token": "generated-expired-fixture",
                        "refresh_token": "generated-refresh-fixture",
                        "expires_at": 0,
                        "scopes": auth.SCOPE.split(),
                    }
                },
            }
        )
        calls = []

        async def request(method, url, **kwargs):
            calls.append(url)
            await asyncio.sleep(0)
            if url.endswith("openid-configuration"):
                return {
                    "revocation_endpoint": auth.ISSUER + "/api/accounts/oauth/revoke"
                }
            if url.endswith("revoke"):
                return {}
            return {
                "access_token": "generated-new-fixture",
                "refresh_token": "generated-rotated-fixture",
                "expires_in": 300,
                "token_type": "Bearer",
            }

        self.patch_request(request)

        async def scenario():
            result = await asyncio.gather(auth.access_token(), auth.access_token())
            assert result == ["generated-new-fixture"] * 2
            assert calls.count(auth.TOKEN) == 1
            assert (
                auth._read()["accounts"]["issued"]["refresh_token"]
                == "generated-rotated-fixture"
            )
            done = await auth.logout()
            assert done["remote_revoked"]
            assert not done["connected"]
            assert done["accounts"][0]["account_id"] == "issued"
            assert "refresh_token" not in auth._read()["accounts"]["issued"]

        asyncio.run(scenario())

    def test_malformed_storage_is_safe(self):
        for accounts in (
            {"issued": None},
            {"issued": {"client_id": "issued", "subject": "subject", "email": []}},
        ):
            auth._path().write_text(
                json.dumps(
                    {"host_id": "host", "accounts": accounts, "active": "issued"}
                )
            )
            with self.assertRaises(AIError):
                auth.status()

    def test_malformed_tokens_are_rejected(self):
        for field in ("refresh_token", "id_token"):
            with self.assertRaises(AIError):
                auth._tokens(
                    {
                        "access_token": "generated-fixture",
                        "expires_in": 300,
                        "token_type": "Bearer",
                        field: [],
                    }
                )

    def test_jwt_claim_and_signature_rejection(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        public["kid"] = "generated-test-key"

        async def request(*args, **kwargs):
            return {"keys": [public]}

        self.patch_request(request)
        for bad in ("aud", "iss", "exp", "signature", "azp"):
            with self.subTest(bad=bad):
                claims = {
                    "iss": auth.ISSUER,
                    "aud": "issued-client",
                    "sub": "subject",
                    "exp": time.time() + 300,
                    "nonce": "expected",
                }
                if bad == "azp":
                    claims["aud"] = ["issued-client", "other-client"]
                    claims["azp"] = "wrong"
                elif bad != "signature":
                    claims[bad] = 0 if bad == "exp" else "wrong"
                token = jwt.encode(
                    claims,
                    other if bad == "signature" else private,
                    algorithm="RS256",
                    headers={"kid": public["kid"]},
                )
                with self.assertRaises(AIError):
                    asyncio.run(auth._identity(token, "issued-client", "expected"))

    def test_logout_invalidates_inflight_callback(self):
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            revoked = []

            async def request(method, url, **kwargs):
                if url == auth.TOKEN:
                    entered.set()
                    await release.wait()
                    return {
                        "access_token": "generated-access",
                        "refresh_token": "generated-refresh",
                        "id_token": "generated-id",
                        "expires_in": 300,
                        "token_type": "Bearer",
                        "scope": auth.SCOPE,
                    }
                if url.endswith("openid-configuration"):
                    return {
                        "revocation_endpoint": auth.ISSUER
                        + "/api/accounts/oauth/revoke"
                    }
                revoked.append(kwargs["data"]["token"])
                return {}

            async def identity(*args):
                return {"sub": "generated-subject", "email": "test@example.invalid"}

            self.patch_request(request)
            with patch.object(auth, "_identity", identity):
                attempt = await auth.begin("http://127.0.0.1:8743/api/chatgpt/callback")
                callback = asyncio.create_task(
                    auth.complete("generated-code", attempt["state"], "issued")
                )
                await entered.wait()
                await auth.logout()
                release.set()
                with self.assertRaises(AIError):
                    await callback
                self.assertFalse(auth.status()["connected"])
                self.assertTrue(auth.status()["accounts"][0]["provisional"])
                self.assertIsNone(auth.status()["active"])
                self.assertEqual(revoked, ["generated-refresh"])

        asyncio.run(scenario())

    def test_cancelled_refresh_saves_rotation_before_unlock(self):
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {
                    "issued": {
                        "client_id": "issued",
                        "subject": "subject",
                        "email": "test@example.invalid",
                        "access_token": "generated-expired",
                        "refresh_token": "generated-old-refresh",
                        "expires_at": 0,
                        "scopes": auth.SCOPE.split(),
                    }
                },
            }
        )

        async def scenario():
            validating, release = asyncio.Event(), asyncio.Event()
            calls = []

            async def request(method, url, **kwargs):
                calls.append(url)
                return {
                    "access_token": "generated-new-access",
                    "refresh_token": "generated-rotated-refresh",
                    "id_token": "generated-id",
                    "expires_in": 300,
                    "token_type": "Bearer",
                }

            async def identity(*args):
                validating.set()
                await release.wait()
                return {"sub": "subject"}

            self.patch_request(request)
            with patch.object(auth, "_identity", identity):
                first = asyncio.create_task(auth.access_token())
                await validating.wait()
                first.cancel()
                second = asyncio.create_task(auth.access_token())
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                self.assertFalse(second.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                self.assertEqual(await second, "generated-new-access")
                self.assertEqual(calls, [auth.TOKEN])
                self.assertEqual(
                    auth._read()["accounts"]["issued"]["refresh_token"],
                    "generated-rotated-refresh",
                )

        asyncio.run(scenario())

    def test_refresh_rotation_survives_jwks_network_failure(self):
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {
                    "issued": {
                        "client_id": "issued",
                        "subject": "subject",
                        "email": "test@example.invalid",
                        "access_token": "generated-expired",
                        "refresh_token": "generated-old-refresh",
                        "id_token": "generated-validated-id",
                        "expires_at": 0,
                        "scopes": auth.SCOPE.split(),
                    }
                },
            }
        )
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(
            {
                "iss": auth.ISSUER,
                "aud": "issued",
                "sub": "subject",
                "exp": time.time() + 300,
            },
            private,
            algorithm="RS256",
            headers={"kid": "generated-key"},
        )
        submitted = []

        async def request(method, url, **kwargs):
            if url.endswith("jwks.json"):
                raise AIError("Generated network failure")
            submitted.append(kwargs["data"]["refresh_token"])
            result = {
                "access_token": "generated-new-access",
                "refresh_token": "generated-rotated-refresh",
                "expires_in": 300,
                "token_type": "Bearer",
            }
            if len(submitted) == 1:
                result["id_token"] = token
            return result

        self.patch_request(request)

        async def scenario():
            with self.assertRaises(auth._IdentityUnavailable):
                await auth.access_token()
            stored = auth._read()["accounts"]["issued"]
            self.assertEqual(stored["refresh_token"], "generated-rotated-refresh")
            self.assertEqual(stored["access_token"], "generated-expired")
            self.assertEqual(stored["id_token"], "generated-validated-id")
            self.assertEqual(stored["expires_at"], 0)
            self.assertEqual(await auth.access_token(), "generated-new-access")
            self.assertEqual(
                submitted, ["generated-old-refresh", "generated-rotated-refresh"]
            )

        asyncio.run(scenario())

    def test_rejected_refresh_identity_requires_signin(self):
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {
                    "issued": {
                        "client_id": "issued",
                        "subject": "subject",
                        "access_token": "generated-expired",
                        "refresh_token": "generated-old-refresh",
                        "expires_at": 0,
                        "scopes": auth.SCOPE.split(),
                    }
                },
            }
        )

        async def request(method, url, **kwargs):
            if url.endswith("openid-configuration"):
                return {
                    "revocation_endpoint": auth.ISSUER + "/api/accounts/oauth/revoke"
                }
            if url.endswith("revoke"):
                return {}
            return {
                "access_token": "generated-new-access",
                "refresh_token": "generated-rotated-refresh",
                "id_token": "generated-id",
                "expires_in": 300,
                "token_type": "Bearer",
            }

        async def identity(*args):
            return {"sub": "different-subject"}

        self.patch_request(request)

        async def scenario():
            with patch.object(auth, "_identity", identity):
                with self.assertRaises(AIError):
                    await auth.access_token()
            self.assertFalse(auth.status()["connected"])
            with self.assertRaises(AIError):
                await auth.access_token()
            self.assertEqual(auth._read()["accounts"]["issued"]["subject"], "subject")

        asyncio.run(scenario())

    def test_signed_identity_clock_skew_is_bounded(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        public["kid"] = "generated-clock-key"

        async def request(*args, **kwargs):
            return {"keys": [public]}

        self.patch_request(request)
        for offset in (2, 120):
            claims = {
                "iss": auth.ISSUER,
                "aud": "issued",
                "sub": "subject",
                "iat": time.time() + offset,
                "exp": time.time() + 300,
                "nonce": "expected",
            }
            token = jwt.encode(
                claims, private, algorithm="RS256", headers={"kid": public["kid"]}
            )
            with self.subTest(offset=offset):
                if offset < 60:
                    self.assertEqual(
                        asyncio.run(auth._identity(token, "issued", "expected"))["sub"],
                        "subject",
                    )
                else:
                    with self.assertRaises(AIError):
                        asyncio.run(auth._identity(token, "issued", "expected"))

    def test_malformed_refresh_discards_consumed_session(self):
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {
                    "issued": {
                        "client_id": "issued",
                        "subject": "subject",
                        "access_token": "generated-expired",
                        "refresh_token": "generated-old-refresh",
                        "expires_at": 0,
                        "scopes": auth.SCOPE.split(),
                    }
                },
            }
        )
        submitted = []
        revoked = []

        async def request(method, url, **kwargs):
            if url.endswith("openid-configuration"):
                return {
                    "revocation_endpoint": auth.ISSUER + "/api/accounts/oauth/revoke"
                }
            if url.endswith("revoke"):
                revoked.append(kwargs["data"]["token"])
                return {}
            submitted.append(kwargs["data"]["refresh_token"])
            return {
                "refresh_token": "generated-new-refresh",
                "expires_in": 300,
                "token_type": "Bearer",
            }

        self.patch_request(request)

        async def scenario():
            for _ in range(2):
                with self.assertRaises(AIError):
                    await auth.access_token()
            self.assertEqual(submitted, ["generated-old-refresh"])
            self.assertEqual(revoked, ["generated-new-refresh"])
            self.assertFalse(auth.status()["connected"])
            self.assertEqual(auth._read()["accounts"]["issued"]["subject"], "subject")

        asyncio.run(scenario())

    def test_terminal_and_temporary_refresh_errors_via_httpx(self):
        import httpx

        original_client = httpx.AsyncClient
        terminal_codes = (
            "invalid_grant",
            "invalid_refresh_token",
            "token_expired",
            "refresh_token_expired",
            "refresh_token_invalidated",
            "refresh_token_reused",
            "refresh_token_revoked",
        )
        cases = [
            (
                status,
                {"error": error, "error_description": "private provider detail"},
                True,
            )
            for code in terminal_codes
            for error in (code, code.upper(), {"code": code}, {"code": code.upper()})
            for status in (400, 401, 403)
        ]
        cases.extend(
            (status, {"code": value}, True)
            for code in terminal_codes
            for value in (code, code.upper())
            for status in (400, 401, 403)
        )
        cases.extend(
            [
                (400, {"error": "invalid_client"}, False),
                (401, {"error": "unauthorized"}, False),
                (503, {"error": "invalid_grant"}, False),
                (429, {"error": "rate_limit"}, False),
            ]
        )
        for status, body, terminal in cases:
            with self.subTest(status=status, body=body):
                record = {
                    "client_id": "issued",
                    "subject": "subject",
                    "access_token": "generated-expired",
                    "refresh_token": "generated-old-refresh",
                    "id_token": "generated-validated-id",
                    "expires_at": 0,
                    "scopes": auth.SCOPE.split(),
                }
                other = {"client_id": "other", "subject": "other-subject"}
                auth._save(
                    {
                        "host_id": "generated-host",
                        "active": "issued",
                        "accounts": {"issued": record.copy(), "other": other.copy()},
                    }
                )
                submitted = []

                def handler(request):
                    submitted.append(request)
                    return httpx.Response(status, json=body)

                def client(**kwargs):
                    return original_client(
                        transport=httpx.MockTransport(handler), **kwargs
                    )

                async def scenario():
                    with patch.object(auth.httpx, "AsyncClient", client):
                        for _ in range(2):
                            with self.assertRaises(AIError) as raised:
                                await auth.access_token()
                            self.assertNotIn(
                                "private provider detail", str(raised.exception)
                            )
                    stored = auth._read()
                    self.assertEqual(stored["host_id"], "generated-host")
                    self.assertEqual(stored["accounts"]["other"], other)
                    if terminal:
                        self.assertFalse(auth.status()["connected"])
                        self.assertFalse(auth.status()["ready"])
                        self.assertEqual(len(submitted), 1)
                        self.assertEqual(
                            stored["accounts"]["issued"],
                            {"client_id": "issued", "subject": "subject"},
                        )
                    else:
                        self.assertEqual(stored["accounts"]["issued"], record)
                        self.assertEqual(len(submitted), 2)

                asyncio.run(scenario())

    def test_atomic_save_without_fchmod(self):
        data = {"host_id": "generated-host", "accounts": {}, "active": None}
        if hasattr(auth.os, "fchmod"):
            original = auth.os.fchmod
            delattr(auth.os, "fchmod")
            try:
                auth._save(data)
            finally:
                auth.os.fchmod = original
        else:
            auth._save(data)
        self.assertEqual(auth._read(), data)
        self.assertEqual(list(auth._path().parent.glob(".chatgpt-*")), [])

    def test_save_filesystem_error_is_safe(self):
        with patch.object(
            auth.tempfile, "mkstemp", side_effect=OSError("private filesystem detail")
        ):
            with self.assertRaises(AIError) as raised:
                auth._save(
                    {"host_id": "generated-host", "accounts": {}, "active": None}
                )
        self.assertNotIn("private filesystem detail", str(raised.exception))

    def test_windows_sharing_violation_replace_retry(self):
        data = {"host_id": "generated-host", "accounts": {}, "active": None}
        original = os.replace
        attempts = []

        def replace(source, destination):
            attempts.append(source)
            if len(attempts) <= 2:
                error = OSError("generated sharing violation")
                error.winerror = 32
                raise error
            return original(source, destination)

        with (
            patch.object(auth.os, "replace", replace),
            patch.object(auth.time, "sleep") as sleep,
        ):
            auth._save(data)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(auth._read(), data)
        self.assertEqual(list(auth._path().parent.glob(".chatgpt-*")), [])

    def test_permission_denied_replace_is_not_retried(self):
        error = OSError("generated permission denied")
        error.winerror = 5
        with (
            patch.object(auth.os, "replace", side_effect=error) as replace,
            patch.object(auth.time, "sleep") as sleep,
        ):
            with self.assertRaises(AIError):
                auth._save(
                    {"host_id": "generated-host", "accounts": {}, "active": None}
                )
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(list(auth._path().parent.glob(".chatgpt-*")), [])

    def test_initial_scope_omission_preserves_requested_grant(self):
        response = {
            "access_token": "generated-access",
            "expires_in": 300,
            "token_type": "Bearer",
        }
        self.assertEqual(auth._tokens(response)["scopes"], auth.SCOPE.split())
        self.assertEqual(auth._tokens({**response, "scope": ""})["scopes"], [])
        self.assertEqual(
            auth._tokens(response, {"scopes": ["openid"]})["scopes"], ["openid"]
        )

    def test_reconnect_revokes_superseded_grant_after_save(self):
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {
                    "issued": {
                        "client_id": "issued",
                        "subject": "subject",
                        "access_token": "generated-old-access",
                        "refresh_token": "generated-old-refresh",
                        "expires_at": 0,
                        "scopes": auth.SCOPE.split(),
                    }
                },
            }
        )
        revoked = []

        async def request(*args, **kwargs):
            return {
                "access_token": "generated-new-access",
                "refresh_token": "generated-new-refresh",
                "id_token": "generated-new-id",
                "expires_in": 300,
                "token_type": "Bearer",
            }

        async def identity(*args):
            return {"sub": "subject"}

        async def revoke(token, client):
            self.assertEqual(
                auth._read()["accounts"]["issued"]["refresh_token"],
                "generated-new-refresh",
            )
            self.assertFalse(auth._LOCK.locked())
            revoked.append((token, client))
            return True

        self.patch_request(request)

        async def scenario():
            with (
                patch.object(auth, "_identity", identity),
                patch.object(auth, "_revoke", revoke),
            ):
                attempt = await auth.begin(
                    "http://127.0.0.1:8743/api/chatgpt/callback", "issued"
                )
                result = await auth.complete("generated-code", attempt["state"])
                self.assertTrue(result["ready"])
            self.assertEqual(revoked, [("generated-old-refresh", "issued")])

        asyncio.run(scenario())

    def test_logout_revocation_does_not_hold_locks_and_survives_cancellation(self):
        record = {
            "client_id": "issued",
            "subject": "subject",
            "access_token": "generated-access",
            "refresh_token": "generated-refresh",
            "expires_at": time.time() + 300,
            "scopes": auth.SCOPE.split(),
        }
        other = {**record, "client_id": "other", "subject": "other-subject"}
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {"issued": record, "other": other},
            }
        )

        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()

            async def revoke(token, client):
                self.assertFalse(auth._LOCK.locked())
                entered.set()
                await release.wait()
                return True

            with patch.object(auth, "_revoke", revoke):
                logout = asyncio.create_task(auth.logout())
                await entered.wait()
                self.assertFalse(auth.status()["connected"])
                async with auth._locked():
                    self.assertNotIn(
                        "refresh_token", auth._read()["accounts"]["issued"]
                    )
                await auth.select("other")
                self.assertEqual(await auth.access_token(), "generated-access")
                logout.cancel()
                await asyncio.sleep(0)
                self.assertFalse(logout.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await logout
                self.assertTrue(auth.status()["ready"])
                self.assertEqual(auth.status()["active"], "other")

        asyncio.run(scenario())

    def test_missing_plan_scope_requests_consent_only_when_needed(self):
        record = {
            "client_id": "issued",
            "subject": "subject",
            "access_token": "generated-access",
            "scopes": ["openid"],
        }
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {"issued": record},
            }
        )

        async def scenario():
            attempt = await auth.begin(
                "http://127.0.0.1:8743/api/chatgpt/callback", "issued"
            )
            query = parse_qs(urlsplit(attempt["url"]).query)
            self.assertEqual(query["prompt"], ["consent"])
            self.assertEqual(query["scope"], [auth.SCOPE])
            with self.assertRaises(AIError):
                await auth.access_token()
            data = auth._read()
            data["accounts"]["issued"]["scopes"] = auth.SCOPE.split()
            auth._save(data)
            ordinary = await auth.begin(
                "http://127.0.0.1:8743/api/chatgpt/callback", "issued"
            )
            self.assertNotIn("prompt", parse_qs(urlsplit(ordinary["url"]).query))

        asyncio.run(scenario())

    def test_failed_initial_exchange_reuses_issued_registration(self):
        calls = []

        async def request(*args, **kwargs):
            calls.append(kwargs["data"]["client_id"])
            raise auth._TerminalGrantError("invalid_grant")

        self.patch_request(request)

        async def scenario():
            initial = await auth.begin("http://127.0.0.1:8743/api/chatgpt/callback")
            with self.assertRaises(AIError):
                await auth.complete(
                    "generated-expired-code", initial["state"], "issued"
                )
            self.assertEqual(calls, ["issued"])
            status = auth.status()
            self.assertFalse(status["ready"])
            self.assertFalse(status["connected"])
            self.assertIsNone(status["active"])
            retry = await auth.begin("http://127.0.0.1:8743/api/chatgpt/callback")
            query = parse_qs(urlsplit(retry["url"]).query)
            self.assertEqual(query["client_id"], ["issued"])
            self.assertNotIn("agent_name_hint", query)
            fresh = await auth.begin(
                "http://127.0.0.1:8743/api/chatgpt/callback", new_account=True
            )
            self.assertEqual(
                parse_qs(urlsplit(fresh["url"]).query)["client_id"],
                ["dynamic_agent_client"],
            )
            with self.assertRaises(AIError):
                await auth.select("issued")

        asyncio.run(scenario())

    def test_new_callback_cannot_overwrite_existing_registration(self):
        saved = {
            "client_id": "issued",
            "subject": "subject",
            "access_token": "generated-existing",
            "scopes": auth.SCOPE.split(),
        }
        auth._save(
            {
                "host_id": "generated-host",
                "active": "issued",
                "accounts": {"issued": saved},
            }
        )

        async def scenario():
            initial = await auth.begin(
                "http://127.0.0.1:8743/api/chatgpt/callback", new_account=True
            )
            with patch.object(auth, "_request") as request:
                with self.assertRaises(AIError):
                    await auth.complete("generated-code", initial["state"], "issued")
                request.assert_not_called()
            self.assertEqual(auth._read()["accounts"]["issued"], saved)

        asyncio.run(scenario())
