# Epistemic Ledger — browser extension (Chrome, MV3)

Shows an X author's topic-level reliability (from their resolved track record) as a
floating panel on their profile, plus a popup for manual lookups.

## How to open it

1. **Start the read API** (serves the ledger from Supabase):
   ```bash
   cd ../pipeline && ../.venv/bin/python api.py
   ```
   It listens on `http://localhost:8000`. Leave it running.

2. **Load the extension in Chrome:**
   - Open `chrome://extensions`
   - Toggle **Developer mode** ON (top-right)
   - Click **Load unpacked** → select this `extension/` folder
   - (Optional) pin it: click the puzzle-piece icon in the toolbar → pin **Epistemic Ledger**

3. **Use it — three ways:**
   - **From any tweet (live analysis):** on the home feed, a reply, search, or
     anywhere a tweet shows, a small **reliability chip** (e.g. `● 72%`) appears
     next to the author's name. **Click it to analyze *that* tweet** — the backend
     LLM classifies the tweet's claim(s) into the taxonomy and shows the author's
     record in that exact subfield (plus the full breakdown). Each click runs one
     small LLM call. No need to visit their profile.
   - **On a profile or single tweet:** open `https://x.com/chamath` (or any author
     page / `.../status/...`) and the full panel shows automatically, bottom-right.
   - **Manual lookup:** click the extension icon and type a handle.

Chips only appear for authors that have a ledger (currently `@chamath`,
`@cathiedwood`, and the simulated seed authors) — so the timeline stays uncluttered.

## Cold authors (build on the fly)

Open the profile of an author with no ledger and the panel shows a **Build ledger**
button. Clicking it runs the full pipeline on the backend (pull ~6 months of posts →
extract claims → check outcomes → score) as an async job; the panel polls and shows
progress, then renders the ledger when done. This takes a few minutes and spends real
X + Anthropic credits.

Server controls (env on `api.py`):
- `EPI_ALLOW_BUILD=0` — disable building (set this for the **public 15-day demo** so
  visitors can't trigger paid builds). Default `1`.
- `EPI_BUILD_MONTHS` (default 6), `EPI_BUILD_MAX_CLAIMS` (default 60, caps resolve
  spend), `EPI_MAX_BUILDS` (default 2, concurrent build cap).

## Pointing at a hosted API

Click the extension icon → set **API URL** to your deployed read API (e.g. the
15-day demo endpoint) → Save. Default is `http://localhost:8000`.

## Notes

- The background service worker does the fetch; content scripts on x.com can't
  reach localhost directly (page CSP), so the worker is required.
- The panel reports a *track record*, not a verdict on any single post.
- Only authors present in the `scores` table return data (currently `@chamath`,
  `@cathiedwood`, and the simulated seed authors).
