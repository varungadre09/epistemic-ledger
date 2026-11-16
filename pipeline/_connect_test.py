"""Connectivity check only. Does not print the connection string."""
import psycopg
from config import SUPABASE_DB_URL

if not SUPABASE_DB_URL:
    raise SystemExit("SUPABASE_DB_URL not set (checked env + .env)")
try:
    with psycopg.connect(SUPABASE_DB_URL) as conn:
        ver = conn.execute("select version()").fetchone()[0]
        n = conn.execute("select count(*) from information_schema.tables "
                         "where table_schema='public'").fetchone()[0]
        has_claims = conn.execute(
            "select exists (select 1 from information_schema.tables "
            "where table_schema='public' and table_name='claims')").fetchone()[0]
        print("CONNECT OK")
        print("server:", ver.split(",")[0])
        print("public tables:", n, "| claims table exists:", has_claims)
except Exception as e:
    print("CONNECT FAILED:", type(e).__name__)
    print(str(e)[:400])
