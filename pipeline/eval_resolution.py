"""Resolution eval: does the resolver (Sonnet 5.5 + web_search) recover the planted
ground_truth, across diverse authors/behaviours?

IMPORTANT: the planted ground_truth is SYNTHETIC — it may not match reality. This
eval reports resolver-vs-planted AGREEMENT, and flags every disagreement for manual
inspection (the resolver may be right and the synthetic label wrong). It does NOT
write to the DB (read-only on claims; results saved to a local JSON).

Sampling is balanced across (author, planted truth) so truthful- and false-labelled
claims from different personas are all represented.

Usage:
    python eval_resolution.py [--limit N] [--per-cell K] [--dry-run]
--dry-run shows the chosen sample without any API calls.
Run (no --dry-run) needs ANTHROPIC_API_KEY (put it in ../.env).
"""
from __future__ import annotations
import argparse, json, os, time
from collections import defaultdict
import psycopg
import config  # loads .env -> SUPABASE_DB_URL, ANTHROPIC_API_KEY


def fetch_resolved(conn):
    # author derived from the scoped handle 'ledger::@handle' (handle_raw not stored)
    return conn.execute(
        "select case when position('::' in src.handle)>0 "
        "            then split_part(src.handle,'::',2) else src.handle end as author, "
        "       coalesce(src.persona,''), c.id::text, c.text, "
        "       c.ground_truth, c.posted_at::text, c.resolves_on::text "
        "from claims c join sources src on src.id = c.source_id "
        "where c.status='resolved' and c.text is not null "
        "order by author, c.ground_truth").fetchall()


def diverse_sample(rows, limit, per_cell):
    """Balanced across (handle_raw, ground_truth); round-robin over authors."""
    cells = defaultdict(list)
    for r in rows:
        cells[(r[0], r[4])].append(r)       # (handle_raw, ground_truth) -> rows
    authors = sorted({k[0] for k in cells})
    picked, seen = [], defaultdict(int)
    # round-robin: one author at a time, alternating true/false, up to per_cell
    progressed = True
    while progressed and len(picked) < limit:
        progressed = False
        for a in authors:
            for gt in ("true", "false"):
                bucket = cells.get((a, gt), [])
                if seen[(a, gt)] < min(per_cell, len(bucket)):
                    picked.append(bucket[seen[(a, gt)]])
                    seen[(a, gt)] += 1
                    progressed = True
                    if len(picked) >= limit:
                        return picked
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--per-cell", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with psycopg.connect(config.SUPABASE_DB_URL) as conn:
        rows = fetch_resolved(conn)
    sample = diverse_sample(rows, a.limit, a.per_cell)

    print(f"resolved claims available: {len(rows)}")
    print(f"sample: {len(sample)} claims across "
          f"{len({r[0] for r in sample})} authors, "
          f"planted true={sum(r[4]=='true' for r in sample)} "
          f"false={sum(r[4]=='false' for r in sample)}\n")
    for r in sample:
        print(f"  [{r[4]:5s}] {r[0]:17s} | {r[1][:32]:32s} | {r[3][:70]}")

    if a.dry_run:
        print("\n--dry-run: no API calls made.")
        return

    import anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("\nANTHROPIC_API_KEY not set (add it to ../.env).")
    from resolve import resolve_claim
    client = anthropic.Anthropic()

    results, agree, resolved_n, unresolvable = [], 0, 0, 0
    print("\nresolving (real paid calls)...\n")
    for i, r in enumerate(sample, 1):
        handle, persona, cid, text, gt, posted, resolves = r
        v = resolve_claim(client, text, posted, resolves)
        ok = (v.verdict == gt)
        if v.verdict in ("true", "false"):
            resolved_n += 1
            agree += int(ok)
        else:
            unresolvable += 1
        results.append(dict(handle=handle, persona=persona, claim_id=cid, text=text,
                            planted=gt, verdict=v.verdict, agree=ok,
                            n_evidence=len(v.evidence_urls),
                            evidence=v.evidence_urls[:3], confidence=v.confidence))
        print(f"  {i:2d}. [{gt:5s}->{v.verdict:11s}] "
              f"{'OK ' if ok else 'XX '}ev={len(v.evidence_urls)} {handle} | {text[:55]}")

    print("\n" + "=" * 70)
    print(f"resolved (true/false): {resolved_n}/{len(sample)}   "
          f"unresolvable: {unresolvable}")
    if resolved_n:
        print(f"agreement with planted label: {agree}/{resolved_n} "
              f"({agree/resolved_n:.0%})")
    disagree = [r for r in results if r["verdict"] in ("true","false") and not r["agree"]]
    if disagree:
        print(f"\nDISAGREEMENTS ({len(disagree)}) — INSPECT (resolver may be right, "
              f"synthetic label wrong):")
        for r in disagree:
            print(f"  planted={r['planted']} verdict={r['verdict']} {r['handle']}")
            print(f"    {r['text']}")
            for u in r["evidence"]:
                print(f"    ev: {u}")

    os.makedirs("../eval_runs", exist_ok=True)
    out = f"../eval_runs/resolution_{int(time.time())}.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
