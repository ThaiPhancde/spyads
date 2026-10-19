"use client";
import { api } from "@/lib/api";

/** Phase 3 behavior signals. Fire-and-forget after render: never awaited by a card, views batched (≤100 per POST),
 *  the server dedupes repeats, failures are dropped (tracking must never break or slow the Ads page). */
type Kind = "ad_view" | "ad_detail_view" | "ad_skip" | "ad_not_interested";

const pending = new Set<number>();
const seen = new Set<number>();
let timer: ReturnType<typeof setTimeout> | null = null;

export function track(kind: Kind, adIds: number[]) {
  if (!adIds.length) return Promise.resolve();
  return api("/behavior/events", { method: "POST", body: JSON.stringify({ kind, ad_ids: adIds.slice(0, 100) }), keepalive: true })
    .catch(() => undefined);
}

function flush() {
  timer = null;
  const ids = Array.from(pending);
  pending.clear();
  for (let i = 0; i < ids.length; i += 100) track("ad_view", ids.slice(i, i + 100));
}

let observer: IntersectionObserver | null = null;

/** A card counts as viewed once at least half of it has been on screen; one shared observer for the whole page. */
export function observeView(el: Element | null, adId: number) {
  if (!el || typeof IntersectionObserver === "undefined" || seen.has(adId)) return () => {};
  if (!observer) {
    observer = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const id = Number((e.target as HTMLElement).dataset.adId);
        observer?.unobserve(e.target);
        if (!id || seen.has(id)) continue;
        seen.add(id); pending.add(id);
        if (!timer) timer = setTimeout(flush, 3000);
      }
    }, { threshold: 0.5 });
    window.addEventListener("pagehide", flush);
  }
  (el as HTMLElement).dataset.adId = String(adId);
  observer.observe(el);
  return () => observer?.unobserve(el);
}
