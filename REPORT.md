# Epistemic Ledger

**A track-record reliability layer for X.** For any author, it computes topic-level
reliability from their *resolved* past claims — how often they've been right, on
what, with citations — and surfaces it inline while you browse.

---

## 1. Why this project

Social media rewards confidence, not accuracy. The loudest and most certain voices
are indistinguishable, at a glance, from the ones with a real record — and that gap
is exactly what bad actors exploit. New or casual users feel it most: the feed is
intimidating, and there's no cheap signal for who to trust on what.

The usual responses are moderation and labels, which fight over *what is true*. This
project takes a different line: don't adjudicate individual posts — **show the
author's track record.** Surface who has been right over time, per topic, with
receipts, and let the reader weight accordingly. The aim is to make browsing safer,
less intimidating, and more enriching without censoring anyone.

My motivation is personal. I want social media to be a place you can learn from,
not one you have to defend yourself against. Today the feed rewards whoever sounds
most certain, which makes it easy for bad actors to mislead and makes the whole
experience intimidating — especially for people who are new or just trying to make
sense of a topic. A visible, per-topic track record changes the question from "who
sounds right" to "who has actually been right," so you can browse with context
instead of taking confidence at face value. I want that to make social media safer,
less intimidating, and genuinely more enriching to spend time on.

## 2. What it does

Pick an author → get their topic-level reliability, e.g.:

```
@chamath — 38 resolved claims
  finance     72% reliable  (90% CI 60–84%, n=25)
  politics    76% reliable  (90% CI 56–91%, n=5)
  technology  76% reliable  (90% CI 56–91%, n=5)
```

Each number is backed by past claims that were resolved against real-world outcomes
with cited evidence. A Chrome extension renders this on the author's X profile. It
reports a **track record, not a verdict on any single post.**

## 3. Architecture & design decisions

**Pipeline (offline, per author):**

```
X full-archive search → extract claims → resolve → score → scores table → read API → extension
     (ingest)           (LLM, taxonomy)  (LLM+web)  (Beta-Binomial)      (JSON)
```

1. **Ingest** — pull the author's posts for a window (e.g. 6 months) via X API v2
   full-archive search.
2. **Extract** — an LLM pulls *falsifiable* claims from each post and classifies
   them into a topic taxonomy. It does **not** judge truth here.
3. **Resolve** — a second LLM researches each claim with web search and records a
   verdict (`true` / `false` / `unresolvable`) with evidence URLs.
4. **Score** — a deterministic job turns resolved verdicts into per-(author, topic)
   reliability with credible intervals.
5. **Serve** — scores are precomputed into a table; a read API and the extension
   only *read* them.

**Design decisions and why:**

- **The LLM never does the score arithmetic.** Scores come from a deterministic
  statistical job. This keeps the numbers auditable and reproducible, and isolates
  model error to extraction/resolution where it can be checked against citations.
- **Extraction and resolution are separate jobs.** They have different inputs,
  costs, and failure modes — a cheap bulk classification pass vs. an expensive
  judgment pass. Splitting them keeps each simple and independently testable.
- **Retrieval + citation validation (anti-hallucination).** Every evidence URL a
  verdict cites must actually appear in the web-search results; otherwise the
  verdict is downgraded to `unresolvable`. The resolver is also told the current
  date and the claim's window, and returns `unresolvable` for anything not yet
  determinable — guarding against lookahead and fabricated sources.
- **Hierarchical taxonomy + Beta-Binomial back-off.** Topics form a tree
  (`finance > us_equities > earnings`). A thin leaf (few resolved claims) borrows
  strength from its parent via a shrinkage prior, instead of over-claiming from
  n=2. Credible intervals widen honestly when data is thin.
- **Precomputed, read-only hot path.** The extension triggers no LLM calls and no
  web searches at request time — it reads a scores table. Cheap and fast per view.
- **Supabase / Postgres as the store**, using `ltree` for topic paths and
  `pgvector` (reserved) for future taxonomy canonicalization. New topics the model
  proposes enter as `provisional` nodes, so the taxonomy can grow with the data.

**Validation.** The scorer was first validated on a **simulated** dataset (generated
via Adaption) where ground-truth labels are planted: the back-off and interval
coverage behave as designed. Live authors were then run end-to-end. Current live
ledgers: **Cathie Wood** (125 posts → 70 claims → 37 resolved; finance 80% n=18,
economics 63% n=16) and **Chamath Palihapitiya** (765 posts → 115 claims → 38
resolved; finance 72% n=25). The resolver cites primary sources (e.g. SEC filings,
Eurostat, company IR) and refuses to guess — roughly half of extracted claims come
back `unresolvable`, which is correct conservative behavior (see Limitations).

## 4. Running it locally & publishing securely

**Run locally** (full detail in `README.md`):

1. `cp .env.example .env` and fill in `SUPABASE_DB_URL`, `ANTHROPIC_API_KEY`,
   `X_BEARER_TOKEN`.
2. Create a Supabase project; apply `db/001_schema.sql` and `db/002_seed_taxonomy.sql`.
3. Build a ledger: `python run_new_author.py estimate|ingest|extract|resolve|score
   --handle <handle> --months 6` (each stage is gated; nothing paid runs implicitly).
4. Serve: `python api.py`, then load `extension/` unpacked in Chrome.

**Before publishing to GitHub** (enforced by `.gitignore`): remove `.env` (DB
password, Anthropic + X keys), `ingest/` (raw tweets — X ToS restricts
redistribution), `data_flat/` (Adaption-generated data — confirm licensing before
sharing), `eval_runs/`, and `*.log`. Ship `.env.example` instead.

**15-day shared access — two tracks, both key-safe:**

- **Read-only live demo (on my data).** Host `api.py` (or Supabase PostgREST)
  against a **read-only Postgres role** that can only `SELECT scores/sources`, or
  Supabase RLS + the publishable anon key. Judges see real ledgers; no one can
  trigger paid writes; no secret key ever ships. After 15 days: revoke the role /
  drop the RLS policy / pause the project.
- **Repo, bring-your-own-keys.** Anyone cloning the public repo runs the full
  pipeline with **their own** Anthropic / X / Supabase credentials. My keys never
  leave my machine.

## 5. Limitations & running at scale

**Current limitations:**

- **Extraction precision.** The extractor over-includes opinion and open-ended
  future statements, so ~50–66% of claims resolve as `unresolvable`. This is safe
  (we never fabricate a verdict) but thins the ledger; a stricter falsifiability
  filter would help.
- **Resolution is bounded by the open web.** Niche or very recent claims may have no
  authoritative source and stay unresolvable.
- **Synthetic labels are not a gold accuracy standard.** The Adaption data validates
  the *plumbing*; true resolution accuracy needs human-checked outcomes.
- **Taxonomy canonicalization is string-based for now** (no embedder wired in), so
  near-duplicate topic nodes can appear. `pgvector` columns are in place for this.
- **Identity.** Handle matching is exact; impersonators / renames aren't handled.
- **Cold start.** A brand-new author needs a full ingest→resolve pass before the
  extension shows anything.

**At millions of concurrent users** (the read path is trivial to scale; the write
path is the cost):

- **Split read vs write.** Serve `scores` from a cache/CDN (e.g. Redis + edge
  caching) — reads are small, static between recomputes.
- **Asynchronous resolution.** Move ingest/extract/resolve to a queue with workers
  and the LLM **batch** API; resolution is the latency and dollar bottleneck.
- **Stream ingestion.** A log/stream (e.g. Kafka) for incoming posts; object storage
  for raw text; the relational store holds only claims/verdicts/scores.
- **Scale the store.** Partition by author; for very large footprints, a distributed
  Postgres (Citus / CockroachDB / Cloud Spanner). A dedicated vector store
  (pgvector at first, Pinecone / Milvus if it outgrows it) for taxonomy matching.
- **Cost + compliance.** X API and LLM spend dominate; dedupe, cache verdicts, and
  resolve on a schedule rather than on demand.

## 6. APIs & tools used

| Tool | Role |
|---|---|
| **Anthropic Claude API** (Sonnet 5.5) | Claim extraction (structured output) and resolution (with the `web_search` server tool + citations) |
| **X API v2** (pay-per-use) | Full-archive post search for author history |
| **Supabase / Postgres 17** | Ledger store: `ltree` (topic tree), `pgvector` (taxonomy), `pgcrypto` |
| **Adaption** | Used to generate the small simulated tweet/claim dataset that validated the scorer before running on live data. |
| **Python** | `psycopg3` (Postgres), `pydantic` (LLM I/O schemas), `scipy` (Beta-Binomial intervals), `pandas`/`pyarrow` (seed data) |
| **Chrome MV3 extension** | Reads the ledger via a background service worker and renders it on X |

---

*Status: scorer validated on simulated data; live ledgers built for @CathieDWood and
@chamath; read API + Chrome extension working against Supabase. Resolution measured
at ~$0.08–0.11 per claim.*
