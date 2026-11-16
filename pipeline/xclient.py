"""Minimal X (Twitter) API v2 client for ingestion — stdlib only (urllib).

Scope: full-archive post search + counts, which pay-per-use access includes
(per X's Enterprise-vs-pay-per-use docs table). No third-party deps so the pipeline
venv stays as-is.

Endpoints used:
  GET /2/tweets/counts/all   -> volume estimate (cheap, for cost projection)
  GET /2/tweets/search/all   -> the actual posts, paginated via meta.next_token

Auth: Bearer token (config.X_BEARER_TOKEN). Full-archive search is rate-limited
(historically ~1 req/sec), so we sleep between pages.

NOTE: not run yet (no token). Treat numeric costs as estimates until confirmed
against the developer console.
"""
from __future__ import annotations
import json, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

import config


class XAuthError(RuntimeError):
    pass


def _get(path: str, params: dict) -> dict:
    if not config.X_BEARER_TOKEN:
        raise XAuthError("X_BEARER_TOKEN not set — add it to ../.env")
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{config.X_API_BASE}{path}?{qs}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {config.X_BEARER_TOKEN}",
        "User-Agent": "epistemic-ledger/0.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:600]
        if e.code == 429:
            raise RuntimeError(f"X API 429 rate-limited: {body}") from None
        raise RuntimeError(f"X API {e.code} on {path}: {body}") from None


def window(months: float) -> tuple[str, str]:
    """(start_time, end_time) RFC3339 strings for the last `months`."""
    # X requires end_time to be >=10s before request time; back off a minute.
    end = datetime.now(timezone.utc) - timedelta(minutes=1)
    start = end - timedelta(days=round(months * 30.44))
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    return fmt(start), fmt(end)


def build_query(handle: str, exclude_retweets: bool = True,
                exclude_replies: bool = False) -> str:
    q = f"from:{handle.lstrip('@')}"
    if exclude_retweets:
        q += " -is:retweet"
    if exclude_replies:
        q += " -is:reply"
    return q


def count_posts(query: str, start_time: str, end_time: str) -> int:
    """Full-archive post count for the query+window. Sums paginated buckets."""
    total, next_token = 0, None
    while True:
        params = {"query": query, "start_time": start_time,
                  "end_time": end_time, "granularity": "day"}
        if next_token:
            params["next_token"] = next_token
        data = _get("/tweets/counts/all", params)
        total += int(data.get("meta", {}).get("total_tweet_count", 0))
        next_token = data.get("meta", {}).get("next_token")
        if not next_token:
            return total
        time.sleep(1.1)


def search_all(query: str, start_time: str, end_time: str,
               max_pages: int | None = None, sleep: float = 1.1):
    """Yield posts (dicts with id, text, created_at, ...) for the query+window."""
    next_token, pages = None, 0
    while True:
        params = {"query": query, "start_time": start_time, "end_time": end_time,
                  "max_results": "500",
                  "tweet.fields": "created_at,lang,referenced_tweets,public_metrics"}
        if next_token:
            params["next_token"] = next_token
        data = _get("/tweets/search/all", params)
        for t in data.get("data", []):
            yield t
        pages += 1
        next_token = data.get("meta", {}).get("next_token")
        if not next_token or (max_pages and pages >= max_pages):
            return
        time.sleep(sleep)
