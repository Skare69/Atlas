# newznab-compatible http api, stdlib only, read-only over the db
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import hmac
import re
import sys
import traceback

from src.config import api_settings
from src.newznab import DEFAULT_LIMIT, MAX_LIMIT, build_caps, build_results, error_xml, search_releases
from src.nzb import nzb_payload


def _int(val):
    # safe int parse, None when missing or garbage
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _xml(self, body):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=UTF-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _plain(self, code, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.rstrip("/") != "/api":
            self._plain(404, "not found\n")
            return

        qs = parse_qs(url.query)
        key = qs.get("apikey", [None])[0]
        if not key or not hmac.compare_digest(key, self.server.apikey):
            self._xml(error_xml(100, "Incorrect user credentials"))
            return

        t = qs.get("t", [None])[0]
        try:
            if not t:
                self._xml(error_xml(200, "Missing parameter: t"))
            elif t == "caps":
                self._xml(build_caps())
            elif t in ("search", "tvsearch", "movie"):
                self._xml(self._search(qs))
            elif t == "get":
                self._get(qs)
            else:
                # music/book/etc arent modeled in the db
                self._xml(error_xml(203, "Function not available"))
        except Exception:
            # one line is enough, keep the request thread alive
            sys.stderr.write("api error: " + traceback.format_exc().strip().splitlines()[-1] + "\n")
            self._xml(error_xml(500, "Internal error"))

    def _search(self, qs):
        limit = _int(qs.get("limit", [None])[0])
        params = {
            "q": qs.get("q", [None])[0],
            "cat": qs.get("cat", [None])[0],
            "limit": min(limit, MAX_LIMIT) if limit and limit > 0 else DEFAULT_LIMIT,
            "offset": max(_int(qs.get("offset", [None])[0]) or 0, 0),
            "season": _int(qs.get("season", [None])[0]),
            "ep": _int(qs.get("ep", [None])[0]),
            "imdbid": qs.get("imdbid", [None])[0],
        }
        rows, total = search_releases(params)

        host = self.headers.get("Host")
        if host:
            base_url = f"http://{host}/api"
        else:
            bind = self.server.server_address[0]
            if bind in ("0.0.0.0", "::"):
                bind = "127.0.0.1"
            base_url = f"http://{bind}:{self.server.server_address[1]}/api"

        return build_results(base_url, self.server.apikey, params, rows, total)

    def _get(self, qs):
        rid = _int(qs.get("id", [None])[0])
        filename, text = nzb_payload(rid) if rid is not None else (None, None)
        if not text:
            self._xml(error_xml(300, "No such item"))
            return

        fname = re.sub(r"[^A-Za-z0-9._ -]", "_", filename or "").strip() or "release.nzb"
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-nzb")
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run(host, port, apikey, serve=True):
    server = Server((host, port), Handler)
    server.apikey = apikey
    if serve:
        server.serve_forever()
    return server


def main():
    s = api_settings()
    print(f"newznab api on http://{s['host']}:{s['port']}")
    run(s["host"], s["port"], s["key"])


if __name__ == "__main__":
    main()
