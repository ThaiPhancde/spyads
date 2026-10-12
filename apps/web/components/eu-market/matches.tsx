"use client";
import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { Pill } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Thumb, productHref } from "@/components/eu-market/shared";
import { fmt } from "@/lib/api";

/** EU product ↔ ad record. "Đã chạy ads" only for confirmed matches; weak (title) matches show a review prompt. */
export function AdMatchRow({ m, onReview, busy, hideProduct }: { m: any; onReview: (s: string) => void; busy?: boolean; hideProduct?: boolean }) {
  const a = m.ad;
  return (
    <div className="flex flex-wrap items-start gap-3 border-b py-3 text-sm last:border-0">
      {!hideProduct && (
        <Link href={productHref(m.product_id)} className="flex w-64 min-w-0 items-center gap-2 hover:underline">
          <Thumb src={m.product_image} alt={m.product_title} size={44} />
          <span className="line-clamp-2">{m.product_title}</span>
        </Link>
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={m.strength === "strong" ? "default" : "outline"}>{m.match_method} · {m.strength} · {m.score}</Badge>
          <Pill tone={m.review_status === "confirmed" ? "good" : m.review_status === "rejected" ? "bad" : "warn"}>
            {m.review_status === "confirmed" ? "đã chạy ads (có ad record)" : m.review_status === "rejected" ? "không khớp" : "cần review"}
          </Pill>
          {a && <span className="text-xs text-muted-foreground">{a.network || a.platform} · {a.country || a.countries?.join(" ")} · {a.is_active ? "đang chạy" : "đã dừng"} · {fmt.date(a.first_seen_at)} → {fmt.date(a.last_seen_at)}</span>}
        </div>
        {a && <div className="font-medium">{a.title || "(không tiêu đề)"}</div>}
        {a?.text && <p className="line-clamp-2 text-xs text-muted-foreground">{a.text}</p>}
        <div className="flex flex-wrap gap-3 text-xs">
          {a?.product_id && <Link href={`/products/${a.product_id}`} className="text-series-1 hover:underline">Sản phẩm SpyAds #{a.product_id}</Link>}
          {a?.snapshot_url && <a href={a.snapshot_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-series-1">Ad Library<ExternalLink className="size-3" /></a>}
          {a?.landing_url && <a href={a.landing_url} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-80 items-center gap-1 truncate text-series-1">{a.landing_url}<ExternalLink className="size-3" /></a>}
          <span className="text-muted-foreground">bằng chứng: {JSON.stringify(m.evidence)}</span>
        </div>
      </div>
      <div className="flex gap-1">
        <Button size="xs" variant="outline" disabled={busy || m.review_status === "confirmed"} onClick={() => onReview("confirmed")}>Đúng</Button>
        <Button size="xs" variant="outline" disabled={busy || m.review_status === "rejected"} onClick={() => onReview("rejected")}>Sai</Button>
      </div>
    </div>
  );
}
