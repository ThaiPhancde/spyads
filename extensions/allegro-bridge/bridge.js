/* ToolSpy web ↔ extension channel. Content script on the ToolSpy web origin only (static localhost:3000 + one user-set origin).
 * Accepts window.postMessage only from this same window + origin; replies with targetOrigin = location.origin (never "*"). */
(() => {
  if (window.__toolspyAllegroBridge) return;
  window.__toolspyAllegroBridge = true;
  const post = (msg) => window.postMessage(msg, location.origin);
  const fail = (job_id, error) => post({ type: "TOOLSPY_ALLEGRO_RESULT", job_id, status: "error", count: 0, error });

  window.addEventListener("message", (e) => {
    if (!isTrustedMessage(e, window) || !e.data) return;
    const t = e.data.type;
    if (t === "TOOLSPY_ALLEGRO_PING") return post({ type: "TOOLSPY_ALLEGRO_PONG", version: chrome.runtime.getManifest().version });
    if (t !== "TOOLSPY_ALLEGRO_SEARCH") return;
    const job = parseJobMessage(e.data);
    if (!job) return fail(typeof e.data.job_id === "string" ? e.data.job_id : null, "Yêu cầu không hợp lệ (job_id/keyword).");
    try {
      chrome.runtime.sendMessage({ type: "allegro_job", job })
        .then((r) => { if (!r || !r.ok) fail(job.job_id, (r && r.error) || "Extension từ chối yêu cầu."); })
        .catch((err) => fail(job.job_id, "Không liên lạc được extension: " + err.message));
    } catch (err) {   // extension reloaded/updated → this content script is orphaned
      return fail(job.job_id, "Extension vừa được tải lại — hãy tải lại trang ToolSpy.");
    }
    post({ type: "TOOLSPY_ALLEGRO_ACK", job_id: job.job_id, status: "queued" });
  });

  chrome.runtime.onMessage.addListener((m) => {
    if (m && m.type === "allegro_result" && m.result) post({ type: "TOOLSPY_ALLEGRO_RESULT", ...m.result });
  });
})();
