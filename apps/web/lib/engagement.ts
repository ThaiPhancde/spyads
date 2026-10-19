"use client";
/** Ad engagement client: cards register the ad they show, one batched POST /engagement/enrich per paint queues what memory lacks (a free pass:
 *  ad numbers + post mapping + cache), then a short GET /engagement poll for the ads still queued — never a request per React render.
 *  Every number is `number | null`; null renders as "N/A", never 0. The post-scope numbers (the original post / video) are separate from the
 *  ad-scope numbers (the ad itself) and are labelled as such. */
import { useEffect, useSyncExternalStore } from "react";
import { api } from "@/lib/api";

export type Counts = { likes: number | null; comments: number | null; shares: number | null; views: number | null };
export type Engagement = {
  ad_id: number;
  ad: (Counts & { provider?: string; observed_at?: string | null }) | null;
  post: (Counts & { provider?: string; platform?: string; post_id?: string | null; url?: string | null; observed_at?: string; stale?: boolean }) | null;
  status: string;   // ok | unmapped | needs_provider | disabled | quota | not_found | error | queued | unknown | looking
  reason?: string | null;
  job?: string | null;
  error?: string | null;
};

const store = new Map<number, Engagement>();
const listeners = new Set<() => void>();
const pending = new Set<number>();
const polling = new Map<number, number>(); // ad id → first polled at
let enqueueTimer: any = null;
let pollTimer: any = null;
const POLL_MS = 5000;
const GIVE_UP_MS = 60_000;

function emit() { listeners.forEach((l) => l()); }

function absorb(rows: Record<string, Engagement> | undefined) {
  for (const e of Object.values(rows || {})) {
    store.set(e.ad_id, e);
    if (e.status === "queued" || (e.status === "unknown" && e.job === "queued")) { if (!polling.has(e.ad_id)) polling.set(e.ad_id, Date.now()); } else polling.delete(e.ad_id);
  }
  emit();
  schedulePoll();
}

async function flush() {
  enqueueTimer = null;
  const ids = [...pending].slice(0, 200);
  pending.clear();
  if (!ids.length) return;
  try { absorb((await api("/engagement/enrich", { method: "POST", body: JSON.stringify({ ad_ids: ids }) })).engagement); } catch { /* the card keeps the ad's own numbers */ }
}

function schedulePoll() {
  if (pollTimer || !polling.size) return;
  pollTimer = setTimeout(async () => {
    pollTimer = null;
    const now = Date.now();
    const ids = [...polling.keys()].filter((id) => now - (polling.get(id) || now) < GIVE_UP_MS);
    [...polling.keys()].filter((id) => !ids.includes(id)).forEach((id) => polling.delete(id));
    if (!ids.length) return;
    try { absorb((await api(`/engagement?ads=${ids.slice(0, 200).join(",")}`)).engagement); } catch { schedulePoll(); }
  }, POLL_MS);
}

export function requestEngagement(adId: number) {
  if (store.has(adId) && store.get(adId)!.status !== "unknown") return;
  pending.add(adId);
  if (!enqueueTimer) enqueueTimer = setTimeout(flush, 200);
}

/** The "Lấy tương tác" button: may spend one Bright Data Posts record (its own small budget), inline. */
export async function lookupEngagement(adId: number) {
  store.set(adId, { ...(store.get(adId) || { ad_id: adId, ad: null, post: null }), status: "looking" });
  emit();
  try {
    const r = await api("/engagement/lookup", { method: "POST", body: JSON.stringify({ ad_id: adId }) });
    const e = r.engagement?.[adId] || { ad_id: adId, ad: null, post: null };
    store.set(adId, { ...e, status: r.error ? "error" : r.status === "ok" ? "ok" : r.status || e.status, reason: r.error?.message || r.reason || e.reason, error: r.error?.message || null });
  } catch (e: any) {
    store.set(adId, { ad_id: adId, ad: null, post: null, status: "error", reason: e?.message || "lỗi khi lấy tương tác", error: e?.message });
  }
  emit();
}

export function useEngagement(adId: number | null): Engagement | null {
  const snap = useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => (adId != null ? store.get(adId) || null : null), () => null);
  useEffect(() => { if (adId != null) requestEngagement(adId); }, [adId]);
  return snap;
}
