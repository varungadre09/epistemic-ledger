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

**The model:** judges/users install **only the extension**; it talks to **one API you
host**, which holds all the keys server-side. They need no Supabase and no API tokens.

**1. Host the API** (stable URL — needed so it can be baked into the extension). Deploy
this repo to Render / Railway / Fly; it includes `requirements.txt` and a `Procfile`
(`web: HOST=0.0.0.0 python pipeline/api.py`). Set these as the host's env vars (in its
secret store, never in the repo):
- `SUPABASE_DB_URL` → the **read-only role** from `db/003_readonly_role.sql`
- `ANTHROPIC_API_KEY` → your key (used only by `/analyze`)
- `EPI_ALLOW_BUILD=0` → disable paid cold-builds on the public demo

With build off, every exposed endpoint is read-only on the DB. You get a stable URL
like `https://epistemic-ledger.onrender.com`.

*(Quick alternative, less stable:* run `api.py` locally and expose it with
`cloudflared tunnel --url http://localhost:8000` — but the URL changes each run, so the
hosted option is better for a baked-in demo.)*

**2. Bake the URL into the extension.** In `extension/background.js`, set
`DEFAULT_API` to your hosted URL, then commit and push. Now the repo's extension points
at your backend by default.

**3. Judges just install the extension** — `chrome://extensions` → Load unpacked →
`extension/` — and browse X. Zero config, no keys, no database. (They can still
override the API URL in the popup.)

**After the 15 days:** drop the read-only role (`db/003` has the command) and/or stop
the host; the shared link goes dead and nothing of yours was ever exposed.

**Build controls** (env on `api.py`): `EPI_ALLOW_BUILD` (default 1),
`EPI_BUILD_MONTHS` (6), `EPI_BUILD_MAX_CLAIMS` (60, caps resolve spend),
`EPI_MAX_BUILDS` (2).

## Running the full pipeline yourself

Anyone cloning this repo runs it with **their own** keys (`.env`). The full pipeline
(ingest/extract/resolve) spends real X + Anthropic credits; `estimate` first to see
volume and cost.
