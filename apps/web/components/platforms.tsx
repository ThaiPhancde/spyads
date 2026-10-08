"use client";
import { useEffect, useState } from "react";
import { api, fmt } from "@/lib/api";

export type Network = { key: string; label: string; channel: "ads" | "commerce" | "organic"; color: string };

/** Network registry served by /api/meta (backend platforms.py) — one source of truth for labels / colours. */
type Registry = { networks: Network[]; channels: Record<string, string>; byKey: Record<string, Network> };
const EMPTY: Registry = { networks: [], channels: {}, byKey: {} };
let cached: Registry | null = null;
let pending: Promise<Registry> | null = null;

export function usePlatforms(): Registry {
  const [reg, setReg] = useState<Registry>(cached || EMPTY);
  useEffect(() => {
    if (cached) return;
    pending ||= api("/meta").then((m) => {
      const p = m.platforms || { networks: [], channels: {} };
      const byKey: Record<string, Network> = {};
      p.networks.forEach((n: Network) => (byKey[n.key] = n));
      return (cached = { networks: p.networks, channels: p.channels, byKey });
    }).catch(() => { pending = null; return EMPTY; });  // fetched once per page load, shared by every card
    pending.then(setReg);
  }, []);
  return reg;
}

export function NetworkBadge({ network, byKey, small }: { network?: string | null; byKey: Record<string, Network>; small?: boolean }) {
  const n = byKey[network || "meta"];
  const label = n?.label || network || "—";
  return (
    <span className={`inline-flex items-center gap-1 rounded font-medium ${small ? "text-[10px] px-1 py-0" : "text-[11px] px-1.5 py-0.5"}`}
          style={{ background: "var(--surface-2)" }} title={label}>
      <span className="inline-block w-2 h-2 rounded-full" style={{ background: n?.color || "var(--muted)" }} />{label}
    </span>
  );
}

/** Product → the networks it was seen on (ads, China source). */
export function NetworkStrip({ networks, byKey }: { networks?: string[]; byKey: Record<string, Network> }) {
  if (!networks?.length) return null;
  return <div className="flex flex-wrap gap-1">{networks.map((n) => <NetworkBadge key={n} network={n} byKey={byKey} small />)}</div>;
}

/** Network filter chips with live counts (`facets` from /api/ads). Only networks of `channel` are offered. */
export function NetworkChips({ value, onChange, channel, facets, networks }: {
  value: string; onChange: (v: string) => void; channel: string; facets?: Record<string, number>; networks: Network[];
}) {
  const list = networks.filter((n) => n.channel === channel);
  const total = Object.values(facets || {}).reduce((a, b) => a + b, 0);
  const chip = (key: string, label: string, color: string | null, count?: number) => (
    <button key={key || "all"} onClick={() => onChange(key)}
            className="text-xs px-2.5 py-1 rounded-full border inline-flex items-center gap-1.5"
            style={{ borderColor: value === key ? "var(--series-1)" : "var(--border)", background: value === key ? "var(--surface-2)" : "var(--surface-1)",
                     fontWeight: value === key ? 600 : 400, opacity: count === 0 && value !== key ? 0.5 : 1 }}>
      {color && <span className="inline-block w-2 h-2 rounded-full" style={{ background: color }} />}
      {label}{count !== undefined && <span className="text-muted tnum">{fmt.n(count)}</span>}
    </button>
  );
  return (
    <div className="flex flex-wrap gap-1.5">
      {chip("", "Tất cả", null, facets ? total : undefined)}
      {list.map((n) => chip(n.key, n.label, n.color, facets ? facets[n.key] || 0 : undefined))}
    </div>
  );
}
