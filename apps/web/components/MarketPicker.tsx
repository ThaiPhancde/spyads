"use client";
import { useState } from "react";
import { Hint } from "@/components/platforms";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
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
  const selectedCountry = !isRegion && countries.includes(value) ? value : "";
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {!compact && <span className="mr-1 text-xs text-muted-foreground">Thị trường:</span>}
      <ToggleGroup type="single" variant="outline" size="sm" spacing={1} value={isRegion ? value || "all" : ""} onValueChange={(v) => v && onChange(v === "all" ? "" : v)}
                   className="flex-wrap justify-start gap-1.5">
        {REGION_CHIPS.map(([k, l]) => (
          <ToggleGroupItem key={k || "all"} value={k || "all"} className="h-7 rounded-full border px-2.5 text-xs data-[state=on]:border-primary data-[state=on]:font-semibold">{l}</ToggleGroupItem>
        ))}
      </ToggleGroup>
      <Select value={selectedCountry} onValueChange={onChange}>
        <SelectTrigger size="sm" className="h-7 text-xs"><SelectValue placeholder="Nước…" /></SelectTrigger>
        <SelectContent>{countries.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
      </Select>
      <form onSubmit={(e) => { e.preventDefault(); if (other.trim()) onChange(other.trim().toUpperCase()); }}>
        <Hint tip="Xem riêng một nước ngoài thị trường mục tiêu (vd TH, MY) — không lẫn vào số liệu tổng">
          <Input className="h-7 w-24 text-xs" placeholder="Nước khác" value={other} onChange={(e) => setOther(e.target.value.toUpperCase())} />
        </Hint>
      </form>
      {value && !isRegion && !countries.includes(value) && <span className="text-[11px] text-muted-foreground">đang xem {value} (ngoài thị trường mục tiêu)</span>}
    </div>
  );
}

/** Countries used for a *live* search from a scope (keeps the request count sane). */
export function liveCountries(scope: string): string[] {
  const m: Record<string, string[]> = { "": ["PH", "ALL"], PH: ["PH"], WW: ["ALL"], ME: ["SA", "AE", "KW"], US: ["US"], EU: ["GB", "DE", "FR", "IT", "ES"], AU: ["AU"], VN: ["VN"] };
  return m[scope] || [scope];
}
