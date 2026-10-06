// Offline test: loads injected.js into a fake browser, replays a REAL Meta Ad Library response through the
// hooked fetch / XHR / SSR-script paths and checks the ads are forwarded exactly once per path.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const sample = fs.readFileSync(path.join(__dirname, "sample_response.txt"), "utf8");
const expected = JSON.parse(sample.replace(/^for \(;;\);/, "")).data.ad_library_main.search_results_connection.edges
  .flatMap((e) => e.node.collated_results).length;

function makeSandbox(responseText) {
  const posted = [];
  const listeners = {};
  const scripts = [];
  class XHR { open() {} addEventListener(n, f) { (this._l ||= {})[n] = f; } }
  const win = {
    postMessage: (m) => posted.push(m),
    addEventListener: (n, f) => { listeners[n] = f; },
    fetch: async (url) => ({ clone: () => ({ text: async () => (responseText === undefined ? sample : responseText) }) }),
  };
  const sb = {
    window: win, XMLHttpRequest: XHR, console,
    document: {
      querySelectorAll: () => scripts.map((t) => ({ textContent: t })),
      addEventListener: (n, f) => { listeners["doc_" + n] = f; },
    },
  };
  win.window = win;
  sb.window = win;
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "injected.js"), "utf8"), sb);
  return { sb, win, posted, XHR, scripts, listeners };
}

(async () => {
  let ok = true;
  const check = (name, cond, extra) => { console.log((cond ? "PASS" : "FAIL") + "  " + name + (extra ? "  " + extra : "")); if (!cond) ok = false; };

  // 1) fetch to /api/graphql/
  let t = makeSandbox();
  await t.sb.window.fetch("https://www.facebook.com/api/graphql/");
  await new Promise((r) => setTimeout(r, 20));
  const n1 = t.posted.reduce((a, m) => a + m.items.length, 0);
  check("fetch(/api/graphql/) → items forwarded", n1 === expected, `${n1}/${expected}`);
  check("items keep video urls", t.posted[0].items.some((i) => JSON.stringify(i).includes("video_hd_url") || JSON.stringify(i).includes("original_image_url")));

  // 2) unrelated fetch is ignored
  t = makeSandbox();
  await t.sb.window.fetch("https://www.facebook.com/some/other/endpoint");
  await new Promise((r) => setTimeout(r, 20));
  check("non-graphql fetch ignored", t.posted.length === 0);

  // 3) XHR path
  t = makeSandbox();
  const x = new t.sb.XMLHttpRequest();
  x.open("POST", "https://www.facebook.com/api/graphql/");
  x.responseType = "";
  x.responseText = sample;
  x._l.load.call(x);
  check("XHR(/api/graphql/) → items forwarded", t.posted.reduce((a, m) => a + m.items.length, 0) === expected);

  // 4) streamed (one JSON per line) response
  const lines = JSON.parse(sample.replace(/^for \(;;\);/, "")).data.ad_library_main.search_results_connection.edges
    .map((e) => "for (;;);" + JSON.stringify({ data: { ad_library_main: { search_results_connection: { edges: [e] } } } })).join("\n");
  t = makeSandbox(lines);
  await t.sb.window.fetch("/api/graphql/");
  await new Promise((r) => setTimeout(r, 20));
  check("newline-delimited stream parsed", t.posted.reduce((a, m) => a + m.items.length, 0) === expected);

  // 5) SSR-embedded JSON scan
  t = makeSandbox();
  t.scripts.push(sample.replace(/^for \(;;\);/, ""));
  t.sb.window.__miScan();
  check("SSR <script type=application/json> scanned", t.posted.reduce((a, m) => a + m.items.length, 0) === expected);

  // 6) garbage never throws
  t = makeSandbox("ad_archive_id {{{ not json");
  await t.sb.window.fetch("/api/graphql/");
  await new Promise((r) => setTimeout(r, 20));
  check("malformed payload ignored safely", t.posted.length === 0);

  process.exit(ok ? 0 : 1);
})();
