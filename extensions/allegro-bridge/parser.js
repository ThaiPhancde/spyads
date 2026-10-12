/* ToolSpy Browser Bridge — Allegro listing parser.
 * extractAllegroListing() is injected with chrome.scripting.executeScript({func}) into the tab the user is viewing,
 * so it MUST stay self-contained (no outer variables). It only reads the already-loaded DOM: no navigation, no fetch,
 * no clicks, no scrolling. Untested against the live Allegro DOM — check `log` on the first real run. */
function extractAllegroListing(limit, doc) {
  doc = doc || document;
  limit = Math.max(1, Math.min(200, Number(limit) || 5));
  var BASE = "https://allegro.pl";
  var log = { page_url: String(doc.location ? doc.location.href : ""), strategies: {}, json_blobs: 0, json_offers: 0,
              articles: 0, offer_links: 0, unresolved_urls: 0, missing: { price: 0, image_url: 0, seller: 0 }, warnings: [] };
  var ID_RE = /\/oferta\/[^?#\s"']*?-(\d{8,})(?:[?#/]|$)/;

  function abs(u) { try { return new URL(u, BASE).href; } catch (e) { return null; } }
  function offerIdFromUrl(u) { var m = u && String(u).match(ID_RE); return m ? m[1] : null; }
  function cleanOfferUrl(u) {
    var a; try { a = new URL(u, BASE); } catch (e) { return null; }
    if (!/(^|\.)allegro\.pl$/.test(a.hostname) || a.pathname.indexOf("/oferta/") !== 0) return null;
    return "https://allegro.pl" + a.pathname.replace(/\/+$/, "");
  }
  /* -> {url, id, sponsored, unresolved} ; resolves sponsored/tracking redirects only if the target is in the URL itself */
  function resolveLink(href) {
    var u = abs(href); if (!u) return null;
    var direct = cleanOfferUrl(u);
    if (direct) return { url: direct, id: offerIdFromUrl(direct), sponsored: false, unresolved: false };
    var parsed; try { parsed = new URL(u); } catch (e) { return null; }
    var tracking = /events\/clicks|\/redirect|clicks?\?/.test(parsed.pathname + parsed.search);
    var cands = [];
    parsed.searchParams.forEach(function (v) { cands.push(v); });
    for (var i = 0; i < cands.length; i++) {
      var v = cands[i];
      for (var k = 0; k < 3 && /%2F|%3A/i.test(v); k++) { try { v = decodeURIComponent(v); } catch (e) { break; } }
      if (v.indexOf("/oferta/") >= 0) {
        var c = cleanOfferUrl(v.indexOf("http") === 0 ? v : BASE + v.slice(v.indexOf("/oferta/")));
        if (c && offerIdFromUrl(c)) return { url: c, id: offerIdFromUrl(c), sponsored: tracking, unresolved: false };
      }
    }
    var id = offerIdFromUrl(decodeURIComponent(u));
    if (tracking || id) return { url: u, id: id, sponsored: tracking, unresolved: true };
    return null;
  }
  function parsePrice(t) {
    if (t == null) return null;
    if (typeof t === "number") return t;
    var s = String(t).replace(/[\u00a0\u202f]/g, " ");
    var m = s.match(/(\d{1,3}(?:[ .]\d{3})*|\d+)(?:,(\d{1,2}))?\s*(zł|pln)/i) || s.match(/(\d{1,3}(?:[ .]\d{3})*|\d+)(?:[,.](\d{1,2}))?/);
    if (!m) return null;
    var n = parseFloat(m[1].replace(/[ .]/g, "") + (m[2] ? "." + m[2] : ""));
    return isFinite(n) ? n : null;
  }
  function bestImg(img) {
    if (!img) return null;
    var best = null, bw = -1;
    ["srcset", "data-srcset"].forEach(function (attr) {
      (img.getAttribute(attr) || "").split(",").forEach(function (part) {
        var bits = part.trim().split(/\s+/); if (!bits[0]) return;
        var w = parseFloat(bits[1]) || 1; if (w > bw) { bw = w; best = bits[0]; }
      });
    });
    var u = best || img.getAttribute("data-src") || img.getAttribute("src");
    u = u && abs(u);
    return u && /^https:\/\//.test(u) ? u : null;
  }
  function text(el) { return el ? (el.textContent || "").replace(/\s+/g, " ").trim() : ""; }

  /* ---------- strategy A: DOM cards ---------- */
  var domRecs = [];
  var cards = Array.prototype.slice.call(doc.querySelectorAll('article, [data-role="offer"], [role="article"]'));
  log.articles = cards.length;
  var seenCard = new Set();
  function fromCard(card, link, strategy) {
    if (seenCard.has(card)) return; seenCard.add(card);
    link = link || card.querySelector('a[href*="/oferta/"], a[href*="events/clicks"], a[href*="redirect"]');
    var r = link ? resolveLink(link.getAttribute("href")) : null;
    var id = (r && r.id) || card.getAttribute("data-analytics-view-value") || card.getAttribute("data-item-id") ||
             card.getAttribute("data-offer-id") || card.getAttribute("data-id");
    if (id && !/^\d{8,}$/.test(id)) id = null;
    if (!id) return;
    var h = card.querySelector("h2 a, h3 a, h2, h3");
    var img = card.querySelector("img");
    var title = text(h) || (link && (link.getAttribute("title") || text(link))) || (img && img.getAttribute("alt")) || "";
    var priceEl = card.querySelector('[aria-label*="cena" i], [data-role="price"], [class*="price" i]');
    var price = priceEl ? (parsePrice(priceEl.getAttribute("aria-label")) || parsePrice(text(priceEl))) : null;
    if (price == null) { var pm = text(card).match(/\d[\d\s\u00a0.]*,\d{2}\s*zł/i); price = pm ? parsePrice(pm[0]) : null; }
    var sellerEl = card.querySelector('[data-role="seller"], a[href*="/uzytkownik/"], [href*="/sklep/"]');
    var seller = sellerEl ? text(sellerEl).replace(/^(od|sprzedawca:?)\s+/i, "") || null : null;
    var cardText = text(card);
    var sponsored = !!(r && r.sponsored) || /sponsorowan|promowan/i.test(cardText) ||
                    /sponsored/i.test(card.getAttribute("data-analytics-view-label") || "");
    domRecs.push({ offer_id: id, title: title, price: price, currency: "PLN", image_url: bestImg(img),
                   product_url: r && !r.unresolved ? r.url : (r ? r.url : BASE + "/oferta/" + id), seller: seller,
                   sponsored: sponsored, url_unresolved: !!(r && r.unresolved), _strategy: strategy });
  }
  cards.forEach(function (c) { fromCard(c, null, "article"); });
  /* strategy B: any offer link whose card was not caught above */
  var links = Array.prototype.slice.call(doc.querySelectorAll('a[href*="/oferta/"], a[href*="events/clicks"]'));
  log.offer_links = links.length;
  links.forEach(function (a) {
    var card = a.closest('article, li, [data-role="offer"], [role="article"]') || a.parentElement;
    if (card && !seenCard.has(card) && !cards.some(function (c) { return c.contains(card) || card.contains(c); })) fromCard(card, a, "link");
  });

  /* ---------- strategy C: embedded JSON (script[type=application/json], data-serialize-box) ---------- */
  var jsonRecs = {};
  function pick(o, keys) { for (var i = 0; i < keys.length; i++) if (o[keys[i]] != null && o[keys[i]] !== "") return o[keys[i]]; return null; }
  function jPrice(o) {
    var p = o.price || (o.sellingMode && o.sellingMode.price);
    if (p && typeof p === "object") {
      var inner = p.mainPrice || p.normal || p.buyNow || p;
      return [parsePrice(inner.amount != null ? inner.amount : inner.value), inner.currency || "PLN"];
    }
    return [parsePrice(p), o.currency || "PLN"];
  }
  function walk(o, depth) {
    if (!o || typeof o !== "object" || depth > 40) return;
    if (Array.isArray(o)) { o.forEach(function (x) { walk(x, depth + 1); }); return; }
    var url = pick(o, ["url", "offerUrl", "link", "href"]);
    var id = String(pick(o, ["offerId", "id"]) || "");
    var title = pick(o, ["name", "title"]);
    if (typeof title === "object" && title) title = title.text || title.name || null;
    var r = typeof url === "string" ? resolveLink(url) : null;
    if (!/^\d{8,}$/.test(id) && r && r.id) id = r.id;
    if (/^\d{8,}$/.test(id) && typeof title === "string" && (r || o.sellingMode || o.price)) {
      var pr = jPrice(o), imgs = o.images || o.photos, img = null;
      if (Array.isArray(imgs) && imgs.length) img = typeof imgs[0] === "string" ? imgs[0] : (imgs[0].url || imgs[0].original || null);
      img = img || (typeof o.mainThumbnail === "string" ? o.mainThumbnail : null) || (typeof o.image === "string" ? o.image : null);
      var s = o.seller; var seller = s ? (typeof s === "string" ? s : (s.login || s.name || null)) : null;
      if (!jsonRecs[id]) {
        jsonRecs[id] = { offer_id: id, title: title.trim(), price: pr[0], currency: String(pr[1] || "PLN").toUpperCase(),
                         image_url: img && /^https:\/\//.test(img) ? img : null,
                         product_url: r && !r.unresolved ? r.url : BASE + "/oferta/" + id, seller: seller,
                         sponsored: !!(o.isSponsored || o.sponsored || o.promoted || (r && r.sponsored) || /sponsor/i.test(String(o.type || ""))),
                         url_unresolved: !!(r && r.unresolved), _strategy: "json" };
        log.json_offers++;
      }
    }
    for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k) && o[k] && typeof o[k] === "object") walk(o[k], depth + 1);
  }
  var nodes = Array.prototype.slice.call(doc.querySelectorAll('script[type="application/json"], script[data-serialize-box-id], [data-serialize-box]'));
  nodes.forEach(function (n) {
    var raw = n.tagName === "SCRIPT" ? n.textContent : (n.getAttribute("data-serialize-box") || n.textContent);
    if (!raw || (raw.indexOf("oferta") < 0 && raw.indexOf("offerId") < 0)) return;
    try { walk(JSON.parse(raw), 0); log.json_blobs++; } catch (e) { log.warnings.push("bad JSON in " + n.tagName); }
  });

  /* ---------- merge: DOM order first, fill gaps from JSON, then JSON-only offers ---------- */
  var out = [], seen = new Set(), fetchedAt = new Date().toISOString();
  function add(rec) {
    if (seen.has(rec.offer_id) || out.length >= limit) return;
    var j = jsonRecs[rec.offer_id];
    if (j && j !== rec) ["title", "price", "image_url", "seller"].forEach(function (k) { if (rec[k] == null || rec[k] === "") rec[k] = j[k]; });
    if (j && rec.url_unresolved && !j.url_unresolved) { rec.product_url = j.product_url; rec.url_unresolved = false; }
    if (j && j.sponsored) rec.sponsored = true;
    if (!rec.title) { log.warnings.push("no title for " + rec.offer_id); return; }
    seen.add(rec.offer_id);
    log.strategies[rec._strategy] = (log.strategies[rec._strategy] || 0) + 1;
    delete rec._strategy;
    rec.source = "allegro"; rec.fetched_at = fetchedAt;
    if (rec.url_unresolved) log.unresolved_urls++;
    ["price", "image_url", "seller"].forEach(function (k) { if (rec[k] == null) log.missing[k]++; });
    out.push(rec);
  }
  domRecs.forEach(add);
  Object.keys(jsonRecs).forEach(function (id) { add(jsonRecs[id]); });
  if (!out.length) log.warnings.push("0 offers found — is this an allegro.pl listing page that has finished loading?");
  log.found = out.length;
  try { console.log("[ToolSpy Bridge] parser log", log); } catch (e) {}
  return { records: out, log: log };
}
if (typeof module !== "undefined") module.exports = { extractAllegroListing: extractAllegroListing };
