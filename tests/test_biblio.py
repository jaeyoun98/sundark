import json
import unittest
from unittest.mock import patch

from app import biblio

CROSSREF = {
    "message": {
        "title": [
            "Two-Dimensional Melting:  From Liquid-Hexatic\n Coexistence to Continuous Transitions"
        ],
        "author": [
            {"given": "Sebastian C.", "family": "Kapfer"},
            {"given": "Werner", "family": "Krauth"},
        ],
        "issued": {"date-parts": [[2015, 1, 21]]},
    }
}
ARXIV = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>Parallel computations for Metropolis Markov chains
 with Picard maps</title><published>2025-06-11T00:00:00Z</published>
<author><name>Sebastiano Grazzi</name></author><author><name>Giacomo Zanella</name></author>
</entry></feed>"""


class LookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_crossref_record(self):
        seen = []

        def fake(url):
            seen.append(url)
            return json.dumps(CROSSREF).encode()

        with patch("app.biblio._get", new=fake):
            rec = await biblio.lookup("doi:10.1103/physrevlett.114.035702")
        self.assertEqual(
            rec,
            {
                "title": "Two-Dimensional Melting: From Liquid-Hexatic Coexistence to Continuous Transitions",
                "authors": ["Sebastian C. Kapfer", "Werner Krauth"],
                "year": 2015,
                "source": "crossref",
            },
        )
        self.assertEqual(
            seen, ["https://api.crossref.org/works/10.1103/physrevlett.114.035702"]
        )

    async def test_arxiv_record(self):
        with patch("app.biblio._get", return_value=ARXIV):
            rec = await biblio.lookup("arxiv:2506.09762")
        self.assertEqual(
            rec["title"],
            "Parallel computations for Metropolis Markov chains with Picard maps",
        )
        self.assertEqual(rec["authors"], ["Sebastiano Grazzi", "Giacomo Zanella"])
        self.assertEqual((rec["year"], rec["source"]), (2025, "arxiv"))

    async def test_network_or_parse_failure_is_none(self):
        for error in (OSError("offline"), ValueError("bad json")):
            with patch("app.biblio._get", side_effect=error):
                self.assertIsNone(await biblio.lookup("doi:10.1/x"))
        with patch(
            "app.biblio._get",
            return_value=b"<feed xmlns='http://www.w3.org/2005/Atom'/>",
        ):
            self.assertIsNone(await biblio.lookup("arxiv:2506.09762"))

    async def test_file_and_zotero_ids_are_not_looked_up(self):
        with patch("app.biblio._get", side_effect=AssertionError("no network")):
            self.assertIsNone(await biblio.lookup("file:abc"))
            self.assertIsNone(await biblio.lookup("zotero:ABCD1234"))


class MatchTests(unittest.TestCase):
    PAGE = "PHYSICAL REVIEW LETTERS\nTwo-Dimensional Melting: From Liquid-Hexatic Coexistence\nto Continuous Transitions\nSebastian C. Kapfer"

    def test_title_matches_its_own_first_page(self):
        self.assertTrue(
            biblio.title_matches(CROSSREF["message"]["title"][0], self.PAGE)
        )

    def test_title_matches_rejects_unrelated_title(self):
        self.assertFalse(
            biblio.title_matches(
                "Deep residual learning for image recognition", self.PAGE
            )
        )

    def test_byline(self):
        self.assertEqual(
            biblio.byline({"authors": ["A B", "C D", "E F"], "year": 2020}),
            "A B et al. (2020)",
        )
        self.assertEqual(
            biblio.byline({"authors": ["A B", "C D"], "year": None}), "A B, C D"
        )
        self.assertEqual(biblio.byline(None), "")
