const $ = (id) => document.getElementById(id);
const status = (msg, cls = "") => { $("status").textContent = msg; $("status").className = cls; };
let last = null;

chrome.storage.local.get({ limit: 5, api: "http://127.0.0.1:8000", token: "" }, (s) => {
  $("limit").value = s.limit; $("api").value = s.api; $("token").value = s.token;
});
["limit", "api", "token"].forEach((k) => $(k).addEventListener("change", () =>
  chrome.storage.local.set({ [k]: k === "limit" ? Number($(k).value) || 5 : $(k).value.trim() })));

async function extract() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https:\/\/allegro\.pl\//.test(tab.url || "")) throw new Error("Tab hiện tại không phải allegro.pl. Hãy tự mở trang listing Allegro rồi bấm lại.");
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractAllegroListing, args: [Number($("limit").value) || 5] });
  last = res.result;
  $("preview").value = JSON.stringify(last, null, 2);
  return last;
}

async function send(data) {
  const api = $("api").value.trim().replace(/\/+$/, ""), token = $("token").value.trim();
  if (!token) throw new Error("Chưa nhập token.");
  const r = await fetch(api + "/api/imports/allegro", {
    method: "POST", headers: { "Content-Type": "application/json", "X-ToolSpy-Token": token },
    body: JSON.stringify({ records: data.records, page_url: data.log.page_url, parser_log: data.log }),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`API ${r.status}: ${body.detail ? JSON.stringify(body.detail) : r.statusText}`);
  return body;
}

$("extractOnly").onclick = async () => {
  try { const d = await extract(); status(`Tìm thấy ${d.records.length} sản phẩm (chưa gửi). Xem log trong JSON.`, d.records.length ? "ok" : "err"); }
  catch (e) { status(e.message, "err"); }
};
$("run").onclick = async () => {
  try {
    status("Đang đọc trang...");
    const d = await extract();
    if (!d.records.length) return status("Không tìm thấy sản phẩm nào — xem phần log trong JSON.", "err");
    status(`Tìm thấy ${d.records.length} sản phẩm, đang gửi...`);
    const r = await send(d);
    status(`Đã gửi: received ${r.received}, added ${r.added}, updated ${r.updated}, total ${r.total}` +
           (r.rejected && r.rejected.length ? `\nBị từ chối: ${JSON.stringify(r.rejected)}` : ""), "ok");
  } catch (e) { status(e.message, "err"); }
};
$("copy").onclick = async () => {
  if (!last) return status("Chưa có dữ liệu — bấm extract trước.", "err");
  await navigator.clipboard.writeText(JSON.stringify(last.records, null, 2));
  status("Đã copy JSON.", "ok");
};
