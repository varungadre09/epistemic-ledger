"""Scoring job: hierarchical Beta-Binomial back-off over the ledger -> scores table.

Reuses the exact shrinkage from scripts/test_a.py, fed by the raw_score_counts view
(which already rolls each claim up to all ancestor nodes). The LLM is NOT involved.

Run after new resolutions land. Scaffold only — not executed.
Requires: psycopg[binary], scipy, numpy, and SUPABASE_DB_URL.
"""
from __future__ import annotations
import psycopg
from scipy.stats import beta

from config import SUPABASE_DB_URL, PARENT_STRENGTH, CI


def shrink(hits: int, n: int, prior_mean: float):
    a = prior_mean * PARENT_STRENGTH + hits
    b = (1 - prior_mean) * PARENT_STRENGTH + (n - hits)
    lo, hi = beta.ppf(CI, a, b)
    return a / (a + b), float(lo), float(hi)


def recompute_scores(conn: psycopg.Connection) -> int:
    # population prior = resolved true-rate across all claims (root prior)
    pop_rate = conn.execute(
        "select avg((ground_truth='true')::int)::float "
        "from claims where status='resolved'"
    ).fetchone()[0] or 0.5

    rows = conn.execute(
        "select source_id, topic_path::text, n, hits from raw_score_counts"
    ).fetchall()

    # group by source
    by_source: dict = {}
    for source_id, path, n, hits in rows:
        by_source.setdefault(source_id, []).append((path, int(n), int(hits)))

    upserts = []
    for source_id, nodes in by_source.items():
        # shallow -> deep so a parent posterior exists before its children
        nodes.sort(key=lambda t: t[0].count("."))
        post: dict = {}
        for path, n, hits in nodes:
            parent = path.rsplit(".", 1)[0] if "." in path else None
            prior = post.get(parent, pop_rate)
            mean, lo, hi = shrink(hits, n, prior)
            post[path] = mean
            upserts.append((source_id, path, n, hits,
                            (hits / n if n else None), mean, lo, hi, prior))

    with conn.cursor() as cur:
        cur.executemany(
            "insert into scores (source_id, topic_path, n, hits, raw_rate, "
            "shrunk_mean, ci_lo, ci_hi, parent_prior, updated_at) "
            "values (%s, %s::ltree, %s, %s, %s, %s, %s, %s, %s, now()) "
            "on conflict (source_id, topic_path) do update set "
            "n=excluded.n, hits=excluded.hits, raw_rate=excluded.raw_rate, "
            "shrunk_mean=excluded.shrunk_mean, ci_lo=excluded.ci_lo, "
            "ci_hi=excluded.ci_hi, parent_prior=excluded.parent_prior, "
            "updated_at=now()",
            upserts,
        )
    conn.commit()
    return len(upserts)


if __name__ == "__main__":
    with psycopg.connect(SUPABASE_DB_URL) as conn:
        print(f"upserted {recompute_scores(conn)} score rows")
