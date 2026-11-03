importScripts("outbox.js");
const DEFAULTS = { api: "http://localhost:8000", token: "", user: "" };

async function cfg() { return chrome.storage.sync.get(DEFAULTS); }
const base = (c) => c.api.replace(/\/+$/, "");

async function post(path, body, endpoint) {
  const c = await cfg();
  if (endpoint && endpoint !== base(c)) throw new Error("API changed; restore original API to retry this batch");
  const headers = { "Content-Type": "application/json" };
  if (c.token) headers["X-Ingest-Token"] = c.token;
  const r = await fetch(base(c) + path, { method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(30000) });
  if (!r.ok) throw Object.assign(new Error(r.status + " " + (await r.text()).slice(0, 160)), { status: r.status });
  return r.json();
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      const c = await cfg();
      if (msg.type === "ads") {
        const id = msg.idempotency_key || crypto.randomUUID();
        const env = {};
        for (const k of ["job_id", "batch_id", "fetched_at", "schema_version", "cursor_before", "cursor_after"]) if (msg[k]) env[k] = msg[k];
        await ToolSpyOutbox.queue(ToolSpyOutbox.store, { id, endpoint: base(c), attempts: 0, nextAt: 0,
          body: { items: msg.items, country: msg.country, saved_by: c.user || null, idempotency_key: id, ...env } });
        sendResponse({ ok: true, queued: true, idempotency_key: id });
        void drainOutbox();
      } else if (msg.type === "outbox_status") {
        sendResponse({ ok: true, ...(await ToolSpyOutbox.stats(ToolSpyOutbox.store)) });
      } else if (msg.type === "outbox_retry") {
        const n = await ToolSpyOutbox.retry(ToolSpyOutbox.store);
        await drainOutbox();
        sendResponse({ ok: true, retried: n, ...(await ToolSpyOutbox.stats(ToolSpyOutbox.store)) });
      } else if (msg.type === "drift") {
        // an alert, not data: best effort, at most one report per 10 minutes
        const { lastDriftAt = 0 } = await chrome.storage.local.get({ lastDriftAt: 0 });
        if (Date.now() - lastDriftAt > 600000) {
          await chrome.storage.local.set({ lastDriftAt: Date.now() });
          await post("/api/ingest/meta-library/drift", { keys: msg.keys, path: msg.path }).catch(console.error);
        }
        sendResponse({ ok: true });
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

let draining = null;
function drainOutbox() {
  if (draining) return draining;
  draining = ToolSpyOutbox.drain(ToolSpyOutbox.store, async (row) => {
    const { pending } = await ToolSpyOutbox.stats(ToolSpyOutbox.store); // P5: the server sees how much is still queued here
    const res = await post("/api/ingest/meta-library", { ...row.body, outbox_backlog: Math.max(0, pending - 1) }, row.endpoint);
    if (res.idempotency_key !== row.id) throw new Error("Missing batch acknowledgement; upgrade API");
    const counters = await chrome.storage.local.get({ total: 0, lastCountedBatch: "" });
    if (counters.lastCountedBatch !== row.id) await chrome.storage.local.set({
      total: counters.total + (res.new_ads || 0), lastCountedBatch: row.id });
  }).catch(console.error).finally(() => { draining = null; });
  return draining;
}
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "toolspy-outbox") void drainOutbox(); });
chrome.runtime.onStartup.addListener(() => { void drainOutbox(); });
chrome.runtime.onInstalled.addListener(() => { void drainOutbox(); });
// Recreate on every service-worker start; alarms survive ordinary worker suspension.
chrome.alarms.create("toolspy-outbox", { periodInMinutes: 1 });
void drainOutbox();
