// Service worker: all cross-origin fetches to the read API happen here.
// Content scripts on x.com can't fetch the API directly (page CSP), so they
// message this worker.
const DEFAULT_API = "http://localhost:8000";

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
