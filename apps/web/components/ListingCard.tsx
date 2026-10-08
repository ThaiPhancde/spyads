"use client";
import Link from "next/link";
import { CreativeMedia } from "@/components/media";
import { NetworkBadge } from "@/components/platforms";
import { fmt } from "@/lib/api";

export const money = (v: number | null | undefined, cur?: string | null) =>
  v === null || v === undefined ? "—" : `${Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${cur || ""}`.trim();

/** A China-source (AliExpress / 1688 / Taobao) listing: price, how it sells (reviews, sold), who sells it. */
export function ListingCard({ l, byKey }: { l: any; byKey: any }) {
  const discount = l.original_price && l.price && l.original_price > l.price ? 1 - l.price / l.original_price : null;
  return (
    <div className="card overflow-hidden flex flex-col">
      <div className="p-2 pb-0 relative">
        <CreativeMedia c={l.creatives?.[0]} height={200} />
        {l.rank ? <span className="absolute top-3 left-3 text-[11px] font-semibold rounded px-1.5 py-0.5" style={{ background: "var(--surface-1)" }}>#{l.rank}</span> : null}
      </div>
      <div className="p-3 flex-1 flex flex-col gap-1.5 text-xs">
        <NetworkBadge network={l.network} byKey={byKey} small />
        <a href={l.landing_url || l.snapshot_url} target="_blank" rel="noreferrer" className="text-[13px] font-semibold leading-snug line-clamp-2 hover:underline" title={l.text}>
          {l.title || l.text}
        </a>
        <div className="flex items-baseline gap-2">
          <span className="text-base font-semibold tnum">{money(l.price, l.currency)}</span>
          {discount !== null && <span className="text-muted line-through tnum">{money(l.original_price)}</span>}
          {discount !== null && <span style={{ color: "var(--good-text)" }}>−{Math.round(discount * 100)}%</span>}
        </div>
        <div className="text-ink2 flex flex-wrap gap-x-3">
          {l.rating ? <span>★ {l.rating}{l.review_count ? ` · ${fmt.n(l.review_count)} đánh giá` : ""}</span> : null}
          {l.sold_count ? <span>🛒 {fmt.n(l.sold_count)} đã bán</span> : null}
          {(l.markets?.[0] || l.country) && <span>📍 {l.markets?.[0] || l.country}</span>}
        </div>
        {l.advertiser && <div className="text-muted truncate">Bán bởi: {l.advertiser}</div>}
        <div className="mt-auto pt-1 flex items-center justify-between">
          <span className="text-muted">Thấy lần đầu {fmt.date(l.first_seen)}</span>
          {l.product && <Link href={`/products/${l.product.id}`} className="text-accent hover:underline">Sản phẩm →</Link>}
        </div>
      </div>
    </div>
  );
}
