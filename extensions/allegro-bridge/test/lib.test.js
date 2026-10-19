// Node unit test for lib.js pure helpers (no browser, no jsdom). Run: node test/lib.test.js
const assert = require("assert");
const { probeAllegroPage, blockStatus, parseJobMessage, isTrustedMessage, originPattern, admitJob } = require("../lib.js");

// minimal fake document: srcs = iframe/script src list, links = number of offer links
const fakeDoc = (srcs, text, links) => ({
  title: "", location: { href: "https://allegro.pl/listing?string=x" }, body: { textContent: text },
  querySelectorAll: (sel) => sel.includes("iframe") ? srcs.map((s) => ({ getAttribute: () => s })) : new Array(links).fill({}),
});
const st = (srcs, text, links) => blockStatus(probeAllegroPage(fakeDoc(srcs, text, links)));
assert.strictEqual(st(["https://geo.captcha-delivery.com/captcha/?x=1"], "", 0), "captcha");
assert.strictEqual(st([], "You have been blocked. Sorry.", 0), "blocked");
assert.strictEqual(st([], "Nie znaleźliśmy ofert", 0), "ok", "empty search is not a block");
assert.strictEqual(st(["https://ct.captcha-delivery.com/c.js"], "", 12), "ok", "stray tag on a working listing");
assert.strictEqual(blockStatus(null), "ok");

assert.deepStrictEqual(parseJobMessage({ job_id: "a".repeat(24), keyword: " lampa ", limit: 999 }), { job_id: "a".repeat(24), keyword: "lampa", limit: 50 });
assert.strictEqual(parseJobMessage({ job_id: "x", keyword: "k" }).limit, 20);
assert.strictEqual(parseJobMessage({ job_id: "x", keyword: "k", limit: 0 }).limit, 20);
assert.strictEqual(parseJobMessage({ job_id: "../evil", keyword: "k" }), null);
assert.strictEqual(parseJobMessage({ job_id: "x", keyword: "" }), null);
assert.strictEqual(parseJobMessage({ job_id: "x", keyword: "k".repeat(201) }), null);
assert.strictEqual(parseJobMessage("nope"), null);

const win = { location: { origin: "http://localhost:3000" } };
assert.ok(isTrustedMessage({ source: win, origin: "http://localhost:3000" }, win));
assert.ok(!isTrustedMessage({ source: {}, origin: "http://localhost:3000" }, win), "other window (iframe/opener)");
assert.ok(!isTrustedMessage({ source: win, origin: "https://evil.example" }, win), "other origin");

assert.strictEqual(originPattern("http://192.168.1.10:3000/eu-market?x=1"), "http://192.168.1.10:3000/*");
assert.strictEqual(originPattern("https://toolspy.example.com"), "https://toolspy.example.com/*");
assert.strictEqual(originPattern("file:///C:/x"), null);
assert.strictEqual(originPattern("not a url"), null);
const qu = [], J = (id) => ({ job_id: id, keyword: "k", limit: 20 });
assert.deepStrictEqual(admitJob(qu, null, J("a"), 2), { ok: true, position: 1 });
assert.deepStrictEqual(admitJob(qu, null, J("a"), 2), { ok: true, position: 1, dup: true }, "re-sent job_id is not queued twice");
assert.ok(admitJob(qu, "r", J("r"), 2).dup, "running job is deduped");
assert.ok(admitJob(qu, null, J("b"), 2).ok);
assert.strictEqual(admitJob(qu, null, J("c"), 2).ok, false, "queue cap");
assert.strictEqual(qu.length, 2);
console.log("lib.test.js OK");
