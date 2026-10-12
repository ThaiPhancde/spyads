// Node unit test for parser.js against the hand-written TEST FIXTURE (needs jsdom). Run: node test/parser.test.js
const assert = require("assert"), fs = require("fs"), path = require("path");
let JSDOM;
for (const p of ["jsdom", path.join(__dirname, "node_modules", "jsdom")]) { try { ({ JSDOM } = require(p)); break; } catch (e) {} }
if (!JSDOM) { console.log("SKIP: jsdom not installed (cd test && npm install jsdom@16)"); process.exit(0); }
const { extractAllegroListing } = require("../parser.js");
const html = fs.readFileSync(path.join(__dirname, "fixture_listing.html"), "utf8");
const dom = new JSDOM(html, { url: "https://allegro.pl/listing?string=bransoletka%20damska" });
global.URL = dom.window.URL;
const { records, log } = extractAllegroListing(10, dom.window.document);
const by = Object.fromEntries(records.map((r) => [r.offer_id, r]));

assert.strictEqual(new Set(records.map((r) => r.offer_id)).size, records.length, "deduped");
const a = by["12345678901"];
assert.strictEqual(a.product_url, "https://allegro.pl/oferta/bransoletka-damska-srebrna-12345678901");
assert.strictEqual(a.price, 1234.56); assert.strictEqual(a.currency, "PLN");
assert.strictEqual(a.image_url, "https://a.allegroimg.com/s360/aa/x.jpg");
assert.strictEqual(a.seller, "SklepTest"); assert.strictEqual(a.sponsored, false);
const b = by["22222222222"];
assert.strictEqual(b.product_url, "https://allegro.pl/oferta/bransoletka-zlota-22222222222");
assert.strictEqual(b.sponsored, true); assert.strictEqual(b.price, 49.99); assert.strictEqual(b.seller, null);
const c = by["33333333333"];
assert.strictEqual(c.price, 15.5); assert.strictEqual(c.seller, "JsonSeller"); assert.strictEqual(c.image_url, "https://a.allegroimg.com/original/cc/z.jpg");
assert.strictEqual(records.length, 3, "opaque redirect without id is skipped");
assert.ok(records.every((r) => r.source === "allegro" && /Z$/.test(r.fetched_at)));
assert.strictEqual(extractAllegroListing(1, dom.window.document).records.length, 1, "limit respected");
console.log("parser.test.js OK", JSON.stringify(log));
