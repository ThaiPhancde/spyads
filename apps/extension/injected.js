// Runs in the PAGE's JS world on facebook.com/ads/library. It does not call Meta itself: it only listens to
// the responses the Ad Library page already loads for the logged-in user and forwards the ad objects.
(() => {
  if (window.__miHooked) return;
  window.__miHooked = true;
  const MARK = "__mi_ads__";

  function collect(node, out, depth) {
    if (!node || typeof node !== "object" || depth > 40) return;
    if (Array.isArray(node)) { for (const x of node) collect(x, out, depth + 1); return; }
    if (node.ad_archive_id && (node.snapshot || node.page_id)) { out.push(node); return; }
    for (const k in node) collect(node[k], out, depth + 1);
  }

  function handle(text) {
    if (!text || typeof text !== "string" || text.indexOf("ad_archive_id") === -1) return;
    const items = [];
    const body = text.replace(/^for \(;;\);/, "");
    let parsedWhole = false;
    try { collect(JSON.parse(body), items, 0); parsedWhole = true; } catch (e) { /* streamed: one JSON per line */ }
    if (!parsedWhole) {
      for (const line of body.split("\n")) {
        const l = line.trim();
        if (!l || l.indexOf("ad_archive_id") === -1) continue;
        try { collect(JSON.parse(l.replace(/^for \(;;\);/, "")), items, 0); } catch (e) { /* ignore */ }
      }
    }
    if (items.length) window.postMessage({ [MARK]: true, items }, "*");
  }

  const isAdsUrl = (u) => typeof u === "string" && /graphql|ads\/library\/async/.test(u);

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    const p = origFetch.apply(this, args);
    try {
      const url = typeof args[0] === "string" ? args[0] : args[0] && args[0].url;
      if (isAdsUrl(url)) p.then((r) => r.clone().text().then(handle).catch(() => {})).catch(() => {});
    } catch (e) { /* never break the page */ }
    return p;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    try {
      if (isAdsUrl(String(url))) {
        this.addEventListener("load", function () {
          try { if (this.responseType === "" || this.responseType === "text") handle(this.responseText); } catch (e) { /* ignore */ }
        });
      }
    } catch (e) { /* ignore */ }
    return origOpen.call(this, method, url, ...rest);
  };

  // The first batch of results is embedded in the HTML (server-side rendered JSON), not fetched.
  function scanScripts() {
    try {
      for (const s of document.querySelectorAll('script[type="application/json"]')) {
        const t = s.textContent;
        if (t && t.indexOf("ad_archive_id") !== -1) handle(t);
      }
    } catch (e) { /* ignore */ }
  }
  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", scanScripts);
    window.addEventListener("load", () => setTimeout(scanScripts, 1500));
  }
  window.__miScan = scanScripts;
})();
