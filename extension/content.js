// Epistemic Ledger content script.
//  - CHIP on every tweet by a tracked author; click = analyze THAT tweet live.
//  - On a profile, auto-show the ledger; for a COLD author (no ledger) show a
//    "Build ledger" button that runs the full pipeline async with progress.
// All network calls go through the background service worker (x.com CSP).

const RESERVED = new Set([
  "home", "explore", "notifications", "messages", "i", "settings", "search",
  "compose", "hashtag", "tos", "privacy", "login", "signup", "about"
]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------- messaging
const cache = new Map();
function send(msg) { return new Promise((res) => chrome.runtime.sendMessage(msg, res)); }

async function lookup(handle, force) {
  const key = handle.toLowerCase().replace(/^@/, "");
  if (force) cache.delete(key);
  if (cache.has(key)) return cache.get(key);
  const r = await send({ type: "lookup", handle: "@" + key });
  const d = r && r.ok && r.data && r.data.found ? r.data : null;
  cache.set(key, d);
  return d;
}
async function analyze(handle, text) {
  const r = await send({ type: "analyze", handle: "@" + handle.toLowerCase().replace(/^@/, ""), text });
  return r && r.ok ? r.data : { found: false, error: (r && r.error) || "unreachable" };
}
async function buildReq(handle) {
  const r = await send({ type: "build", handle: "@" + handle.toLowerCase().replace(/^@/, "") });
  return r && r.ok ? r.data : { state: "error", error: (r && r.error) || "unreachable" };
}
async function statusReq(handle) {
  const r = await send({ type: "status", handle: "@" + handle.toLowerCase().replace(/^@/, "") });
  return r && r.ok ? r.data : { state: "error", error: (r && r.error) || "unreachable" };
}

// ------------------------------------------------------------- helpers
const pct = (x) => Math.round(x * 100) + "%";
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function relColor(x) { const h = Math.round(8 + 122 * Math.max(0, Math.min(1, x))); return `hsl(${h}, 62%, 72%)`; }
function relColorDeep(x) { const h = Math.round(8 + 122 * Math.max(0, Math.min(1, x))); return `hsl(${h}, 55%, 45%)`; }
const MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function ym(s) { if (!s) return ""; const p = s.split("-"); return MON[+p[1]] + " " + p[0]; }
function windowStr(w) { return w && w.from ? ym(w.from) + " – " + ym(w.to) : "last 6 months"; }
const topic = (t) => (t || "").split(">").slice(-1)[0].replace(/_/g, " ");
const STEP = { ingest: "Pulling posts from X…", extract: "Extracting claims…",
  resolve: "Checking outcomes on the web…", score: "Scoring…" };

function ring(x) {
  const r = 26, c = 2 * Math.PI * r, off = c * (1 - x);
  return `<svg width="68" height="68" viewBox="0 0 68 68">
    <circle cx="34" cy="34" r="${r}" fill="none" stroke="#efeaf6" stroke-width="8"/>
    <circle cx="34" cy="34" r="${r}" fill="none" stroke="${relColor(x)}" stroke-width="8"
      stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"
      transform="rotate(-90 34 34)"/>
    <text x="34" y="39" text-anchor="middle" font-size="17" font-weight="800"
      fill="${relColorDeep(x)}">${pct(x)}</text></svg>`;
}
function bars(domains) {
  return (domains || []).slice().sort((a, b) => b.n - a.n).map((t) => `
    <div class="epi-item"><div class="epi-row">
        <span class="epi-topic">${esc(topic(t.topic))}</span>
        <span class="epi-bar"><span class="epi-fill" style="width:${pct(t.shrunk_mean)};background:${relColor(t.shrunk_mean)}"></span></span>
        <span class="epi-val">${pct(t.shrunk_mean)}</span>
      </div><div class="epi-ci">90% CI ${pct(t.ci_lo)}–${pct(t.ci_hi)} · n=${t.n}${t.n < 5 ? " · thin" : ""}</div>
    </div>`).join("");
}
function legend(w) {
  return `<div class="epi-legend">
    <div><b>Resolved</b> past claims we could check against real outcomes.</div>
    <div><b>n</b> how many resolved claims back the number.</div>
    <div><b>90% CI</b> the range the true rate most likely sits in — wider = less certain.</div>
    <div><b>Window</b> claims are from ${esc(windowStr(w))}.</div></div>`;
}
function headHTML(handle) {
  return `<div class="epi-head"><span class="epi-brand"><span class="epi-dot"></span>Epistemic Ledger</span>
      <span class="epi-close">×</span></div>
    <div class="epi-who">@${esc(handle.replace(/^@/, ""))}</div>`;
}

// ------------------------------------------------------------- panels
function removePanel() { const p = document.getElementById("epi-panel"); if (p) p.remove(); }
function mountPanel(html, handle) {
  removePanel();
  const p = document.createElement("div");
  p.id = "epi-panel"; p.innerHTML = html;
  document.body.append(p);
  p.querySelector(".epi-close").onclick = removePanel;
  const b = p.querySelector(".epi-build-btn");
  if (b) b.onclick = () => runBuild(handle);
}

function ledgerHTML(handle, d) {
  const meta = `<div class="epi-meta">${d.n_resolved_claims} resolved claims · ${esc(windowStr(d.window))}</div>`;
  let tweetSec = "";
  if (d.tweet_claims) {
    if (!d.tweet_claims.length || d.no_claim) {
      tweetSec = `<div class="epi-tweet"><div class="epi-section">This tweet</div>
        <div class="epi-muted">No falsifiable claim found in this post.</div></div>`;
    } else {
      tweetSec = `<div class="epi-tweet"><div class="epi-section">This tweet</div>` +
        d.tweet_claims.map((c) => c.shrunk_mean == null
          ? `<div class="epi-claim"><div class="epi-claimtxt">“${esc(c.text)}”</div>
               <div class="epi-ci">Classified: ${esc(c.classified || "—")} · no track record in this topic yet</div></div>`
          : `<div class="epi-claim"><div class="epi-claimrow">
               <div class="epi-ringwrap">${ring(c.shrunk_mean)}</div>
               <div class="epi-claimbody">
                 <div class="epi-claimtxt">“${esc(c.text)}”</div>
                 <div class="epi-claimtopic">${esc(topic(c.matched_topic))}${c.backed_off ? " <span class='epi-tag'>nearest tracked</span>" : ""}</div>
                 <div class="epi-ci">90% CI ${pct(c.ci_lo)}–${pct(c.ci_hi)} · n=${c.n}</div>
               </div></div></div>`).join("") + `</div>`;
    }
  }
  return headHTML(handle) + meta + tweetSec +
    `<div class="epi-section">Track record by topic</div>${bars(d.domains)}` +
    legend(d.window) + `<div class="epi-foot">A track record, not a verdict on any single post.</div>`;
}

function coldHTML(handle) {
  return headHTML(handle) + `<div class="epi-build">
      <div class="epi-muted">No ledger for this author yet.</div>
      <button class="epi-build-btn">Build ledger</button>
      <div class="epi-ci" style="margin-top:7px">Pulls ~6 months of posts, extracts claims, and
      checks their outcomes. Takes a few minutes and runs live LLM calls.</div></div>`;
}
function buildingHTML(handle, label) {
  return headHTML(handle) + `<div class="epi-build">
      <div class="epi-spinner"></div>
      <div class="epi-section" style="margin-top:10px">Building ledger</div>
      <div class="epi-muted">${esc(label)}</div>
      <div class="epi-ci" style="margin-top:6px">This can take several minutes — you can keep browsing.</div></div>`;
}
function noteHTML(handle, msg) {
  return headHTML(handle) + `<div class="epi-build"><div class="epi-muted">${esc(msg)}</div></div>`;
}

// ------------------------------------------------------------- build flow
async function runBuild(handle) {
  mountPanel(buildingHTML(handle, STEP.ingest), handle);
  const r = await buildReq(handle);
  if (r.state === "disabled") return mountPanel(noteHTML(handle, "Building new authors is turned off on this demo."), handle);
  if (r.state === "busy") return mountPanel(noteHTML(handle, "Build queue is full right now — try again shortly."), handle);
  if (r.state === "error") return mountPanel(noteHTML(handle, "Couldn't start: " + r.error), handle);
  if (r.state === "ready") { const d = await lookup(handle, true); return mountPanel(ledgerHTML(handle, d), handle); }
  // building -> poll
  for (let i = 0; i < 240; i++) {               // ~20 min at 5s
    await sleep(5000);
    const s = await statusReq(handle);
    if (s.state === "building") mountPanel(buildingHTML(handle, STEP[s.step] || "Working…"), handle);
    else if (s.state === "ready") { const d = await lookup(handle, true); return mountPanel(ledgerHTML(handle, d), handle); }
    else if (s.state === "empty") return mountPanel(noteHTML(handle, "Built, but no resolvable claims were found for this author."), handle);
    else if (s.state === "error") return mountPanel(noteHTML(handle, "Build failed: " + (s.error || "unknown")), handle);
  }
  mountPanel(noteHTML(handle, "Still building — check back in a bit."), handle);
}

// ------------------------------------------------------------- chips
function handleFromTweet(article) {
  const nb = article.querySelector('[data-testid="User-Name"]');
  if (!nb) return null;
  const link = nb.querySelector('a[href^="/"]');
  if (!link) return null;
  const seg = link.getAttribute("href").split("/").filter(Boolean);
  if (seg.length !== 1) return null;
  const h = seg[0];
  return RESERVED.has(h.toLowerCase()) ? null : h;
}
function tweetText(article) {
  const t = article.querySelector('[data-testid="tweetText"]');
  return t ? t.innerText : "";
}
async function annotate(article) {
  if (article.dataset.epiDone) return;
  article.dataset.epiDone = "1";
  const handle = handleFromTweet(article);
  if (!handle) return;
  const d = await lookup(handle);
  if (!d) return;
  if (article.querySelector(".epi-chip")) return;
  const dom = (d.domains || []).slice().sort((a, b) => b.n - a.n)[0];
  const chip = document.createElement("button");
  chip.type = "button"; chip.className = "epi-chip";
  chip.style.setProperty("--epi-c", relColor(dom ? dom.shrunk_mean : 0.5));
  chip.innerHTML = `<span class="epi-chip-dot"></span>${dom ? pct(dom.shrunk_mean) : "ledger"}`;
  chip.title = "Epistemic Ledger — click to analyze this tweet";
  chip.addEventListener("click", async (e) => {
    e.preventDefault(); e.stopPropagation();
    mountPanel(ledgerHTML(handle, { ...d, tweet_claims: undefined }), handle); // instant breakdown
    mountPanel(headHTML(handle) + `<div class="epi-build"><div class="epi-spinner"></div>
      <div class="epi-muted" style="margin-top:8px">Analyzing this tweet…</div></div>`, handle);
    const text = tweetText(article);
    const res = text ? await analyze(handle, text) : d;
    mountPanel(res && res.found ? ledgerHTML(handle, res) : ledgerHTML(handle, d), handle);
  });
  (article.querySelector('[data-testid="User-Name"]') || article).append(chip);
}
function scan(root) { (root || document).querySelectorAll('article[data-testid="tweet"]').forEach(annotate); }
let timer = null;
function schedule() { if (timer) return; timer = setTimeout(() => { timer = null; scan(); }, 400); }
new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });

// ------------------------------- auto panel on profile / single-tweet pages
function currentHandle() {
  const seg = location.pathname.split("/").filter(Boolean);
  if (seg.length >= 1 && !RESERVED.has(seg[0].toLowerCase())) return seg[0];
  return null;
}
let lastHandle = null;
async function autoPanel() {
  const h = currentHandle();
  if (!h) {
    const panel = document.getElementById("epi-panel");
    if (panel && !panel.querySelector(".epi-tweet") && !panel.querySelector(".epi-build")) removePanel();
    lastHandle = null; return;
  }
  if (h === lastHandle) return;
  lastHandle = h;
  const d = await lookup(h);
  if (currentHandle() !== h) return;
  mountPanel(d ? ledgerHTML(h, d) : coldHTML(h), h);   // cold -> build CTA
}
setInterval(() => { autoPanel(); schedule(); }, 1500);
autoPanel(); scan();
