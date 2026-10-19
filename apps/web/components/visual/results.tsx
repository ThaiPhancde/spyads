"use client";
import Link from "next/link";
import { ReactNode, useState } from "react";
import { Check, ExternalLink, History, ImageOff, Megaphone, ScanSearch, Tag, X } from "lucide-react";
import { toast } from "sonner";
import { SimpleSelect } from "@/components/simple-select";
import { Card, Empty, Pill, type Tone } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { splitTags as split, TagInput } from "@/components/filters/tag-input";
import { visualHref } from "@/components/visual/global-image-search";
import { ZoomThumb } from "@/components/visual/web-results";
import { PriceTag } from "@/components/pricing/price-tag";
import { api, fmt } from "@/lib/api";
import { cn } from "@/lib/utils";

export const MODES: [string, string, string][] = [
  ["visual", "Giống hình", "Ảnh trùng + ảnh giống về hình dáng / màu sắc (hash + CLIP)"],
  ["exact", "Trùng ảnh", "Chỉ ảnh trùng hoặc gần trùng (đổi cỡ, nén lại)"],
  ["same_product", "Cùng sản phẩm", "Trùng ảnh hoặc rất giống + trùng metadata (sản phẩm / EAN) — không suy đoán cùng SKU chỉ vì giống hình"],
  ["similar_product", "Sản phẩm tương tự", "Giống về hình nhưng không khẳng định cùng SKU"],
  ["ads_match", "Quảng cáo dùng ảnh", "Chỉ quảng cáo (Meta / TikTok) có ảnh trùng hoặc giống"],
  ["find_suppliers", "Tìm nguồn hàng", "Listing nguồn hàng TQ (AliExpress / 1688 / Taobao) và nhà cung cấp B2B EU"],
  ["compare_prices", "So sánh giá", "Mọi offer có giá thật, tách cùng-SKU và tương-tự"],
];
export const TIER: Record<string, [string, Tone, string]> = {
  EXACT: ["Trùng ảnh", "good", "Cùng một tấm ảnh (sha256 / pHash ≤ 6 bit)"],
  LIKELY_SAME_PRODUCT: ["Có thể cùng SP", "info", "Rất giống + trùng metadata (cùng sản phẩm / EAN / family) — cần xác nhận"],
  VISUALLY_SIMILAR: ["Giống về hình", "neutral", "Giống hình dáng theo CLIP / hash — chưa chắc cùng sản phẩm"],
};
export const KINDS: [string, string][] = [["ad", "Quảng cáo"], ["listing", "Nguồn hàng TQ"], ["eu_listing", "EU Market"]];
const KIND_LABEL: Record<string, string> = { ...Object.fromEntries(KINDS), web: "Web (Google Lens)" };
const COUNTRIES = ["PH", "SA", "AE", "KW", "QA", "EG", "US", "GB", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "PL", "CZ", "SE", "RO", "AU", "NZ", "VN"];
export const SORTS: [string, string][] = [["similarity", "Độ giống"], ["price", "Giá thấp → cao"], ["observed", "Mới quan sát"]];

export type Filters = { mode: string; keywords: string[]; country: string; sources: string[]; price_min: string; price_max: string; sort: string };
export const DEFAULT_FILTERS: Filters = { mode: "visual", keywords: [], country: "", sources: [], price_min: "", price_max: "", sort: "similarity" };

export function TierBadge({ tier, similarity }: { tier: string; similarity?: number }) {
  const t = TIER[tier] || [tier, "neutral", ""];
  return <Pill tone={t[1]} className="gap-1" ><span title={t[2]}>{t[0]}</span>{similarity != null && <span className="tnum opacity-70">{Math.round(similarity * 100)}%</span>}</Pill>;
}

export function VisualSearchFilters({ value, onChange, disabled }: { value: Filters; onChange: (f: Filters) => void; disabled?: boolean }) {
  const [draft, setDraft] = useState("");
  const set = (p: Partial<Filters>) => onChange({ ...value, ...p });
  return (
    <div className="space-y-2">
      <ToggleGroup type="single" variant="outline" size="sm" spacing={1} value={value.mode} onValueChange={(v) => v && set({ mode: v })} className="flex-wrap" disabled={disabled}>
        {MODES.map(([k, l, hint]) => <ToggleGroupItem key={k} value={k} className="text-xs" title={hint} aria-label={l}>{l}</ToggleGroupItem>)}
      </ToggleGroup>
      <div className="flex flex-wrap items-center gap-2">
        <TagInput tags={value.keywords} setTags={(t) => set({ keywords: t })} draft={draft} setDraft={setDraft}
                  placeholder="Kết hợp từ khoá (tuỳ chọn): lucky bracelet ⏎ feng shui bracelet ⏎" className="min-w-[280px]" />
        <SimpleSelect value={value.country} onChange={(v) => set({ country: v })} placeholder="Mọi quốc gia" options={COUNTRIES} className="w-32 text-xs" />
        <Input className="h-8 w-24 text-xs" placeholder="Giá từ (€)" inputMode="decimal" value={value.price_min} onChange={(e) => set({ price_min: e.target.value.replace(/[^\d.]/g, "") })} />
        <Input className="h-8 w-24 text-xs" placeholder="đến (€)" inputMode="decimal" value={value.price_max} onChange={(e) => set({ price_max: e.target.value.replace(/[^\d.]/g, "") })} />
        <SimpleSelect value={value.sort} onChange={(v) => set({ sort: v || "similarity" })} options={SORTS} className="w-36 text-xs" />
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-muted-foreground">Nguồn:</span>
        {KINDS.map(([k, l]) => {
          const on = !value.sources.length || value.sources.includes(k);
          return (
            <Badge key={k} asChild variant="outline" className={cn("cursor-pointer px-2 py-0.5", on ? "border-series-1" : "opacity-45")}>
              <button type="button" aria-pressed={on} onClick={() => {
                const all = KINDS.map(([x]) => x);
                const cur = value.sources.length ? value.sources : all;
                const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k];
                set({ sources: next.length === all.length ? [] : next });
              }}>{l}</button>
            </Badge>
          );
        })}
      </div>
    </div>
  );
}

export function ImageSearchProgress({ label }: { label?: string }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-live="polite">
      <div className="flex items-center gap-3 text-sm text-muted-foreground"><Progress value={null as any} className="w-48" /> {label || "Đang băm ảnh, tính embedding và so khớp với kho…"}</div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 10 }).map((_, i) => <div key={i} className="space-y-2"><Skeleton className="aspect-square w-full" /><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-1/2" /></div>)}
      </div>
    </div>
  );
}

function Thumb({ src, alt, className }: { src?: string | null; alt: string; className?: string }) {
  const [bad, setBad] = useState(false);
  if (!src || bad) return <div className={cn("flex items-center justify-center bg-muted text-muted-foreground", className)}><ImageOff className="size-5" /></div>;
  return <img src={src} alt={alt} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setBad(true)} className={cn("bg-muted object-contain", className)} />;
}

export function PriceCell({ r, compact }: { r: any; compact?: boolean }) {
  if (r.price == null) return <span className="text-muted-foreground">giá: unknown</span>;
  return (
    <span className="tnum">
      <b>{fmt.n(r.price, 2)} {r.currency || ""}</b>
      {r.price_eur && r.currency !== "EUR" && <span className="text-muted-foreground" title={`ECB ${r.price_eur.rate_date}: 1 EUR = ${r.price_eur.rate} ${r.currency}`}> ≈ €{fmt.n(r.price_eur.eur, 2)}</span>}
      {!compact && r.price_basis === "wholesale_per_unit" && <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px]">sỉ / chiếc</Badge>}
      {!compact && r.price_includes_vat === false && <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px]">chưa VAT</Badge>}
      {!compact && r.price_basis === "search_result" && (r.price_verified
        ? <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px] text-good-text">Price Verified</Badge>
        : <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px]" title="Giá quan sát trong kết quả tìm kiếm — chưa chắc là giá hiện tại">giá quan sát · Unverified</Badge>)}
      {!compact && r.price_basis === "ad_offer" && <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px]" title={`nguồn giá: ${r.price_source || "ad"}`}>giá trong ad</Badge>}
    </span>
  );
}

const stale = (r: any) => r.observed_at && (Date.now() - new Date(r.observed_at + "Z").getTime()) / 86400000 > 14;

/** One matched entity: its own thumbnail, where it is, price, similarity, links (spec §5.3 / §10). */
export function ProductImageCard({ r, reviewFor, onReviewed }: { r: any; reviewFor?: { source: string; source_id: number } | null; onReviewed?: () => void }) {
  const [reviewed, setReviewed] = useState<string | null>(null);
  const review = async (status: "confirmed" | "rejected") => {
    if (!reviewFor) return;
    const tgt = r.eu_listing_id ? { target_source: "eu_listing", target_id: r.eu_listing_id } : r.product_id && r.product_id !== reviewFor.source_id
      ? { target_source: "product", target_id: r.product_id } : { target_source: "creative", target_id: r.creative_id };
    try {
      await api("/products/image-matches/review", { method: "POST", body: JSON.stringify({ ...reviewFor, ...tgt, status, similarity_score: r.similarity,
        match_type: r.tier === "EXACT" ? "exact" : r.tier === "LIKELY_SAME_PRODUCT" ? "likely_same_product" : "visually_similar", evidence: { hash_distance: r.hash_distance, cosine: r.cosine } }) });
      setReviewed(status); onReviewed?.();
      toast.success(status === "confirmed" ? "Đã xác nhận cùng sản phẩm" : "Đã đánh dấu không phải");
    } catch (e: any) { toast.error(String(e.message || e)); }
  };
  const kind = KIND_LABEL[r.kind] || r.kind;
  return (
    <Card className="flex h-full flex-col gap-0 overflow-hidden py-0" pad={false}>
      <ZoomThumb src={r.thumb} alt={r.title || ""} className="aspect-square w-full" />
      <div className="flex flex-1 flex-col gap-1.5 p-2.5 text-xs">
        <div className="flex flex-wrap items-center gap-1"><TierBadge tier={r.tier} similarity={r.similarity} /><Badge variant="secondary" className="px-1.5 py-0 text-[10px]">{r.source_label || r.source}</Badge><span className="text-muted-foreground">{kind}{r.country ? ` · ${r.country}` : ""}</span>{r.copies > 1 && <Badge variant="outline" className="px-1 py-0 text-[10px]" title="Cùng một ảnh dùng trong nhiều quảng cáo / listing">×{r.copies}</Badge>}</div>
        <div className="line-clamp-2 leading-snug font-medium" title={r.title || ""}>{r.title || <span className="text-muted-foreground">(không có tiêu đề)</span>}</div>
        <div>{r.ad_id || r.product_id || r.eu_listing_id ? <PriceTag subject={r.ad_id ? `ad:${r.ad_id}` : r.product_id ? `product:${r.product_id}` : `listing:${r.eu_listing_id}`} fallback={{ price: r.price, currency: r.currency }} /> : <PriceCell r={r} compact />}</div>
        {r.seller && <div className="truncate text-muted-foreground" title={r.seller}>{r.seller}</div>}
        <div className={cn("text-muted-foreground", stale(r) && "text-warning")} title={stale(r) ? "Giá / quan sát cũ hơn 14 ngày" : ""}>{fmt.dt(r.observed_at)}{stale(r) ? " · cũ" : ""}{r.active === false ? " · đã dừng" : ""}</div>
        <div className="mt-auto flex flex-wrap items-center gap-1 pt-1">
          {r.url && <Button variant="outline" size="xs" asChild><a href={r.url} target="_blank" rel="noreferrer" title={r.url}>Mở <ExternalLink /></a></Button>}
          {(r.creative_id || r.eu_listing_id) && <Button variant="ghost" size="xs" asChild><Link href={visualHref(r.creative_id ? { creative_id: r.creative_id } : { eu_listing_id: r.eu_listing_id })} title="Tìm tiếp bằng ảnh này"><ScanSearch /></Link></Button>}
          {r.product_id && <Button variant="outline" size="xs" asChild><Link href={`/products/${r.product_id}`} title={r.product_name || ""}><Tag /> SP</Link></Button>}
          {r.eu_product_id && <Button variant="outline" size="xs" asChild><Link href={`/eu-market/products/view?id=${r.eu_product_id}`}><Tag /> EU</Link></Button>}
          {r.price_history_url && <Button variant="ghost" size="xs" asChild><Link href={`/eu-market/products/view?id=${r.eu_product_id}`} title="Lịch sử giá"><History /></Link></Button>}
          {r.creative_id && <Button variant="ghost" size="xs" asChild><Link href={visualHref({ creative_id: r.creative_id, mode: "ads_match" })} title="Quảng cáo liên quan"><Megaphone /></Link></Button>}
          {reviewFor && r.tier !== "EXACT" && (reviewed ? <span className="text-muted-foreground">{reviewed === "confirmed" ? "✓ cùng SP" : "✗ khác SP"}</span> : (
            <span className="ml-auto flex gap-0.5">
              <Button variant="ghost" size="xs" title="Xác nhận cùng sản phẩm" onClick={() => review("confirmed")}><Check className="text-good-text" /></Button>
              <Button variant="ghost" size="xs" title="Không phải sản phẩm này" onClick={() => review("rejected")}><X className="text-critical" /></Button>
            </span>
          ))}
        </div>
      </div>
    </Card>
  );
}

export function VisualMatchResults({ data, reviewFor, extra }: { data: any; reviewFor?: { source: string; source_id: number } | null; extra?: ReactNode }) {
  const rows: any[] = data?.rows || [];
  if (!rows.length) {
    return <Card><Empty title="Không tìm thấy ảnh khớp trong kho">Kho chưa có ảnh trùng / giống với ảnh này{data?.engine?.embedding?.enabled ? "" : " (mới có tầng hash — ảnh khác góc chụp cần bật CLIP)"}. Thử crop đúng vật thể, đổi chế độ, hoặc tìm từ khoá rồi quét nguồn trực tiếp.</Empty></Card>;
  }
  // one card per distinct picture + owner (Meta reuses one creative across many ads): the card shows "×N", every ad
  // keeps its own row in SimilarAdsPanel (spec §5.3: group, but keep each offer's picture and URL)
  const collapse = (list: any[]) => {
    const seen = new Map<string, any>();
    for (const r of list) {
      const k = `${r.image_sha256 || r.key}|${r.seller || ""}|${r.price ?? ""}|${r.product_id || ""}`;
      const g = seen.get(k);
      if (g) g.copies = (g.copies || 1) + 1; else seen.set(k, { ...r, copies: 1 });
    }
    return [...seen.values()];
  };
  const groups = ["EXACT", "LIKELY_SAME_PRODUCT", "VISUALLY_SIMILAR"].map((t) => [t, collapse(rows.filter((r) => r.tier === t))] as [string, any[]]).filter(([, l]) => l.length);
  return (
    <div className="space-y-4">
      {extra}
      {groups.map(([t, list]) => (
        <section key={t}>
          <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold"><TierBadge tier={t} /><span className="text-muted-foreground">{list.length} · {TIER[t][2]}</span></h3>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            {list.map((r) => <ProductImageCard key={r.key} r={r} reviewFor={reviewFor} />)}
          </div>
        </section>
      ))}
    </div>
  );
}

/** Ads that use the same / a similar picture — spec §8 Ads Search. */
export function SimilarAdsPanel({ rows }: { rows: any[] }) {
  const ads = rows.filter((r) => r.kind === "ad");
  if (!ads.length) return null;
  return (
    <Card title={`Quảng cáo liên quan (${ads.length})`} pad={false}>
      <div className="divide-y">
        {ads.slice(0, 30).map((r) => (
          <div key={r.key} className="flex items-center gap-3 px-4 py-2 text-xs">
            <Thumb src={r.thumb} alt="" className="size-12 shrink-0 rounded" />
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{r.title || "—"}</div>
              <div className="text-muted-foreground">{r.seller || "—"} · {r.source_label} · {r.country || "—"} · {r.funnel || ""} · {fmt.date(r.first_seen_at)}{r.active === false ? " · đã dừng" : ""}</div>
            </div>
            <TierBadge tier={r.tier} similarity={r.similarity} />
            {r.url && <Button variant="ghost" size="xs" asChild><a href={r.url} target="_blank" rel="noreferrer"><ExternalLink /></a></Button>}
          </div>
        ))}
      </div>
    </Card>
  );
}

function StatsRow({ s }: { s: any }) {
  if (!s || !s.with_price_eur) return <span className="text-muted-foreground">{s?.offers || 0} offer, chưa có giá quy đổi</span>;
  return (
    <span className="tnum">
      min <b>€{fmt.n(s.min_eur, 2)}</b> · median <b>€{fmt.n(s.median_eur, 2)}</b> · max <b>€{fmt.n(s.max_eur, 2)}</b>
      {s.spread_pct != null && <> · chênh <b>{s.spread_pct}%</b></>} · {s.with_price_eur}/{s.offers} offer có giá
      {s.freshest && <span className="text-muted-foreground"> · mới nhất {fmt.date(s.freshest)}</span>}
    </span>
  );
}

/** Spec §5.3: every row shows the shop's own picture, origin + converted price, VAT, shipping, total only when known. */
export function PriceComparisonTable({ data }: { data: any }) {
  const same = data?.same_product, sim = data?.similar_products;
  const Row = ({ r }: { r: any }) => (
    <TableRow className={cn(r.stale && "opacity-70")}>
      <TableCell><ZoomThumb src={r.thumb} big={r.image_url} alt={r.title || ""} className="size-12 rounded" /></TableCell>
      <TableCell className="max-w-xs"><div className="line-clamp-2 text-xs font-medium" title={r.title || ""}>{r.title || "—"}</div><TierBadge tier={r.tier} similarity={r.similarity} /></TableCell>
      <TableCell className="text-xs"><Badge variant="secondary" className="px-1.5 py-0 text-[10px]">{r.source_label || r.source}</Badge><div className="text-muted-foreground">{KIND_LABEL[r.kind] || r.kind}{r.marketplace ? ` · ${r.marketplace}` : ""}</div></TableCell>
      <TableCell className="text-xs">{r.country || "—"}</TableCell>
      <TableCell className="max-w-[140px] truncate text-xs" title={r.seller || ""}>{r.seller || "—"}</TableCell>
      <TableCell className="text-right text-xs whitespace-nowrap"><PriceCell r={r} /></TableCell>
      <TableCell className="text-right text-xs">{r.price_includes_vat === true ? "gồm VAT" : r.price_includes_vat === false ? "chưa VAT" : <span className="text-muted-foreground">?</span>}</TableCell>
      <TableCell className="tnum text-right text-xs">{r.shipping_price != null ? `${fmt.n(r.shipping_price, 2)} ${r.currency}` : <span className="text-muted-foreground">?</span>}</TableCell>
      <TableCell className="tnum text-right text-xs">{r.total != null ? <b>{fmt.n(r.total, 2)} {r.currency}</b> : <span className="text-muted-foreground" title="Chưa biết phí ship → không gọi là tổng">—</span>}</TableCell>
      <TableCell className="text-xs">{r.active === false ? "đã dừng" : r.active ? "đang bán / chạy" : "?"}</TableCell>
      <TableCell className={cn("text-xs whitespace-nowrap", r.stale && "text-warning")}>{fmt.dt(r.observed_at)}{r.stale ? " · cũ" : ""}</TableCell>
      <TableCell className="whitespace-nowrap">
        {r.url && <Button variant="ghost" size="xs" asChild><a href={r.url} target="_blank" rel="noreferrer" title="Listing gốc"><ExternalLink /></a></Button>}
        {r.eu_product_id && <Button variant="ghost" size="xs" asChild><Link href={`/eu-market/products/view?id=${r.eu_product_id}`} title="Lịch sử giá"><History /></Link></Button>}
        {r.product_id && <Button variant="ghost" size="xs" asChild><Link href={`/products/${r.product_id}`} title="Lịch sử giá / sản phẩm"><History /></Link></Button>}
        {(r.creative_id || r.eu_listing_id) && <Button variant="ghost" size="xs" asChild><Link href={visualHref(r.creative_id ? { creative_id: r.creative_id, mode: "ads_match" } : { eu_listing_id: r.eu_listing_id, mode: "ads_match" })} title="Quảng cáo liên quan"><Megaphone /></Link></Button>}
      </TableCell>
    </TableRow>
  );
  const Head = () => (
    <TableHeader><TableRow>
      <TableHead>Ảnh</TableHead><TableHead>Sản phẩm</TableHead><TableHead>Nguồn</TableHead><TableHead>QG</TableHead><TableHead>Shop</TableHead>
      <TableHead className="text-right">Giá gốc ≈ EUR</TableHead><TableHead className="text-right">VAT</TableHead><TableHead className="text-right">Ship</TableHead>
      <TableHead className="text-right">Tổng</TableHead><TableHead>Tình trạng</TableHead><TableHead>Lấy giá</TableHead><TableHead />
    </TableRow></TableHeader>
  );
  if (!same?.rows?.length && !sim?.rows?.length) {
    return <Card><Empty title="Không có offer nào có giá">Kho chưa có listing / quảng cáo khớp ảnh này kèm giá thật. Không ước lượng giá.</Empty></Card>;
  }
  return (
    <div className="space-y-4">
      <Card title="Cùng sản phẩm (EXACT / LIKELY_SAME) — bảng so sánh giá" pad={false}
            description={<div className="space-y-0.5 text-xs"><div><StatsRow s={same?.stats} /></div>
              <div className="text-muted-foreground">Bán lẻ gồm VAT: <StatsRow s={same?.by_basis?.retail_incl_vat} /> · VAT chưa rõ: <StatsRow s={same?.by_basis?.retail_vat_unknown} /> · Sỉ chưa VAT: <StatsRow s={same?.by_basis?.wholesale_excl_vat} /></div></div>}>
        {same?.rows?.length ? <Table><Head /><TableBody>{same.rows.map((r: any) => <Row key={r.key} r={r} />)}</TableBody></Table>
          : <p className="px-4 pb-2 text-xs text-muted-foreground">Chưa có offer được xác định là cùng SKU — xem “Sản phẩm tương tự” bên dưới.</p>}
      </Card>
      {sim?.rows?.length > 0 && (
        <Card title={`Sản phẩm tương tự (${sim.rows.length}) — không khẳng định cùng SKU`} pad={false} description={<StatsRow s={sim.stats} />}>
          <Table><Head /><TableBody>{sim.rows.map((r: any) => <Row key={r.key} r={r} />)}</TableBody></Table>
        </Card>
      )}
      <p className="text-xs text-muted-foreground">{data.note}</p>
    </div>
  );
}

export function IndexCoverage({ st }: { st: any }) {
  if (!st) return null;
  const c = st.creatives, e = st.eu_listings;
  const pct = c.with_thumbnail ? Math.round((c.indexed / c.with_thumbnail) * 100) : 0;
  const limited = pct < 90 || !st.engine?.embedding?.enabled;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <ScanSearch className="size-3.5" />
      <span>Chỉ mục nội bộ: <b>{fmt.n(c.indexed)}</b>/{fmt.n(c.with_thumbnail)} ảnh quảng cáo & nguồn hàng ({pct}%), <b>{fmt.n(e.indexed)}</b>/{fmt.n(e.with_thumbnail)} listing EU</span>
      <span>· engine: hash{st.engine?.embedding?.enabled ? ` + CLIP (${fmt.n(st.embeddings)} vector)` : " (CLIP tắt)"}</span>
      {limited && <Pill tone="warn">Limited coverage</Pill>}
      <span>· tìm trong kho SpyAds, không tìm live trên marketplace</span>
    </div>
  );
}
