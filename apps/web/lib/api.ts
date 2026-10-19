"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (init?.method && !["GET", "HEAD", "OPTIONS"].includes(init.method.toUpperCase())) {
    let csrf = typeof document === "undefined" ? "" : document.cookie.split("; ").find((c) => c.startsWith("toolspy_csrf="))?.slice(13) || "";
    if (!csrf) {
      const challenge = await fetch("/api/auth/csrf", { credentials: "same-origin", cache: "no-store" });
      if (!challenge.ok) throw new Error("Không lấy được CSRF token. Tải lại trang để thử lại.");
      csrf = (await challenge.json()).csrf_token;
    }
    headers.set("X-CSRF-Token", decodeURIComponent(csrf));
  }
  const res = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/") && typeof window !== "undefined") window.location.assign("/login/");
    let msg = `${res.status}`;
    try {
      const j = await res.json();
      const d = j.detail;  // FastAPI: string, or our {code, message} objects, or a validation list
      msg = typeof d === "string" ? d : d?.message ? `${d.message}${d.code ? ` (${d.code})` : ""}` : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : JSON.stringify(j);
    } catch {}
    throw new Error(msg);
  }
  return res.json();
}

export function useApi<T = any>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const reload = useCallback(() => {
    if (!path) return;
    setLoading(true);
    api<T>(path)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => { setData(null); setError(String(e.message || e)); })  // drop stale data so pages render the error
      .finally(() => setLoading(false));
  }, [path]);
  useEffect(reload, [reload]);
  useEffect(() => {
    const changed = () => reload();
    window.addEventListener("workspace-filters", changed);
    return () => window.removeEventListener("workspace-filters", changed);
  }, [reload]);
  return { data, error, loading, reload };
}

/** Wrap a mutation (save / toggle / retry…): surfaces the thrown message instead of swallowing it. */
export function useAction() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (fn: () => Promise<any>) => {
    setBusy(true); setError(null);
    try { return await fn(); } catch (e: any) { setError(String(e.message || e)); } finally { setBusy(false); }
  }, []);
  return { run, error, busy };
}

export const fmt = {
  n: (v: any, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })),
  pct: (v: any, d = 1) => (v === null || v === undefined ? "—" : `${(Number(v) * 100).toFixed(d)}%`),
  signedPct: (v: any, d = 0) => (v === null || v === undefined ? "—" : `${v >= 0 ? "+" : ""}${(Number(v) * 100).toFixed(d)}%`),
  money: (v: any) => (v === null || v === undefined ? "—" : `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`),
  date: (v: any) => (v ? new Date(v).toLocaleDateString("vi-VN") : "—"),
  dt: (v: any) => (v ? new Date(v + (String(v).endsWith("Z") ? "" : "Z")).toLocaleString("vi-VN") : "—"),
};

export function qs(params: Record<string, any>) {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "" && v !== false) s.set(k, String(v));
  });
  const str = s.toString();
  return str ? `?${str}` : "";
}

/** Paged list: accumulates pages (offset/limit) so lists keep loading as the user scrolls. */
/** Refresh in place: rows already on screen keep their position (data updated, vanished ones dropped), new ones go last —
 *  a re-ranked list under the reader's eyes is what made the page "jump" while a live search ran. */
function stable(old: any[], fresh: any[]) {
  if (!fresh.every((x) => x?.id != null)) return fresh;
  const m = new Map(fresh.map((x) => [x.id, x]));
  const kept = old.filter((x) => m.has(x?.id)).map((x) => m.get(x.id));
  const seen = new Set(kept.map((x) => x.id));
  return [...kept, ...fresh.filter((x) => !seen.has(x.id))];
}

// Back-navigation cache: the list + scroll position per path, so coming back from a detail page shows the same
// rows at the same place without refetching. In-memory (SPA navigation); a full reload starts clean.
const pagedCache = new Map<string, { rows: any[]; total: number; last: any; y: number }>();

/** `append` (ref): set true right before the path changes to grow the list in place — rows already on screen keep their
 *  place and new ones land at the end — instead of replacing it (live search adds products while the reader scrolls). */
export function usePaged<T = any>(path: string, pageSize = 40, append?: { current: boolean }) {
  const hit = useRef(pagedCache.get(path));
  const [rows, setRows] = useState<T[]>(hit.current?.rows ?? []);
  const [total, setTotal] = useState(hit.current?.total ?? 0);
  const [last, setLast] = useState<any>(hit.current?.last ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gen = useRef(0);
  const count = useRef(hit.current?.rows.length ?? 0);
  const busy = useRef(false);
  const lastReload = useRef(0);
  const sep = path.includes("?") ? "&" : "?";
  useEffect(() => {
    const e = { rows, total, last, y: pagedCache.get(path)?.y ?? 0 };
    if (rows.length) pagedCache.set(path, e);
  }, [path, rows, total, last]);
  useEffect(() => {
    const onScroll = () => { const e = pagedCache.get(path); if (e) e.y = window.scrollY; };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [path]);
  useEffect(() => {  // restore once, after the cached rows have laid out
    const y = hit.current?.y;
    if (!y) return;
    const t = [0, 100, 300].map((ms) => setTimeout(() => window.scrollTo({ top: y }), ms));
    return () => t.forEach(clearTimeout);
  }, []);

  const load = useCallback(async (offset: number, replace: boolean, limit = pageSize, keepOrder = false) => {
    const my = replace ? ++gen.current : gen.current;
    busy.current = true;
    setLoading(true);
    try {
      const d = await api(`${path}${sep}limit=${limit}&offset=${offset}`);
      if (my !== gen.current) return;
      setRows((r) => { const next = keepOrder ? stable(r, d.rows) : replace ? d.rows : [...r, ...d.rows]; count.current = next.length; return next; });
      setTotal(d.total ?? 0);
      setLast(d);
      setError(null);
    } catch (e: any) {
      if (my === gen.current) setError(String(e.message || e));
    } finally {
      if (my === gen.current) { busy.current = false; setLoading(false); }
    }
  }, [path, pageSize, sep]);
  useEffect(() => {
    const changed = () => { pagedCache.clear(); load(0, true); };
    window.addEventListener("workspace-filters", changed);
    return () => window.removeEventListener("workspace-filters", changed);
  }, [load]);

  // keep the old rows on screen until the new path answers: live search changes the path every poll (ids=…), and
  // blanking the list each time made the page flash back to "Đang tải…", jump to the top and restart every video
  useEffect(() => { if (hit.current) { hit.current = undefined; return; }
    const keep = !!append?.current && count.current > 0;
    if (append) append.current = false;
    load(0, true, keep ? Math.min(Math.max(count.current, pageSize), 200) : pageSize, keep);
  }, [load]);  // cache hit: skip the first fetch
  const loadMore = useCallback(() => { if (!loading) load(count.current, false); }, [load, loading]);
  /** refresh what is already on screen (keeps the scroll position / loaded pages). Driven by realtime events that can
   *  fire many times a second (CREATIVE_STORED): never cancel a load in flight, at most one refresh per 15 s —
   *  otherwise each event restarted the request and the list stayed on "Đang tải…" forever. */
  const reload = useCallback(() => {
    if (busy.current || Date.now() - lastReload.current < 15_000) return;
    lastReload.current = Date.now();
    // backend caps limit (200, /creatives 300): past that only the first pages are refreshed, the rest stays as loaded
    load(0, true, Math.min(Math.max(count.current, pageSize), path.startsWith("/creatives") ? 300 : 200), true);
  }, [load, pageSize, path]);
  return { rows, total, last, loading, error, hasMore: rows.length < total, loadMore, reload };
}
