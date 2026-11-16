-- Read-only role for the PUBLIC / shared demo API.
-- Use this role's connection string as SUPABASE_DB_URL when running the API for
-- others (with EPI_ALLOW_BUILD=0). The read path (/author, /authors, /analyze,
-- /status) only SELECTs; only /build writes, so under this role build is impossible
-- even if the flag were on. Keys/owner creds never ship this way.
--
-- Apply in the Supabase SQL editor. Change the password first.

create role ledger_readonly login password 'CHANGE_ME_STRONG';

grant connect on database postgres to ledger_readonly;
grant usage   on schema public       to ledger_readonly;

-- exactly the tables the read path touches
grant select on sources, scores, topic_nodes, claims to ledger_readonly;

-- Connection string to put in .env on the demo host:
--   SUPABASE_DB_URL=host=db.<ref>.supabase.co port=5432 dbname=postgres \
--     user=ledger_readonly password=<the password> sslmode=require connect_timeout=10
--
-- To revoke after the demo window:
--   drop owned by ledger_readonly;  drop role ledger_readonly;
