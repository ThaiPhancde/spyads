"use client";
/** Background price enrichment client (docs/PRICE_INTELLIGENCE.md §X): cards register the subject they show
 *  ("ad:12" / "product:3" / "listing:7"), one batched POST /prices/enrich per paint queues what memory lacks, then one
 *  GET /prices every few seconds for the subjects still pending — never a request per React render. */
import { useEffect, useSyncExternalStore } from "react";
import { api } from "@/lib/api";

export type PriceInfo = {
  subject: string; status: string; price_type?: string; price?: number | null; low?: number | null; high?: number | null; currency?: string | null;
  original_price?: number | null; seller?: string | null; provider?: string; method?: string | null; url?: string | null; confidence?: number;
  observed_at?: string; expires_at?: string; market?: string | null; error?: string | null; advertorial?: boolean; role?: string;
};

const store = new Map<string, PriceInfo>();
const listeners = new Set<() => void>();
const pendingEnqueue = new Map<string, number>(); // subject → priority
const polling = new Set<string>();
const since = new Map<string, number>();
let enqueueTimer: any = null;
let pollTimer: any = null;
const POLL_MS = 6000;
const GIVE_UP_MS = 4 * 60 * 1000;

function emit() { listeners.forEach((l) => l()); }
function absorb(prices: Record<string, PriceInfo>) {
  for (const [s, p] of Object.entries(prices || {})) {
    store.set(s, p);
    if (p.status === "done" || p.status === "error" || p.status === "unknown") polling.delete(s);
    else { polling.add(s); if (!since.has(s)) since.set(s, Date.now()); }
  }
  emit();
  schedulePoll();
}

async function flushEnqueue() {
  enqueueTimer = null;
  const subs = [...pendingEnqueue.entries()];
  if (!subs.length) return;
  pendingEnqueue.clear();
  const priority = Math.max(...subs.map(([, p]) => p));
  try {
    const r = await api("/prices/enrich", { method: "POST", body: JSON.stringify({ subjects: subs.map(([s]) => s).slice(0, 200), priority }) });
    absorb(r.prices);
  } catch {
    subs.forEach(([s]) => store.set(s, { subject: s, status: "unknown" }));
    emit();
  }
}

function schedulePoll() {
  if (pollTimer || !polling.size) return;
  pollTimer = setTimeout(async () => {
    pollTimer = null;
    const now = Date.now();
    const subs = [...polling].filter((s) => now - (since.get(s) || now) < GIVE_UP_MS);
    [...polling].filter((s) => !subs.includes(s)).forEach((s) => { polling.delete(s); store.set(s, { ...(store.get(s) as PriceInfo), status: "slow" }); });
    if (!subs.length) { emit(); return; }
    try { absorb((await api(`/prices?subjects=${encodeURIComponent(subs.slice(0, 200).join(","))}`)).prices); } catch { schedulePoll(); }
  }, POLL_MS);
}

/** Ask for one subject's price; `priority` 10 = the marketer opened it (jumps the queue). */
export function requestPrice(subject: string, priority = 1) {
  const have = store.get(subject);
  if (have && (have.status === "done" || have.status === "queued" || have.status === "running")) return;
  pendingEnqueue.set(subject, Math.max(priority, pendingEnqueue.get(subject) || 0));
  if (!enqueueTimer) enqueueTimer = setTimeout(flushEnqueue, 150);
}

export const ADVERTORIAL = "Bài review / advertorial — không bán một sản phẩm cụ thể nên không có giá";

const WHY: Record<string, string> = {
  no_product_name: "chưa có tên sản phẩm đáng tin cậy để tìm (thiếu metadata / caption / OCR)", needs_click: "chưa tìm tự động — bấm “Hiện giá”",
  brightdata_disabled: "Bright Data đang tắt", missing_api_token: "thiếu Bright Data token", search_disabled: "Bright Data Search đang tắt",
  shopping_disabled: "SerpApi Shopping đang tắt", missing_api_key: "thiếu SerpApi key", image_search_disabled: "tìm ảnh đang tắt",
  quota_exceeded_daily: "hết hạn mức trong ngày", quota_exceeded_monthly: "hết hạn mức tháng", provider_quota_exhausted: "hết hạn mức của nhà cung cấp",
  timeout: "nhà cung cấp không trả lời kịp", rate_limited: "bị giới hạn tốc độ", bad_request: "yêu cầu bị từ chối", internal_error: "lỗi nội bộ (đã ghi log)",
  no_title_overlap: "kết quả không khớp tên sản phẩm", incoherent_range: "các giá lệch nhau quá xa (không chắc cùng sản phẩm)", no_public_page: "quảng cáo không có trang bán công khai",
};

/** Say what really ran: a provider that errored / was out of quota did not "search and find nothing". */
function notFoundWhy(steps: any[] = [], error?: any) {
  if (error?.message) return `Lỗi hệ thống: ${error.message}`;
  if (steps.some((s) => s.code === "advertorial")) return ADVERTORIAL;
  const prov = steps.filter((s) => /^(brightdata|serpapi)/.test(s.step));
  const ok = prov.filter((s) => s.ok).map((s) => (s.step.startsWith("bright") ? "Bright Data" : "SerpApi"));
  if (ok.length) return `Đã tìm bằng ${[...new Set(ok)].join(" / ")} nhưng không có giá khớp sản phẩm`;
  const bad = steps.filter((s) => !s.ok && s.code && WHY[s.code]).map((s) => `${s.step.replace(/_/g, " ")}: ${WHY[s.code]}`);
  if (bad.length) return `Chưa có giá — ${[...new Set(bad)].slice(0, 3).join("; ")}`;
  if (prov.length) return `Không tìm được giá — nguồn lỗi: ${prov.map((s) => `${s.step} (${s.code})`).join(", ")}`;
  return "Chưa có nguồn giá nào chạy được cho sản phẩm này (không có trang bán, ảnh hay tên đủ tin cậy)";
}

/** The "Hiện giá" button: the only call that spends Bright Data / SerpApi budget (POST /prices/lookup, inline, ≤ ~60 s). */
export async function lookupPrice(subject: string) {
  store.set(subject, { subject, status: "looking" });
  emit();
  try {
    const r = await api("/prices/lookup", { method: "POST", body: JSON.stringify({ subject }) });
    if (r.pending) {
      absorb({ [subject]: { ...r.prices[subject], subject, status: "queued" } });
    } else store.set(subject, { ...r.prices[subject], ...(r.found ? {} : { status: "done", price_type: "NOT_FOUND", error: notFoundWhy(r.steps, r.error), advertorial: (r.steps || []).some((s: any) => s.code === "advertorial") }) });
  } catch (e: any) {
    store.set(subject, { subject, status: "done", price_type: "NOT_FOUND", error: e?.message || "lỗi khi tìm giá" });
  }
  emit();
}

export function usePrice(subject: string | null, priority = 1): PriceInfo | null {
  const snap = useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => (subject ? store.get(subject) || null : null), () => null);
  useEffect(() => { if (subject) requestPrice(subject, priority); }, [subject, priority]);
  return snap;
}
