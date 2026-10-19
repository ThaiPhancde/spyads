"use client";
import { BadgeCheck, ExternalLink, Search } from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { fmt } from "@/lib/api";
import { ADVERTORIAL, lookupPrice, usePrice } from "@/lib/prices";
import { cn } from "@/lib/utils";

const SYM: Record<string, string> = { PHP: "₱", USD: "$", EUR: "€", GBP: "£", VND: "₫", AUD: "A$", NZD: "NZ$", CAD: "C$", PLN: "zł" };
export const money = (v: number | null | undefined, cur?: string | null) => {
  if (v == null) return "—";
  const s = SYM[cur || ""];
  const n = fmt.n(v, v >= 1000 || Number.isInteger(v) ? 0 : 2);
  return s ? `${s}${n}` : `${n} ${cur || ""}`.trim();
};
const PROVIDER: Record<string, string> = { brightdata: "Google (Bright Data)", serpapi_shopping: "Google Shopping", serpapi_lens: "Google Lens", direct: "trang shop",
  landing: "landing page", ad_text: "nội dung quảng cáo", source: "nguồn quảng cáo" };

/** The price line every product / ad card carries (spec §X): verified → reference → not found, nothing else.
 *  Subject = "ad:<id>" | "product:<id>" | "listing:<id>"; `fallback` = a price the row itself already had. */
export function PriceTag({ subject, priority = 1, fallback, className }: { subject: string | null; priority?: number; fallback?: { price?: number | null; currency?: string | null }; className?: string }) {
  const p = usePrice(subject, priority);
  const base = "inline-flex min-w-0 flex-wrap items-center gap-1 text-[11px]";
  if (p?.status === "looking") return <span className={cn(base, "text-muted-foreground", className)}><Spinner className="size-3" /> đang tìm và xác minh giá…</span>;
  if (!p || p.status === "queued" || p.status === "running") {
    if (fallback?.price && fallback.currency) return <span className={cn(base, className)}><b className="tnum">{money(fallback.price, fallback.currency)}</b><span className="text-muted-foreground">· giá đã ghi nhận</span></span>;
    return <span className={cn(base, "text-muted-foreground", className)} title="Đang tìm giá ở shop / marketplace (chạy ngầm)"><Spinner className="size-3" /> đang tìm giá…</span>;
  }
  if (p.status !== "done" || p.price_type === "NOT_FOUND") {
    if (fallback?.price && fallback.currency) return <span className={cn(base, className)}><b className="tnum">{money(fallback.price, fallback.currency)}</b><span className="text-muted-foreground">· giá đã ghi nhận</span></span>;
    if (p.advertorial) return <span className={cn(base, "text-muted-foreground", className)} title={ADVERTORIAL}>📰 {ADVERTORIAL}</span>;
    const slow = p.status === "slow";
    return <span className={cn(base, "text-muted-foreground", className)} title={p.error || (slow ? "Hàng đợi đang bận — giá sẽ hiện khi mở lại" : "Chưa tìm được giá. Bấm để tìm thêm trên các nguồn; có thể dùng hạn mức API.")}>
      {slow ? <><Search className="size-3" /> giá: đang chờ</> : <button type="button" disabled={!subject} onClick={(e) => { e.preventDefault(); e.stopPropagation(); subject && lookupPrice(subject); }}
        className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 hover:bg-muted"><Search className="size-3" /> Hiện giá</button>}
      {p.error && !slow && <span className="truncate">{p.error}</span>}</span>;
  }
  const verified = p.price_type === "VERIFIED_COMPETITOR" || p.price_type === "VERIFIED_MARKETPLACE";
  const when = p.observed_at ? fmt.dt(p.observed_at) : "";
  const who = p.role === "supplier" ? `Giá nguồn hàng${p.seller ? ` (${p.seller})` : ""}` : verified ? (p.price_type === "VERIFIED_COMPETITOR" ? `Shop đối thủ${p.seller ? ` (${p.seller})` : ""}` : `Marketplace${p.seller ? ` (${p.seller})` : ""}`) : `Tham khảo · ${PROVIDER[p.provider || ""] || p.provider}${p.market ? ` ${p.market}` : ""}`;
  const label = verified ? money(p.price, p.currency) : p.low != null && p.high != null && p.low !== p.high ? `${money(p.low, p.currency)}–${money(p.high, p.currency)}` : money(p.price, p.currency);
  return (
    <span className={cn(base, className)} title={`${verified ? "Giá đọc từ trang bán" : "Khoảng giá quan sát trong kết quả tìm kiếm — chưa xác minh là cùng sản phẩm"} · ${who} · ${when}${p.method ? ` · ${p.method}` : ""}`}>
      {verified && <BadgeCheck className="size-3 text-good-text" />}
      <b className="tnum">{label}</b>
      {p.original_price && p.price && p.original_price > p.price && <span className="tnum text-muted-foreground line-through">{money(p.original_price, p.currency)}</span>}
      <span className="truncate text-muted-foreground">— {who}</span>
      {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="text-link" title={p.url}><ExternalLink className="size-3" /></a>}
    </span>
  );
}
