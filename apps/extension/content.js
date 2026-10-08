// Isolated world: receives ads from injected.js, de-duplicates them for this tab, batches them and hands them
// to the background worker (which talks to the Market Intelligence API).
(() => {
  const seen = new Set();     // ids the background confirmed saved
  const pending = new Map();  // id → send attempts, for ids queued / in flight
  const queue = [];
  let captured = 0, sent = 0, failed = 0, lastError = "";
  let timer = null;
  let badge = null;

  function ensureBadge() {
    if (badge || !document.documentElement) return;
    badge = document.createElement("div");
    badge.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:2147483647;background:#1a1a19;color:#fff;font:12px system-ui;" +
      "padding:8px 12px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.35);max-width:280px;line-height:1.35;pointer-events:none";
    document.documentElement.appendChild(badge);
  }

  function render() {
    ensureBadge();
    if (!badge) return;
    badge.textContent = "Market Intelligence — bắt " + captured + " · đã lưu " + sent +
      (failed ? " · lỗi " + failed + (lastError ? " (" + lastError.slice(0, 80) + ")" : "") : "");
    badge.style.background = failed ? "#9b2c2c" : "#1a1a19";
  }

  function flush() {
    timer = null;
    if (!queue.length) return;
    const items = queue.splice(0, 20);
    const country = new URL(location.href).searchParams.get("country");
    chrome.runtime.sendMessage({ type: "ads", items, country }, (resp) => {
      const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : resp && resp.ok ? "" : (resp && resp.error) || "unknown";
      if (!err) {
        sent += items.length;
        for (const it of items) { const id = String(it.ad_archive_id); pending.delete(id); seen.add(id); }
      } else {
        lastError = err;
        for (const it of items) {  // re-queue, give up after 3 attempts
          const id = String(it.ad_archive_id);
          const n = (pending.get(id) || 0) + 1;
          if (n >= 3) { pending.delete(id); failed++; } else { pending.set(id, n); queue.push(it); }
        }
        if (queue.length && !timer) timer = setTimeout(flush, 3000);
      }
      render();
    });
    if (queue.length && !timer) timer = setTimeout(flush, 400);
  }

  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || !e.data.__mi_ads__) return;
    for (const it of e.data.items || []) {
      const id = String(it.ad_archive_id);
      if (seen.has(id) || pending.has(id)) continue;
      pending.set(id, 0);
      captured++;
      queue.push(it);
    }
    render();
    if (!timer) timer = setTimeout(flush, 800);
  });
})();
