"""Zotero local API client (read-only). Tests feed canned API responses; no Zotero needed."""

import unittest
from pathlib import Path
from unittest.mock import patch

from app import zotero

ITEM = {
    "key": "4SSLMJWN",
    "data": {
        "key": "4SSLMJWN",
        "itemType": "journalArticle",
        "title": "Massively parallel Monte Carlo",
        "creators": [
            {"creatorType": "author", "firstName": "Joshua A.", "lastName": "Anderson"},
            {"creatorType": "author", "firstName": "Eric", "lastName": "Jankowski"},
            {"creatorType": "editor", "name": "Ed Itor"},
        ],
        "date": "2013-11-01",
        "DOI": "10.1016/j.jcp.2013.07.023",
    },
}
NOPDF = {
    "key": "BOOK0001",
    "data": {
        "key": "BOOK0001",
        "itemType": "book",
        "title": "A Book",
        "creators": [{"creatorType": "author", "name": "Org"}],
        "date": "1999",
    },
}
CHILDREN = {
    "4SSLMJWN": [
        {
            "key": "2VJZD3NN",
            "data": {
                "itemType": "attachment",
                "contentType": "application/pdf",
                "filename": "Anderson 2013.pdf",
            },
        },
        {
            "key": "SUPP0001",
            "data": {
                "itemType": "attachment",
                "contentType": "application/pdf",
                "title": "Supplementary",
            },
        },
        {
            "key": "VWWKWPGD",
            "data": {
                "itemType": "attachment",
                "contentType": "text/html",
                "filename": "page.html",
            },
        },
        {"key": "4UYWKH7Y", "data": {"itemType": "note"}},
    ],
    "BOOK0001": [],
}


def fake_get(path, params=None):
    if path == "/collections":
        start = (params or {}).get("start", 0)
        if start:
            return [], 2
        return [
            {
                "key": "R9PK77NC",
                "data": {"name": "CG", "parentCollection": False},
                "meta": {"numItems": 13},
            },
            {
                "key": "SUBCOLL1",
                "data": {"name": "Sub", "parentCollection": "R9PK77NC"},
                "meta": {"numItems": 2},
            },
        ], 2
    if path in ("/items/top", "/collections/R9PK77NC/items/top"):
        fake_get.last = (path, params)
        return [ITEM, NOPDF], 2
    if path.endswith("/children"):
        return CHILDREN[path.split("/")[2]], None
    if path == "/items/4SSLMJWN":
        return ITEM, None
    raise AssertionError(path)


class ZoteroTests(unittest.TestCase):
    def setUp(self):
        p = patch("app.zotero._get", side_effect=fake_get)
        p.start()
        self.addCleanup(p.stop)

    def test_collections_are_flattened_with_parents(self):
        self.assertEqual(
            zotero.collections(),
            [
                {"key": "R9PK77NC", "name": "CG", "parent": None, "count": 13},
                {"key": "SUBCOLL1", "name": "Sub", "parent": "R9PK77NC", "count": 2},
            ],
        )

    def test_items_carry_authors_year_doi_and_pdfs(self):
        page = zotero.items(collection="R9PK77NC", q="monte", start=0)
        self.assertEqual(fake_get.last[0], "/collections/R9PK77NC/items/top")
        self.assertEqual(fake_get.last[1]["q"], "monte")
        self.assertEqual(page["total"], 2)
        first, book = page["items"]
        self.assertEqual(
            first,
            {
                "key": "4SSLMJWN",
                "title": "Massively parallel Monte Carlo",
                "authors": ["Joshua A. Anderson", "Eric Jankowski"],
                "year": "2013",
                "doi": "10.1016/j.jcp.2013.07.023",
                "pdfs": [
                    {"key": "2VJZD3NN", "name": "Anderson 2013.pdf"},
                    {"key": "SUPP0001", "name": "Supplementary"},
                ],
            },
        )
        self.assertEqual(book["pdfs"], [])
        self.assertEqual(book["authors"], ["Org"])

    def test_item_doi(self):
        self.assertEqual(zotero.item_doi("4SSLMJWN"), "10.1016/j.jcp.2013.07.023")

    def test_pdf_path_follows_the_file_redirect(self):
        with patch(
            "app.zotero._file_location",
            return_value="file:///Users/x/Zotero/storage/2VJZD3NN/Anderson%20et%20al.%202013.pdf",
        ):
            self.assertEqual(
                zotero.pdf_path("2VJZD3NN"),
                Path("/Users/x/Zotero/storage/2VJZD3NN/Anderson et al. 2013.pdf"),
            )

    def test_non_file_location_is_refused(self):
        with patch(
            "app.zotero._file_location", return_value="https://example.com/x.pdf"
        ):
            with self.assertRaises(zotero.ZoteroError):
                zotero.pdf_path("2VJZD3NN")

    def test_unreachable_zotero_is_reported(self):
        with patch(
            "app.zotero._get", side_effect=zotero.ZoteroError("Zotero is not running")
        ):
            self.assertFalse(zotero.available())


STANDALONE = {
    "key": "PDFONLY1",
    "data": {
        "key": "PDFONLY1",
        "itemType": "attachment",
        "contentType": "application/pdf",
        "title": "dropped-in.pdf",
        "filename": "dropped-in.pdf",
    },
}
NOTE = {"key": "NOTE0001", "data": {"key": "NOTE0001", "itemType": "note"}}
WEBPAGE = {
    "key": "HTML0001",
    "data": {"key": "HTML0001", "itemType": "attachment", "contentType": "text/html"},
}


class ReviewFixTests(unittest.TestCase):
    def test_standalone_pdf_is_listed_as_its_own_openable_item(self):
        def get(path, params=None):
            if path == "/items/top":
                return [STANDALONE, NOTE, WEBPAGE], 3
            raise AssertionError(path)

        with patch("app.zotero._get", side_effect=get):
            page = zotero.items()
        self.assertEqual(
            page["items"],
            [
                {
                    "key": "PDFONLY1",
                    "title": "dropped-in.pdf",
                    "authors": [],
                    "year": "",
                    "doi": None,
                    "pdfs": [{"key": "PDFONLY1", "name": "dropped-in.pdf"}],
                }
            ],
        )

    def test_next_offset_counts_every_upstream_entry(self):
        def get(path, params=None):
            if path == "/items/top":
                return [NOTE, ITEM, WEBPAGE], 120
            if path.endswith("/children"):
                return [], None
            raise AssertionError(path)

        with patch("app.zotero._get", side_effect=get):
            page = zotero.items(start=50)
        self.assertEqual([i["key"] for i in page["items"]], ["4SSLMJWN"])
        self.assertEqual(page["next"], 53)  # 50 + all three raw entries, not 50 + 1
        self.assertEqual(page["total"], 120)
