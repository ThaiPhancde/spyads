"use client";
import { FollowButton } from "@/components/workspace/actions";
import { NetworkStrip, usePlatforms } from "@/components/platforms";
import Link from "next/link";
import { useState } from "react";
import { Clock, Copy, Download, ExternalLink, ImageOff, Link2, Pin, Play, RotateCw, ScanSearch } from "lucide-react";
import { toast } from "sonner";
import { api, fmt, useAction } from "@/lib/api";
import { getUser } from "@/lib/realtime";
import { RecBadge } from "@/components/ui";
import { PriceTag } from "@/components/pricing/price-tag";
import { ProductName } from "@/components/product-name";
import { visualHref } from "@/components/visual/global-image-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const FUNNEL_LABEL: Record<string, string> = { mess: "Mess", ladi: "Ladi", form: "Form", app: "App", other: "Khác", shop: "Shop" };

export function FunnelChips({ mix }: { mix: Record<string, number> }) {
  const total = Object.values(mix || {}).reduce((a, b) => a + b, 0);
  if (!total) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {Object.entries(mix).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
        <Badge key={k} variant="secondary" className="rounded px-1.5 text-[10px] text-ink2">
          {FUNNEL_LABEL[k] || k} {Math.round((v / total) * 100)}%
        </Badge>
      ))}
    </span>
  );
}

/** Muted placeholder box with an icon + message, same footprint as the media. */
function Placeholder({ height, icon: Icon, children }: { height: number; icon: typeof Clock; children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-lg bg-muted px-3 text-center text-xs text-muted-foreground" style={{ height }}>
      <Icon className="size-5 opacity-60" />
      {children}
    </div>
  );
}

/** Video / image player for a stored creative + download button. */
export function CreativeMedia({ c, height = 220, autoPlay = false }: { c: any; height?: number; autoPlay?: boolean }) {
  if (!c) return <Placeholder height={height} icon={Clock}>Media đang chờ tải…</Placeholder>;
  if (c.type === "image" && c.status !== "stored" && c.thumb) {  // ≤480 px thumbnail saved at collect time (or on first view)
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={c.thumb} alt="" loading="lazy" decoding="async" className="w-full rounded-lg bg-muted object-contain" style={{ height }} />;
  }
  if (c.stream_url) return <SourceVideo c={c} height={height} />;  // not in the vault: platform thumbnail, source streamed on Play
  if (c.status !== "stored" && c.thumb) return <ExpiredCover c={c} height={height} />;  // archived / expired / failed
  if (c.status !== "stored") {
    return (
      <Placeholder height={height} icon={c.status === "pending" ? Clock : ImageOff}>
        <span>{c.type === "video" && !c.origin_alive ? "Link video gốc đã hết hạn" : c.status === "pending" && c.type === "video" ? "Chưa có ảnh bìa — link video gốc đã hết hạn" : c.status === "pending" ? "Link ảnh gốc đã hết hạn" : c.status === "archived" ? "Đã dọn khỏi kho" : c.status === "expired" ? "Link gốc đã hết hạn" : "Tải media lỗi"}</span>
        {c.error && <span className="text-[10px]">{c.error}</span>}
        {c.ad?.snapshot_url && <a href={c.ad.snapshot_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-link hover:underline">Xem quảng cáo gốc <ExternalLink className="size-3" /></a>}
      </Placeholder>
    );
  }
  return c.type === "video" ? (
    <video src={c.url} poster={c.thumb || undefined} controls preload="none" playsInline autoPlay={autoPlay} muted={autoPlay}
           className="w-full rounded-lg bg-black object-contain" style={{ height }} />
  ) : (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={c.url} alt="" loading="lazy" className="w-full rounded-lg bg-muted object-contain" style={{ height }} />
  );
}

/** Still show the frame so MKT can tell the product; a dead TikTok link can be re-fetched by ad id (~10 s) and streamed. */
function ExpiredCover({ c, height }: { c: any; height: number }) {
  const [fresh, setFresh] = useState<any>(null);
  const act = useAction();
  if (fresh?.stream_url) return <SourceVideo c={fresh} height={height} />;
  const dead = c.type === "video" && (c.status === "expired" || c.origin_alive === false);
  const refresh = () => act.run(async () => setFresh(await api(`/creatives/${c.id}/retry`, { method: "POST" })));
  return (
    <div className="relative overflow-hidden rounded-lg bg-muted" style={{ height }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={c.thumb} alt="" loading="lazy" className="h-full w-full object-contain opacity-70" />
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-1 bg-black/65 px-2 py-1.5 text-center text-[11px] text-white">
        {c.status === "archived" ? "Video không lưu trong kho — chỉ còn ảnh bìa + thông tin" : dead ? "Link video gốc đã hết hạn" : "Video chưa tải được — đang hiện ảnh bìa"}
        {dead && c.source?.includes("tiktok") && (
          <Button type="button" size="xs" variant="secondary" disabled={act.busy} onClick={refresh}>
            <RotateCw className={cn(act.busy && "animate-spin")} /> {act.busy ? "Đang lấy link mới…" : "Lấy link mới"}
          </Button>
        )}
        {act.error && <span className="text-[10px] text-red-300">{act.error}</span>}
      </div>
    </div>
  );
}

/** Thumbnail first; the source video (light rendition / MP4) is fetched only after Play. */
export function SourceVideo({ c, height }: { c: any; height: number }) {
  const [play, setPlay] = useState(false);
  const [failed, setFailed] = useState(false);
  const page = c.ad?.snapshot_url;
  if (failed) return (
    <Placeholder height={height} icon={ImageOff}>
      <span>Không phát được video từ link gốc</span>
      {page && <a href={page} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-link hover:underline">Mở trang quảng cáo gốc <ExternalLink className="size-3" /></a>}
    </Placeholder>
  );
  if (!play) return (
    <button type="button" onClick={() => setPlay(true)} aria-label="Phát video"
            className="relative flex w-full items-center justify-center overflow-hidden rounded-lg bg-muted" style={{ height }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {c.thumb && <img src={c.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" className="absolute inset-0 h-full w-full object-contain" />}
      <span className="relative flex size-12 items-center justify-center rounded-full bg-black/65 text-white"><Play className="size-5 fill-current" /></span>
      {c.duration ? <span className="absolute right-2 bottom-2 rounded bg-black/65 px-1.5 py-0.5 text-[10px] text-white">{Math.round(c.duration)}s</span> : null}
    </button>
  );
  return (
    <video src={c.stream_url} poster={c.thumb || undefined} controls autoPlay playsInline onError={() => setFailed(true)}
           className="w-full rounded-lg bg-black object-contain" style={{ height }} />
  );
}

/** Not in the vault (videos never are): open / copy the original link. */
function SourceActions({ c, small }: { c: any; small?: boolean }) {
  const size = small ? "xs" : "sm";
  const copy = () => navigator.clipboard?.writeText(c.origin_url).then(() => toast.success("Đã copy URL"), () => toast.error("Copy lỗi"));
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {c.origin_alive && <Button asChild variant="outline" size={size}><a href={c.origin_url} target="_blank" rel="noreferrer" title="Mở / tải video gốc từ nền tảng"><ExternalLink /> Video gốc</a></Button>}
      <Button type="button" variant="outline" size={size} onClick={copy} title={c.origin_url}><Copy /> Copy URL</Button>
    </span>
  );
}

export function DownloadButton({ c, small }: { c: any; small?: boolean }) {
  if (!c?.download) return c?.origin_url ? <SourceActions c={c} small={small} /> : null;
  return (
    <Button asChild variant="outline" size={small ? "xs" : "sm"}>
      <a href={c.download} download={c.file_name} title="Tải file gốc về máy">
        <Download /> Tải {c.type === "video" ? "video" : "ảnh"}{c.size ? ` · ${(c.size / 1048576).toFixed(1)}MB` : ""}
      </a>
    </Button>
  );
}

export const VOTES: [string, string][] = [["LOVE", "🔥"], ["TEST", "👍"], ["WATCH", "👀"], ["NORMAL", "😐"], ["SKIP", "👎"]];

export function VoteBar({ productId, market, onVoted, compact }: { productId: number; market?: string; onVoted?: () => void; compact?: boolean }) {
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const vote = async (decision: string) => {
    const u = getUser();
    if (!u.name) { setErr("Nhập tên ở thanh bên trái trước khi vote"); return; }
    try {
      await api(`/products/${productId}/vote`, { method: "POST", body: JSON.stringify({ user: u.name, team: u.team || null, market: market || null, decision, reasons: [] }) });
      setDone(decision); setErr(""); onVoted?.();
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div>
      <div className="flex gap-1">
        {VOTES.map(([d, icon]) => (
          <Button key={d} type="button" variant="outline" size={compact ? "xs" : "sm"} onClick={() => vote(d)} title={d} aria-pressed={done === d}
                  className={cn(done === d && "border-series-1 bg-muted")}>
            {icon}{!compact && <span className="text-[11px]">{d}</span>}
          </Button>
        ))}
      </div>
      {err && <div className="mt-1 text-[11px] text-critical">{err}</div>}
    </div>
  );
}

const hostOf = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };

const QUADRANT: Record<string, string> = { BREAKOUT: "🚀 Breakout", EXPERIMENTAL: "💎 Experimental", STABLE_WINNER: "🏛 Stable winner", LOW_SIGNAL: "· Low signal" };

/** Product card for search / discovery grids: video first. */
export function ProductMediaCard({ p, onVoted }: { p: any; onVoted?: () => void }) {
  const v = p.vector || {};
  const { byKey } = usePlatforms();
  return (
    <Card className="flex flex-col gap-0 overflow-hidden py-0">
      <div className="p-2 pb-0"><CreativeMedia c={p.cover} height={260} /></div>
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <ProductName p={p} href={`/products/${p.id}`} className="flex-1" />
          <RecBadge rec={p.recommendation} />
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 text-[11px]">
          <dt className="text-muted-foreground">Advertiser</dt><dd className="truncate" title={p.advertiser || ""}>{p.advertiser || "—"}{p.advertisers > 1 ? <span className="text-muted-foreground"> +{p.advertisers - 1}</span> : null}</dd>
          <dt className="text-muted-foreground">Nguồn</dt><dd className="truncate"><NetworkStrip networks={p.networks} byKey={byKey} /></dd>
          <dt className="text-muted-foreground">Danh mục</dt><dd className="truncate">{p.category || "—"} · {p.product_code} · {(p.markets || []).join(", ") || p.country || "—"}</dd>
        </dl>
        <div className="flex flex-wrap gap-2"><FollowButton id={p.advertiser_id} />{p.advertiser_id && <Link className="self-center text-xs underline" href={`/competitors/${p.advertiser_id}`}>Chi tiết advertiser</Link>}</div>
        <div className="flex flex-wrap items-center gap-1">
          {p.classification && <Badge variant="secondary" className="rounded px-1.5 text-[11px]">{QUADRANT[p.classification] || p.classification}</Badge>}
          {p.hidden_winner && <Badge variant="outline" className="rounded px-1.5 text-[11px]">💎 Hidden Winner</Badge>}
          <FunnelChips mix={p.funnel_mix} />
        </div>
        <div className="grid grid-cols-4 gap-1 text-center text-[11px]">
          {[["Opp.", v.opportunity ?? p.opportunity], ["Wave", v.wave_potential], ["Novelty", v.novelty], ["Creative", v.creative_potential]].map(([l, x]) => (
            <div key={l as string} className="rounded bg-muted py-1">
              <div className="tnum text-sm font-semibold">{fmt.n(x)}</div><div className="text-muted-foreground">{l}</div>
            </div>
          ))}
        </div>
        {p.landing_url && (
          <a href={p.landing_url} target="_blank" rel="noreferrer" title={p.landing_url}
             className="inline-flex min-w-0 items-center gap-1 text-[11px] text-link hover:underline"><Link2 className="size-3 shrink-0" /><span className="truncate">{hostOf(p.landing_url)}</span><ExternalLink className="size-3 shrink-0" /></a>
        )}
        <PriceTag subject={`product:${p.id}`} fallback={{ price: p.price, currency: p.currency }} />
        {(p.listings > 0 || p.organic_views > 0) && (
          <div className="flex flex-wrap gap-x-2 text-[11px] text-ink2">
            {p.reviews_total ? <span>★ {fmt.n(p.reviews_total)} đánh giá</span> : null}
            {p.sold_total ? <span>🛒 {fmt.n(p.sold_total)} đã bán</span> : null}
            {p.supplier_price_usd ? <span title="Giá thấp nhất trên AliExpress — mốc giá nhập">💲 nhập ~${p.supplier_price_usd}</span> : null}
            {p.organic_views ? <span className="inline-flex items-center gap-0.5"><Play className="size-3" /> {fmt.n(p.organic_views)} view viral</span> : null}
          </div>
        )}
        <div className="text-[11px] text-ink2">
          {p.advertisers} advertiser · {p.active_ads} ads active · +{p.new_ads_7d} ads 7d
          {p.matched_ads !== undefined && <> · {p.matched_ads} ads khớp</>}
          {p.matched_keywords?.length > 1 && <> · khớp {p.matched_keywords.length} từ: {p.matched_keywords.join(", ")}</>}
          {p.match_tier && p.match_tier !== "exact" && <> · <span title="Khớp qua bản dịch / từ đồng nghĩa (synonym) hoặc khái niệm liên quan (related)">khớp {p.match_tier === "synonym" ? "từ đồng nghĩa / bản dịch" : "liên quan"}</span></>}
        </div>
        <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
          <VoteBar productId={p.id} compact onVoted={onVoted} />
          <span className="inline-flex items-center gap-1">
            <Button asChild variant="outline" size="xs" title="Tìm sản phẩm giống bằng ảnh"><Link href={visualHref({ product_id: p.id, mode: "similar_product" })}><ScanSearch /> Tương tự</Link></Button>
            <DownloadButton c={p.cover} small />
          </span>
        </div>
      </div>
    </Card>
  );
}

export function PinButton({ c, onChange }: { c: any; onChange?: () => void }) {
  const [pinned, setPinned] = useState<boolean>(!!c.pinned);
  const act = useAction();
  if (c.status !== "stored") return null;
  const toggle = () => act.run(async () => {
    const next = !pinned;
    await api(`/creatives/${c.id}/pin?pinned=${next}`, { method: "POST" });
    setPinned(next); onChange?.();
  });
  return (
    <Button variant="outline" size="xs" onClick={toggle} disabled={act.busy} aria-pressed={pinned}
            title={act.error ? `Lỗi: ${act.error}` : pinned ? "Đang ghim — không bị tự dọn" : "Ghim để không bị tự dọn sau vài ngày"}
            className={cn(act.error ? "border-critical text-critical" : pinned && "border-series-1 bg-muted")}>
      <Pin className={cn(pinned && "fill-current")} /> {act.error ? "Ghim lỗi" : pinned ? "Đã ghim" : "Ghim"}
    </Button>
  );
}
