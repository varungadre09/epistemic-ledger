-- Epistemic truth-ledger — core schema (Supabase / Postgres)
-- Apply in Supabase SQL editor or: supabase db push / psql < 001_schema.sql
-- Design notes:
--   * Topic taxonomy is a first-class registry (topic_nodes) the inference LLM
--     MUST consult before classifying; new nodes enter as 'provisional' and are
--     deduped against existing nodes by embedding similarity (pgvector).
--   * ltree gives hierarchical path queries + Beta-Binomial back-off (ancestors).
--     ltree labels allow [A-Za-z0-9_] only, so hyphens in slugs ('us-equities')
--     are stored as underscores in `path`; the original '>'-form is kept in `slug`.
--   * Scoring (shrinkage + credible intervals) is computed by a job, NOT the LLM,
--     and written to `scores` for the API/plugin to read.

create extension if not exists ltree;
create extension if not exists vector;      -- pgvector
create extension if not exists pgcrypto;    -- gen_random_uuid()

-- ---------------------------------------------------------------- sources
create table if not exists sources (
    id            uuid primary key default gen_random_uuid(),
    handle        text not null unique,          -- canonical, lowercased, e.g. '@calibrated_gen'
    display_name  text,
    aliases       text[] not null default '{}',  -- known casing/spelling variants
    persona       text,
    is_seeded     boolean not null default false, -- one of the ~20 pre-built authors
    first_seen_at timestamptz,
    created_at    timestamptz not null default now()
);

-- ------------------------------------------------------------- topic_nodes
-- Taxonomy registry. Seed canonical nodes up front (002_seed_taxonomy.sql).
create table if not exists topic_nodes (
    id          uuid primary key default gen_random_uuid(),
    path        ltree not null unique,             -- e.g. 'finance.us_equities.earnings'
    slug        text  not null unique,             -- original form, e.g. 'finance>us-equities>earnings'
    label       text  not null,                    -- leaf label, e.g. 'earnings'
    status      text  not null default 'canonical'
                 check (status in ('canonical','provisional')),
    embedding   vector(1536),                      -- CHANGE dim to match your embedder (no Claude embeddings; use Voyage/OpenAI/etc.)
    created_at  timestamptz not null default now()
);
create index if not exists topic_nodes_path_gist on topic_nodes using gist (path);
-- Vector index for canonicalization nearest-neighbour. Build after rows exist.
-- create index topic_nodes_embedding_hnsw on topic_nodes using hnsw (embedding vector_cosine_ops);

-- ------------------------------------------------------------------ claims
create table if not exists claims (
    id                uuid primary key default gen_random_uuid(),
    source_id         uuid not null references sources(id) on delete cascade,
    tweet_id          text,                        -- ref to originating tweet
    text              text not null,               -- the claim/prediction text
    is_falsifiable    boolean,
    stated_confidence numeric check (stated_confidence between 0 and 1),
    posted_at         timestamptz,
    resolves_on       date,
    status            text not null default 'open'
                       check (status in ('open','resolved','unresolvable')),
    ground_truth      text check (ground_truth in ('true','false')),  -- set only when resolved
    edge_flags        text[] not null default '{}',-- normalized enum (see CHECK via trigger/app)
    raw               jsonb,                       -- original extraction payload
    created_at        timestamptz not null default now(),
    -- resolved claims must carry a verdict; open/unresolvable must not
    constraint claims_resolved_has_truth check (
        (status = 'resolved' and ground_truth is not null)
        or (status <> 'resolved' and ground_truth is null)
    )
);
create index if not exists claims_source_idx on claims(source_id);
create index if not exists claims_status_idx on claims(status);

-- Canonical edge-flag vocabulary (reference; enforce in app or add a FK/array check)
create table if not exists edge_flag_vocab (
    flag   text primary key,
    status text not null default 'canonical' check (status in ('canonical','provisional'))
);
insert into edge_flag_vocab(flag) values
    ('flip_flop'),('barely_resolved'),('multi_topic'),('retracted'),('open')
on conflict do nothing;

-- -------------------------------------------------- claim <-> topic (N:M)
-- A claim may map to several topic paths (multi_topic). Exactly one is primary.
create table if not exists claim_topics (
    claim_id      uuid not null references claims(id) on delete cascade,
    topic_node_id uuid not null references topic_nodes(id),
    is_primary    boolean not null default false,
    primary key (claim_id, topic_node_id)
);
create index if not exists claim_topics_node_idx on claim_topics(topic_node_id);

-- ------------------------------------------------------------- resolutions
-- Provenance of every verdict. This is where pt-4 "judge correctness" lives.
create table if not exists resolutions (
    id           uuid primary key default gen_random_uuid(),
    claim_id     uuid not null references claims(id) on delete cascade,
    verdict      text not null check (verdict in ('true','false','unresolvable')),
    method       text not null check (method in
                  ('dataset_ground_truth','model_parametric','web_retrieval','human')),
    evidence_ref text,         -- url / citation / source id (required for web_retrieval, enforce in app)
    resolver     text,         -- model id (e.g. 'claude-...') or user id
    confidence   numeric check (confidence between 0 and 1),
    resolved_at  timestamptz not null default now()
);
create index if not exists resolutions_claim_idx on resolutions(claim_id);

-- Keep claims in sync with the latest resolution.
create or replace function apply_resolution() returns trigger as $$
begin
    if new.verdict in ('true','false') then
        update claims
           set status = 'resolved', ground_truth = new.verdict
         where id = new.claim_id;
    elsif new.verdict = 'unresolvable' then
        update claims
           set status = 'unresolvable', ground_truth = null
         where id = new.claim_id;
    end if;
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_apply_resolution on resolutions;
create trigger trg_apply_resolution
    after insert on resolutions
    for each row execute function apply_resolution();

-- ------------------------------------------------------------------ scores
-- Precomputed per (source, topic node) reliability, written by the scoring job.
-- The API / browser plugin READS this; it is never computed at request time.
create table if not exists scores (
    source_id    uuid not null references sources(id) on delete cascade,
    topic_path   ltree not null,
    n            integer not null,       -- resolved claims at node + descendants
    hits         integer not null,       -- of which ground_truth = 'true'
    raw_rate     numeric,                -- hits/n (null when n=0)
    shrunk_mean  numeric not null,       -- Beta-Binomial back-off posterior mean
    ci_lo        numeric not null,       -- 90% credible interval
    ci_hi        numeric not null,
    parent_prior numeric,
    updated_at   timestamptz not null default now(),
    primary key (source_id, topic_path)
);
create index if not exists scores_path_gist on scores using gist (topic_path);

-- ------------------------------------------------- raw counts helper view
-- Exact (source, node) resolved counts the scoring job reads to apply shrinkage.
-- Expands each claim to all ancestor paths so a node aggregates its descendants.
create or replace view raw_score_counts as
select c.source_id,
       anc.ancestor            as topic_path,
       count(*)                                        as n,
       count(*) filter (where c.ground_truth = 'true') as hits
from claims c
join claim_topics ct on ct.claim_id = c.id
join topic_nodes tn  on tn.id = ct.topic_node_id
cross join lateral (
    -- every ancestor prefix of the node path, inclusive
    select subpath(tn.path, 0, g.i) as ancestor
    from generate_series(1, nlevel(tn.path)) as g(i)
) anc
where c.status = 'resolved'
group by c.source_id, anc.ancestor;

-- NOTE on scoring: the hierarchical back-off (prior at a node = parent's
-- posterior mean, PARENT_STRENGTH pseudo-counts) is applied top-down by the job
-- using the logic in scripts/test_a.py, then upserted into `scores`. A pure-SQL
-- recursive CTE is possible but deferred for v1.

-- ------------------------------------------------------------------- RLS
-- For the browser plugin: enable RLS and expose read-only on `scores`/`sources`
-- via the anon key; writes happen through the backend with the service role.
-- (Left disabled here; enable before exposing the anon key.)
-- alter table scores enable row level security;
-- create policy scores_read on scores for select to anon using (true);
