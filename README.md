# Epistemic Ledger

A track-record reliability layer for X. For any author it computes **topic-level
reliability** from their *resolved* past claims — how often they've been right, on
what, with citations — and surfaces it in the browser. See **[REPORT.md](REPORT.md)**
for the full writeup (motivation, design decisions, limitations, scale).

## How it works

```
X full-archive search → extract claims → resolve (web + cite) → score → scores table → read API → extension
     (ingest)           (LLM, taxonomy)   (LLM, verdict)        (Beta-Binomial)
```

The LLM never does the score arithmetic; scores are a deterministic Beta-Binomial
back-off over a topic tree, written to a table the API only reads.

## Repo layout

| Path | What |
|---|---|
| `db/` | Postgres schema (`001`), seed taxonomy (`002`), read-only demo role (`003`) |
| `pipeline/` | the pipeline: `run_new_author.py` (ingest→extract→resolve→score), `answer.py`/`analyze.py` (read + live tweet analysis), `api.py` (HTTP read API) |
| `extension/` | Chrome MV3 extension (chips on tweets, live analyze, cold-author build) |
| `scripts/` | data adapter + reference scorer used to validate on simulated data |
| `REPORT.md` / `REPORT.pdf` | project report |

## Run it locally

1. **Install** (Python 3.10+):
   ```bash
   python3 -m venv .venv && . .venv/bin/activate
   pip install anthropic psycopg[binary] pydantic scipy pandas pyarrow markdown xhtml2pdf
   ```
2. **Configure**: `cp .env.example .env` and fill in `SUPABASE_DB_URL`,
   `ANTHROPIC_API_KEY`, `X_BEARER_TOKEN` (see `.env.example` for the format).
3. **Create the DB**: in your Supabase project's SQL editor, run `db/001_schema.sql`
   then `db/002_seed_taxonomy.sql`.
4. **Build a ledger** for an author (each stage is gated; nothing paid runs implicitly):
   ```bash
   cd pipeline
   python run_new_author.py estimate --handle chamath --months 6   # volume + cost, cheap
   python run_new_author.py run      --handle chamath --months 6 --yes --max-claims 60
   ```
5. **Serve + use**: `python api.py` (listens on `http://localhost:8000`), then load
   `extension/` unpacked in Chrome (see `extension/README.md`).

## Using the API in a browser

**Directly** — the read endpoints return JSON; open in any browser:
- `GET /health` — liveness + whether building is enabled
- `GET /authors` — authors that have a ledger
- `GET /author?handle=@chamath` — an author's full ledger (topics, CIs, window)
- `GET /status?handle=@x` — ledger/build state for an author
- `POST /analyze {handle, text}` — classify a specific tweet, return the author's record in that subfield
- `POST /build {handle}` — build a cold author's ledger (async; gated/capped)

**With the extension** — load `extension/` unpacked, click the extension icon, set
**API URL** to your API base (default `http://localhost:8000`), then browse X: a
reliability chip appears on tweets by tracked authors (click to analyze that tweet),
and a cold author's profile shows a **Build ledger** button.

## Sharing a demo (and which links are safe)

**Safe to share:** the GitHub repo URL, and your deployed **API base URL** — the read
endpoints above expose only public reliability data.

**Never share / never commit:** `SUPABASE_DB_URL` (DB password), `ANTHROPIC_API_KEY`,
`X_BEARER_TOKEN`, or the Supabase service-role key. These live only in `.env`
(git-ignored) or your host's secret store.

**Recommended posture for a public / 15-day demo:**
- Set `EPI_ALLOW_BUILD=0` so visitors can't trigger paid builds.
- Point `SUPABASE_DB_URL` at the **read-only role** in `db/003_readonly_role.sql`.
  With build off, every exposed endpoint is read-only on the DB (`/analyze` only
  *reads* scores — it spends a small Anthropic amount per call but writes nothing).
- Expose it with a tunnel (fastest) while `api.py` runs on your machine:
  ```bash
  cloudflared tunnel --url http://localhost:8000     # or: ngrok http 8000
  ```
  Share the HTTPS URL it prints; your keys stay on your machine. Or deploy `api.py`
  to a host (Render/Railway/Fly) with the env vars set in the host's secret store.

**Build controls** (env on `api.py`): `EPI_ALLOW_BUILD` (default 1),
`EPI_BUILD_MONTHS` (6), `EPI_BUILD_MAX_CLAIMS` (60, caps resolve spend),
`EPI_MAX_BUILDS` (2).

## Running the full pipeline yourself

Anyone cloning this repo runs it with **their own** keys (`.env`). The full pipeline
(ingest/extract/resolve) spends real X + Anthropic credits; `estimate` first to see
volume and cost.
