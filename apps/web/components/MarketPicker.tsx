"use client";
import { useState } from "react";
import { useApi } from "@/lib/api";

export const REGION_CHIPS: [string, string][] = [["", "Tất cả thị trường mục tiêu"], ["PH", "Philippines ★"], ["ME", "Trung Đông"], ["US", "Mỹ"], ["EU", "Châu Âu + UK"], ["AU", "Úc / NZ"], ["VN", "Việt Nam"], ["WW", "Toàn cầu"]];

/** Market scope picker. value: "" (all target markets) | region code (PH/ME/US/EU/AU/VN/WW) | country code (SA, DE…).
 *  Countries outside TARGET_MARKETS are only reachable through "Nước khác" and never mixed into "".
 */
export function MarketPicker({ value, onChange, compact }: { value: string; onChange: (v: string) => void; compact?: boolean }) {
  const meta = useApi("/meta");
  const [other, setOther] = useState("");
  const countries: string[] = meta.data?.countries || [];
  const isRegion = REGION_CHIPS.some(([k]) => k === value);
  const chip = (k: string, l: string) => (
    <button key={k || "all"} onClick={() => onChange(k)} className="text-xs rounded-full px-2.5 py-1 border whitespace-nowrap"
            style={{ borderColor: value === k ? "var(--series-1)" : "var(--border)", background: value === k ? "var(--surface-2)" : undefined, fontWeight: value === k ? 600 : 400 }}>
      {l}
    </button>
  );
  return (
    <div className="flex flex-wrap gap-1.5 items-center">
      {!compact && <span className="text-xs text-muted mr-1">Thị trường:</span>}
      {REGION_CHIPS.map(([k, l]) => chip(k, l))}
      <select className="input text-xs py-1" value={!isRegion && countries.includes(value) ? value : ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">Nước…</option>
        {countries.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      <form onSubmit={(e) => { e.preventDefault(); if (other.trim()) onChange(other.trim().toUpperCase()); }} className="flex gap-1">
        <input className="input text-xs py-1 w-24" placeholder="Nước khác" value={other} onChange={(e) => setOther(e.target.value.toUpperCase())}
               title="Xem riêng một nước ngoài thị trường mục tiêu (vd TH, MY) — không lẫn vào số liệu tổng" />
      </form>
      {value && !isRegion && !countries.includes(value) && <span className="text-[11px] text-muted">đang xem {value} (ngoài thị trường mục tiêu)</span>}
    </div>
  );
}

/** Countries used for a *live* search from a scope (keeps the request count sane). */
export function liveCountries(scope: string): string[] {
  const m: Record<string, string[]> = { "": ["PH", "ALL"], PH: ["PH"], WW: ["ALL"], ME: ["SA", "AE", "KW"], US: ["US"], EU: ["GB", "DE", "FR", "IT", "ES"], AU: ["AU"], VN: ["VN"] };
  return m[scope] || [scope];
}
