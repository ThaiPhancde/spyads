"use client";
import Link from "next/link";
import { ArrowRight, MapPin, ShoppingCart, Star } from "lucide-react";
import { CreativeMedia } from "@/components/media";
import { NetworkBadge } from "@/components/platforms";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { fmt } from "@/lib/api";

export const money = (v: number | null | undefined, cur?: string | null) =>
  v === null || v === undefined ? "—" : `${Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${cur || ""}`.trim();

// which provider delivered a 1688 listing (adapters/ali1688.py hybrid): official AK API vs paid Apify fallback
const PROVIDER: Record<string, string> = { ali1688: "1688 · AK", apify_1688: "1688 · Apify" };

/** A China-source (AliExpress / 1688 / Taobao) listing: price, how it sells (reviews, sold), who sells it. */
export function ListingCard({ l, byKey }: { l: any; byKey: any }) {
  const discount = l.original_price && l.price && l.original_price > l.price ? 1 - l.price / l.original_price : null;
  return (
    <Card className="gap-0 overflow-hidden py-0">
      <div className="relative p-2 pb-0">
        <CreativeMedia c={l.creatives?.[0]} height={200} />
        {l.rank ? <Badge variant="outline" className="absolute top-3 left-3 bg-card text-[11px] font-semibold">#{l.rank}</Badge> : null}
      </div>
      <CardContent className="flex flex-1 flex-col gap-1.5 p-3 text-xs">
        <div className="flex items-center gap-1.5">
          <NetworkBadge network={l.network} byKey={byKey} small />
          {PROVIDER[l.source] && <span className="text-[10px] text-muted-foreground" title={`Cập nhật ${fmt.dt(l.last_seen)}`}>{PROVIDER[l.source]}</span>}
        </div>
        <a href={l.landing_url || l.snapshot_url} target="_blank" rel="noreferrer" className="line-clamp-2 text-[13px] leading-snug font-semibold hover:underline" title={l.text}>
          {l.title || l.text}
        </a>
        <div className="flex items-baseline gap-2">
          <span className="tnum text-base font-semibold">{money(l.price, l.currency)}</span>
          {discount !== null && <span className="tnum text-muted-foreground line-through">{money(l.original_price)}</span>}
          {discount !== null && <span className="text-good-text">−{Math.round(discount * 100)}%</span>}
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-ink2">
          {l.rating ? <span className="inline-flex items-center gap-1"><Star className="size-3" /> {l.rating}{l.review_count ? ` · ${fmt.n(l.review_count)} đánh giá` : ""}</span> : null}
          {l.sold_count ? <span className="inline-flex items-center gap-1"><ShoppingCart className="size-3" /> {fmt.n(l.sold_count)} đã bán</span> : null}
          {(l.markets?.[0] || l.country) && <span className="inline-flex items-center gap-1"><MapPin className="size-3" /> {l.markets?.[0] || l.country}</span>}
        </div>
        {l.advertiser && <div className="truncate text-muted-foreground">Bán bởi: {l.advertiser}</div>}
        <div className="mt-auto flex items-center justify-between pt-1">
          <span className="text-muted-foreground">Thấy lần đầu {fmt.date(l.first_seen)}</span>
          {l.product && <Link href={`/products/${l.product.id}`} className="inline-flex items-center gap-1 text-link hover:underline">Sản phẩm <ArrowRight className="size-3" /></Link>}
        </div>
      </CardContent>
    </Card>
  );
}
