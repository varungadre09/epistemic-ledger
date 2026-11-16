// Service worker: all cross-origin fetches to the read API happen here.
// Content scripts on x.com can't fetch the API directly (page CSP), so they
// message this worker.
//
// === BEFORE SHARING: set this to your HOSTED public API URL ===
// Then anyone who loads the extension uses your backend with zero setup — no
// Supabase, no API keys on their side. Leave it as localhost for local dev.
// (Users can still override it in the extension popup.)
const DEFAULT_API = "https://epistemic-ledger.onrender.com";   // hosted read-only demo backend

async function apiBase() {
  try {
    const { apiBase } = await chrome.storage.local.get("apiBase");
    return apiBase || DEFAULT_API;
  } catch (e) {
    return DEFAULT_API;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg) return;

  if (msg.type === "lookup") {
    apiBase()
      .then((b) => fetch(b + "/author?handle=" + encodeURIComponent(msg.handle)))
      .then((r) => r.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }

  if (msg.type === "analyze") {
    apiBase()
      .then((b) => fetch(b + "/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle: msg.handle, text: msg.text }),
      }))
      .then((r) => r.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }

  if (msg.type === "build") {
    apiBase()
      .then((b) => fetch(b + "/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle: msg.handle }),
      }))
      .then((r) => r.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }

  if (msg.type === "status") {
    apiBase()
      .then((b) => fetch(b + "/status?handle=" + encodeURIComponent(msg.handle)))
      .then((r) => r.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }

  if (msg.type === "authors") {
    apiBase()
      .then((b) => fetch(b + "/authors"))
      .then((r) => r.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
});
