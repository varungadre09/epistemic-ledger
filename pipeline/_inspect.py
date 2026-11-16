"""Inspect current DB state. Read-only."""
import psycopg
from config import SUPABASE_DB_URL

with psycopg.connect(SUPABASE_DB_URL) as conn:
    rels = conn.execute(
        "select table_name, table_type from information_schema.tables "
        "where table_schema='public' order by table_type, table_name").fetchall()
    print("public relations:")
    for name, typ in rels:
        print(f"  {typ:11s} {name}")

    def count(sql):
        try:
            return conn.execute(sql).fetchone()[0]
        except Exception as e:
            return f"ERR {type(e).__name__}"

    print("\ncounts:")
    print("  topic_nodes total  :", count("select count(*) from topic_nodes"))
    print("  topic_nodes canon  :", count("select count(*) from topic_nodes where status='canonical'"))
    print("  sources            :", count("select count(*) from sources"))
    print("  claims             :", count("select count(*) from claims"))
    print("  claim_topics       :", count("select count(*) from claim_topics"))
    print("  resolutions        :", count("select count(*) from resolutions"))
    print("  scores             :", count("select count(*) from scores"))

    try:
        print("\nsample canonical topic_nodes:")
        for slug, path in conn.execute(
                "select slug, path::text from topic_nodes where status='canonical' "
                "order by slug limit 6").fetchall():
            print("  ", slug, "->", path)
    except Exception as e:
        print("  (topic_nodes query failed:", type(e).__name__, str(e)[:120], ")")
