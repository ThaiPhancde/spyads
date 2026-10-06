const DEFAULTS = { api: "http://localhost:8000", token: "", user: "" };

async function cfg() { return chrome.storage.sync.get(DEFAULTS); }
const base = (c) => c.api.replace(/\/+$/, "");

async function post(path, body) {
  const c = await cfg();
  const headers = { "Content-Type": "application/json" };
  if (c.token) headers["X-Ingest-Token"] = c.token;
  const r = await fetch(base(c) + path, { method: "POST", headers, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(r.status + " " + (await r.text()).slice(0, 160));
  return r.json();
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      const c = await cfg();
      if (msg.type === "ads") {
        const res = await post("/api/ingest/meta-library", { items: msg.items, country: msg.country, saved_by: c.user || null });
        const st = await chrome.storage.local.get({ total: 0 });
        await chrome.storage.local.set({ total: st.total + (res.new_ads || 0) });
        sendResponse({ ok: true, result: res });
      } else if (msg.type === "product") {
        const res = await post("/api/ingest/products", Object.assign({}, msg.payload, { saved_by: c.user || null }));
        sendResponse({ ok: true, result: res });
      } else if (msg.type === "health") {
        const r = await fetch(base(c) + "/api/health");
        sendResponse({ ok: r.ok, result: await r.json().catch(() => ({})) });
      } else {
        sendResponse({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message ? e.message : e) });
    }
  })();
  return true; // keep the channel open for the async response
});
