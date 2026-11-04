"use client";
import Link from "next/link";
import { Fragment, ReactNode, useEffect, useState } from "react";
import { BadgeEuro, Camera, MessageCircle, RefreshCw, Share2, ThumbsDown, ThumbsUp, Play, Truck } from "lucide-react";
import { visualHref } from "@/components/visual/global-image-search";
import { toast } from "sonner";
import { BarList, Breakdown, Funnel, TrendChart } from "@/components/charts";
import { Card, Empty, LIFECYCLE_TONE, Loading, PageHeader, Pill, RecBadge, SELLER_SCOPE, sellerLabel, Stat } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, fmt, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { CommentsPanel } from "@/components/CommentsPanel";
import { DiscoveryPanel } from "@/components/DiscoveryPanel";
import { NetworkBadge, NetworkStrip, usePlatforms } from "@/components/platforms";
import { ProductCluster } from "@/components/product-cluster";
import { ProductName } from "@/components/product-name";
import { PriceTag } from "@/components/pricing/price-tag";
import { usePrice } from "@/lib/prices";
import { WinChecklist } from "@/components/win-checklist";

/** Where else the product shows up: networks, China-source / store listings (price / reviews / sold) and the
 *  supplier-price margin estimate (lowest 1688 / Taobao / AliExpress price × 1.6 ≈ landed cost: shipping + fees not included). */
function CrossPlatformCard({ productId, f }: { productId: number; f: any }) {
  const { byKey } = usePlatforms();
  const { data } = useApi(`/ads?product_id=${productId}&channel=commerce&sort=price&limit=50`);
  const rows: any[] = data?.rows || [];
  const sell = f.avg_price;
  const margin = f.supplier_price_usd && sell ? 1 - (f.supplier_price_usd * 1.6) / sell : null;
  return (
    <Card title="Đa nền tảng — sản phẩm xuất hiện ở đâu" className="xl:col-span-2" pad={false}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 pb-3 text-sm">
        <NetworkStrip networks={f.networks} byKey={byKey} />
        {f.reviews_total ? <span><b>{fmt.n(f.reviews_total)}</b> đánh giá{f.rating_avg ? ` · ★ ${f.rating_avg}` : ""}</span> : null}
        {f.sold_total ? <span><b>{fmt.n(f.sold_total)}</b> đã bán</span> : null}
        {f.organic_views ? <span><b>{fmt.n(f.organic_views)}</b> view viral</span> : null}
        {f.supplier_price_usd ? <span title="Giá nguồn thấp nhất (1688 / Taobao / AliExpress) — mốc giá nhập">Giá nhập ~<b>${f.supplier_price_usd}</b></span> : null}
        {margin !== null && <span title="1 − giá nhập × 1.6 / giá bán (chưa gồm ads, COD, hoàn)">Biên gộp ước tính <b className={cn(margin > 0.5 && "text-good-text")}>{Math.round(margin * 100)}%</b></span>}
      </div>
      {rows.length > 0 ? (
        <ScrollArea className="max-h-80 overflow-y-auto">
          <Table>
            <TableHeader><TableRow><TableHead>Nguồn</TableHead><TableHead>Listing</TableHead><TableHead className="text-right">Giá</TableHead><TableHead className="text-right">Đánh giá</TableHead><TableHead className="text-right">Đã bán</TableHead><TableHead>Seller</TableHead></TableRow></TableHeader>
            <TableBody>{rows.map((l) => (
              <TableRow key={l.id}>
                <TableCell><NetworkBadge network={l.network} byKey={byKey} small /></TableCell>
                <TableCell className="max-w-md truncate text-xs"><a href={l.landing_url} target="_blank" rel="noreferrer" className="hover:underline">{l.title || l.text}</a></TableCell>
                <TableCell className="tnum text-right whitespace-nowrap">{fmt.n(l.price, 2)} {l.currency || ""}{l.original_price && l.original_price > l.price ? <div className="text-[10px] text-muted-foreground line-through">{fmt.n(l.original_price, 2)}</div> : null}</TableCell>
                <TableCell className="tnum text-right">{l.review_count ? `${fmt.n(l.review_count)}${l.rating ? ` · ★${l.rating}` : ""}` : "—"}</TableCell>
                <TableCell className="tnum text-right">{l.sold_count ? fmt.n(l.sold_count) : "—"}</TableCell>
                <TableCell className="max-w-[160px] truncate text-xs">{l.advertiser || "—"}</TableCell>
              </TableRow>
            ))}</TableBody>
          </Table>
        </ScrollArea>
      ) : <div className="px-4 pb-4 text-xs text-muted-foreground">Chưa thấy nguồn hàng. Tìm từ khoá ở “Tìm sản phẩm” để quét AliExpress (1688 / Taobao khi đã gắn Apify).</div>}
    </Card>
  );
}

/** Definition-list column: title + rows separated by dividers. */
function Col({ title, hint, empty, rows, children }: { title: ReactNode; hint?: ReactNode; empty?: ReactNode; rows: ReactNode[]; children?: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="mb-1 font-medium">{title} {hint && <span className="font-normal text-muted-foreground">{hint}</span>}</div>
      {rows.length ? <div className="divide-y">{rows}</div> : <div className="text-muted-foreground">{empty}</div>}
      {children}
    </div>
  );
}

/** First thing a marketer needs: what competitors actually SELL at (ad copy / landing page — never a marketplace median),
 *  how many ads that rests on, floor price per marketplace, landed cost → margin, what customers say, proof and ad age. */
function MktCard({ m, c, productId, onChange }: { m: any; c: any; productId: number; onChange: () => void }) {
  const { byKey } = usePlatforms();
  const [scan, setScan] = useState<{ busy: boolean; msg: string }>({ busy: false, msg: "" });
  const price = usePrice(`product:${productId}`, 10);
  useEffect(() => { if (price?.status === "done" && price.price_type !== "NOT_FOUND") onChange(); }, [price?.observed_at, price?.price_type, price?.status, onChange]);
  if (!m) return null;
  const e = m.engagement || {}, mp = m.marketplace || {}, ads = m.ads || {}, cov = m.price_coverage || {};
  const usd = (v: any) => (v === null || v === undefined ? "—" : `$${fmt.n(v, 2)}`);
  const covPct = cov.ads ? cov.priced / cov.ads : 0;
  const covClass = covPct >= 0.7 ? "text-good-text" : covPct >= 0.4 ? undefined : "text-critical";
  const SRC: Record<string, string> = { landing: "landing page", ad_text: "nội dung ad", source: "nguồn" };
  const doScan = async () => {
    setScan({ busy: true, msg: "Đang quét landing page + review…" });
    try {
      const r = await api(`/products/${productId}/scan`, { method: "POST" });
      const s = r.stats;
      const msg = `Xong: ${s.landing_priced} giá từ landing · ${s.text_priced} từ ad text · ${s.urls_fetched} trang (${s.landing_failed} lỗi) · ${s.reviews_added} review mới`;
      setScan({ busy: false, msg });
      toast.success("Đã quét giá & review", { description: msg });
      onChange();
    } catch (err: any) { setScan({ busy: false, msg: `Lỗi: ${err.message}` }); toast.error("Quét thất bại", { description: err.message }); }
  };
  const Item = ({ l, v, sub }: { l: ReactNode; v: any; sub?: any }) => (
    <div className="min-w-0"><div className="text-[11px] text-muted-foreground">{l}</div><div className="tnum text-lg leading-tight font-semibold">{v}</div>{sub && <div className="text-[11px] text-ink2">{sub}</div>}</div>
  );
  const sent = c?.analysed ? c.overall : null;
  const srcLabel = (k: string) => (k === "review" ? "AliExpress" : k === "landing_review" ? "landing" : k);
  return (
    <Card title="Thông tin cho MKT — giá, tương tác, bằng chứng bán" className="mb-4"
          action={<span className="flex items-center gap-2 text-xs"><span className="max-w-md truncate text-muted-foreground" title={scan.msg}>{scan.msg}</span>
            <Button variant="outline" size="xs" onClick={doScan} disabled={scan.busy}><RefreshCw className={cn(scan.busy && "animate-spin")} />{scan.busy ? "Đang quét…" : "Quét giá & review đối thủ"}</Button></span>}>
      <div className="mb-3"><PriceTag subject={`product:${productId}`} priority={10} /></div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-8">
        <Item l="Giá đối thủ đang bán" v={usd(m.sell_usd)}
              sub={cov.ads ? <span className={covClass}>{cov.priced}/{cov.ads} ads có giá ({Math.round(covPct * 100)}%){m.ad_price_range_usd ? ` · ${usd(m.ad_price_range_usd[0])} – ${usd(m.ad_price_range_usd[1])}` : ""}</span> : "chưa có ad"} />
        <Item l="Giá nguồn hàng" v={usd(m.supplier_usd)} sub="landed ≈ ×1.6" />
        <Item l="Biên gộp ước tính" v={m.margin_est === null || m.margin_est === undefined ? "—" : <span className={cn(m.margin_est > 0.5 && "text-good-text", m.margin_est < 0.3 && "text-critical")}>{Math.round(m.margin_est * 100)}%</span>} sub="giá đối thủ vs nhập ×1.6 · chưa gồm ads, COD, hoàn" />
        <Item l="Giá sàn (thấp nhất trên sàn)" v={usd(m.floor_usd)} sub={m.floor?.[0] && m.floor[0].network !== "aliexpress" ? byKey[m.floor[0].network]?.label : m.retail_usd ? `bán lẻ sàn TB ${usd(m.retail_usd)}` : "chưa thấy trên sàn"} />
        <Item l="Khách nói gì" v={sent ? <span className="inline-flex items-center gap-2"><span className="inline-flex items-center gap-1 text-good-text"><ThumbsUp className="size-4" />{Math.round(sent.positive * 100)}%</span><span className="inline-flex items-center gap-1 text-critical"><ThumbsDown className="size-4" />{Math.round(sent.negative * 100)}%</span></span> : "—"}
              sub={c?.analysed ? `${fmt.n(c.analysed)} review${c.rating_avg ? ` · ★ ${c.rating_avg}` : ""}` : "chưa có review / comment"} />
        <Item l={<span className="inline-flex items-center gap-1"><ThumbsUp className="size-3" /> Like / <MessageCircle className="size-3" /> Comment (ads)</span>} v={`${e.likes == null ? "N/A" : fmt.n(e.likes)} / ${e.comments == null ? "N/A" : fmt.n(e.comments)}`}
              sub={<span className="inline-flex items-center gap-1"><Share2 className="size-3" />{e.shares == null ? "N/A" : fmt.n(e.shares)} share{e.views ? <> · <Play className="size-3" />{fmt.n(e.views)} view</> : null}</span>} />
        <Item l="Đã bán (sàn)" v={mp.sold ? fmt.n(mp.sold) : "—"} sub={`${fmt.n(mp.listings)} listing${mp.reviews ? ` · ${fmt.n(mp.reviews)} đánh giá${mp.rating ? ` ★ ${mp.rating}` : ""}` : ""}`} />
        <Item l="Ads đang chạy" v={`${fmt.n(ads.active)} / ${fmt.n(ads.total)}`} sub={`${fmt.n(ads.advertisers)} advertiser · lâu nhất ${ads.longest_days ?? "—"} ngày`} />
      </div>
      <Separator className="my-4" />
      <div className="grid grid-cols-1 gap-4 text-xs md:grid-cols-2 xl:grid-cols-4">
        <Col title="Giá từng đối thủ" hint="· nguồn giá" empty="Chưa trích được giá nào từ ad — bấm “Quét giá & review đối thủ”."
             rows={(m.sell_by_advertiser || []).map((r: any, i: number) => (
               <div key={i} className="flex items-center justify-between gap-2 py-1">
                 <a href={r.url || undefined} target="_blank" rel="noreferrer" className="flex-1 truncate hover:underline" title={r.domain || ""}>{r.advertiser || r.domain || "—"} <span className="text-muted-foreground">· {r.ads} ads</span></a>
                 <span className="text-muted-foreground">{SRC[r.source] || r.source}</span>
                 <span className="tnum font-medium whitespace-nowrap">{fmt.n(r.price, 2)} {r.currency || ""} <span className="text-muted-foreground">≈ {usd(r.usd)}</span></span>
               </div>))}>
          {cov.ads ? <div className="mt-1 text-muted-foreground">{cov.inbox} ads dẫn về inbox / app (không có trang giá) · {cov.unchecked} landing page chưa quét{Object.keys(cov.sources || {}).length ? ` · nguồn: ${Object.entries(cov.sources).map(([k, v]) => `${SRC[k] || k} ${v}`).join(", ")}` : ""}</div> : null}
        </Col>
        <Col title="Giá sàn theo từng sàn" empty="Chưa thấy nguồn hàng — bấm “Tìm trực tiếp trên nguồn” để quét AliExpress."
             rows={(m.floor || []).map((r: any) => (
               <div key={r.network} className="flex items-center justify-between gap-2 py-1">
                 <NetworkBadge network={r.network} byKey={byKey} small />
                 <a href={r.url} target="_blank" rel="noreferrer" className="flex-1 truncate hover:underline" title={r.title}>{r.title}</a>
                 <span className="tnum font-medium whitespace-nowrap">{fmt.n(r.price, 2)} {r.currency || ""}</span>
               </div>))} />
        <Col title="Khách khen / chê" hint={c?.sources && Object.keys(c.sources).length ? `· ${Object.entries(c.sources).map(([k, v]) => `${srcLabel(k)} ${v}`).join(", ")}` : ""}
             empty="Meta Ad Library không công khai comment. Bấm “Quét giá & review” để lấy review AliExpress của nguồn hàng và review trên landing page đối thủ."
             rows={c?.analysed ? [
               ...(c.top_complaints || []).slice(0, 3).map((r: any) => <div key={`n${r.aspect}`} className="flex justify-between py-1"><span className="inline-flex items-center gap-1"><ThumbsDown className="size-3 text-critical" />{r.aspect}</span><span className="tnum text-muted-foreground">{r.count}</span></div>),
               ...(c.top_positive || []).slice(0, 2).map((r: any) => <div key={`p${r.aspect}`} className="flex justify-between py-1"><span className="inline-flex items-center gap-1"><ThumbsUp className="size-3 text-good-text" />{r.aspect}</span><span className="tnum text-muted-foreground">{r.count}</span></div>),
             ] : []}>
          {(c?.negative_examples || []).slice(0, 2).map((x: any, i: number) => <div key={i} className="mt-1 line-clamp-2 text-ink2" title={x.text}>“{x.text}” <span className="text-muted-foreground">{x.rating ? `★${x.rating}` : ""} {x.country || ""}</span></div>)}
        </Col>
        <Col title="Landing page đối thủ dùng" empty="Chưa có landing page (ads dẫn về inbox / form)."
             rows={(m.landing_domains || []).map((d: any) => (
               <div key={d.domain} className="flex justify-between py-1">
                 <a href={`https://${d.domain}`} target="_blank" rel="noreferrer" className="truncate hover:underline">{d.domain}</a><span className="tnum text-muted-foreground">{d.ads} ads</span>
               </div>))}>
          {ads.funnels && <div className="mt-2 text-muted-foreground">Funnel: {Object.entries(ads.funnels).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>}
          {m.top_ads?.length ? <div className="mt-2"><div className="mb-1 font-medium">Ad nhiều tương tác nhất</div><div className="divide-y">{m.top_ads.slice(0, 3).map((a: any) => (
            <a key={a.id} href={a.url || undefined} target="_blank" rel="noreferrer" className="block py-1 hover:underline">
              <span className="text-muted-foreground">{a.advertiser || "—"} · </span>
              <span className="inline-flex items-center gap-2"><span className="inline-flex items-center gap-1"><ThumbsUp className="size-3" />{fmt.n(a.likes)}</span><span className="inline-flex items-center gap-1"><MessageCircle className="size-3" />{fmt.n(a.comments)}</span><span className="inline-flex items-center gap-1"><Share2 className="size-3" />{fmt.n(a.shares)}</span></span>
              <div className="line-clamp-1 text-ink2">{a.text}</div>
            </a>))}</div></div> : null}
        </Col>
      </div>
    </Card>
  );
}

/** Video identity (design §2.2): the product's video run by the most pages — first sighting on any page (stopped ads
 *  included), pages a source re-confirmed, and who came in when. */
function VideoCard({ vf }: { vf: any }) {
  const v = vf?.primary;
  return (
    <Card title="Video">
      {!v ? <div className="text-sm text-muted-foreground">Chưa nhận diện được video (ảnh bìa creative chưa được hash hoặc là khung chung).</div> : (
        <>
          <div className="mb-3 grid grid-cols-3 gap-3 text-sm">
            <div><div className="text-[11px] text-muted-foreground">Thấy lần đầu</div><div className="tnum font-semibold">{fmt.date(v.video_first_seen)}</div></div>
            <div><div className="text-[11px] text-muted-foreground">Page đang chạy</div><div className="tnum font-semibold">{v.verified_live_pages} / {v.pages}</div></div>
            <div><div className="text-[11px] text-muted-foreground">Page mới 7 ngày</div><div className="tnum font-semibold">{v.new_pages_7d}</div></div>
          </div>
          <ScrollArea className="max-h-48 overflow-y-auto">
            <ol className="divide-y text-xs">
              {(v.entrants || []).map((e: any) => (
                <li key={e.page} className="flex items-center justify-between gap-2 py-1">
                  <span className="flex min-w-0 items-center gap-1.5">
                    {e.advertiser_id ? <Link href={`/competitors/${e.advertiser_id}`} className="truncate hover:underline">{e.name || e.page}</Link> : <span className="truncate">{e.name || e.page}</span>}
                    {e.new_this_week && <Badge variant="secondary" className="shrink-0 px-1.5 text-[10px]">mới tuần này</Badge>}
                  </span>
                  <span className="shrink-0 text-muted-foreground">{fmt.date(e.first_seen)} · {e.verified_live ? "đang chạy" : "chưa xác minh"}</span>
                </li>
              ))}
            </ol>
          </ScrollArea>
          {vf.count > 1 && <div className="mt-2 text-[11px] text-muted-foreground">Sản phẩm có {vf.count} video · đang hiện video nhiều page nhất.</div>}
        </>
      )}
    </Card>
  );
}

/** Store page age (design §2.3): platform + published date read from the landing; red flag when the page is more than
 *  14 days older than the video (someone sold it before). */
function StorePageCard({ stores }: { stores: any[] }) {
  const rows = stores.filter((s) => (s.page_platform && s.page_platform !== "unknown") || s.page_published_at);
  return (
    <Card title="Trang bán">
      {!rows.length ? <div className="text-sm text-muted-foreground">Chưa đọc được trang bán nào. Bấm “Quét giá & review đối thủ” để đọc nền tảng và ngày đăng.</div> : (
        <ul className="divide-y text-xs">
          {rows.map((s) => (
            <li key={s.domain} className="py-1.5">
              <div className="flex items-center justify-between gap-2">
                <a href={`https://${s.domain}`} target="_blank" rel="noreferrer" className="truncate hover:underline">{s.domain}</a>
                <span className="shrink-0 text-muted-foreground">{s.page_platform || "chưa rõ nền tảng"} · {s.page_published_at ? `đăng ${fmt.date(s.page_published_at)}` : "chưa biết ngày đăng"}</span>
              </div>
              {s.page_age?.flag && <Badge variant="destructive" className="mt-1 text-[10px]">⚠ Trang bán cũ hơn video {s.page_age.days_older} ngày (người khác đã bán trước)</Badge>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

const TABS = ["Video & Discovery", "Overview", "Market & Ads", "Creative Library", "Comments"];

const EVIDENCE: Record<string, string> = { video_fp_v2: "Vân tay video v2", video_fp_v1: "Vân tay video v1", poster_phash: "Ảnh bìa giống", same_file: "Cùng file" };

/** P4 — experimental: who else runs this product's videos. Our observation times + the evidence; never part of TEST / TEST_NOW. */
function ReuseCard({ productId }: { productId: string }) {
  const { data } = useApi(`/products/${productId}/creative-reuse`);
  const fams: any[] = data?.families || [];
  return (
    <Card title="Video được tái sử dụng (thử nghiệm — không tính vào TEST / TEST_NOW)" className="xl:col-span-3" pad={false}>
      {!fams.length ? <div className="p-4"><Empty>Chưa có video nào của sản phẩm thuộc một nhóm vân tay.</Empty></div> : fams.map((f) => (
        <div key={f.family_key} className="border-b p-4 last:border-b-0">
          <div className="text-xs text-muted-foreground">
            {f.family_key} · {f.features.ad_count} ad · {f.features.collation_variants} biến thể Meta · {f.features.distinct_pages} page ·{" "}
            {f.features.distinct_shop_id} shop · thị trường {f.features.markets.join(", ") || "—"} · +{f.features.new_distinct_pages_7d} page mới 7 ngày ·{" "}
            {f.features.reuse_velocity_7d} ad/ngày (7 ngày) · thấy lần đầu {f.features.earliest_observed_video_at ? fmt.dt(f.features.earliest_observed_video_at) : "—"}
          </div>
          <Table>
            <TableHeader><TableRow><TableHead>Page / nhà quảng cáo</TableHead><TableHead>Thị trường</TableHead><TableHead>Bằng chứng</TableHead><TableHead>Thấy đầu → cuối</TableHead></TableRow></TableHeader>
            <TableBody>{f.observations.map((o: any) => (
              <TableRow key={o.ad_id}>
                <TableCell className="text-xs">{o.advertiser || o.page_id || "—"}<div className="text-muted-foreground">page {o.page_id || "—"}</div></TableCell>
                <TableCell className="text-xs">{o.market || "—"}</TableCell>
                <TableCell className="text-xs">{EVIDENCE[o.evidence?.kind] || o.evidence?.kind}{o.evidence?.confidence != null ? ` · tin cậy ${Math.round(o.evidence.confidence * 100)}%` : ""}
                  {o.evidence?.snapshot_url && <> · <a className="underline" href={o.evidence.snapshot_url} target="_blank" rel="noreferrer">Ad Library</a></>}</TableCell>
                <TableCell className="text-xs">{o.first_seen_at ? fmt.dt(o.first_seen_at) : "—"} → {o.last_seen_at ? fmt.dt(o.last_seen_at) : "—"}</TableCell>
              </TableRow>
            ))}</TableBody>
          </Table>
        </div>
      ))}
    </Card>
  );
}

export default function Product360({ params }: { params: { id: string } }) {
  const { data, error, reload } = useApi(`/products/${params.id}`);
  if (!data) return <Loading error={error} />;
  const p = data.product, f = data.features, b = data.breakdown;

  return (
    <div>
      <PageHeader title={<ProductName key={`${p.id}:${p.name}:${p.name_status}`} p={p} big onChanged={reload} />}
                  subtitle={`${p.product_code} · ${p.category || "—"} · Advertiser: ${p.advertiser || "—"} · Markets: ${p.markets.join(", ") || p.country} · Giá TB ${fmt.n(p.price, 2)} ${p.currency || ""}`}>
        <span className="text-xs text-muted-foreground">Lifecycle</span><Pill tone={LIFECYCLE_TONE[p.lifecycle]}>{p.lifecycle}</Pill>
        <span className="ml-2 text-xs text-muted-foreground">Action</span><RecBadge rec={p.recommendation} />
        <span className="ml-2 flex flex-wrap gap-1">
          <Button variant="outline" size="xs" asChild><Link href={visualHref({ product_id: p.id })}><Camera /> Tìm bằng ảnh</Link></Button>
          <Button variant="outline" size="xs" asChild><Link href={visualHref({ product_id: p.id, mode: "compare_prices" })}><BadgeEuro /> So sánh giá</Link></Button>
          <Button variant="outline" size="xs" asChild><Link href={visualHref({ product_id: p.id, mode: "find_suppliers" })}><Truck /> Tìm nguồn hàng</Link></Button>
        </span>
      </PageHeader>

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Opportunity" value={fmt.n(p.opportunity_score)} sub={`Raw ${fmt.n(p.opportunity_raw)} × confidence adj.`} />
        <Stat label="Action" value={<RecBadge rec={p.recommendation} />} sub={(p.reasons || [])[0]} />
        <Stat label="Saturation" value={fmt.n(p.saturation_score)} sub={<span title={SELLER_SCOPE}>{sellerLabel(f.advertiser_count)}</span>} />
        <Stat label="Confidence" value={`${fmt.n(p.confidence_score)}%`} sub={p.confidence_label} />
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Checklist win — thiếu một vế là chưa win"><WinChecklist items={data.win_checklist} /></Card>
        <VideoCard vf={data.video_facts} />
        <StorePageCard stores={data.stores || []} />
      </div>

      <MktCard m={data.mkt} c={data.comments} productId={p.id} onChange={reload} />

      <Tabs defaultValue={TABS[0]} className="gap-4">
        <ScrollArea className="w-full">
          <TabsList variant="line" className="w-full justify-start border-b pb-1">
            {TABS.map((t) => <TabsTrigger key={t} value={t} className="flex-none px-3">{t}</TabsTrigger>)}
          </TabsList>
        </ScrollArea>

        <TabsContent value="Video & Discovery"><DiscoveryPanel productId={p.id} onChange={reload} /></TabsContent>

        <TabsContent value="Overview">
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card title="Decision engine">
              <div className="mb-2 flex items-center gap-2"><RecBadge rec={p.recommendation} /><span className="text-sm text-ink2">Opportunity {fmt.n(p.opportunity_score)} · Confidence {p.confidence_label}</span></div>
              <ul className="list-disc space-y-1 pl-5 text-sm">{(p.reasons || []).map((r: string) => <li key={r}>{r}</li>)}</ul>
            </Card>
            <Card title="Saturation — breakdown"><Breakdown parts={b.saturation} /></Card>
            {f.hidden_verdict && <Card title="Hidden Winner — bằng chứng">
              <Badge variant="outline">{f.hidden_verdict.status_label}</Badge>
              <ul className="mt-3 space-y-2 text-xs">{f.hidden_verdict.checks.map((c: any) => <li key={c.key}>{c.passed ? "✓" : "○"} {c.reason}</li>)}</ul>
              <p className="mt-3 text-[11px] text-muted-foreground">Nhãn bổ trợ; xếp hạng dùng Opportunity.</p>
            </Card>}
            <Card title="Rarity & Confidence">
              <Breakdown parts={b.rarity} />
              <Separator className="my-3" />
              <Breakdown parts={b.confidence} />
              <div className="mt-3 text-xs text-ink2">Growth velocity {fmt.n(data.derived.growth_velocity)} · Market fit {fmt.n(data.derived.market_fit)} · Margin potential {fmt.n(data.derived.margin_potential)}</div>
            </Card>
            <Card title="Lifecycle">{/* transitions are system work (decision engine / service token): history only */}
              <ScrollArea className="max-h-48 overflow-y-auto">
                <ol className="space-y-1.5 text-xs">
                  {data.lifecycle.events.map((e: any, i: number) => (
                    <li key={i}><span className="text-muted-foreground">{fmt.dt(e.at)}</span> · {e.from || "—"} → <b>{e.to}</b> <span className="text-ink2">({e.reason})</span></li>
                  ))}
                  {!data.lifecycle.events.length && <li className="text-muted-foreground">Chưa có chuyển trạng thái.</li>}
                </ol>
              </ScrollArea>
            </Card>
            <ProductCluster p={p} ads={data.cluster_ads || []} onChange={reload} />
            <Card title="Entity resolution — các tên khác của sản phẩm" className="xl:col-span-3">
              {p.aliases.length ? (
                <div className="flex flex-wrap gap-2">{p.aliases.map((a: any) => <Badge key={a.name} variant="secondary" className="font-normal">{a.name} <span className="text-muted-foreground">· {a.source} · match {fmt.n(a.match_score * 100)}%</span></Badge>)}</div>
              ) : <div className="text-sm text-muted-foreground">Không có alias.</div>}
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="Market & Ads">
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <CrossPlatformCard productId={p.id} f={f} />
            <Card title="Active ads theo ngày"><TrendChart data={data.timeseries} series={[{ key: "active_ads", label: "Active ads" }]} /></Card>
            <Card title="Saturation theo ngày"><TrendChart data={data.timeseries} series={[{ key: "saturation", label: "Saturation" }]} /></Card>
            <Card title="Market performance (active ads)">
              <BarList rows={data.market_performance.map((m: any) => ({ label: m.country, value: m.active_ads }))} />
            </Card>
            <Card title="Signals">
              <div className="grid grid-cols-1 gap-x-6 text-sm sm:grid-cols-2">
                {[["Advertisers", f.advertiser_count], ["New advertisers 7d", f.new_advertisers_7d], ["Active ads", f.active_ads], ["New ads 7d", f.new_ads_7d],
                  ["Ads running >30d", f.ads_running_30d], ["Creative duplication", fmt.pct(f.creative_duplication)], ["Stores", f.store_count],
                  ["Price trend 14d", fmt.signedPct(f.price_trend, 1)]].map(([l, v]) => (
                  <div key={l as string}>
                    <div className="flex justify-between py-1.5"><span className="text-muted-foreground">{l}</span><span className="tnum font-medium">{v as any}</span></div>
                    <Separator />
                  </div>
                ))}
              </div>
            </Card>
            <Card title={`Advertisers (${data.advertisers.length})`} pad={false}>
              <ScrollArea className="max-h-80 overflow-y-auto">
                <Table>
                  <TableHeader><TableRow><TableHead>Advertiser</TableHead><TableHead>Country</TableHead><TableHead className="text-right">Active</TableHead><TableHead className="text-right">New 7d</TableHead><TableHead className="text-right">Avg price</TableHead><TableHead>First seen</TableHead></TableRow></TableHeader>
                  <TableBody>{data.advertisers.map((a: any) => (
                    <TableRow key={a.id}><TableCell><Link href={`/competitors?focus=${a.id}`} className="hover:underline">{a.name}</Link></TableCell><TableCell>{a.country}</TableCell><TableCell className="tnum text-right">{a.active_ads}</TableCell><TableCell className="tnum text-right">{a.new_7d}</TableCell><TableCell className="tnum text-right">{fmt.n(a.avg_price, 1)}</TableCell><TableCell>{fmt.date(a.first_seen)}</TableCell></TableRow>
                  ))}</TableBody>
                </Table>
              </ScrollArea>
            </Card>
            <Card title={`Cửa hàng (${data.stores.length})`} pad={false}>
              <ScrollArea className="max-h-80 overflow-y-auto">
                <Table>
                  <TableHeader><TableRow><TableHead>Domain</TableHead><TableHead>Country</TableHead><TableHead className="text-right">Price</TableHead></TableRow></TableHeader>
                  <TableBody>{data.stores.map((s: any) => (
                    <TableRow key={s.domain}><TableCell>{s.domain}</TableCell><TableCell>{s.country}</TableCell><TableCell className="tnum text-right">{fmt.n(s.price, 1)}</TableCell></TableRow>
                  ))}</TableBody>
                </Table>
                {!data.stores.length && <Empty>Chưa có dữ liệu store (Similarweb / store tracking).</Empty>}
              </ScrollArea>
            </Card>
            <Card title="Recent ads" className="xl:col-span-2" pad={false}>
              <ScrollArea className="max-h-96 overflow-y-auto">
                <Table>
                  <TableHeader><TableRow><TableHead>First seen</TableHead><TableHead>Advertiser</TableHead><TableHead>Country</TableHead><TableHead>Platform</TableHead><TableHead>Source</TableHead><TableHead>Ad text</TableHead><TableHead className="text-right">Price</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                  <TableBody>{data.recent_ads.map((a: any) => (
                    <TableRow key={a.id}><TableCell className="whitespace-nowrap">{fmt.date(a.first_seen)}</TableCell><TableCell>{a.advertiser}</TableCell><TableCell>{a.country}</TableCell><TableCell>{a.platform}</TableCell><TableCell>{a.source}</TableCell>
                      <TableCell className="max-w-md truncate text-xs">{a.text}</TableCell><TableCell className="tnum text-right">{fmt.n(a.price, 1)}</TableCell><TableCell>{a.active ? <Pill tone="good">active</Pill> : <Pill>stopped</Pill>}</TableCell></TableRow>
                  ))}</TableBody>
                </Table>
              </ScrollArea>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="Creative Library">
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card title="Hooks (active ads)"><BarList rows={Object.entries(data.creative_mix.hooks).map(([k, v]) => ({ label: k, value: v as number }))} /></Card>
            <Card title="Angles"><BarList rows={Object.entries(data.creative_mix.angles).map(([k, v]) => ({ label: k, value: v as number }))} /></Card>
            <Card title="Offers"><BarList rows={Object.entries(data.creative_mix.offers).map(([k, v]) => ({ label: k, value: v as number }))} /></Card>
            <Card title="Creatives — gom theo fingerprint (trùng lặp = nhiều advertiser copy cùng creative)" className="xl:col-span-3" pad={false}>
              <ScrollArea className="max-h-[32rem] overflow-y-auto">
                <Table>
                  <TableHeader><TableRow><TableHead>Creative</TableHead><TableHead>Hook</TableHead><TableHead>Angle</TableHead><TableHead>Offer</TableHead><TableHead className="text-right">Copies</TableHead><TableHead className="text-right">Active</TableHead><TableHead className="text-right">Longest run</TableHead><TableHead className="text-right">Engagement</TableHead><TableHead>Countries</TableHead></TableRow></TableHeader>
                  <TableBody>{data.creatives.map((c: any) => (
                    <TableRow key={c.fingerprint}><TableCell className="max-w-md text-xs whitespace-normal">{c.ad_text}<div className="text-muted-foreground">{c.media_type} · {c.platforms.join(", ")}</div></TableCell>
                      <TableCell>{c.hook}</TableCell><TableCell>{c.angle}</TableCell><TableCell className="text-xs">{c.offer}</TableCell><TableCell className="tnum text-right">{c.count}</TableCell><TableCell className="tnum text-right">{c.active}</TableCell>
                      <TableCell className="tnum text-right">{c.longest_days}d</TableCell><TableCell className="tnum text-right">{fmt.n(c.engagement)}</TableCell><TableCell className="text-xs">{c.countries.join(", ")}</TableCell></TableRow>
                  ))}</TableBody>
                </Table>
                {!data.creatives.length && <Empty>Chưa có creative.</Empty>}
              </ScrollArea>
            </Card>
            <ReuseCard productId={params.id} />
          </div>
        </TabsContent>

        <TabsContent value="Comments"><CommentsPanel c={data.comments} /></TabsContent>
      </Tabs>
    </div>
  );
}
