"""Read-only HTTP API over the ledger — the backend the browser extension calls.

Stdlib only (http.server) so the repo needs no extra deps. It wraps answer.py's
build_answer / list_sources and returns JSON with permissive CORS (the data is
public, read-only reliability scores).

Endpoints:
  GET /author?handle=@chamath[&topic=finance]   -> full ledger (build_answer dict)
  GET /authors[?like=finance]                    -> scored sources
  GET /health                                    -> {"ok": true}

SECURITY for public hosting (the 15-day demo): do NOT expose this with the
service-role SUPABASE_DB_URL. Either (a) point SUPABASE_DB_URL at a read-only
Postgres role that can only SELECT scores/sources, or (b) skip this server and
let the extension hit Supabase PostgREST with the anon key under RLS. Bind to
127.0.0.1 for local dev; only bind 0.0.0.0 behind a read-only role.

Run: cd pipeline && ../.venv/bin/python api.py   (PORT env, default 8000)
"""
from __future__ import annotations
import json, os, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import config
from answer import build_answer, list_sources

# ---- cold-author build (async) ----
# A cold author has no ledger. Building one runs the full pipeline, which is slow
# and spends real money, so it runs in a background thread with status polling.
# Gated + cost-capped; a PUBLIC demo should set EPI_ALLOW_BUILD=0 to avoid abuse.
ALLOW_BUILD = os.environ.get("EPI_ALLOW_BUILD", "1") == "1"
BUILD_MONTHS = float(os.environ.get("EPI_BUILD_MONTHS", "6"))
BUILD_MAX_CLAIMS = int(os.environ.get("EPI_BUILD_MAX_CLAIMS", "60"))   # resolve-spend cap
MAX_CONCURRENT_BUILDS = int(os.environ.get("EPI_MAX_BUILDS", "2"))

JOBS = {}                       # handle(lower) -> {state, step, error}
JOBS_LOCK = threading.Lock()


def _key(h):
    return h.lower().lstrip("@")


def has_scores(handle):
    with psycopg.connect(config.SUPABASE_DB_URL) as conn:
        row = conn.execute(
            "select 1 from scores s join sources src on src.id = s.source_id "
            "where src.handle = %s limit 1", ("@" + _key(handle),)).fetchone()
    return bool(row)


def build_worker(handle):
    import run_new_author as rn
    k = _key(handle)
    try:
        for step, fn in (("ingest", lambda: rn.ingest(handle, BUILD_MONTHS, False)),
                         ("extract", lambda: rn.extract(handle)),
                         ("resolve", lambda: rn.resolve(handle, BUILD_MAX_CLAIMS)),
                         ("score", lambda: rn.score())):
            with JOBS_LOCK:
                JOBS[k]["step"] = step
            fn()
        with JOBS_LOCK:
            JOBS[k] = {"state": "ready" if has_scores(handle) else "empty"}
    except Exception as e:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS[k] = {"state": "error", "error": str(e)}


def start_build(handle):
    k = _key(handle)
    with JOBS_LOCK:
        cur = JOBS.get(k)
        if cur and cur.get("state") == "building":
            return cur
        if sum(1 for v in JOBS.values() if v.get("state") == "building") >= MAX_CONCURRENT_BUILDS:
            return {"state": "busy"}
        JOBS[k] = {"state": "building", "step": "ingest"}
    threading.Thread(target=build_worker, args=(handle,), daemon=True).start()
    return {"state": "building", "step": "ingest"}


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        one = lambda k: q.get(k, [None])[0]
        try:
            if u.path in ("/", "/health"):
                return self._json({"ok": True, "allow_build": ALLOW_BUILD})
            if u.path == "/status":
                h = one("handle")
                if not h:
                    return self._json({"error": "handle required"}, 400)
                with JOBS_LOCK:
                    job = JOBS.get(_key(h))
                return self._json(job if job else
                                  {"state": "ready" if has_scores(h) else "none"})
            with psycopg.connect(config.SUPABASE_DB_URL) as conn:
                if u.path == "/authors":
                    rows = list_sources(conn, one("like"))
                    return self._json([
                        {"handle": h, "persona": p, "is_seeded": s,
                         "nodes": n, "max_n": m} for h, p, s, n, m in rows])
                if u.path == "/author":
                    handle = one("handle")
                    if not handle:
                        return self._json({"error": "handle required"}, 400)
                    return self._json(build_answer(conn, handle, one("topic")))
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001 — surface as JSON, don't 500 silently
            return self._json({"error": str(e), "type": type(e).__name__}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if u.path == "/analyze":
                handle, text = body.get("handle"), body.get("text")
                if not handle or not text:
                    return self._json({"error": "handle and text required"}, 400)
                from analyze import build_analysis
                from aclient import make_anthropic
                client = make_anthropic(force_ipv4=True)   # hosted egress may be IPv4-only
                with psycopg.connect(config.SUPABASE_DB_URL) as conn:
                    return self._json(build_analysis(conn, client, handle, text))
            if u.path == "/build":
                if not ALLOW_BUILD:
                    return self._json({"state": "disabled"}, 403)
                handle = body.get("handle")
                if not handle:
                    return self._json({"error": "handle required"}, 400)
                if has_scores(handle):
                    return self._json({"state": "ready"})
                return self._json(start_build(handle))
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e), "type": type(e).__name__}, 500)

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    if os.environ.get("EPI_FORCE_IPV4", "1") == "1":
        from aclient import force_ipv4_dns
        force_ipv4_dns()
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"ledger read API on http://{host}:{port}  (/author?handle=@chamath)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
