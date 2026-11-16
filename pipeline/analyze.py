"""Live tweet analysis: classify a specific tweet, then report the author's
reliability in exactly that subfield.

Flow (the browsing vision):
  tweet text + author  ->  LLM extracts the falsifiable claim(s) and classifies
  each into the taxonomy  ->  look up the author's precomputed score AT that topic
  node, backing off to the nearest scored ancestor if the exact leaf isn't scored
  ->  return that, plus the author's full breakdown for context.

This DOES make one LLM call (extraction) at request time — that's legitimate: it's
about this specific tweet, not recomputing scores. It does NOT resolve the tweet's
own claim (its outcome may be in the future); it classifies, then reads the track
record. Cost ~1 cheap Sonnet call per analyze.
"""
from __future__ import annotations
import psycopg

import config
import db
from answer import build_answer, resolve_source, _slug
from extract import extract_claims
from run_seed import slug_to_ltree


def score_at_topic(conn, source_id: str, ltree_path: str):
    """Author's score at this topic node, or the nearest scored ANCESTOR (back-off)."""
    if not ltree_path:
        return None
    row = conn.execute(
        "select topic_path::text, n, hits, shrunk_mean, ci_lo, ci_hi "
        "from scores where source_id=%s and topic_path @> %s::ltree "
        "order by nlevel(topic_path) desc limit 1",
        (source_id, ltree_path)).fetchone()
    if not row:
        return None
    p, n, hits, mean, lo, hi = row
    return {"topic_path": p, "matched_topic": _slug(p),
            "n": int(n), "hits": int(hits), "shrunk_mean": float(mean),
            "ci_lo": float(lo), "ci_hi": float(hi)}


def build_analysis(conn, client, handle: str, tweet_text: str) -> dict:
    base = build_answer(conn, handle)
    if not base.get("found"):
        return base
    src, _ = resolve_source(conn, handle)
    sid = src[0]
    taxonomy = db.canonical_slugs(conn)
    extracted = extract_claims(client, tweet_text, taxonomy)

    out = []
    for c in extracted:
        slug = c.topic_slug or c.proposed_path
        path = slug_to_ltree(slug) if slug else None
        sc = score_at_topic(conn, sid, path)
        item = {"text": c.text, "classified": slug,
                "backed_off": bool(sc and sc["topic_path"] != path)}
        if sc:
            item.update(sc)
        out.append(item)

    base["tweet_claims"] = out
    base["no_claim"] = (len(out) == 0)
    return base


if __name__ == "__main__":  # quick manual test: python analyze.py @chamath "some tweet text"
    import sys, json, anthropic
    handle, text = sys.argv[1], sys.argv[2]
    with psycopg.connect(config.SUPABASE_DB_URL) as conn:
        a = build_analysis(conn, anthropic.Anthropic(), handle, text)
    print(json.dumps(a.get("tweet_claims"), indent=2))
