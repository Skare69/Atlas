import os
import sqlite3
import tempfile

#src.database resolves the db path at import time, so redirect it first
os.environ["ATLAS_HOME"] = tempfile.mkdtemp(prefix="atlas_newznab_")

import unittest
import xml.etree.ElementTree as et

from src import newznab
from src.database import create_db, database
from src.nzb import generate_nzb, nzb_payload

#parsed newznab:* tags show up in clark notation
NS = "{http://www.newznab.com/DTD/2010/feeds/attributes/}"

RELEASES = [
    #name, group, complete
    ("Linux.Distro.2024.x264", "alt.binaries.multimedia", 1),
    ("Great.Movie.2019.1080p", "alt.binaries.movies", 1),
    ("Some.Show.S01E01.720p", "alt.binaries.teevee", 1),
    ("Atlas.Movie.tt0117731.1080p", "alt.binaries.movies", 1),
    ("Linux.Incomplete.Disc", "alt.binaries.misc", 0),
]


def setUpModule():
    create_db()
    conn = sqlite3.connect(database)

    for name, group, complete in RELEASES:
        conn.execute(
            "insert into releases (name, group_name, poster, posted_date, size, complete, parts)"
            " values (?, ?, ?, ?, ?, ?, ?)",
            (name, group, "poster@site", "Wed, 01 Jan 2025 12:00:00 +0000", 12345, complete, 2),
        )

    show = conn.execute(
        "select id from releases where name = ?", ("Some.Show.S01E01.720p",)
    ).fetchone()[0]

    for part in (1, 2):
        conn.execute(
            "insert into articles (release_id, message_id, subject, filename, part, total_parts, bytes)"
            " values (?, ?, ?, ?, ?, ?, ?)",
            (show, f"<part{part}@news>", f"Some.Show.S01E01 part {part}", "Some.Show.S01E01.mkv", part, 2, 500 * part),
        )

    conn.commit()
    conn.close()


def rid(name):
    conn = sqlite3.connect(database)
    row = conn.execute("select id from releases where name = ?", (name,)).fetchone()
    conn.close()
    return row[0]


class CategorizeTest(unittest.TestCase):
    def test_group_substrings(self):
        cases = {
            "alt.binaries.movies": 2000,
            "alt.binaries.teevee": 5000,
            "alt.binaries.tv.series": 5000,
            "alt.binaries.sounds.mp3": 3000,
            "alt.binaries.music": 3000,
            "alt.binaries.pc.games": 4000,
            "alt.binaries.ebooks": 7000,
            "alt.binaries.erotica": 6000,
            "alt.binaries.misc": 1000,
            None: 1000,
        }

        for group, expected in cases.items():
            self.assertEqual(newznab.categorize(group), expected, group)


class SearchTest(unittest.TestCase):
    def test_q_matches_and_complete_only(self):
        rows, total = newznab.search_releases({"q": "linux"})

        self.assertEqual(total, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], rid("Linux.Distro.2024.x264"))
        self.assertEqual(rows[0][1], "Linux.Distro.2024.x264")
        self.assertEqual(len(rows[0]), 8)
        self.assertEqual(rows[0][6], 1)

    def test_cat_filter(self):
        rows, total = newznab.search_releases({"q": "movie", "cat": "2000"})
        self.assertEqual(total, 2)
        self.assertEqual({r[0] for r in rows}, {rid("Great.Movie.2019.1080p"), rid("Atlas.Movie.tt0117731.1080p")})

        rows, total = newznab.search_releases({"q": "movie", "cat": "2000,5000"})
        self.assertEqual(total, 2)

    def test_season_ep_folding(self):
        rows, total = newznab.search_releases({"season": "1", "ep": "01"})
        self.assertEqual(total, 1)
        self.assertEqual(rows[0][0], rid("Some.Show.S01E01.720p"))

        #season alone folds to s01
        rows, total = newznab.search_releases({"season": 1})
        self.assertEqual(total, 1)
        self.assertEqual(rows[0][0], rid("Some.Show.S01E01.720p"))

    def test_imdbid_folding(self):
        #junk chars get stripped, digits re-prefixed with tt
        rows, total = newznab.search_releases({"imdbid": "tt-0117731"})
        self.assertEqual(total, 1)
        self.assertEqual(rows[0][0], rid("Atlas.Movie.tt0117731.1080p"))

    def test_no_tokens_returns_newest(self):
        #no q param: standard newznab behaviour = newest releases first,
        #complete-only still enforced, unknown cats still match nothing
        rows, total = newznab.search_releases({})
        self.assertEqual(total, 4)
        self.assertEqual(len(rows), 4)
        dates = [r[4] for r in rows]
        self.assertEqual(dates, sorted(dates, reverse = True))

        rows_cat, total_cat = newznab.search_releases({"cat": "2000"})
        self.assertEqual([r[1] for r in rows_cat], ["Atlas.Movie.tt0117731.1080p", "Great.Movie.2019.1080p"])
        self.assertEqual(total_cat, 2)

        rows_unknown, total_unknown = newznab.search_releases({"cat": "9999"})
        self.assertEqual((rows_unknown, total_unknown), ([], 0))

    def test_limit_offset_total(self):
        base, total = newznab.search_releases({"q": "movie"})
        page1, total1 = newznab.search_releases({"q": "movie", "limit": 1})
        page2, total2 = newznab.search_releases({"q": "movie", "limit": 1, "offset": 1})

        self.assertEqual((total, total1, total2), (2, 2, 2))
        self.assertEqual(len(page1), 1)
        self.assertEqual(len(page2), 1)
        self.assertNotEqual(page1[0][0], page2[0][0])
        self.assertEqual({page1[0][0], page2[0][0]}, {r[0] for r in base})

        #garbage values clamp instead of exploding
        rows, total = newznab.search_releases({"q": "movie", "limit": "99999", "offset": "-5"})
        self.assertEqual(total, 2)


class CapsTest(unittest.TestCase):
    def test_caps_parses(self):
        root = et.fromstring(newznab.build_caps())

        self.assertEqual(root.tag, "caps")
        self.assertEqual(root.find("server").get("version"), "1.1")
        self.assertEqual(root.find("server").get("title"), "Atlas")
        self.assertEqual(root.find("registration").get("available"), "no")
        self.assertEqual(root.find("registration").get("open"), "no")

        searching = root.find("searching")
        self.assertEqual(searching.find("search").get("available"), "yes")
        self.assertIn("q", searching.find("search").get("supportedParams"))
        self.assertEqual(searching.find("tv-search").get("available"), "yes")
        self.assertIn("season", searching.find("tv-search").get("supportedParams"))
        self.assertIn("ep", searching.find("tv-search").get("supportedParams"))
        self.assertEqual(searching.find("movie-search").get("available"), "yes")
        self.assertIn("imdbid", searching.find("movie-search").get("supportedParams"))
        self.assertEqual(searching.find("audio-search").get("available"), "no")
        self.assertEqual(searching.find("book-search").get("available"), "no")

        cats = root.findall("categories/category")
        self.assertEqual(len(cats), len(newznab.CATEGORIES))
        self.assertEqual({c.get("id") for c in cats}, {str(cid) for cid, _ in newznab.CATEGORIES})


class ResultsTest(unittest.TestCase):
    def test_results_parse(self):
        rows, total = newznab.search_releases({"q": "movie"})
        xml = newznab.build_results("http://localhost:8085/api", "key&stuff", {"q": "movie", "offset": 0}, rows, total)
        root = et.fromstring(xml)

        self.assertEqual(root.tag, "rss")
        channel = root.find("channel")
        self.assertEqual(channel.find("title").text, "Atlas")

        response = channel.find(NS + "response")
        self.assertEqual(response.get("offset"), "0")
        self.assertEqual(response.get("total"), str(total))

        items = channel.findall("item")
        self.assertEqual(len(items), len(rows))

        item = items[0]
        self.assertIn("t=get&id=", item.find("link").text)
        self.assertIn("apikey=key%26stuff", item.find("link").text)
        self.assertTrue(item.find("guid").text.startswith("atlas-"))
        self.assertEqual(item.find("guid").get("isPermaLink"), "false")

        enclosure = item.find("enclosure")
        self.assertIn("t=get&id=", enclosure.get("url"))
        self.assertEqual(enclosure.get("type"), "application/x-nzb")
        self.assertEqual(int(enclosure.get("length")), rows[0][5])

        attrs = {a.get("name"): a.get("value") for a in item.findall(NS + "attr")}
        self.assertEqual(set(attrs), {"category", "size", "poster", "group"})
        self.assertEqual(attrs["category"], str(newznab.categorize(rows[0][2])))
        self.assertEqual(attrs["poster"], "poster@site")
        self.assertEqual(attrs["group"], rows[0][2])


class ErrorXmlTest(unittest.TestCase):
    def test_error_parses_and_escapes(self):
        xml = newznab.error_xml(203, "Function not <available>")
        root = et.fromstring(xml)

        self.assertEqual(root.tag, "error")
        self.assertEqual(root.get("code"), "203")
        self.assertEqual(root.get("description"), "Function not <available>")
        self.assertIn("&lt;available&gt;", xml)


class NzbPayloadTest(unittest.TestCase):
    def test_payload_for_known_release(self):
        show = rid("Some.Show.S01E01.720p")
        filename, xml = nzb_payload(show)

        self.assertTrue(filename.endswith(".nzb"))
        root = et.fromstring(xml)
        #body declares the newzbin default xmlns, so parsed tags are clark notation
        nzbns = "{http://www.newzbin.com/DTD/2003/nzb}"
        self.assertEqual(root.tag, nzbns + "nzb")
        self.assertEqual(len(root.findall(nzbns + "file")), 1)
        self.assertEqual(len(root.findall(f"{nzbns}file/{nzbns}segments/{nzbns}segment")), 2)

    def test_unknown_release(self):
        self.assertEqual(nzb_payload(999999), (None, None))

    def test_generate_nzb_still_writes_file(self):
        out = os.path.join(os.environ["ATLAS_HOME"], "nzbout")
        os.makedirs(out, exist_ok=True)

        show = rid("Some.Show.S01E01.720p")
        generate_nzb(show, out)

        written = [f for f in os.listdir(out) if f.endswith(".nzb")]
        self.assertEqual(len(written), 1)

        with open(os.path.join(out, written[0]), encoding="utf-8") as f:
            content = f.read()

        self.assertIn("<!DOCTYPE nzb", content)
        self.assertIn("<nzb", content)
        self.assertIn("<segment", content)


if __name__ == "__main__":
    unittest.main()
