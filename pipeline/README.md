# Inference pipeline

Scaffold against the `db/` schema. **Nothing here has been executed**; deps are not
installed and no API calls were made.

## Modules
- `config.py` — models, web-search tool def, scoring constants, thresholds, env.
- `schemas.py` — extraction contract (`ExtractionResult`) + resolution contract
  (`RECORD_VERDICT_TOOL`, `Verdict`).
- `extract.py` — `extract_claims(client, tweet_text, taxonomy_slugs)`; `messages.parse`
  structured output on `claude-sonnet-5-5`. Tweet text + taxonomy only (user pt 2).
- `resolve.py` — `resolve_claim(...)`; `claude-opus-5-5` + `web_search_20260209` loop,
  strict `record_verdict` tool, **evidence URLs validated against actual search
  results**, time-scoped against lookahead (user pt 4).
- `db.py` — psycopg reads/writes (sources, claims, claim_topics, resolutions, taxonomy).
  `canonicalize_topic` is a **stub** pending an embedder.
- `score_job.py` — Beta-Binomial back-off (same math as `scripts/test_a.py`) over the
  `raw_score_counts` view → upserts `scores`. No LLM.

## Two flows
**Seeding (pt 2/3)** — initial ledgers for the ~20 known authors, from the flat
dataset. No web resolution: `ground_truth` already exists in the data.
`tweet_text → extract_claims → map topic → insert_claim(ground_truth=…) → score_job`.

**Live new author (pt 4)** — pull ~1yr of tweets, then:
`extract_claims → canonicalize_topic → insert_claim(open) → resolve_claim → insert_resolution → score_job`.
Open claims (future / undeterminable) stay `open` and are not scored until resolved.

**Answering "judge author X"** — read `scores` for the source + relevant subtree and
narrate. The LLM interprets precomputed numbers; it does not compute them (prevents
hallucinated scores).

## Not built yet / decisions
- **Embedder** — blocks `canonicalize_topic` (live new-node dedup). No Claude
  embeddings endpoint; pick Voyage/OpenAI/etc., set `EMBED_MODEL` + the
  `topic_nodes.embedding` dim in `db/001_schema.sql`.
- **Orchestrators** — `run_seed.py` and `run_new_author.py` not written yet.
- **Answer/read path** — not written yet.
- **Source of tweets** — "simulate browsing X" = feed stored tweet text; real X API
  is rate-limited/paid and not wired.

## Deps (when you run it)
`pip install anthropic pydantic "psycopg[binary]" scipy numpy` + `ANTHROPIC_API_KEY`
(or `ant auth login`) + `SUPABASE_DB_URL`.

## Cost/rigor notes
- Extraction is the volume driver — batch, cache the taxonomy prefix, cheap model.
- Resolution spends web-search + Opus tokens per claim — only run on claims whose
  `resolves_on` has passed.
- Lookahead/leakage guard lives in `resolve.py` (today's date, no author-as-evidence).
- A claim that can't be verified becomes `unresolvable`, never a guess.
