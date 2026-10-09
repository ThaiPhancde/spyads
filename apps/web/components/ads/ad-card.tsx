"use client";
import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Check, ExternalLink, Eye, Heart, Link2, Maximize2, RotateCcw, Trophy } from "lucide-react";
import { AdDetailSheet, FUNNEL, PLATFORM, TIER, ago, marketsOf } from "@/components/ads/ad-detail-sheet";
import { CreativeMedia, DownloadButton } from "@/components/media";
import { Hint, NetworkBadge, usePlatforms } from "@/components/platforms";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { fmt } from "@/lib/api";
import { cn } from "@/lib/utils";

/** One ad from any ad library (or one viral organic post), laid out like an Ad Library card: network, status,
 *  library id, start date, placements, advertiser, copy, media (single / carousel) and the link bar with the CTA. */
export function AdCard({ ad }: { ad: any }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(false);
  const text: string = ad.text || "";
  const long = text.length > 220;
  const media = ad.creatives || [];
  const fn = FUNNEL[ad.funnel] || FUNNEL.other;
  const { byKey } = usePlatforms();
  const organic = ad.channel === "organic";
  return (
    <Card className="gap-0 overflow-hidden py-0">
      <div className="space-y-1.5 px-3 pt-3 text-xs">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-2">
            <NetworkBadge network={ad.network} byKey={byKey} />
            {!organic && (
              <span className={cn("inline-flex items-center gap-1.5 font-medium", ad.active ? "text-good-text" : "text-muted-foreground")}>
                <span className={cn("inline-block size-2 rounded-full", ad.active ? "bg-good" : "bg-muted-foreground")} />
                {ad.active ? "Đang chạy" : "Đã dừng"}
              </span>
            )}
          </span>
          <span className="tnum max-w-[45%] truncate text-muted-foreground" title={ad.external_id}>ID: {ad.external_id}</span>
        </div>
        {organic && (ad.views || ad.likes) ? (
          <div className="flex items-center gap-1 text-ink2"><Eye className="size-3" /> {fmt.n(ad.views)} lượt xem{ad.likes ? <> · <Heart className="size-3" /> {fmt.n(ad.likes)}</> : null}</div>
        ) : null}
        <div className="flex flex-wrap items-center gap-1.5">
          {ad.force_tier && (
            <Hint tip="Độ mạnh: ngày chạy + số phiên bản + số nền tảng">
              <Badge variant="secondary" className={cn("tnum", ad.force_tier === "legendary" && "text-good-text")}>
                {(ad.force_tier === "legendary" || ad.force_tier === "strong") && <Trophy />}{TIER[ad.force_tier] || ad.force_tier} {Math.round(ad.force_score)}
              </Badge>
            </Hint>
          )}
          {ad.last_verified && <span className="inline-flex items-center gap-1 text-muted-foreground" title={fmt.dt(ad.last_verified)}><Check className="size-3" /> xác minh {ago(ad.last_verified)}</span>}
          {ad.reactivated_at && <span className="inline-flex items-center gap-1 text-good-text"><RotateCcw className="size-3" /> chạy lại</span>}
          {!ad.active && ad.inactive_at && <span className="text-muted-foreground">dừng {fmt.date(ad.inactive_at)}</span>}
        </div>
        {(ad.impressions_text || ad.spend_text || ad.reach) && (
          <div className="text-ink2">{[ad.impressions_text && `Hiển thị: ${ad.impressions_text}`, ad.spend_text && `Chi tiêu: ${ad.spend_text}`, ad.reach && `Reach EU: ${fmt.n(ad.reach)}`].filter(Boolean).join(" · ")}</div>
        )}
        <div className="text-ink2">
          {organic ? "Thu thập" : "Bắt đầu chạy"}: {fmt.date(ad.first_seen)}{ad.days_running !== null && ad.days_running !== undefined ? ` · ${ad.days_running} ngày` : ""}
        </div>
        <div className="flex flex-wrap gap-1">
          {(ad.platforms || []).map((p: string) => <Badge key={p} variant="secondary" className="font-normal">{PLATFORM[p] || p}</Badge>)}
          {marketsOf(ad).slice(0, 6).map((m: string) => <Badge key={m} variant="outline" className="font-normal">{m}</Badge>)}
        </div>
        {ad.variants > 1 && <div className="text-ink2">Quảng cáo này có {ad.variants} phiên bản</div>}
        {ad.snapshot_url && (
          <Button variant="outline" size="sm" className="mt-1 w-full" asChild>
            <a href={ad.snapshot_url} target="_blank" rel="noreferrer">{organic ? "Mở bài gốc" : "Xem chi tiết quảng cáo"} <ExternalLink /></a>
          </Button>
        )}
      </div>
      <Separator className="mx-3 my-2.5 w-auto!" />

      <div className="flex items-center gap-2 px-3">
        <Avatar className="size-8"><AvatarFallback className="text-sm font-semibold">{(ad.advertiser || "?").slice(0, 1).toUpperCase()}</AvatarFallback></Avatar>
        <div className="min-w-0">
          {ad.advertiser_url ? <a href={ad.advertiser_url} target="_blank" rel="noreferrer" className="block truncate text-sm font-semibold hover:underline">{ad.advertiser}</a>
            : <div className="truncate text-sm font-semibold">{ad.advertiser || "—"}</div>}
          <div className="text-[11px] text-muted-foreground">{organic ? "Bài viral (organic — không phải quảng cáo)" : "Được tài trợ"}</div>
        </div>
      </div>

      {text && (
        <div className="px-3 pt-2 text-[13px] leading-snug break-words whitespace-pre-wrap">
          {open || !long ? text : `${text.slice(0, 220)}…`}
          {long && <Button variant="link" size="xs" className="h-auto px-1 text-link" onClick={() => setOpen(!open)}>{open ? "Thu gọn" : "Xem thêm"}</Button>}
        </div>
      )}

      <div className="px-3 pt-2">
        {media.length === 0 ? (
          <div className="flex h-40 items-center justify-center rounded-lg bg-muted px-3 text-center text-xs text-muted-foreground">
            {ad.media_type === "text" ? "Quảng cáo dạng chữ (Search) — không có ảnh / video." : "Nguồn không trả file media (catalog / quảng cáo động). Xem ở “Xem chi tiết quảng cáo”."}
          </div>
        ) : media.length === 1 ? (
          <CreativeMedia c={media[0]} height={320} />
        ) : (
          <div className="flex snap-x gap-2 overflow-x-auto pb-2 [scrollbar-width:thin]">
            {media.map((c: any) => (
              <div key={c.id} className="w-[230px] shrink-0 snap-start"><CreativeMedia c={c} height={280} /></div>
            ))}
          </div>
        )}
        {media.length > 1 && <div className="text-[11px] text-muted-foreground">{media.length} ảnh / video trong quảng cáo — vuốt ngang để xem</div>}
      </div>

      {(ad.landing_domain || ad.title || ad.cta) && (
        <div className="mx-3 mt-2 flex items-center justify-between gap-2 rounded-lg bg-muted px-3 py-2">
          <div className="min-w-0">
            {ad.landing_domain && (ad.landing_url
              ? <a href={ad.landing_url} target="_blank" rel="noreferrer" title={ad.landing_url} className="flex items-center gap-1 truncate text-[10px] tracking-wide text-link uppercase hover:underline"><Link2 className="size-3 shrink-0" /> {ad.landing_domain}</a>
              : <div className="truncate text-[10px] tracking-wide text-muted-foreground uppercase">{ad.landing_domain}</div>)}
            {ad.title && <div className="truncate text-[13px] font-semibold">{ad.title}</div>}
          </div>
          {ad.cta && (ad.landing_url
            ? <Button variant="outline" size="xs" className="shrink-0" asChild><a href={ad.landing_url} target="_blank" rel="noreferrer">{ad.cta}</a></Button>
            : <Badge variant="outline" className="shrink-0">{ad.cta}</Badge>)}
        </div>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-1.5 px-3 py-3 text-[11px]">
        <Hint tip={fn[1]}><Badge variant="secondary">{fn[0]}</Badge></Hint>
        {ad.hook && ad.hook !== "statement" && <Badge variant="outline" className="font-normal">hook: {ad.hook}</Badge>}
        {ad.angle && ad.angle !== "general" && <Badge variant="outline" className="font-normal">angle: {ad.angle}</Badge>}
        {ad.product && <Link href={`/products/${ad.product.id}`} className="inline-flex max-w-[160px] items-center gap-1 truncate text-link hover:underline" title={ad.product.name}>Sản phẩm <ArrowRight className="size-3" /></Link>}
        <span className="flex-1" />
        <Button variant="ghost" size="xs" onClick={() => setDetail(true)} title="Xem đầy đủ nội dung, media và thông tin quảng cáo"><Maximize2 /> Chi tiết</Button>
        {media.filter((c: any) => c.download).slice(0, 3).map((c: any) => (
          <DownloadButton key={c.id} c={{ ...c, size: undefined }} small />
        ))}
      </div>
      {detail && <AdDetailSheet ad={ad} open={detail} onOpenChange={setDetail} />}
    </Card>
  );
}
