"""New-author pipeline: pull a real author's posts from X -> ledger in Supabase.

This is the live counterpart to run_seed.py (which loads the SIMULATED data with
labels already attached). Here there are NO labels — the resolver must determine
each claim's outcome from the web (user pt 4). Stages, each gated so nothing paid
runs implicitly:

  estimate  counts/all only -> volume + projected cost (run this FIRST, cheap)
  ingest    search/all -> posts saved to ../ingest/<handle>.jsonl (paid: X reads)
  extract   extract.py per post -> claims (status=open) + topics (paid: Sonnet)
  resolve   resolve.py per open+resolvable claim -> verdict (paid: Sonnet+web_search)
  score     score_job.recompute_scores -> scores table (free)
  run       estimate -> (with --yes) ingest -> extract -> resolve -> score

Idempotency: ingest writes JSONL so re-running extract never re-pays X. extract
skips posts whose tweet_id already produced claims. resolve only touches status=
'open' claims, so resolved/unresolvable ones are never re-billed.

Topic mapping (no embedder yet): the extractor picks from the canonical taxonomy;
anything new is created as a provisional node from its slug (same tradeoff as
run_seed — string-based, canonicalization via embeddings is deferred).

Usage:
    python run_new_author.py estimate --handle CathieDWood --months 6
    python run_new_author.py run      --handle CathieDWood --months 6 --yes --max-claims 150
    python run_new_author.py resolve  --handle CathieDWood --limit 50
Needs ../.env with X_BEARER_TOKEN (ingest/estimate), ANTHROPIC_API_KEY
(extract/resolve), SUPABASE_DB_URL (all DB stages).
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import date

import psycopg
import config
import db
import xclient
from ltree_util import slug_to_ltree

INGEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingest")


# ------------------------------------------------------------------ estimate
def estimate(handle: str, months: float, exclude_replies: bool) -> int:
    q = xclient.build_query(handle, exclude_retweets=True, exclude_replies=exclude_replies)
    start, end = xclient.window(months)
    print(f"query:  {q}\nwindow: {start} .. {end}")
    n = xclient.count_posts(q, start, end)
    reads = n * config.X_READ_PRICE
    est_claims = int(n * config.FALSIFIABLE_YIELD)
    print(f"\nposts in window:        {n}")
    print(f"X read cost (~${config.X_READ_PRICE}/post): ${reads:,.2f}  [verify price in console]")
    print(f"extraction calls:       {n}  (1 Sonnet call/post, effort=low)")
    print(f"est. falsifiable claims: ~{est_claims}  (@ {config.FALSIFIABLE_YIELD:.0%} yield, rough)")
    print(f"  -> resolution runs Sonnet+web_search per RESOLVABLE claim (biggest variable cost);")
    print(f"     many long-horizon ARK/VC calls won't be resolvable yet -> fewer paid resolves.")
    print("\nNOTE: these are estimates. Confirm the read price and your monthly cap "
          "in the X developer console.")
    return n


# -------------------------------------------------------------------- ingest
def ingest(handle: str, months: float, exclude_replies: bool, max_pages=None) -> str:
    os.makedirs(INGEST_DIR, exist_ok=True)
    q = xclient.build_query(handle, exclude_retweets=True, exclude_replies=exclude_replies)
    start, end = xclient.window(months)
    out = os.path.join(INGEST_DIR, f"{handle.lstrip('@').lower()}.jsonl")
    n = 0
    with open(out, "w") as f:
        for t in xclient.search_all(q, start, end, max_pages=max_pages):
            f.write(json.dumps(t) + "\n")
            n += 1
            if n % 500 == 0:
                print(f"  ...{n} posts")
    print(f"ingested {n} posts -> {out}")
    # register the source now so later stages have it
    with psycopg.connect(config.SUPABASE_DB_URL) as conn:
        sid = db.get_or_create_source(conn, "@" + handle.lstrip("@"),
                                      is_seeded=True, persona=None)
        conn.commit()
    print(f"source registered: @{handle.lstrip('@')}  id={sid}")
    return out


# -------------------------------------------------------- topic node mapping
def _resolve_node(conn, cache: dict, slug: str) -> str | None:
    """Existing node id for a slug, or create it provisional. Cached per run."""
    if not slug:
        return None
    if slug in cache:
        return cache[slug]
    row = conn.execute("select id from topic_nodes where slug=%s", (slug,)).fetchone()
    if row:
        cache[slug] = row[0]
        return row[0]
    path = slug_to_ltree(slug)
    if not path:
        return None
    label = path.rsplit(".", 1)[-1]
    nid = conn.execute(
        "insert into topic_nodes (path, slug, label, status) "
        "values (%s::ltree, %s, %s, 'provisional') "
        "on conflict (path) do update set slug=excluded.slug returning id",
        (path, slug, label)).fetchone()[0]
    cache[slug] = nid
    return nid


def _claim_slugs(c) -> list[str]:
    """Slugs for one ExtractedClaim: chosen canonical first, else proposed."""
    if c.topic_slug:
        return [c.topic_slug]
    if c.proposed_path:
        slug = c.proposed_path
        if c.proposed_parent and not slug.startswith(c.proposed_parent):
            slug = f"{c.proposed_parent}>{slug.split('>')[-1]}"
        return [slug]
    return []


# ------------------------------------------------------------------- extract
def extract(handle: str) -> None:
    import anthropic
    from extract import extract_claims
    path = os.path.join(INGEST_DIR, f"{handle.lstrip('@').lower()}.jsonl")
    if not os.path.exists(path):
        sys.exit(f"no ingest file {path} — run `ingest` first")
    client = anthropic.Anthropic()
    with psycopg.connect(config.SUPABASE_DB_URL) as conn:
        sid = db.get_or_create_source(conn, "@" + handle.lstrip("@"), is_seeded=True)
        done = {r[0] for r in conn.execute(
            "select distinct tweet_id from claims where source_id=%s and tweet_id is not null",
            (sid,)).fetchall()}
        taxonomy = db.canonical_slugs(conn)
        cache: dict = {}
        posts = n_claims = skipped = 0
        with open(path) as f:
            for line in f:
                t = json.loads(line)
                posts += 1
                tid = str(t.get("id"))
                if tid in done:
                    skipped += 1
                    continue
                text = t.get("text", "")
                claims = extract_claims(client, text, taxonomy)
                for c in claims:
                    nids = [nid for nid in
                            (_resolve_node(conn, cache, s) for s in _claim_slugs(c)) if nid]
                    if not nids:
                        continue
                    db.insert_claim(conn, sid, c.text, nids, tweet_id=tid,
                                    posted_at=t.get("created_at"),
                                    resolves_on=c.resolves_on,
                                    stated_confidence=c.stated_confidence,
                                    is_falsifiable=c.is_falsifiable,
                                    edge_flags=c.edge_flags)
                    n_claims += 1
                conn.commit()
                if posts % 100 == 0:
                    print(f"  ...{posts} posts, {n_claims} claims")
    print(f"extracted: {posts} posts ({skipped} already done) -> {n_claims} new claims")


# ------------------------------------------------------------------- resolve
def resolve(handle: str, limit: int | None, sample: bool = False) -> None:
    import anthropic
    from resolve import resolve_claim
    client = anthropic.Anthropic()
    today = config.TODAY or date.today().isoformat()
    order = "random()" if sample else "posted_at"
    with psycopg.connect(config.SUPABASE_DB_URL) as conn:
        sid = db.get_or_create_source(conn, "@" + handle.lstrip("@"), is_seeded=True)
        sql = ("select id::text, text, coalesce(posted_at::text,''), "
               "coalesce(resolves_on::text,'') from claims "
               "where source_id=%s and status='open' "
               "and (resolves_on is null or resolves_on <= %s) "
               f"order by {order}")
        rows = conn.execute(sql, (sid, today)).fetchall()
        if limit:
            rows = rows[:limit]
        print(f"resolving {len(rows)} open+resolvable claims for @{handle.lstrip('@')} "
              f"(paid{', random sample' if sample else ''})...")
        tally = {"true": 0, "false": 0, "unresolvable": 0}
        tin = tout = tws = 0
        for i, (cid, text, posted, resolves) in enumerate(rows, 1):
            v = resolve_claim(client, text, posted, resolves, today=today)
            db.insert_resolution(conn, cid, v.verdict, method="web_retrieval",
                                 resolver=config.RESOLVE_MODEL,
                                 evidence_ref=(v.evidence_urls[0] if v.evidence_urls else None),
                                 confidence=v.confidence)
            conn.commit()
            tally[v.verdict] = tally.get(v.verdict, 0) + 1
            tin += v.input_tokens; tout += v.output_tokens; tws += v.web_searches
            print(f"  {i:3d}. [{v.verdict:11s}] ev={len(v.evidence_urls)} "
                  f"tok={v.input_tokens}/{v.output_tokens} ws={v.web_searches} {text[:52]}")
        n = len(rows) or 1
        cost = (tin / 1e6 * config.SONNET_IN_PER_M
                + tout / 1e6 * config.SONNET_OUT_PER_M
                + tws / 1000 * config.WEB_SEARCH_PER_1K)
        print(f"\nverdicts: {tally}")
        print(f"usage: in={tin} out={tout} web_searches={tws}")
        print(f"cost (approx prices): ${cost:.3f} total  |  ${cost/n:.4f}/claim  "
              f"[Sonnet ${config.SONNET_IN_PER_M}/{config.SONNET_OUT_PER_M} per M, "
              f"web ${config.WEB_SEARCH_PER_1K}/1k — verify]")


def score() -> None:
    import score_job
    with psycopg.connect(config.SUPABASE_DB_URL) as conn:
        print(f"recomputed {score_job.recompute_scores(conn)} score rows")


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["estimate", "ingest", "extract", "resolve", "score", "run"])
    ap.add_argument("--handle", help="X handle, e.g. CathieDWood (no @ needed)")
    ap.add_argument("--months", type=float, default=6.0)
    ap.add_argument("--no-replies", action="store_true", help="exclude replies (fewer, cleaner)")
    ap.add_argument("--max-pages", type=int, default=None, help="cap ingest pages (500 posts each)")
    ap.add_argument("--max-claims", type=int, default=None, help="cap resolve calls (spend guard)")
    ap.add_argument("--limit", type=int, default=None, help="alias for --max-claims on resolve")
    ap.add_argument("--sample", action="store_true", help="resolve a RANDOM subset (calibration)")
    ap.add_argument("--yes", action="store_true", help="confirm paid stages in `run`")
    a = ap.parse_args()

    if a.stage in ("estimate", "ingest", "extract", "resolve", "run") and not a.handle:
        sys.exit("--handle required")

    try:
        if a.stage == "estimate":
            estimate(a.handle, a.months, a.no_replies)
        elif a.stage == "ingest":
            ingest(a.handle, a.months, a.no_replies, max_pages=a.max_pages)
        elif a.stage == "extract":
            extract(a.handle)
        elif a.stage == "resolve":
            resolve(a.handle, a.limit or a.max_claims, sample=a.sample)
        elif a.stage == "score":
            score()
        elif a.stage == "run":
            n = estimate(a.handle, a.months, a.no_replies)
            if not a.yes:
                print(f"\n{n} posts would be pulled (paid). Re-run with --yes to proceed.")
                return
            ingest(a.handle, a.months, a.no_replies, max_pages=a.max_pages)
            extract(a.handle)
            resolve(a.handle, a.limit or a.max_claims)
            score()
    except xclient.XAuthError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
