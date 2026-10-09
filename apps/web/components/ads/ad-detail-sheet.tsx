"use client";
import Link from "next/link";
import { ArrowRight, ExternalLink } from "lucide-react";
import { CreativeMedia, DownloadButton } from "@/components/media";
import { NetworkBadge, usePlatforms } from "@/components/platforms";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { fmt } from "@/lib/api";

export const PLATFORM: Record<string, string> = {
  facebook: "Facebook", instagram: "Instagram", messenger: "Messenger", audience_network: "Audience Network",
  threads: "Threads", whatsapp: "WhatsApp", tiktok: "TikTok", snapchat: "Snapchat",
};
export const FUNNEL: Record<string, [string, string]> = {
  mess: ["💬 MKT Mess", "Quảng cáo dẫn vào inbox / WhatsApp"], ladi: ["🧾 MKT Ladi", "Quảng cáo dẫn tới landing page / shop"],
  form: ["📝 Form", "Quảng cáo thu lead bằng form"], app: ["📱 App", "Quảng cáo cài app"], other: ["Khác", ""],
};
export const TIER: Record<string, string> = { legendary: "Huyền thoại", strong: "Mạnh", regular: "Thường", testing: "Đang test" };

export function ago(ts: string): string {
  const m = Math.max(0, (Date.now() - new Date(ts + (ts.endsWith("Z") ? "" : "Z")).getTime()) / 60000);
  return m < 60 ? `${Math.round(m)} phút trước` : m < 1440 ? `${Math.round(m / 60)} giờ trước` : `${Math.round(m / 1440)} ngày trước`;
}

/** Markets of an ad as display labels (ALL → Toàn cầu). */
export const marketsOf = (ad: any): string[] => (ad.markets?.length ? ad.markets : ad.country ? [ad.country] : []).map((m: string) => (m === "ALL" ? "Toàn cầu" : m));

function Row({ k, v }: { k: string; v: any }) {
  if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return null;
  return (
    <div className="flex gap-3 text-sm">
      <span className="w-32 shrink-0 text-muted-foreground">{k}</span>
      <span className="min-w-0 break-words">{Array.isArray(v) ? v.join(", ") : v}</span>
    </div>
  );
}

function ExtLink({ href, children }: { href?: string | null; children: React.ReactNode }) {
  if (!href) return null;
  return (
    <Button variant="outline" size="sm" asChild>
      <a href={href} target="_blank" rel="noreferrer">{children} <ExternalLink /></a>
    </Button>
  );
}

/** Everything about one ad: all creatives at full size, every field and the external links. */
export function AdDetailSheet({ ad, open, onOpenChange }: { ad: any; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { byKey } = usePlatforms();
  const media: any[] = ad.creatives || [];
  const organic = ad.channel === "organic";
  const fn = FUNNEL[ad.funnel] || FUNNEL.other;
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-xl">
        <SheetHeader className="border-b">
          <SheetTitle className="truncate">{ad.advertiser || "—"}</SheetTitle>
          <SheetDescription className="flex flex-wrap items-center gap-2">
            <NetworkBadge network={ad.network} byKey={byKey} />
            {!organic && <Badge variant="outline" className={ad.active ? "text-good-text" : "text-muted-foreground"}>{ad.active ? "Đang chạy" : "Đã dừng"}</Badge>}
            <span className="tnum truncate">ID {ad.external_id}</span>
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="h-[calc(100vh-5rem)]">
          <div className="space-y-4 p-4">
            <div className="flex flex-wrap gap-2">
              <ExtLink href={ad.snapshot_url}>{organic ? "Bài gốc" : "Trang thư viện quảng cáo"}</ExtLink>
              <ExtLink href={ad.advertiser_url}>Trang advertiser</ExtLink>
              <ExtLink href={ad.landing_url}>{ad.landing_domain || "Landing page"}</ExtLink>
              {ad.product && <Button variant="outline" size="sm" asChild><Link href={`/products/${ad.product.id}`}>Sản phẩm: {ad.product.name} <ArrowRight /></Link></Button>}
            </div>

            {media.length > 0 && (
              <div className="space-y-3">
                {media.map((c: any, i: number) => (
                  <div key={c.id} className="space-y-1.5">
                    <CreativeMedia c={c} height={360} />
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span>{i + 1}/{media.length} · {c.type}{c.duration ? ` · ${Math.round(c.duration)}s` : ""}{c.width && c.height ? ` · ${c.width}×${c.height}` : ""}</span>
                      <DownloadButton c={c} small />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {ad.text && <p className="text-sm leading-snug break-words whitespace-pre-wrap">{ad.text}</p>}
            {(ad.title || ad.cta) && (
              <div className="rounded-lg bg-muted px-3 py-2 text-sm">
                {ad.title && <div className="font-semibold">{ad.title}</div>}
                {ad.cta && <div className="text-muted-foreground">CTA: {ad.cta}</div>}
              </div>
            )}
            <Separator />
            <div className="space-y-1.5">
              <Row k="Loại" v={fn[0]} />
              <Row k="Độ mạnh" v={ad.force_tier && `${TIER[ad.force_tier] || ad.force_tier} · ${Math.round(ad.force_score || 0)}`} />
              <Row k={organic ? "Thu thập" : "Bắt đầu chạy"} v={`${fmt.date(ad.first_seen)}${ad.days_running != null ? ` · ${ad.days_running} ngày` : ""}`} />
              <Row k="Xác minh" v={ad.last_verified && `${fmt.dt(ad.last_verified)} (${ago(ad.last_verified)})`} />
              <Row k="Dừng" v={!ad.active && ad.inactive_at ? fmt.date(ad.inactive_at) : null} />
              <Row k="Chạy lại" v={ad.reactivated_at && fmt.dt(ad.reactivated_at)} />
              <Row k="Hiển thị" v={ad.impressions_text} />
              <Row k="Chi tiêu" v={ad.spend_text} />
              <Row k="Reach EU" v={ad.reach && fmt.n(ad.reach)} />
              <Row k="Lượt xem" v={ad.views && fmt.n(ad.views)} />
              <Row k="Thích" v={ad.likes && fmt.n(ad.likes)} />
              <Row k="Nền tảng" v={(ad.platforms || []).map((p: string) => PLATFORM[p] || p)} />
              <Row k="Thị trường" v={marketsOf(ad)} />
              <Row k="Phiên bản" v={ad.variants > 1 ? ad.variants : null} />
              <Row k="Media" v={ad.media_type} />
              <Row k="Hook" v={ad.hook} />
              <Row k="Angle" v={ad.angle} />
              <Row k="Landing" v={ad.landing_url} />
              <Row k="Mã nội bộ" v={ad.id} />
            </div>
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
