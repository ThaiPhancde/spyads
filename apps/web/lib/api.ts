"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json", ...(init?.headers || {}) } : init?.headers,
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = `${res.status}`;
    try {
      const j = await res.json();
      msg = j.detail || JSON.stringify(j);
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
export function usePaged<T = any>(path: string, pageSize = 40) {
  const [rows, setRows] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [last, setLast] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gen = useRef(0);
  const count = useRef(0);
  const busy = useRef(false);
  const lastReload = useRef(0);
  const sep = path.includes("?") ? "&" : "?";

  const load = useCallback(async (offset: number, replace: boolean, limit = pageSize) => {
    const my = replace ? ++gen.current : gen.current;
    busy.current = true;
    setLoading(true);
    try {
      const d = await api(`${path}${sep}limit=${limit}&offset=${offset}`);
      if (my !== gen.current) return;
      setRows((r) => { const next = replace ? d.rows : [...r, ...d.rows]; count.current = next.length; return next; });
      setTotal(d.total ?? 0);
      setLast(d);
      setError(null);
    } catch (e: any) {
      if (my === gen.current) setError(String(e.message || e));
    } finally {
      if (my === gen.current) { busy.current = false; setLoading(false); }
    }
  }, [path, pageSize, sep]);

  useEffect(() => { count.current = 0; setRows([]); setTotal(0); load(0, true); }, [load]);
  const loadMore = useCallback(() => { if (!loading) load(count.current, false); }, [load, loading]);
  /** refresh what is already on screen (keeps the scroll position / loaded pages). Driven by realtime events that can
   *  fire many times a second (CREATIVE_STORED): never cancel a load in flight, at most one refresh per 15 s —
   *  otherwise each event restarted the request and the list stayed on "Đang tải…" forever. */
  const reload = useCallback(() => {
    if (busy.current || Date.now() - lastReload.current < 15_000) return;
    lastReload.current = Date.now();
    // backend caps limit (200, /creatives 300): past that only the first pages are refreshed, the rest stays as loaded
    load(0, true, Math.min(Math.max(count.current, pageSize), path.startsWith("/creatives") ? 300 : 200));
  }, [load, pageSize, path]);
  return { rows, total, last, loading, error, hasMore: rows.length < total, loadMore, reload };
}
