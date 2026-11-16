# Supabase schema — truth ledger

## Files
- `001_schema.sql` — core tables, ltree + pgvector, resolution trigger, scores table, raw-counts view.
- `002_seed_taxonomy.sql` — starter canonical taxonomy (finance / economics / politics).

## Apply
I cannot provision your Supabase account. Create the project yourself, then either:

1. **SQL editor**: paste `001_schema.sql`, run; paste `002_seed_taxonomy.sql`, run.
2. **CLI**: `supabase db push` (put the files under `supabase/migrations/`), or
   `psql "$SUPABASE_DB_URL" -f 001_schema.sql -f 002_seed_taxonomy.sql`.

Extensions used (`ltree`, `vector`, `pgcrypto`) are available on Supabase.

## Model (what each table is for)
- `sources` — authors; canonical `handle` + `aliases[]` (collapses casing/spelling dupes).
- `topic_nodes` — the taxonomy registry the LLM consults. `path` (ltree) for hierarchy,
  `embedding` for canonicalizing proposed new nodes, `status` canonical|provisional.
- `claims` — one extracted claim/prediction; `status` open|resolved|unresolvable,
  `ground_truth` set only when resolved (enforced by CHECK).
- `claim_topics` — N:M claim↔topic (supports multi-topic claims; one `is_primary`).
- `resolutions` — provenance of every verdict (`method`: dataset_ground_truth |
  model_parametric | web_retrieval | human; `evidence_ref`; `resolver`). A trigger
  syncs `claims.status`/`ground_truth`.
- `scores` — precomputed per (source, topic) reliability (shrunk mean + 90% CI).
  The API/plugin reads this; it is never computed per request.

## Scoring
`raw_score_counts` view gives exact (source, node) resolved counts (a node aggregates
its descendants via ltree ancestors). The hierarchical Beta-Binomial back-off
(PARENT_STRENGTH=10, 90% CI) is applied top-down by a job reusing
`../scripts/test_a.py` logic, then upserted into `scores`. Pure-SQL recursive version
deferred.

## Open decisions (before pipeline)
- **Embedder**: no Claude embeddings endpoint — pick Voyage/OpenAI/etc. and set
  `topic_nodes.embedding` dimension in `001_schema.sql` to match (placeholder 1536).
- **Resolution strategy for new authors** (pt 4): parametric-only vs retrieval-backed
  (cite evidence, else `unresolvable`) vs curated outcomes table. Drives the pipeline,
  not the schema.
- **Score target**: hit-rate (current) vs calibration of `stated_confidence` (Brier) vs both.

## Not done / caveats
- RLS is defined but left disabled (commented) — enable read-only on `scores`/`sources`
  before exposing the anon key to the extension.
- Embedding index (HNSW) commented out — build after seeding embeddings.
- `edge_flag_vocab` is a reference table; array membership not yet enforced on
  `claims.edge_flags` (enforce in app or add a constraint trigger).
