"""Read-only access to the user's Zotero library through Zotero's local API.

Zotero 7+ serves the local API at http://127.0.0.1:23119/api when "Allow other applications
on this computer to communicate with Zotero" is enabled (Settings > Advanced). Only the
personal library ("users/0") is read; Paper Reader never writes to Zotero."""

from __future__ import annotations

import http.client
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOST = os.environ.get("ZOTERO_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZOTERO_PORT", "23119"))
PREFIX = "/api/users/0"
TIMEOUT_S = 5
PAGE = 50


class ZoteroError(RuntimeError):
    """Zotero is not running, its local API is off, or a request failed."""


def _request(path: str, params: dict | None = None) -> http.client.HTTPResponse:
    query = "?" + urllib.parse.urlencode(params) if params else ""
    conn = http.client.HTTPConnection(HOST, PORT, timeout=TIMEOUT_S)
    try:
        conn.request("GET", PREFIX + path + query, headers={"Zotero-API-Version": "3"})
        return conn.getresponse()
    except OSError as exc:
        conn.close()
        raise ZoteroError(
            "Zotero is not running, or its local API is off "
            "(Zotero Settings > Advanced > Allow other applications to communicate)"
        ) from exc


def _get(path: str, params: dict | None = None) -> tuple[object, int | None]:
    """JSON body and Total-Results header of a local API GET."""
    resp = _request(path, {**(params or {}), "format": "json"})
    try:
        body = resp.read()
        if resp.status != 200:
            raise ZoteroError(f"Zotero local API returned {resp.status} for {path}")
        total = resp.getheader("Total-Results")
        return json.loads(body), int(total) if total else None
    except ValueError as exc:
        raise ZoteroError("Zotero local API returned invalid JSON") from exc
    finally:
        resp.close()


def _file_location(attachment_key: str) -> str:
    """The file:// URL the local API redirects to for an attachment (not followed)."""
    resp = _request(f"/items/{attachment_key}/file")
    try:
        resp.read()
        if resp.status not in (301, 302, 303, 307):
            raise ZoteroError("This attachment has no local file")
        return resp.getheader("Location") or ""
    finally:
        resp.close()


def available() -> bool:
    try:
        _get("/items/top", {"limit": 1})
        return True
    except ZoteroError:
        return False


def collections() -> list[dict]:
    out, start = [], 0
    while True:
        batch, total = _get("/collections", {"limit": 100, "start": start})
        for c in batch:
            parent = c["data"].get("parentCollection") or None
            out.append(
                {
                    "key": c["key"],
                    "name": c["data"]["name"],
                    "parent": parent,
                    "count": c.get("meta", {}).get("numItems", 0),
                }
            )
        start += len(batch)
        if not batch or (total is not None and start >= total):
            return out


def _authors(creators: list[dict]) -> list[str]:
    names = []
    for c in creators:
        if c.get("creatorType", "author") != "author":
            continue
        name = c.get("name") or " ".join(
            x for x in (c.get("firstName"), c.get("lastName")) if x
        )
        if name:
            names.append(name)
    return names


def _pdfs(item_key: str) -> list[dict]:
    children, _ = _get(f"/items/{item_key}/children")
    return [
        {
            "key": c["key"],
            "name": c["data"].get("filename") or c["data"].get("title") or "PDF",
        }
        for c in children
        if c["data"].get("itemType") == "attachment"
        and c["data"].get("contentType") == "application/pdf"
    ]


def _is_pdf(data: dict) -> bool:
    return (
        data.get("itemType") == "attachment"
        and data.get("contentType") == "application/pdf"
    )


def items(collection: str | None = None, q: str | None = None, start: int = 0) -> dict:
    """One page of top-level items (newest-added first), each with its PDF attachments.

    A PDF dropped into Zotero without a parent item is a top-level attachment; it is listed
    as its own openable item. Notes and non-PDF attachments are skipped. "next" is the offset
    of the following page counted over every upstream entry, skipped ones included, so paging
    never overlaps or stalls."""
    path = f"/collections/{collection}/items/top" if collection else "/items/top"
    params = {"limit": PAGE, "start": start, "sort": "dateAdded", "direction": "desc"}
    if q:
        params.update(q=q, qmode="titleCreatorYear")
    raw, total = _get(path, params)
    kept = [
        i
        for i in raw
        if i["data"].get("itemType") not in ("attachment", "note") or _is_pdf(i["data"])
    ]

    def pdfs_of(i):
        if _is_pdf(i["data"]):  # a standalone PDF opens as itself
            return [
                {
                    "key": i["key"],
                    "name": i["data"].get("filename")
                    or i["data"].get("title")
                    or "PDF",
                }
            ]
        return _pdfs(i["key"])

    with ThreadPoolExecutor(8) as pool:
        pdfs = list(pool.map(pdfs_of, kept))
    return {
        "total": total if total is not None else start + len(raw),
        "next": start + len(raw),
        "items": [
            {
                "key": i["key"],
                "title": i["data"].get("title")
                or i["data"].get("filename")
                or "(untitled)",
                "authors": _authors(i["data"].get("creators", [])),
                "year": (i["data"].get("date") or "")[:4],
                "doi": i["data"].get("DOI") or None,
                "pdfs": p,
            }
            for i, p in zip(kept, pdfs)
        ],
    }


def item_doi(item_key: str) -> str | None:
    item, _ = _get(f"/items/{item_key}")
    return item["data"].get("DOI") or None


def pdf_path(attachment_key: str) -> Path:
    location = _file_location(attachment_key)
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme != "file":
        raise ZoteroError("This attachment is not a local file")
    return Path(urllib.request.url2pathname(parsed.path))
