"""Answer / read path: given an author, return their reliability ledger.

This is the backend the browser plugin calls. It ONLY reads the precomputed
`scores` table (written by score_job.py) joined to `sources` — no LLM, no web
search, no scoring arithmetic at request time. That keeps the hot path cheap
and deterministic.

What it returns (see build_answer -> dict, JSON-serialisable for the API):
  - the matched source (handle, persona, seeded?)
  - per-domain headline reliability = the depth-1 topic node score, which is the
    Beta-Binomial posterior for that whole subtree (finance / economics / politics)
  - the full per-topic breakdown (shrunk_mean + 90% CI + n/hits + parent_prior)
  - a plain-text narration with thin-data caveats

IMPORTANT (identity scoping): simulated seed handles are stored as
'<ledger_id>::@handle' — the SAME @handle can exist as many independent sources
(one per simulated world). A query therefore may match several sources; by
default we report the single source with the most resolved claims and note how
many others matched. Real authors (Cramer, Chamath) will have one unscoped
'@handle' source, so this ambiguity disappears in production.

Usage:
    python answer.py --list [--like finance]      # browse scored sources
    python answer.py "@calibrated_gen"            # full ledger for an author
    python answer.py "@calibrated_gen" --topic finance
    python answer.py "@calibrated_gen" --json
Read-only. Needs SUPABASE_DB_URL (../.env, loaded by config).
"""
from __future__ import annotations
import argparse, json, sys
import psycopg
import config

MIN_N_CONFIDENT = 5          # below this, a node's rate is "thin" (CI dominates)
SEP = config.SEP             # '>' display separator for topic paths


def _slug(path: str) -> str:
    """ltree 'finance.us_equities.earnings' -> display 'finance>us-equities>earnings'.
    Underscores were hyphens in the original slug (ltree label charset); we only
    flip the segment separator for display and leave labels as stored, since the
    seed taxonomy used underscores within labels too."""
    return path.replace(".", SEP)


def list_sources(conn, like: str | None = None, limit: int = 50):
    sql = (
        "select src.handle, coalesce(src.persona,''), src.is_seeded, "
        "       count(*) as n_nodes, "
        "       coalesce(max(s.n),0) as max_n "
        "from sources src join scores s on s.source_id = src.id "
    )
    params: list = []
    if like:
        sql += "where src.handle ilike %s "
        params.append(f"%{like}%")
    sql += "group by src.handle, src.persona, src.is_seeded order by max_n desc limit %s"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def resolve_source(conn, query: str):
    """Return (id, handle, persona, is_seeded, n_matches) for the best match, or None.
    Best = the matching source with the most resolved claims at any node."""
    rows = conn.execute(
        "select src.id::text, src.handle, coalesce(src.persona,''), src.is_seeded, "
        "       coalesce(max(s.n),0) as max_n "
        "from sources src join scores s on s.source_id = src.id "
        "where src.handle ilike %s "
        "group by src.id, src.handle, src.persona, src.is_seeded "
        "order by max_n desc",
        (f"%{query}%",),
    ).fetchall()
    if not rows:
        return None, 0
    return rows[0], len(rows)


def fetch_scores(conn, source_id: str, topic_prefix: str | None = None):
    sql = (
        "select topic_path::text, n, hits, raw_rate, shrunk_mean, ci_lo, ci_hi, parent_prior "
        "from scores where source_id = %s "
    )
    params: list = [source_id]
    if topic_prefix:
        # ltree prefix match: node itself or any descendant
        sql += "and topic_path <@ %s::ltree "
        params.append(topic_prefix.replace(SEP, ".").replace("-", "_"))
    sql += "order by topic_path"
    out = []
    for p, n, hits, raw, mean, lo, hi, prior in conn.execute(sql, params).fetchall():
        out.append(dict(
            topic=_slug(p), depth=p.count(".") + 1, n=int(n), hits=int(hits),
            raw_rate=(float(raw) if raw is not None else None),
            shrunk_mean=float(mean), ci_lo=float(lo), ci_hi=float(hi),
            parent_prior=(float(prior) if prior is not None else None),
        ))
    return out


def build_answer(conn, query: str, topic: str | None = None) -> dict:
    src, n_matches = resolve_source(conn, query)
    if src is None:
        return {"query": query, "found": False,
                "reason": "no scored source matches that handle"}
    sid, handle, persona, is_seeded, max_n = src
    topics = fetch_scores(conn, sid, topic)
    # headline = depth-1 domain nodes (each is the posterior for a whole subtree)
    domains = [t for t in topics if t["depth"] == 1]
    total_n = sum(t["n"] for t in domains)
    total_hits = sum(t["hits"] for t in domains)
    win = conn.execute(
        "select min(posted_at)::date::text, max(posted_at)::date::text "
        "from claims where source_id=%s and status='resolved' and posted_at is not null",
        (sid,)).fetchone()
    return {
        "query": query, "found": True,
        "handle": handle, "persona": persona, "is_seeded": is_seeded,
        "other_sources_matched": n_matches - 1,  # same @handle in other sim worlds
        "n_resolved_claims": total_n,
        "overall_raw_rate": (total_hits / total_n if total_n else None),
        "window": {"from": win[0], "to": win[1]} if win and win[0] else None,
        "domains": domains,
        "topics": topics,
        "topic_filter": topic,
        "source_id": sid,
    }


def narrate(a: dict) -> str:
    if not a.get("found"):
        return f'No ledger for "{a["query"]}": {a.get("reason","")}'
    L = []
    who = a["handle"] + (f'  ({a["persona"]})' if a["persona"] else "")
    seed = "seeded" if a["is_seeded"] else "on-the-fly"
    L.append(f'{who}  [{seed}]  —  {a["n_resolved_claims"]} resolved claims')
    if a["other_sources_matched"]:
        L.append(f'  (note: {a["other_sources_matched"]} other simulated world(s) '
                 f'also use this handle; showing the one with the most data)')
    if a["overall_raw_rate"] is not None:
        L.append(f'  overall hit rate: {a["overall_raw_rate"]:.0%} '
                 f'(raw, across domains)')
    L.append("")
    for d in sorted(a["domains"], key=lambda t: -t["n"]):
        tag = "" if d["n"] >= MIN_N_CONFIDENT else "  [thin]"
        L.append(f'  {d["topic"]:<34} {d["shrunk_mean"]:.0%} reliable  '
                 f'(90% CI {d["ci_lo"]:.0%}–{d["ci_hi"]:.0%}, n={d["n"]}, '
                 f'hits={d["hits"]}){tag}')
    leaves = [t for t in a["topics"] if t["depth"] > 1 and t["n"] >= MIN_N_CONFIDENT]
    if leaves:
        best = max(leaves, key=lambda t: t["shrunk_mean"])
        worst = min(leaves, key=lambda t: t["shrunk_mean"])
        L.append("")
        L.append(f'  strongest: {best["topic"]} {best["shrunk_mean"]:.0%} (n={best["n"]})')
        L.append(f'  weakest:   {worst["topic"]} {worst["shrunk_mean"]:.0%} (n={worst["n"]})')
    thin = [t for t in a["topics"] if t["n"] < MIN_N_CONFIDENT]
    if thin:
        L.append("")
        L.append(f'  {len(thin)} topic(s) have <{MIN_N_CONFIDENT} resolved claims — '
                 f'their rate is mostly the parent prior (shrinkage), not own evidence.')
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="author handle (substring ok)")
    ap.add_argument("--list", action="store_true", help="list scored sources")
    ap.add_argument("--like", help="filter --list by handle substring")
    ap.add_argument("--topic", help="restrict to a topic subtree, e.g. finance or finance>us-equities")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    with psycopg.connect(config.SUPABASE_DB_URL) as conn:
        if a.list or not a.query:
            rows = list_sources(conn, a.like)
            print(f"{len(rows)} scored source(s)"
                  + (f' matching "{a.like}"' if a.like else "") + ":\n")
            for h, p, seeded, n_nodes, max_n in rows:
                print(f'  {h:<40} nodes={n_nodes:<3} maxN={max_n:<4} '
                      f'{"seeded" if seeded else ""}  {p[:30]}')
            if not a.query:
                return
        ans = build_answer(conn, a.query, a.topic)

    if a.json:
        json.dump(ans, sys.stdout, indent=2)
        print()
    else:
        print("\n" + narrate(ans))


if __name__ == "__main__":
    main()
