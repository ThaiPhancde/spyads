"use client";
import Link from "next/link";
import { ReactNode, useState } from "react";
import { ExternalLink, ImageOff, Megaphone, PackageSearch, Truck } from "lucide-react";
import { TrendChart } from "@/components/charts";
import { Card, Empty, Loading, Pill, type Tone } from "@/components/ui";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fmt } from "@/lib/api";
import { cn } from "@/lib/utils";

export const EU_COUNTRIES: [string, string][] = [
  ["DE", "Đức"], ["FR", "Pháp"], ["IT", "Ý"], ["ES", "Tây Ban Nha"], ["NL", "Hà Lan"], ["BE", "Bỉ"], ["PL", "Ba Lan"],
  ["AT", "Áo"], ["CZ", "Séc"], ["SK", "Slovakia"], ["SE", "Thuỵ Điển"], ["RO", "Romania"], ["HU", "Hungary"], ["BG", "Bulgaria"], ["GB", "Anh"],
];
export const SOURCE_LABEL: Record<string, string> = { jsonld: "Trang SP (JSON-LD)", kaufland: "Kaufland", bol: "bol", bigbuy: "BigBuy", allegro: "Allegro", emag: "eMAG" };
export const MANUAL_TAGS: [string, string][] = [["unusual_design", "Thiết kế lạ"], ["novelty", "Novelty"], ["niche", "Ngách"], ["limited_supply", "Ít hàng"], ["emerging", "Mới nổi"]];

/** API error → the module is off or the API wasn't restarted after the upgrade. */
export function EuLoading({ error, rows }: { error?: string | null; rows?: number }) {
  if (error === "Not Found") {  // FastAPI's default 404 detail = no /api/eu-market router (api() throws the detail)
    return (
      <Alert>
        <AlertTitle>EU Market chưa bật trên API</AlertTitle>
        <AlertDescription>Đặt <code>EU_MARKET_ENABLED=1</code> (mặc định) và khởi động lại API.</AlertDescription>
      </Alert>
    );
  }
  return <Loading error={error} rows={rows} />;
}

export function CountryChips({ value, onChange, options = EU_COUNTRIES }: { value: string[]; onChange: (v: string[]) => void; options?: [string, string][] }) {
  const toggle = (c: string) => onChange(value.includes(c) ? value.filter((x) => x !== c) : [...value, c]);
  return (
    <div className="flex flex-wrap gap-1" role="group" aria-label="Quốc gia">
      <Button size="xs" variant={value.length ? "outline" : "secondary"} onClick={() => onChange([])}>Tất cả</Button>
      {options.map(([c, l]) => (
        <Button key={c} size="xs" variant={value.includes(c) ? "default" : "outline"} title={l} aria-pressed={value.includes(c)} onClick={() => toggle(c)}>{c}</Button>
      ))}
    </div>
  );
}

const STATE_TONE: Record<string, Tone> = {
  ok: "good", ready: "good", available: "good", running: "info", missing_credentials: "warn", not_configured: "warn", planned: "neutral",
  access_restricted: "neutral", unauthorized: "bad", rate_limited: "warn", temporarily_unavailable: "warn", unsupported_capability: "neutral",
  bad_response: "bad", error: "bad",
};
export function StateBadge({ state }: { state: string }) {
  return <Pill tone={STATE_TONE[state] || "neutral"}>{state}</Pill>;
}

export function Thumb({ src, alt, size = 64 }: { src?: string | null; alt: string; size?: number }) {
  const [bad, setBad] = useState(false);
  if (!src || bad) {
    return <div className="flex shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground" style={{ width: size, height: size }}><ImageOff className="size-4" /></div>;
  }
  // referenced from the source (never stored); no-referrer = no hotlink header leak
  return <img src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setBad(true)}
              className="shrink-0 rounded-md border bg-white object-contain" style={{ width: size, height: size }} />;
}

/** Original price + currency, reference EUR (ECB, dated), VAT basis. Missing → "unknown", never 0. */
export function Price({ l, compact }: { l: any; compact?: boolean }) {
  if (l.price === null || l.price === undefined) return <span className="text-muted-foreground">giá: unknown</span>;
  const eur = l.price_eur;
  return (
    <span className="tnum">
      <span className="font-semibold">{fmt.n(l.price, 2)} {l.currency}</span>
      {l.shipping_price != null && !compact && <span className="text-muted-foreground"> + ship {fmt.n(l.shipping_price, 2)}</span>}
      {eur && l.currency !== "EUR" && <span className="text-muted-foreground" title={`ECB ${eur.rate_date}: 1 EUR = ${eur.rate} ${l.currency}`}> ≈ €{fmt.n(eur.eur, 2)}</span>}
      {l.price_includes_vat === false && <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px]">chưa VAT</Badge>}
    </span>
  );
}

export const eur = (v: any) => (v === null || v === undefined ? "—" : `€${fmt.n(v, 2)}`);
export const productHref = (id: number) => `/eu-market/products/view?id=${id}`;

export function OfferRow({ o }: { o: any }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
      <Badge variant="secondary" className="px-1.5 py-0">{SOURCE_LABEL[o.source] || o.source}</Badge>
      <span className="font-medium">{o.market_country}</span>
      {o.kind === "supplier_offer" && <Badge variant="outline" className="gap-1 px-1 py-0"><Truck className="size-3" />B2B</Badge>}
      <Price l={o} compact />
      {o.seller_name && <span className="max-w-40 truncate text-muted-foreground" title={o.seller_name}>· {o.seller_name}</span>}
      <span className="text-muted-foreground" title={`observed ${fmt.dt(o.observed_at)}`}>· {fmt.dt(o.last_seen_at)}</span>
      {o.product_url && (
        <a href={o.product_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-series-1 hover:underline">
          mở <ExternalLink className="size-3" />
        </a>
      )}
    </div>
  );
}

export function ProductCard({ p, extra, maxOffers = 3 }: { p: any; extra?: ReactNode; maxOffers?: number }) {
  const offers: any[] = p.offers || [];
  return (
    <Card className="h-full">
      <div className="flex gap-3">
        <Thumb src={p.image_url} alt={p.title} size={72} />
        <div className="min-w-0 flex-1 space-y-1">
          <Link href={productHref(p.id)} className="line-clamp-2 text-sm leading-snug font-medium hover:underline">{p.title}</Link>
          <div className="flex flex-wrap gap-1 text-[11px] text-muted-foreground">
            {p.brand && <span>{p.brand}</span>}
            {p.ean && <span className="tnum">EAN {p.ean}</span>}
            <span>{p.offer_count} offer · {p.sources?.map((s: string) => SOURCE_LABEL[s] || s).join(", ")} · {p.countries?.join(" ")}</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            {p.min_price_eur != null && (
              <span className="tnum font-semibold">{p.min_price_eur === p.max_price_eur ? eur(p.min_price_eur) : `${eur(p.min_price_eur)} – ${eur(p.max_price_eur)}`}</span>
            )}
            {p.ads?.confirmed > 0 && <Pill tone="good"><Megaphone className="size-3" />{p.ads.confirmed} ad đã xác nhận</Pill>}
            {p.ads?.pending > 0 && <Pill tone="warn">{p.ads.pending} ad cần review</Pill>}
            {p.compliance_flags?.length > 0 && <Pill tone="bad">compliance: {p.compliance_flags.join(", ")}</Pill>}
            {p.excluded && <Pill tone="bad">đã loại</Pill>}
          </div>
        </div>
      </div>
      {extra && <div className="mt-2">{extra}</div>}
      <div className="mt-2 space-y-1 border-t pt-2">
        {offers.slice(0, maxOffers).map((o) => <OfferRow key={o.id} o={o} />)}
        {offers.length > maxOffers && <Link href={productHref(p.id)} className="text-xs text-muted-foreground hover:underline">+{offers.length - maxOffers} offer khác</Link>}
      </div>
      {p.reasons?.length > 0 && <p className="mt-2 text-[11px] text-muted-foreground">Lý do gợi ý: {p.reasons.join(" · ")}</p>}
    </Card>
  );
}

export function ProductGrid({ rows, render }: { rows: any[]; render?: (p: any) => ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
      {rows.map((p) => <div key={p.id}>{render ? render(p) : <ProductCard p={p} />}</div>)}
    </div>
  );
}

/** Real snapshots only: <2 points (or mixed currencies) → no line, just the observed price(s). */
export function PriceChart({ points, height = 180 }: { points: any[]; height?: number }) {
  const cur = new Set(points.map((p) => p.currency));
  if (points.length < 2 || cur.size !== 1) {
    return <p className="text-xs text-muted-foreground">{points.length ? `${points.length} snapshot — chưa đủ để vẽ xu hướng giá.` : "Chưa có snapshot giá."}</p>;
  }
  const data = points.map((p) => ({ date: String(p.captured_at).slice(0, 16).replace("T", " "), price: p.price }));
  return <TrendChart data={data} series={[{ key: "price", label: `Giá (${[...cur][0]})` }]} height={height} />;
}

export function NoData({ children }: { children: ReactNode }) {
  return (
    <Empty title={<span className="inline-flex items-center gap-1.5"><PackageSearch className="size-4" />Chưa có dữ liệu</span>}>
      {children} <Link href="/eu-market/sources" className="underline">Data Sources</Link>
    </Empty>
  );
}

export function Kv({ k, v, className }: { k: ReactNode; v: ReactNode; className?: string }) {
  return (
    <div className={cn("flex justify-between gap-3 text-sm", className)}>
      <span className="text-muted-foreground">{k}</span>
      <span className="tnum text-right">{v}</span>
    </div>
  );
}
