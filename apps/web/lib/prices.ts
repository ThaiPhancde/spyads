"use client";
/** Background price enrichment client (docs/PRICE_INTELLIGENCE.md §X): cards register the subject they show
 *  ("ad:12" / "product:3" / "listing:7"), one batched POST /prices/enrich per paint queues what memory lacks, then one
 *  GET /prices every few seconds for the subjects still pending — never a request per React render. */
import { useEffect, useSyncExternalStore } from "react";
import { api } from "@/lib/api";

export type PriceInfo = {
  subject: string; status: string; price_type?: string; price?: number | null; low?: number | null; high?: number | null; currency?: string | null;
  original_price?: number | null; seller?: string | null; provider?: string; method?: string | null; url?: string | null; confidence?: number;
  observed_at?: string; expires_at?: string; market?: string | null; error?: string | null;
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

export function usePrice(subject: string | null, priority = 1): PriceInfo | null {
  const snap = useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => (subject ? store.get(subject) || null : null), () => null);
  useEffect(() => { if (subject) requestPrice(subject, priority); }, [subject, priority]);
  return snap;
}
