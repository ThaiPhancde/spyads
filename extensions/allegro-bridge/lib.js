/* ToolSpy Browser Bridge — pure helpers shared by background.js (importScripts), bridge.js + popup.js, and test/lib.test.js.
 * probeAllegroPage is passed to chrome.scripting.executeScript({func}) → MUST stay self-contained (no outer variables).
 * It only reads the loaded DOM; it never clicks, solves or bypasses anything. */
function probeAllegroPage(doc) {
  doc = doc || document;
  var srcs = Array.prototype.map.call(doc.querySelectorAll("iframe[src], script[src]"), function (e) { return e.getAttribute("src") || ""; });
  var text = (doc.body ? String(doc.body.textContent || "").slice(0, 20000) : "") + " " + String(doc.title || "");
  return { url: String(doc.location ? doc.location.href : ""), title: String(doc.title || ""),
           captcha: srcs.some(function (s) { return /captcha-delivery\.com/i.test(s); }),   // DataDome challenge iframe/script
           blocked: /you have been blocked/i.test(text),
           offer_links: doc.querySelectorAll('a[href*="/oferta/"], a[href*="events/clicks"]').length };
}

/* Markers only count when the page shows NO offer links (a stray DataDome tag on a working listing is not a block).
 * 0 offers without any marker = genuinely empty search → "ok" (reported to API as status ok + 0 records → job "empty"). */
function blockStatus(p) {
  if (!p || p.offer_links > 0) return "ok";
  return p.captcha ? "captcha" : p.blocked ? "blocked" : "ok";
}

/* Validates a TOOLSPY_ALLEGRO_SEARCH payload from the web page → {job_id, keyword, limit} or null. limit clamped 1..50 (API max). */
function parseJobMessage(d) {
  if (!d || typeof d !== "object") return null;
  var id = typeof d.job_id === "string" ? d.job_id.trim() : "", kw = typeof d.keyword === "string" ? d.keyword.trim() : "";
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(id) || !kw || kw.length > 200) return null;
  return { job_id: id, keyword: kw, limit: Math.max(1, Math.min(50, Math.floor(Number(d.limit)) || 20)) };
}

/* window.postMessage guard: same window, same origin only. */
function isTrustedMessage(e, win) { return !!e && e.source === win && e.origin === win.location.origin; }

/* "http://192.168.1.10:3000/abc" → "http://192.168.1.10:3000/*" (match pattern), anything non-http(s) → null. */
function originPattern(v) {
  try { var u = new URL(String(v || "").trim()); return /^https?:$/.test(u.protocol) && u.hostname ? u.origin + "/*" : null; }
  catch (e) { return null; }
}

/* Queue admission: same job_id already queued/running → dedupe (re-sent after a lost ACK); queue full → reject. Pushes job on accept. */
function admitJob(queue, currentId, job, max) {
  if (job.job_id === currentId) return { ok: true, position: 0, dup: true };
  var i = queue.findIndex(function (q) { return q.job_id === job.job_id; });
  if (i >= 0) return { ok: true, position: i + 1, dup: true };
  if (queue.length >= max) return { ok: false, error: "Hàng đợi extension đầy (" + max + " job) — đợi các job trước xong rồi thử lại." };
  queue.push(job);
  return { ok: true, position: queue.length };
}

if (typeof module !== "undefined") module.exports = { probeAllegroPage: probeAllegroPage, blockStatus: blockStatus,
  parseJobMessage: parseJobMessage, isTrustedMessage: isTrustedMessage, originPattern: originPattern, admitJob: admitJob };
