"""Spot-check loaded data + scores. Read-only."""
import psycopg
from config import SUPABASE_DB_URL

with psycopg.connect(SUPABASE_DB_URL) as conn:
    for t in ("sources", "claims", "scores", "topic_nodes"):
        print(f"{t:12s}", conn.execute(f"select count(*) from {t}").fetchone()[0])

    pop = conn.execute("select avg((ground_truth='true')::int)::float "
                       "from claims where status='resolved'").fetchone()[0]
    print(f"\npopulation resolved true-rate (root prior): {pop:.3f}")

    print("\nscores for one source (0::@calibrated_gen):")
    rows = conn.execute(
        "select s.topic_path::text, s.n, s.hits, round(s.raw_rate::numeric,2) raw, "
        "round(s.shrunk_mean::numeric,3) shrunk, round(s.ci_lo::numeric,3) lo, "
        "round(s.ci_hi::numeric,3) hi "
        "from scores s join sources src on src.id=s.source_id "
        "where src.handle='0::@calibrated_gen' order by s.topic_path").fetchall()
    print(f"  {'topic_path':40s} {'n':>3} {'hits':>4} {'raw':>5} {'shrunk':>7} {'lo':>6} {'hi':>6}")
    for path, n, hits, raw, shrunk, lo, hi in rows:
        print(f"  {path:40s} {n:>3} {hits:>4} {str(raw):>5} {str(shrunk):>7} {str(lo):>6} {str(hi):>6}")
