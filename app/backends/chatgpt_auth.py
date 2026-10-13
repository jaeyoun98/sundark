"""Public-client ChatGPT authorization with isolated, protected local credentials."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from filelock import AsyncFileLock

from ..errors import AIError, from_text

ISSUER = "https://auth.openai.com"
AUTHORIZE = f"{ISSUER}/api/accounts/authorize"
TOKEN = f"{ISSUER}/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_LOCK = asyncio.Lock()
_PENDING: dict[str, dict] = {}


def _path() -> Path:
    return Path(
        os.environ.get("SUNDARK_CHATGPT_CREDENTIALS", "~/.config/sundark/chatgpt.json")
    ).expanduser()


def _read() -> dict:
    try:
        path = _path()
        if path.is_symlink():
            raise AIError("ChatGPT credential storage must not be a symbolic link")
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
            raise ValueError
        if not isinstance(data.get("host_id"), str) or not data["host_id"]:
            raise ValueError
        if data.get("active") is not None and (
            not isinstance(data["active"], str)
            or data["active"] not in data["accounts"]
        ):
            raise ValueError
        for key, record in data["accounts"].items():
            if not isinstance(key, str) or not isinstance(record, dict):
                raise ValueError
            provisional = record.get("provisional") is True
            if provisional and (
                record.get("subject") is not None
                or data.get("active") == key
                or any(
                    record.get(field) is not None
                    for field in ("access_token", "refresh_token", "id_token")
                )
            ):
                raise ValueError
            if record.get("client_id") != key or (
                not provisional and not isinstance(record.get("subject"), str)
            ):
                raise ValueError
            if not isinstance(record.get("email", ""), str):
                raise ValueError
            if not isinstance(record.get("scopes", []), list) or any(
                not isinstance(item, str) for item in record.get("scopes", [])
            ):
                raise ValueError
            if not isinstance(record.get("expires_at", 0), (int, float)):
                raise ValueError
            if any(
                record.get(field) is not None and not isinstance(record[field], str)
                for field in ("access_token", "refresh_token", "id_token")
            ):
                raise ValueError
        return data
    except FileNotFoundError:
        return {
            "host_id": "urn:uuid:" + str(uuid.uuid4()),
            "accounts": {},
            "active": None,
        }
    except (OSError, ValueError) as exc:
        raise AIError("Could not read ChatGPT connection settings") from exc


def _save(data: dict) -> None:
    path = _path()
    name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".chatgpt-")
        with os.fdopen(fd, "w") as file:
            if hasattr(os, "fchmod"):
                os.fchmod(file.fileno(), 0o600)
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        for attempt in range(4):
            try:
                os.replace(name, path)
                break
            except OSError as exc:
                if getattr(exc, "winerror", None) not in (32, 33) or attempt == 3:
                    raise
                time.sleep(0.02)
    except OSError as exc:
        raise AIError("Could not save ChatGPT connection settings") from exc
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


@asynccontextmanager
async def _locked():
    async with _LOCK:
        path = _path()
        lock_path = Path(str(path) + ".lock")
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if lock_path.is_symlink():
                raise AIError("ChatGPT credential lock must not be a symbolic link")
            lock = AsyncFileLock(
                lock_path,
                mode=0o600,
                run_in_executor=False,
                fallback_to_soft=False,
                preserve_lock_file=True,
            )
            async with lock:
                yield
        except OSError as exc:
            raise AIError("Could not access ChatGPT connection settings") from exc


def status() -> dict:
    data = _read()
    active = data["accounts"].get(data.get("active"), {})
    connected = bool(active.get("access_token"))
    ready = (
        connected
        and "chatgpt.tokens.use.direct" in active.get("scopes", [])
        and bool(
            active.get("refresh_token") or active.get("expires_at", 0) > time.time()
        )
    )
    return {
        "connected": connected,
        "ready": ready,
        "active": data.get("active"),
        "email": active.get("email"),
        "accounts": [
            {
                "id": key,
                "account_id": key,
                "email": value.get("email"),
                "label": value.get("email", "ChatGPT") + " · " + key[-8:],
                "connected": bool(value.get("access_token")),
                "provisional": value.get("provisional") is True,
            }
            for key, value in data["accounts"].items()
        ],
    }


async def begin(
    redirect_uri: str, account_id: str | None = None, *, new_account: bool = False
) -> dict:
    uri = urlsplit(redirect_uri)
    if (
        uri.scheme != "http"
        or uri.hostname != "127.0.0.1"
        or not uri.port
        or uri.username
        or uri.password
        or uri.query
        or uri.fragment
    ):
        raise AIError("ChatGPT sign-in requires a 127.0.0.1 callback")
    async with _locked():
        data = _read()
        if account_id is None and not new_account and data.get("active") is None:
            provisional = [
                key
                for key, value in data["accounts"].items()
                if value.get("provisional")
            ]
            if provisional:
                account_id = provisional[-1]
        account = data["accounts"].get(account_id) if account_id else None
        if account_id and not account:
            raise AIError("Choose a saved ChatGPT account")
        callback = uri.scheme + "://" + uri.hostname + uri.path
        if data.get("callback") and data["callback"] != callback:
            raise AIError("Keep the registered ChatGPT callback path unchanged")
        data["callback"] = callback
        _save(data)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    now = time.time()
    for key in list(_PENDING):
        if now - _PENDING[key]["created"] > 600:
            _PENDING.pop(key)
    _PENDING[state] = {
        "nonce": nonce,
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "created": now,
        "generation": data.get("generation", 0),
        "account": account_id,
        "client_id": account_id,
        "subject": account.get("subject") if account else None,
    }
    query = {
        "client_id": account_id or "dynamic_agent_client",
        "ext_agent_host_id": data["host_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        )
        .decode()
        .rstrip("="),
    }
    if account:
        if not account.get(
            "provisional"
        ) and "chatgpt.tokens.use.direct" not in account.get("scopes", []):
            query["prompt"] = "consent"
        if account.get("id_token"):
            query["id_token_hint"] = account["id_token"]
        if account.get("email") and not account.get("provisional"):
            query["login_hint"] = account["email"]
    else:
        query["agent_name_hint"] = "Sundark"
    return {"url": AUTHORIZE + "?" + urlencode(query), "state": state}


class _TerminalGrantError(AIError):
    """A structured OAuth response confirms the renewable grant is unusable."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("ChatGPT connection expired; Continue with ChatGPT again")


async def _request(method: str, url: str, **kwargs) -> dict:
    try:
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.request(method, url, **kwargs)
        if response.is_error:
            if url == TOKEN and response.status_code in (400, 401, 403):
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                code = (
                    payload.get("error", payload.get("code"))
                    if isinstance(payload, dict)
                    else None
                )
                if isinstance(code, dict):
                    code = code.get("code")
                code = code.lower() if isinstance(code, str) else None
                if code in (
                    "invalid_grant",
                    "invalid_refresh_token",
                    "token_expired",
                    "refresh_token_expired",
                    "refresh_token_invalidated",
                    "refresh_token_reused",
                    "refresh_token_revoked",
                ):
                    raise _TerminalGrantError(code)
            raise from_text(
                str(response.status_code) + " " + response.text,
                "ChatGPT connection failed; sign in again",
            )
        return response.json() if response.content else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise AIError("Could not connect to ChatGPT; try again") from exc


class _IdentityUnavailable(AIError):
    """Verification keys could not be retrieved; identity has not been rejected."""


async def _identity(token: str, client_id: str, nonce: str | None) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError
        try:
            jwks = await _request("GET", f"{ISSUER}/.well-known/jwks.json")
        except AIError as exc:
            raise _IdentityUnavailable(
                "Could not verify ChatGPT identity; try again"
            ) from exc
        key = next(
            item for item in jwks["keys"] if item.get("kid") == header.get("kid")
        )
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(key).key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            leeway=60,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        if (
            not isinstance(claims["sub"], str)
            or not claims["sub"]
            or (nonce is not None and claims.get("nonce") != nonce)
        ):
            raise ValueError
        if (
            isinstance(claims["aud"], list)
            and len(claims["aud"]) > 1
            and claims.get("azp") != client_id
        ):
            raise ValueError
        return claims
    except (jwt.PyJWTError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise AIError("ChatGPT identity could not be verified; sign in again") from exc


def _tokens(response: dict, old: dict | None = None) -> dict:
    old = old or {}
    for field in ("refresh_token", "id_token"):
        if field in response and (
            not isinstance(response[field], str) or not response[field]
        ):
            raise AIError("ChatGPT returned an incomplete connection")
    access = response.get("access_token")
    expiry = response.get("expires_in")
    if (
        not isinstance(access, str)
        or not access
        or not isinstance(expiry, (int, float))
        or expiry <= 0
        or str(response.get("token_type", "")).lower() != "bearer"
    ):
        raise AIError("ChatGPT returned an incomplete connection")
    scopes = response.get("scope")
    if scopes is not None and not isinstance(scopes, str):
        raise AIError("ChatGPT returned an incomplete connection")
    return {
        **old,
        "access_token": access,
        "refresh_token": response.get("refresh_token", old.get("refresh_token")),
        "id_token": response.get("id_token", old.get("id_token")),
        "scopes": scopes.split()
        if scopes is not None
        else old.get("scopes", SCOPE.split()),
        "expires_at": time.time() + expiry,
    }


async def complete(
    code: str | None, state: str, client_id: str | None = None, error: str | None = None
) -> dict:
    async with _locked():
        pending = _PENDING.pop(state, None)
        if pending and pending["generation"] != _read().get("generation", 0):
            pending = None
    if not pending or time.time() - pending["created"] > 600:
        raise AIError("ChatGPT sign-in expired or state did not match; try again")
    if error or not code:
        raise AIError("ChatGPT sign-in was not completed")
    issued = pending["client_id"] or client_id
    if (
        not issued
        or issued == "dynamic_agent_client"
        or (pending["client_id"] and client_id and issued != client_id)
    ):
        raise AIError("ChatGPT registration did not return the expected client")
    async with _locked():
        data = _read()
        if pending["generation"] != data.get("generation", 0):
            raise AIError("ChatGPT sign-in was cancelled; try again")
        if not pending["client_id"]:
            if issued in data["accounts"]:
                raise AIError(
                    "ChatGPT registration returned an existing client; choose that account"
                )
            data["accounts"][issued] = {
                "client_id": issued,
                "provisional": True,
                "subject": None,
                "email": "ChatGPT registration pending",
            }
            _save(data)
    response = await _request(
        "POST",
        TOKEN,
        data={
            "grant_type": "authorization_code",
            "client_id": issued,
            "code": code,
            "code_verifier": pending["verifier"],
            "redirect_uri": pending["redirect_uri"],
            "resource": RESOURCE,
        },
    )
    try:
        claims = await _identity(response.get("id_token", ""), issued, pending["nonce"])
        if pending["subject"] and pending["subject"] != claims["sub"]:
            raise AIError("ChatGPT sign-in returned a different account")
        record = _tokens(response)
        record.update(
            {
                "client_id": issued,
                "subject": claims["sub"],
                "issuer": ISSUER,
                "email": claims.get("email")
                if isinstance(claims.get("email"), str)
                else "ChatGPT",
            }
        )
        async with _locked():
            data = _read()
            if pending["generation"] != data.get("generation", 0):
                raise AIError("ChatGPT sign-in was cancelled; try again")
            previous = data["accounts"].get(issued)
            if (
                previous
                and not previous.get("provisional")
                and previous["subject"] != claims["sub"]
            ):
                raise AIError("ChatGPT registration identity did not match")
            data["accounts"][issued] = record
            data["active"] = issued
            _save(data)
    except BaseException:
        await _revoke(response.get("refresh_token"), issued)
        raise
    superseded = previous.get("refresh_token") if previous else None
    if superseded and superseded != record.get("refresh_token"):
        await _settle(asyncio.create_task(_revoke(superseded, issued)))
    return status()


async def select(account_id: str) -> dict:
    async with _locked():
        data = _read()
        if account_id not in data["accounts"]:
            raise AIError("Choose a saved ChatGPT account")
        if data["accounts"][account_id].get("provisional"):
            raise AIError("Complete ChatGPT sign-in for this registration first")
        data["generation"] = data.get("generation", 0) + 1
        _PENDING.clear()
        data["active"] = account_id
        _save(data)
    return status()


async def _settle(task):
    """Finish credential rotation before propagating request cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve failures so cancellation does not leave an unobserved task.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def access_token() -> str:
    return await _settle(asyncio.create_task(_access_token()))


async def _access_token() -> str:
    async with _locked():
        data = _read()
        record = data["accounts"].get(data.get("active"), {})
        if not record.get("access_token"):
            raise AIError("Continue with ChatGPT in AI settings first")
        if "chatgpt.tokens.use.direct" not in record.get("scopes", []):
            raise AIError("Enable ChatGPT plan usage by signing in again")
        if record.get("expires_at", 0) <= time.time() + 60:
            if not record.get("refresh_token"):
                raise AIError("ChatGPT connection expired; sign in again")
            try:
                response = await _request(
                    "POST",
                    TOKEN,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": record["client_id"],
                        "refresh_token": record["refresh_token"],
                        "resource": RESOURCE,
                    },
                )
            except _TerminalGrantError:
                for field in (
                    "access_token",
                    "refresh_token",
                    "id_token",
                    "expires_at",
                    "scopes",
                ):
                    record.pop(field, None)
                _save(data)
                raise
            try:
                replacement = _tokens(response, record)
                if response.get("id_token"):
                    claims = await _identity(
                        response["id_token"], record["client_id"], None
                    )
                    if claims["sub"] != record["subject"]:
                        raise AIError("ChatGPT refresh identity did not match")
            except _IdentityUnavailable:
                # Retain only the renewal credential, never unverified access or identity.
                record["refresh_token"] = replacement["refresh_token"]
                record["expires_at"] = 0
                _save(data)
                raise
            except AIError:
                # A rejected identity cannot be repaired by a later ID-token-free refresh.
                for field in (
                    "access_token",
                    "refresh_token",
                    "id_token",
                    "expires_at",
                    "scopes",
                ):
                    record.pop(field, None)
                _save(data)
                renewal = response.get("refresh_token")
                if isinstance(renewal, str) and renewal:
                    await _revoke(renewal, record["client_id"])
                raise
            record = replacement
            data["accounts"][data["active"]] = record
            _save(data)
            if "chatgpt.tokens.use.direct" not in record["scopes"]:
                raise AIError("Enable ChatGPT plan usage by signing in again")
        return record["access_token"]


async def _revoke(refresh_token: str | None, client_id: str) -> bool:
    if not refresh_token:
        return False
    try:
        discovery = await _request("GET", f"{ISSUER}/.well-known/openid-configuration")
        endpoint = discovery["revocation_endpoint"]
        if (
            urlsplit(endpoint).scheme != "https"
            or urlsplit(endpoint).netloc != "auth.openai.com"
        ):
            return False
        for attempt in range(3):
            try:
                await _request(
                    "POST",
                    endpoint,
                    data={
                        "token": refresh_token,
                        "token_type_hint": "refresh_token",
                        "client_id": client_id,
                    },
                )
                return True
            except AIError:
                if attempt < 2:
                    await asyncio.sleep(0.25 * 2**attempt)
    except (AIError, KeyError):
        pass
    return False


async def logout(account_id: str | None = None) -> dict:
    return await _settle(asyncio.create_task(_logout(account_id)))


async def _logout(account_id: str | None) -> dict:
    async with _locked():
        data = _read()
        data["generation"] = data.get("generation", 0) + 1
        _PENDING.clear()
        key = account_id or data.get("active")
        record = data["accounts"].get(key, {})
        token = record.get("refresh_token")
        client_id = record.get("client_id")
        if record:
            for field in (
                "access_token",
                "refresh_token",
                "id_token",
                "expires_at",
                "scopes",
            ):
                record.pop(field, None)
        _save(data)
    confirmed = await _revoke(token, client_id)
    return {**status(), "remote_revoked": confirmed}


async def models() -> list[dict]:
    token = await access_token()
    result = await _request(
        "GET", RESOURCE + "/models", headers={"Authorization": "Bearer " + token}
    )
    entries = result.get("models")
    if not isinstance(entries, list):
        raise AIError("ChatGPT returned an invalid model catalog")
    return [
        {"slug": row["slug"], "display_name": row["display_name"]}
        for row in entries
        if isinstance(row, dict)
        and row.get("visibility") == "list"
        and isinstance(row.get("slug"), str)
        and isinstance(row.get("display_name"), str)
    ]
