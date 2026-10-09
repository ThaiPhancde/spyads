"use client";
import { ReactNode, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { api, fmt } from "@/lib/api";
import { cn } from "@/lib/utils";

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

/** Tooltip shorthand (self-contained provider so it works on any page). `tip` empty → children only. */
export function Hint({ tip, children }: { tip?: ReactNode; children: ReactNode }) {
  if (!tip) return <>{children}</>;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex min-w-0 items-center">{children}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{tip}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function NetworkBadge({ network, byKey, small }: { network?: string | null; byKey: Record<string, Network>; small?: boolean }) {
  const n = byKey[network || "meta"];
  const label = n?.label || network || "—";
  return (
    <Badge variant="secondary" className={cn("font-medium", small ? "px-1.5 py-0 text-[10px]" : "text-[11px]")} title={label}>
      <span className={cn("inline-block size-2 rounded-full", !n?.color && "bg-muted-foreground")} style={n?.color ? { background: n.color } : undefined} />
      {label}
    </Badge>
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
    <ToggleGroupItem key={key || "all"} value={key || "all"} aria-label={label}
                     className={cn("h-7 rounded-full border px-2.5 text-xs data-[state=on]:border-primary data-[state=on]:font-semibold", count === 0 && value !== key && "opacity-50")}>
      {color && <span className="inline-block size-2 rounded-full" style={{ background: color }} />}
      {label}
      {count !== undefined && <span className="tnum text-muted-foreground">{fmt.n(count)}</span>}
    </ToggleGroupItem>
  );
  return (
    <ToggleGroup type="single" variant="outline" size="sm" spacing={1} value={value || "all"} onValueChange={(v) => v && onChange(v === "all" ? "" : v)}
                 className="flex-wrap justify-start gap-1.5">
      {chip("", "Tất cả", null, facets ? total : undefined)}
      {list.map((n) => chip(n.key, n.label, n.color, facets ? facets[n.key] || 0 : undefined))}
    </ToggleGroup>
  );
}
