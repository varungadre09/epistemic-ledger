const out = document.getElementById("out");
const pct = (x) => Math.round(x * 100) + "%";

async function init() {
  const { apiBase } = await chrome.storage.local.get("apiBase");
  document.getElementById("api").value = apiBase || "http://localhost:8000";
}
init();

document.getElementById("save").onclick = async () => {
  const v = document.getElementById("api").value.trim().replace(/\/$/, "");
  await chrome.storage.local.set({ apiBase: v });
  out.textContent = "Saved API URL.";
};

function lookup(handle) {
  return new Promise((resolve) =>
    chrome.runtime.sendMessage({ type: "lookup", handle }, resolve));
}

async function run() {
  let h = document.getElementById("handle").value.trim();
  if (!h) return;
  if (!h.startsWith("@")) h = "@" + h;
  out.textContent = "Looking up " + h + "…";
  const resp = await lookup(h);
  if (!resp || !resp.ok) { out.textContent = "API unreachable. Check the API URL below."; return; }
  const d = resp.data;
  if (!d || !d.found) { out.textContent = "No ledger for " + h + "."; return; }
  out.innerHTML = "";
  const meta = document.createElement("div");
  meta.className = "muted";
  meta.style.marginBottom = "8px";
  meta.textContent = d.handle + " · " + d.n_resolved_claims + " resolved claims";
  out.append(meta);
  (d.domains || []).slice().sort((a, b) => b.n - a.n).forEach((t) => {
    const item = document.createElement("div"); item.className = "item";
    const top = document.createElement("div"); top.className = "top";
    const name = document.createElement("span"); name.textContent = t.topic;
    const val = document.createElement("b"); val.textContent = pct(t.shrunk_mean);
    top.append(name, val);
    const ci = document.createElement("div"); ci.className = "ci";
    ci.textContent = "90% CI " + pct(t.ci_lo) + "–" + pct(t.ci_hi) + " · n=" + t.n;
    item.append(top, ci); out.append(item);
  });
}

document.getElementById("go").onclick = run;
document.getElementById("handle").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
