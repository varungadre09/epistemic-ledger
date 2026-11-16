"""Supabase/Postgres data access for the pipeline.

Uses psycopg (direct Postgres) — verifiable and simple. supabase-py is an
alternative if you prefer the REST client; shapes there are NOT verified here.
Scaffold only — not executed. Requires psycopg[binary] + SUPABASE_DB_URL.

TAXONOMY CANONICALIZATION (canonicalize_topic) needs an embedder, which is NOT
chosen yet (no Claude embeddings endpoint) — that function is a documented stub.
"""
from __future__ import annotations
from typing import List, Optional
import psycopg


# ------------------------------- reads -------------------------------
def canonical_slugs(conn: psycopg.Connection, root: Optional[str] = None) -> List[str]:
    """Canonical taxonomy slugs to show the extraction model. Optionally one subtree."""
    if root:
        rows = conn.execute(
            "select slug from topic_nodes where status='canonical' "
            "and path <@ (select path from topic_nodes where slug=%s) order by slug",
            (root,)).fetchall()
    else:
        rows = conn.execute(
            "select slug from topic_nodes where status='canonical' order by slug"
        ).fetchall()
    return [r[0] for r in rows]


def get_or_create_source(conn: psycopg.Connection, handle: str,
                         is_seeded: bool = False, persona: str | None = None) -> str:
    h = handle.strip().lower()
    row = conn.execute("select id from sources where handle=%s", (h,)).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "insert into sources (handle, is_seeded, persona) values (%s, %s, %s) returning id",
        (h, is_seeded, persona)).fetchone()[0]


# ------------------------------- writes ------------------------------
def insert_claim(conn: psycopg.Connection, source_id: str, text: str,
                 topic_node_ids: List[str], *, tweet_id=None, posted_at=None,
                 resolves_on=None, stated_confidence=None, is_falsifiable=True,
                 edge_flags=None, ground_truth=None) -> str:
    status = "resolved" if ground_truth in ("true", "false") else "open"
    claim_id = conn.execute(
        "insert into claims (source_id, tweet_id, text, is_falsifiable, "
        "stated_confidence, posted_at, resolves_on, status, ground_truth, edge_flags) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (source_id, tweet_id, text, is_falsifiable, stated_confidence, posted_at,
         resolves_on, status, ground_truth, edge_flags or []),
    ).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into claim_topics (claim_id, topic_node_id, is_primary) "
            "values (%s,%s,%s) on conflict do nothing",
            [(claim_id, nid, i == 0) for i, nid in enumerate(topic_node_ids)],
        )
    return claim_id


def insert_resolution(conn: psycopg.Connection, claim_id: str, verdict: str,
                      method: str, resolver: str, evidence_ref: Optional[str] = None,
                      confidence: Optional[float] = None) -> str:
    # The trg_apply_resolution trigger syncs claims.status / ground_truth.
    return conn.execute(
        "insert into resolutions (claim_id, verdict, method, evidence_ref, resolver, "
        "confidence) values (%s,%s,%s,%s,%s,%s) returning id",
        (claim_id, verdict, method, evidence_ref, resolver, confidence),
    ).fetchone()[0]


# --------------------------- taxonomy (stub) -------------------------
def canonicalize_topic(conn: psycopg.Connection, proposed_slug: str,
                       parent_slug: str, label: str) -> str:
    """Map a proposed node to an existing canonical node, or insert it as provisional.

    TODO: implement once an embedder is chosen:
      1. embed(label or proposed_slug)
      2. SELECT id, slug, 1-(embedding <=> %s) AS sim FROM topic_nodes
         ORDER BY embedding <=> %s LIMIT 1;   -- pgvector cosine distance
      3. if sim >= NODE_MATCH_THRESHOLD: return that node's id
      4. else insert (path under parent, slug, label, status='provisional',
         embedding) and return the new id.
    Until implemented, raise so callers don't silently create junk nodes.
    """
    raise NotImplementedError("canonicalize_topic needs an embedder — see config.EMBED_MODEL")
