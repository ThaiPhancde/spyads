// Runs in the PAGE's JS world on facebook.com/ads/library. It does not call Meta itself: it only listens to
// the responses the Ad Library page already loads for the logged-in user and forwards the ad objects.
(() => {
  if (window.__miHooked) return;
  window.__miHooked = true;
  const MARK = "__mi_ads__";
  const DRIFT = "__mi_drift__";

  function collect(node, out, depth) {
    if (!node || typeof node !== "object" || depth > 40) return;
    if (Array.isArray(node)) { for (const x of node) collect(x, out, depth + 1); return; }
    if (node.ad_archive_id && (node.snapshot || node.page_id)) { out.push(node); return; }
    for (const k in node) collect(node[k], out, depth + 1);
  }

  // Key names of the first object carrying ad_archive_id — a schema-drift report never sends values (nothing the user saw).
  function adKeys(node, depth) {
    if (!node || typeof node !== "object" || depth > 40) return null;
    if (Array.isArray(node)) { for (const x of node) { const k = adKeys(x, depth + 1); if (k) return k; } return null; }
    if ("ad_archive_id" in node) return Object.keys(node).slice(0, 40);
    for (const k in node) { const r = adKeys(node[k], depth + 1); if (r) return r; }
    return null;
  }

  function handle(text) {
    if (!text || typeof text !== "string" || text.indexOf("ad_archive_id") === -1) return;
    const items = [];
    const parsed = [];
    const body = text.replace(/^for \(;;\);/, "");
    try { parsed.push(JSON.parse(body)); } catch (e) { /* streamed: one JSON per line */ }
    if (!parsed.length) {
      for (const line of body.split("\n")) {
        const l = line.trim();
        if (!l || l.indexOf("ad_archive_id") === -1) continue;
        try { parsed.push(JSON.parse(l.replace(/^for \(;;\);/, ""))); } catch (e) { /* ignore */ }
      }
    }
    for (const p of parsed) collect(p, items, 0);
    if (items.length) { window.postMessage({ [MARK]: true, items }, "*"); return; }
    // valid JSON that mentions ad_archive_id, yet no ad we can read: Meta changed the shape — report it, never a quiet zero
    const keys = parsed.length ? adKeys(parsed, 0) : null;
    if (keys) window.postMessage({ [DRIFT]: true, keys }, "*");
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
