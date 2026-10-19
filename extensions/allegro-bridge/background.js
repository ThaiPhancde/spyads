/* ToolSpy Browser Bridge — service worker. Runs Allegro searches requested by the ToolSpy web page (via bridge.js):
 * serial queue, one job at a time, ≥5s between job starts, one listing page per job (no pagination, no detail pages).
 * Block/CAPTCHA is only DETECTED and reported: the tab is kept open and focused so the user can solve it themselves. */
importScripts("lib.js", "parser.js");

const DEFAULT_API = "http://127.0.0.1:8000", GAP_MS = 5000, LOAD_TIMEOUT_MS = 45000, SETTLE_MS = 1500;
const MAX_QUEUE = 5, STALE_MS = 170000;   // API job deadline = 3 min after creation → older queued jobs would only get a 409
const STATIC_PATTERNS = ["http://localhost:3000/*", "http://127.0.0.1:3000/*"], WEB_SCRIPT_ID = "toolspy-web";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// ponytail: in-memory queue — if Chrome kills the worker mid-queue those jobs are lost (API marks them timeout after 3 min)
const queue = [];
let running = false, lastStart = 0, currentId = null;

chrome.runtime.onMessage.addListener((m, sender, sendResponse) => {
  if (!m || m.type !== "allegro_job" || !sender.tab) return;
  const job = parseJobMessage(m.job);
  if (!job) return sendResponse({ ok: false });
  sendResponse(admitJob(queue, currentId, { ...job, tabId: sender.tab.id, frameId: sender.frameId || 0, at: Date.now() }, MAX_QUEUE));
  pump();
});

async function pump() {
  if (running) return;
  running = true;
  const keepAlive = setInterval(() => chrome.runtime.getPlatformInfo(), 20000);   // extension API call resets the worker idle timer
  try {
    while (queue.length) {
      const job = queue.shift();
      if (Date.now() - job.at > STALE_MS) { await skip(job, "error", "Job chờ quá lâu trong hàng đợi extension — không mở Allegro, hãy tìm lại."); continue; }
      await sleep(Math.max(0, lastStart + GAP_MS - Date.now()));
      lastStart = Date.now(); currentId = job.job_id;
      let res;
      try { res = await runJob(job); }
      catch (e) {
        res = { status: "error", count: 0, error: String((e && e.message) || e) };
        await report(job, { records: [], status: "error", error: res.error }).catch(() => {});   // job → failed (best effort)
      }
      currentId = null;
      notify(job, res);
      if (res.status === "captcha" || res.status === "blocked")   // user is flagged: no more Allegro tabs for the rest of the queue
        for (const j of queue.splice(0)) await skip(j, "blocked", "Bỏ qua: Allegro đã chặn (bị chặn trước đó) — giải CAPTCHA trong tab Allegro rồi tìm lại.");
    }
  } finally { clearInterval(keepAlive); running = false; currentId = null; }
}

const notify = (job, res) =>
  chrome.tabs.sendMessage(job.tabId, { type: "allegro_result", result: { job_id: job.job_id, ...res } }, { frameId: job.frameId }).catch(() => {});
async function skip(job, status, error) {   // finish a job WITHOUT opening allegro.pl
  await report(job, { records: [], status, error }).catch(() => {});
  notify(job, { status, count: 0, error });
}

async function runJob(job) {
  const { token } = await chrome.storage.local.get({ token: "" });
  if (!token) throw new Error("Chưa nhập token trong popup extension.");
  const tab = await chrome.tabs.create({ url: "https://allegro.pl/listing?string=" + encodeURIComponent(job.keyword), active: false });
  const target = { tabId: tab.id };
  let keepTab = false;
  try {
    await waitComplete(tab.id, LOAD_TIMEOUT_MS);
    await sleep(SETTLE_MS);
    const [{ result: probe }] = await chrome.scripting.executeScript({ target, func: probeAllegroPage });
    const status = blockStatus(probe);
    if (status !== "ok") {   // do NOT parse; hand the tab to the user
      keepTab = true;
      await chrome.tabs.update(tab.id, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      const error = status === "captcha"
        ? "Allegro yêu cầu xác minh (CAPTCHA). Hãy tự giải trong tab vừa mở rồi tìm lại."
        : "Allegro chặn truy cập (You have been blocked). Tab được giữ lại để bạn kiểm tra.";
      await report(job, { records: [], page_url: probe.url, parser_log: { probe }, status, error });
      return { status, count: 0, error };
    }
    const [{ result: data }] = await chrome.scripting.executeScript({ target, func: extractAllegroListing, args: [job.limit] });
    await chrome.tabs.remove(tab.id).catch(() => {});
    const records = data.records.slice(0, job.limit);
    await report(job, { records, page_url: data.log.page_url, parser_log: { ...data.log, probe }, status: "ok" });
    return { status: records.length ? "done" : "empty", count: records.length, error: null };
  } finally {
    if (!keepTab) chrome.tabs.remove(tab.id).catch(() => {});
  }
}

function waitComplete(tabId, ms) {
  return new Promise((resolve, reject) => {
    let over = false;
    const done = (err) => {
      if (over) return; over = true;
      clearTimeout(timer); chrome.tabs.onUpdated.removeListener(onUpd); chrome.tabs.onRemoved.removeListener(onRem);
      err ? reject(err) : resolve();
    };
    const onUpd = (id, info) => { if (id === tabId && info.status === "complete") done(); };
    const onRem = (id) => { if (id === tabId) done(new Error("Tab Allegro bị đóng trước khi tải xong.")); };
    const timer = setTimeout(() => done(new Error(`Hết thời gian chờ trang Allegro tải (${ms / 1000}s).`)), ms);
    chrome.tabs.onUpdated.addListener(onUpd); chrome.tabs.onRemoved.addListener(onRem);
    chrome.tabs.get(tabId).then((t) => { if (t.status === "complete" && t.url && t.url.startsWith("https://allegro.pl/")) done(); }, () => {});
  });
}

async function report(job, body) {
  const { api, token } = await chrome.storage.local.get({ api: DEFAULT_API, token: "" });
  if (!token) throw new Error("Chưa nhập token trong popup extension.");
  const r = await fetch(String(api).replace(/\/+$/, "") + "/api/imports/allegro", {
    method: "POST", headers: { "Content-Type": "application/json", "X-ToolSpy-Token": token },
    body: JSON.stringify({ job_id: job.job_id, keyword: job.keyword, ...body }),
  });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`API ${r.status}: ${(b.detail && b.detail.message) || JSON.stringify(b.detail || r.statusText)}`);
  return b;
}

/* User-configurable web origin (popup → storage.webOrigin + optional host permission) → dynamic content script. Serialized. */
let syncChain = Promise.resolve();
function syncWebOrigin() { return (syncChain = syncChain.then(doSync, doSync)); }
async function doSync() {
  const { webOrigin } = await chrome.storage.local.get({ webOrigin: "" });
  const pattern = originPattern(webOrigin);
  await chrome.scripting.unregisterContentScripts({ ids: [WEB_SCRIPT_ID] }).catch(() => {});
  if (!pattern || STATIC_PATTERNS.includes(pattern) || !(await chrome.permissions.contains({ origins: [pattern] }))) return;
  await chrome.scripting.registerContentScripts([{ id: WEB_SCRIPT_ID, matches: [pattern], js: ["lib.js", "bridge.js"], runAt: "document_start" }]);
}
chrome.runtime.onInstalled.addListener(syncWebOrigin);
chrome.runtime.onStartup.addListener(syncWebOrigin);
chrome.permissions.onAdded.addListener(syncWebOrigin);
chrome.permissions.onRemoved.addListener(syncWebOrigin);
chrome.storage.onChanged.addListener((ch, area) => { if (area === "local" && ch.webOrigin) syncWebOrigin(); });
