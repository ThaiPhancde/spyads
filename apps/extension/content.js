// Hand each captured batch to the durable background outbox immediately.
(() => {
  const seen = new Set();
  let queued = 0, error = "", badge;
  function render() {
    if (!badge && document.documentElement) {
      badge = document.createElement("div");
      badge.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:2147483647;background:#1a1a19;color:white;padding:8px 12px;font:12px system-ui;pointer-events:none";
      document.documentElement.appendChild(badge);
    }
    if (badge) badge.textContent = "ToolSpy — đã xếp hàng " + queued + (error ? " · " + error : " · tự động gửi lại khi có mạng");
  }
  function handoff(batch, attempt = 0) {
    chrome.runtime.sendMessage(batch, (response) => {
      const failure = chrome.runtime.lastError?.message || (!response?.ok && response?.error) || (!response?.ok && "Chưa lưu được hàng đợi");
      if (failure) {
        error = String(failure);
        setTimeout(() => handoff(batch, attempt + 1), Math.min(60000, 1000 * 2 ** Math.min(attempt, 6)));
      } else { queued += batch.items.length; error = ""; }
      render();
    });
  }
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data?.__mi_ads__ || !Array.isArray(e.data.items)) return;
    const items = e.data.items.filter((it) => {
      if (!it?.ad_archive_id || seen.has(String(it.ad_archive_id))) return false;
      seen.add(String(it.ad_archive_id)); return true;
    });
    for (let i = 0; i < items.length; i += 20) handoff({ type: "ads", items: items.slice(i, i + 20),
      country: new URL(location.href).searchParams.get("country"), idempotency_key: crypto.randomUUID() });
  });
})();
