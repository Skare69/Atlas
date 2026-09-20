#newznab api layer, reads the db and spits out xml
import re
import urllib.parse
import xml.etree.ElementTree as et
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime

from src.search import _fts_or_like, fts_query, like_query

DEFAULT_LIMIT = 100
MAX_LIMIT = 100

#group name substrings -> category id, first match wins
CATEGORY_HINTS = (
    ("movie", 2000),
    ("tv", 5000),
    ("teevee", 5000),
    ("series", 5000),
    ("mp3", 3000),
    ("audio", 3000),
    ("sounds", 3000),
    ("music", 3000),
    ("games", 4000),
    ("ebook", 7000),
    ("books", 7000),
    ("xxx", 6000),
    ("erotica", 6000),
    ("porn", 6000),
)

#reverse map so cat=2000 becomes group_name like %movie% etc
CAT_GROUPS = {}
for _hint, _cid in CATEGORY_HINTS:
    CAT_GROUPS.setdefault(_cid, [])
    if _hint not in CAT_GROUPS[_cid]:
        CAT_GROUPS[_cid].append(_hint)

CATEGORIES = (
    (1000, "Other"),
    (2000, "Movies"),
    (3000, "Audio"),
    (4000, "PC"),
    (5000, "TV"),
    (6000, "XXX"),
    (7000, "Books"),
)

DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'


def categorize(group_name):
    g = (group_name or "").lower()

    for hint, cid in CATEGORY_HINTS:
        if hint in g:
            return cid

    return 1000


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _to_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


#fold season/ep/imdbid params into fts tokens (s01e01 / tt0117731 style)
def _tokens(params):
    tokens = []

    q = str(params.get("q") or "").strip()

    if q:
        tokens.append(q)

    season = _digits(params.get("season"))
    ep = _digits(params.get("ep"))
    token = f"s{int(season):02d}" if season else ""

    if ep:
        token += f"e{int(ep):02d}"

    if token:
        tokens.append(token)

    imdb = _digits(params.get("imdbid"))

    if imdb:
        tokens.append(f"tt{imdb}")

    return tokens


def _cat_clause(params):
    raw = str(params.get("cat") or "").strip()

    if not raw:
        return "", ()

    hints = []
    seen = set()

    for part in raw.split(","):
        cid = _to_int(part, 0)

        for hint in CAT_GROUPS.get(cid, []):
            if hint not in seen:
                seen.add(hint)
                hints.append(hint)

    if not hints:
        #cat only had ids we dont know, same as no match
        return " and 0", ()

    ors = " or ".join("r.group_name like ?" for _ in hints)
    return f" and ({ors})", tuple(f"%{h}%" for h in hints)


def search_releases(params):
    tokens = _tokens(params)

    if not tokens:
        return [], 0

    query = " ".join(tokens)
    cat_sql, cat_vals = _cat_clause(params)
    limit = min(MAX_LIMIT, max(1, _to_int(params.get("limit"), DEFAULT_LIMIT)))
    offset = max(0, _to_int(params.get("offset"), 0))

    #fts first, like fallback, same shape as search_all_releases
    rows = _fts_or_like(f"""
        select r.id, r.name, r.group_name, r.poster, r.posted_date, r.size, r.complete, r.parts
        from releases r
        join releases_fts on releases_fts.rowid = r.id
        where releases_fts match ?{cat_sql} and r.complete = 1
        order by bm25(releases_fts)
        limit ? offset ?
    """, f"""
        select r.id, r.name, r.group_name, r.poster, r.posted_date, r.size, r.complete, r.parts
        from releases r
        where r.name like ? escape '\\'{cat_sql} and r.complete = 1
        order by r.name
        limit ? offset ?
    """, (fts_query(query), *cat_vals, limit, offset), (like_query(query), *cat_vals, limit, offset))

    total = _fts_or_like(f"""
        select count(*) from releases r
        join releases_fts on releases_fts.rowid = r.id
        where releases_fts match ?{cat_sql} and r.complete = 1
    """, f"""
        select count(*) from releases r
        where r.name like ? escape '\\'{cat_sql} and r.complete = 1
    """, (fts_query(query), *cat_vals), (like_query(query), *cat_vals), fetch_one = True)

    return rows, total[0]


def build_caps():
    caps = et.Element("caps")

    et.SubElement(caps, "server", version="1.1", title="Atlas")
    et.SubElement(caps, "registration", available="no", open="no")

    searching = et.SubElement(caps, "searching")
    et.SubElement(searching, "search", available="yes", supportedParams="q,cat,limit,offset")
    et.SubElement(searching, "tv-search", available="yes", supportedParams="q,cat,limit,offset,season,ep")
    et.SubElement(searching, "movie-search", available="yes", supportedParams="q,cat,limit,offset,imdbid")
    et.SubElement(searching, "audio-search", available="no", supportedParams="")
    et.SubElement(searching, "book-search", available="no", supportedParams="")

    cats = et.SubElement(caps, "categories")

    for cid, name in CATEGORIES:
        et.SubElement(cats, "category", id=str(cid), name=name)

    et.indent(caps, space="  ")
    return DECLARATION + et.tostring(caps, encoding="unicode")


#posted_date stored as rfc822 or iso, try both then give up and use now
def _rfc822(date_str):
    dt = None

    try:
        dt = parsedate_to_datetime(date_str)
    except (TypeError, ValueError, OverflowError):
        pass

    if dt is None:
        try:
            dt = datetime.fromisoformat(date_str)
        except (TypeError, ValueError):
            dt = datetime.now()

    return format_datetime(dt)


def build_results(base_url, apikey, params, rows, total):
    rss = et.Element("rss", {
        "version": "2.0",
        "xmlns:newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/",
    })
    channel = et.SubElement(rss, "channel")
    et.SubElement(channel, "title").text = "Atlas"

    offset = max(0, _to_int(params.get("offset"), 0))
    et.SubElement(channel, "newznab:response", offset=str(offset), total=str(total))

    key = urllib.parse.quote_plus(str(apikey))

    for rid, name, group_name, poster, posted_date, size, complete, parts in rows:
        item = et.SubElement(channel, "item")
        et.SubElement(item, "title").text = name or ""
        et.SubElement(item, "guid", isPermaLink="false").text = f"atlas-{rid}"

        nzb_url = f"{base_url}?t=get&id={rid}&apikey={key}"
        et.SubElement(item, "link").text = nzb_url
        et.SubElement(item, "pubDate").text = _rfc822(posted_date)
        et.SubElement(item, "category").text = str(categorize(group_name))

        et.SubElement(item, "enclosure", url=nzb_url, length=str(size or 0), type="application/x-nzb")

        et.SubElement(item, "newznab:attr", name="category", value=str(categorize(group_name)))
        et.SubElement(item, "newznab:attr", name="size", value=str(size or 0))
        et.SubElement(item, "newznab:attr", name="poster", value=poster or "")
        et.SubElement(item, "newznab:attr", name="group", value=group_name or "")

    et.indent(rss, space="  ")
    return DECLARATION + et.tostring(rss, encoding="unicode")


def error_xml(code, description):
    error = et.Element("error", code=str(code), description=str(description))
    return DECLARATION + et.tostring(error, encoding="unicode")
