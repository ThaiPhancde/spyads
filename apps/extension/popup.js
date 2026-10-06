const $ = (id) => document.getElementById(id);
const msg = (t) => { $("msg").textContent = t; };
const DEFAULTS = { api: "http://localhost:8000", token: "", user: "" };

chrome.storage.sync.get(DEFAULTS).then((c) => { $("api").value = c.api; $("token").value = c.token; $("user").value = c.user; });
chrome.storage.local.get({ total: 0 }).then((s) => { if (s.total) msg("Đã gửi " + s.total + " quảng cáo mới từ trước đến nay."); });

async function saveSettings() {
  await chrome.storage.sync.set({ api: $("api").value.trim() || DEFAULTS.api, token: $("token").value.trim(), user: $("user").value.trim() });
}
$("save").onclick = async () => { await saveSettings(); msg("Đã lưu cài đặt."); };
$("test").onclick = async () => {
  await saveSettings();
  const r = await chrome.runtime.sendMessage({ type: "health" });
  msg(r && r.ok ? "Kết nối OK ✓ " + JSON.stringify(r.result) : "Không kết nối được: " + ((r && r.error) || "kiểm tra địa chỉ"));
};

// Runs inside the page being viewed (injected via chrome.scripting): must be self-contained.
function pageInfo() {
  const meta = (n) => { const e = document.querySelector('meta[property="' + n + '"],meta[name="' + n + '"]'); return e ? e.content : null; };
  const vids = [];
  for (const v of document.querySelectorAll("video")) {
    const srcs = [v.currentSrc, v.src].concat([...v.querySelectorAll("source")].map((s) => s.src));
    for (const u of srcs) if (u && u.startsWith("http")) vids.push(u);
  }
  const og = meta("og:video") || meta("og:video:url") || meta("og:video:secure_url");
  if (og) vids.push(og);
  const imgs = [meta("og:image"), meta("twitter:image")].filter(Boolean);
  return {
    title: meta("og:title") || document.title, url: location.href, description: meta("og:description") || meta("description"),
    videos: [...new Set(vids)].slice(0, 5), images: [...new Set(imgs)].slice(0, 3),
    price: meta("product:price:amount") || meta("og:price:amount"), currency: meta("product:price:currency") || meta("og:price:currency"),
    site: meta("og:site_name") || location.hostname,
  };
}

$("savePage").onclick = async () => {
  await saveSettings();
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (!tab || !tab.id) return msg("Không tìm thấy tab.");
  let info;
  try {
    const res = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: pageInfo });
    info = res[0].result;
  } catch (e) { return msg("Không đọc được trang này: " + e.message); }
  const r = await chrome.runtime.sendMessage({
    type: "product",
    payload: {
      name: (info.title || "").slice(0, 200), url: info.url, country: $("country").value.trim() || null,
      price: info.price ? Number(info.price) : null, currency: info.currency || null,
      video_urls: info.videos, image_urls: info.images, note: info.description, advertiser: info.site,
    },
  });
  if (r && r.ok) {
    msg("Đã lưu ✓ " + info.title + "\nVideo: " + info.videos.length + " · Ảnh: " + info.images.length +
      (info.videos.length === 0 ? "\n(Trang không lộ link video tải được — video dạng blob/stream sẽ không lưu được.)" : ""));
  } else msg("Lỗi: " + ((r && r.error) || "không rõ"));
};
