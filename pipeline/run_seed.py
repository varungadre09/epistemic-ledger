"""Seed loader: load the SIMULATED flat dataset into Supabase.

The simulated data already contains extracted claims + ground_truth, so this is a
deterministic transform + load — no LLM. Author-agnostic (the real ~20 authors come
later via the same tables).

Identity scoping: each ledger row in the simulated data is an independent synthetic
world that reuses the same handles with different planted qualities, so the default
--scope ledger loads one source per (ledger, handle) to keep each world internally
consistent (matches scripts/test_a.py). --scope global pools by raw handle.

Topic nodes: simulated slugs are mostly outside the starter taxonomy, so missing
nodes are created as status='provisional' (no embedding) directly from the slug.
This reintroduces taxonomy fragmentation — acceptable for test data; canonicalization
(embedder) is a later concern for live authors.

Usage:
    python -I run_seed.py <flat_parquet> [--scope ledger|global] [--limit-ledgers N] [--dry-run]

--dry-run needs only pandas (no DB, no psycopg). A real load needs SUPABASE_DB_URL
and the schema applied (db/001_schema.sql, db/002_seed_taxonomy.sql).
"""
from __future__ import annotations
import sys, argparse, json, re
from collections import OrderedDict
import pandas as pd

from ltree_util import slug_to_ltree  # shared, dependency-free


def transform(df: pd.DataFrame, scope: str):
    """Pure transform (no DB). Returns (sources, claims, topics)."""
    handle_col = "handle" if scope == "ledger" else "handle_raw"

    # --- sources: dedup by scoped handle ---
    sources = OrderedDict()
    for _, r in df[[handle_col, "handle_raw", "persona"]].iterrows():
        h = r[handle_col]
        if h not in sources:
            sources[h] = {"handle": h, "handle_raw": r["handle_raw"],
                          "persona": r.get("persona")}

    # --- topics: distinct slugs -> ltree path (provisional) ---
    topics = {}   # path -> {slug, label, path}
    def ensure_topic(slug):
        path = slug_to_ltree(slug)
        if not path:
            return None
        if path not in topics:
            topics[path] = {"path": path, "slug": slug,
                            "label": path.rsplit(".", 1)[-1]}
        return path

    # --- claims ---
    claims = []
    for _, r in df.iterrows():
        gt = str(r["ground_truth"]).strip().lower()
        ground_truth = gt if gt in ("true", "false") else None
        raw_tp = r["topic_paths"]
        raw_tp = [] if raw_tp is None else list(raw_tp)
        paths = [p for p in (ensure_topic(s) for s in raw_tp) if p]
        edge = r.get("edge_case_norm")
        edge_flags = [edge] if (edge and str(edge) != "None") else []
        claims.append({
            "handle": r[handle_col],
            "tweet_id": r.get("claim_id"),
            "text": r.get("text"),
            "topic_paths": paths,
            "ground_truth": ground_truth,
            "stated_confidence": (None if pd.isna(r.get("stated_confidence"))
                                  else float(r.get("stated_confidence"))),
            "posted_at": r.get("posted_at"),
            "resolves_on": r.get("resolves_on"),
            "is_falsifiable": bool(r.get("is_falsifiable")) if r.get("is_falsifiable") is not None else None,
            "edge_flags": edge_flags,
        })
    return sources, claims, topics


def dry_run(sources, claims, topics):
    resolved = [c for c in claims if c["ground_truth"] in ("true", "false")]
    print(f"sources (scoped):      {len(sources)}")
    print(f"claims:                {len(claims)}  (resolved: {len(resolved)}, "
          f"open/unresolved: {len(claims)-len(resolved)})")
    print(f"distinct topic nodes:  {len(topics)}  (created as provisional)")
    # slug->path collisions (different slugs mapping to same ltree path)
    path_slugs = {}
    for t in topics.values():
        path_slugs.setdefault(t["path"], set()).add(t["slug"])
    # (topics is keyed by path so only first slug kept; recompute collisions from claims)
    print("\nsample sources:")
    for s in list(sources.values())[:3]:
        print("  ", s)
    print("\nsample claims:")
    for c in claims[:3]:
        print("  ", {k: c[k] for k in ("handle", "text", "topic_paths",
                                       "ground_truth", "edge_flags")})
    print("\nsample topic paths:")
    for t in list(topics.values())[:8]:
        print("  ", t["slug"], "->", t["path"])


def load(sources, claims, topics):
    """Real load into Supabase. Lazy imports so --dry-run needs no psycopg."""
    import psycopg
    from config import SUPABASE_DB_URL
    import db, score_job
    if not SUPABASE_DB_URL:
        sys.exit("SUPABASE_DB_URL not set.")
    with psycopg.connect(SUPABASE_DB_URL) as conn:
        # upsert topic nodes (provisional)
        with conn.cursor() as cur:
            cur.executemany(
                "insert into topic_nodes (path, slug, label, status) "
                "values (%s::ltree, %s, %s, 'provisional') on conflict (path) do nothing",
                [(t["path"], t["slug"], t["label"]) for t in topics.values()])
        # path -> node id
        rows = conn.execute("select path::text, id from topic_nodes").fetchall()
        node_id = {p: i for p, i in rows}
        # sources
        src_id = {}
        for h, s in sources.items():
            src_id[h] = db.get_or_create_source(conn, h, is_seeded=False,
                                                persona=s.get("persona"))
        # claims
        n = 0
        for c in claims:
            tnids = [node_id[p] for p in c["topic_paths"] if p in node_id]
            if not tnids:
                continue
            db.insert_claim(conn, src_id[c["handle"]], c["text"], tnids,
                            tweet_id=c["tweet_id"], posted_at=c["posted_at"],
                            resolves_on=c["resolves_on"],
                            stated_confidence=c["stated_confidence"],
                            is_falsifiable=c["is_falsifiable"],
                            edge_flags=c["edge_flags"], ground_truth=c["ground_truth"])
            n += 1
        conn.commit()
        print(f"loaded {len(sources)} sources, {n} claims, {len(topics)} topics")
        print(f"recomputed {score_job.recompute_scores(conn)} score rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("--scope", choices=["ledger", "global"], default="ledger")
    ap.add_argument("--limit-ledgers", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    df = pd.read_parquet(a.parquet)
    if a.limit_ledgers is not None:
        keep = sorted(df["ledger_id"].unique())[:a.limit_ledgers]
        df = df[df["ledger_id"].isin(keep)]
    print(f"input rows: {len(df)}  (scope={a.scope}, "
          f"limit_ledgers={a.limit_ledgers})\n")

    sources, claims, topics = transform(df, a.scope)
    if a.dry_run:
        dry_run(sources, claims, topics)
    else:
        load(sources, claims, topics)


if __name__ == "__main__":
    main()
