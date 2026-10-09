"use client";
import Link from "next/link";
import { Fragment, ReactNode, useState } from "react";
import { ArrowRight, MessageCircle, RefreshCw, Share2, Sparkles, ThumbsDown, ThumbsUp, Play } from "lucide-react";
import { toast } from "sonner";
import { BarList, Breakdown, Funnel, TrendChart } from "@/components/charts";
import { ExperimentCard } from "@/components/ExperimentCard";
import { Card, Empty, LIFECYCLE_TONE, Loading, PageHeader, Pill, RecBadge, Stat } from "@/components/ui";
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

/** Where else the product shows up: networks, China-source / store listings (price / reviews / sold) and the
 *  supplier-price margin estimate (AliExpress price × 1.6 ≈ landed cost: shipping + fees not included). */
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
        {f.supplier_price_usd ? <span title="Giá AliExpress thấp nhất — mốc giá nhập">Giá nhập ~<b>${f.supplier_price_usd}</b></span> : null}
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
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-8">
        <Item l="Giá đối thủ đang bán" v={usd(m.sell_usd)}
              sub={cov.ads ? <span className={covClass}>{cov.priced}/{cov.ads} ads có giá ({Math.round(covPct * 100)}%){m.ad_price_range_usd ? ` · ${usd(m.ad_price_range_usd[0])} – ${usd(m.ad_price_range_usd[1])}` : ""}</span> : "chưa có ad"} />
        <Item l="Giá nhập (AliExpress)" v={usd(m.supplier_usd)} sub="landed ≈ ×1.6" />
        <Item l="Biên gộp ước tính" v={m.margin_est === null || m.margin_est === undefined ? "—" : <span className={cn(m.margin_est > 0.5 && "text-good-text", m.margin_est < 0.3 && "text-critical")}>{Math.round(m.margin_est * 100)}%</span>} sub="giá đối thủ vs nhập ×1.6 · chưa gồm ads, COD, hoàn" />
        <Item l="Giá sàn (thấp nhất trên sàn)" v={usd(m.floor_usd)} sub={m.floor?.[0] && m.floor[0].network !== "aliexpress" ? byKey[m.floor[0].network]?.label : m.retail_usd ? `bán lẻ sàn TB ${usd(m.retail_usd)}` : "chưa thấy trên sàn"} />
        <Item l="Khách nói gì" v={sent ? <span className="inline-flex items-center gap-2"><span className="inline-flex items-center gap-1 text-good-text"><ThumbsUp className="size-4" />{Math.round(sent.positive * 100)}%</span><span className="inline-flex items-center gap-1 text-critical"><ThumbsDown className="size-4" />{Math.round(sent.negative * 100)}%</span></span> : "—"}
              sub={c?.analysed ? `${fmt.n(c.analysed)} review${c.rating_avg ? ` · ★ ${c.rating_avg}` : ""}` : "chưa có review / comment"} />
        <Item l={<span className="inline-flex items-center gap-1"><ThumbsUp className="size-3" /> Like / <MessageCircle className="size-3" /> Comment (ads)</span>} v={`${fmt.n(e.likes)} / ${fmt.n(e.comments)}`}
              sub={<span className="inline-flex items-center gap-1"><Share2 className="size-3" />{fmt.n(e.shares)} share{e.views ? <> · <Play className="size-3" />{fmt.n(e.views)} view</> : null}</span>} />
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

const TABS = ["Video & Discovery", "Overview", "Market & Ads", "Creative Library", "Comments", "Internal & Profit"];

export default function Product360({ params }: { params: { id: string } }) {
  const { data, error, reload } = useApi(`/products/${params.id}`);
  const [explain, setExplain] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lcMsg, setLcMsg] = useState("");
  if (!data) return <Loading error={error} />;
  const p = data.product, f = data.features, b = data.breakdown;

  const doExplain = async () => {
    setBusy(true);
    try { setExplain((await api(`/products/${p.id}/explain`, { method: "POST" })).explanation); }
    catch (e: any) { toast.error("Không giải thích được", { description: e.message }); }
    finally { setBusy(false); }
  };
  const move = async (to: string) => {
    try { await api(`/products/${p.id}/lifecycle`, { method: "POST", body: JSON.stringify({ to, reason: "manual" }) }); setLcMsg(""); toast.success(`Lifecycle → ${to}`); reload(); }
    catch (e: any) { setLcMsg(e.message); toast.error("Không chuyển được lifecycle", { description: e.message }); }
  };

  return (
    <div>
      <PageHeader title={p.name} subtitle={`${p.product_code} · ${p.category || "—"} · Markets: ${p.markets.join(", ") || p.country} · Giá TB ${fmt.n(p.price, 2)} ${p.currency || ""}`}>
        <span className="text-xs text-muted-foreground">Lifecycle</span><Pill tone={LIFECYCLE_TONE[p.lifecycle]}>{p.lifecycle}</Pill>
        <span className="ml-2 text-xs text-muted-foreground">Action</span><RecBadge rec={p.recommendation} />
      </PageHeader>

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Win Score" value={fmt.n(p.win_score)} sub={p.win_label} />
        <Stat label="External / Internal" value={<>{fmt.n(p.external_win_score)}<span className="text-lg text-muted-foreground"> / {p.internal_win_score === null ? "—" : fmt.n(p.internal_win_score)}</span></>}
              sub={`Blend ${Math.round((b.blend?.external ?? 1) * 100)}/${Math.round((b.blend?.internal ?? 0) * 100)}`} />
        <Stat label="Rare Winner" value={fmt.n(p.rare_winner_score)} sub={`Rarity ${fmt.n(p.rarity_score)}`} />
        <Stat label="Saturation" value={fmt.n(p.saturation_score)} sub={p.saturation_state} />
        <Stat label="Opportunity" value={fmt.n(p.opportunity_score)} sub={`Raw ${fmt.n(data.derived.opportunity_raw)} × confidence adj.`} />
        <Stat label="Confidence" value={`${fmt.n(p.confidence_score)}%`} sub={p.confidence_label} />
        <Stat label="Customer rejection" value={p.customer_rejection_score === null ? "—" : fmt.n(p.customer_rejection_score)} sub={`Refusal ${fmt.pct(f.refusal_rate)}`} />
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
              <div className="mb-2 flex items-center gap-2"><RecBadge rec={p.recommendation} /><span className="text-sm text-ink2">{p.win_label} · Confidence {p.confidence_label}</span></div>
              <ul className="mb-4 list-disc space-y-1 pl-5 text-sm">{(p.reasons || []).map((r: string) => <li key={r}>{r}</li>)}</ul>
              <Button className="w-full" onClick={doExplain} disabled={busy}><Sparkles />{busy ? "Đang giải thích…" : "AI giải thích vì sao ra điểm này"}</Button>
              {explain && <div className="mt-3 rounded-md bg-muted p-3 text-sm leading-relaxed whitespace-pre-wrap">{explain}</div>}
              <div className="mt-2 text-[11px] text-muted-foreground">AI chỉ giải thích — điểm số do công thức tính từ database.</div>
            </Card>
            <Card title="External Win Score — breakdown"><Breakdown parts={b.external} /></Card>
            <Card title="Internal Win Score — breakdown">{Object.keys(b.internal || {}).length ? <Breakdown parts={b.internal} /> : <Empty>Chưa test nội bộ — Final score = 100% External.</Empty>}</Card>
            <Card title="Saturation — breakdown"><Breakdown parts={b.saturation} /></Card>
            <Card title="Rarity & Confidence">
              <Breakdown parts={b.rarity} />
              <Separator className="my-3" />
              <Breakdown parts={b.confidence} />
              <div className="mt-3 text-xs text-ink2">Growth velocity {fmt.n(data.derived.growth_velocity)} · Market fit {fmt.n(data.derived.market_fit)} · Margin potential {fmt.n(data.derived.margin_potential)}</div>
            </Card>
            <Card title="Lifecycle">
              <div className="mb-3 flex flex-wrap gap-1.5">
                {data.lifecycle.allowed.map((s: string) => <Button key={s} variant="outline" size="xs" onClick={() => move(s)}><ArrowRight />{s}</Button>)}
              </div>
              {lcMsg && <div className="mb-2 text-xs text-critical">{lcMsg}</div>}
              <ScrollArea className="max-h-48 overflow-y-auto">
                <ol className="space-y-1.5 text-xs">
                  {data.lifecycle.events.map((e: any, i: number) => (
                    <li key={i}><span className="text-muted-foreground">{fmt.dt(e.at)}</span> · {e.from || "—"} → <b>{e.to}</b> <span className="text-ink2">({e.reason})</span></li>
                  ))}
                  {!data.lifecycle.events.length && <li className="text-muted-foreground">Chưa có chuyển trạng thái.</li>}
                </ol>
              </ScrollArea>
            </Card>
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
            <Card title="Scores theo ngày"><TrendChart data={data.timeseries} series={[{ key: "external_win", label: "External Win" }, { key: "saturation", label: "Saturation" }, { key: "opportunity", label: "Opportunity" }]} /></Card>
            <Card title="Market performance (active ads)">
              <BarList rows={data.market_performance.map((m: any) => ({ label: m.country, value: m.active_ads }))} />
            </Card>
            <Card title="Signals">
              <div className="grid grid-cols-1 gap-x-6 text-sm sm:grid-cols-2">
                {[["Advertisers", f.advertiser_count], ["New advertisers 7d", f.new_advertisers_7d], ["Active ads", f.active_ads], ["New ads 7d", f.new_ads_7d],
                  ["Ads running >30d", f.ads_running_30d], ["Creative duplication", fmt.pct(f.creative_duplication)], ["Stores", f.store_count],
                  ["Traffic (est.)", fmt.n(f.estimated_traffic)], ["Traffic growth", `${fmt.n(f.traffic_growth)}%`], ["Search trend", `${fmt.n(p.search_trend)}%`],
                  ["Keyword competition", fmt.pct(p.keyword_competition, 0)], ["Price trend 14d", fmt.signedPct(f.price_trend, 1)]].map(([l, v]) => (
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
            <Card title={`Stores & traffic (${data.stores.length})`} pad={false}>
              <ScrollArea className="max-h-80 overflow-y-auto">
                <Table>
                  <TableHeader><TableRow><TableHead>Domain</TableHead><TableHead>Country</TableHead><TableHead className="text-right">Price</TableHead><TableHead className="text-right">Traffic</TableHead><TableHead className="text-right">Growth</TableHead></TableRow></TableHeader>
                  <TableBody>{data.stores.map((s: any) => (
                    <TableRow key={s.domain}><TableCell>{s.domain}</TableCell><TableCell>{s.country}</TableCell><TableCell className="tnum text-right">{fmt.n(s.price, 1)}</TableCell><TableCell className="tnum text-right">{fmt.n(s.traffic)}</TableCell>
                      <TableCell className={cn("tnum text-right", s.traffic_growth > 0 ? "text-good-text" : "text-critical")}>{fmt.n(s.traffic_growth)}%</TableCell></TableRow>
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
          </div>
        </TabsContent>

        <TabsContent value="Comments"><CommentsPanel c={data.comments} /></TabsContent>

        <TabsContent value="Internal & Profit">
          <div className="space-y-4">
            <div className="flex justify-end gap-2">
              <Button size="sm" asChild><Link href={`/test-lab?product_id=${p.id}&new=1`}>+ Tạo test cho sản phẩm này</Link></Button>
              {f.has_internal && <Button size="sm" variant="outline" asChild><Link href={`/test-lab?product_id=${p.id}`}>Mở Test Lab của sản phẩm</Link></Button>}
            </div>
            {!f.has_internal ? <Card><Empty>Sản phẩm chưa được test nội bộ — chỉ test tạo cho đúng sản phẩm này mới hiện ở đây.</Empty></Card> : (
              <>
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
                  <Stat label="Spend" value={fmt.money(f.internal_spend)} />
                  <Stat label="Orders" value={fmt.n(f.orders)} sub={`Confirm ${fmt.pct(f.confirm_rate)}`} />
                  <Stat label="Revenue (booked)" value={fmt.money(f.revenue)} />
                  <Stat label="Profit (delivered)" value={fmt.money(f.profit)} sub={`CM ${fmt.pct(f.contribution_margin)}`} />
                  <Stat label="ROAS / MER" value={`${fmt.n(f.roas, 2)} / ${fmt.n(f.mer, 2)}`} />
                  <Stat label="CPA / AOV" value={`${fmt.money(f.cpa)} / ${fmt.money(f.aov)}`} />
                  <Stat label="Delivery rate" value={fmt.pct(f.delivery_rate)} />
                  <Stat label="Refusal / Return" value={`${fmt.pct(f.refusal_rate)} / ${fmt.pct(f.return_rate)}`} />
                </div>
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                  <Card title="Order → delivery funnel">
                    <Funnel stages={[
                      { label: "Orders", value: f.orders }, { label: "Confirmed", value: f.confirmed_orders }, { label: "Shipped", value: f.shipped },
                      { label: "Delivered", value: f.delivered }, { label: "Refused", value: f.refused, ok: (f.refusal_rate || 0) < 0.22, of: f.shipped }, { label: "Returned", value: f.returned, of: f.delivered },
                    ]} />
                  </Card>
                  <Card title="Lý do khách từ chối (AI phân loại)">
                    {Object.keys(data.orders.refusal_reasons || {}).length ?
                      <BarList rows={Object.entries(data.orders.refusal_reasons).map(([k, v]) => ({ label: k.replace(/_/g, " "), value: v as number }))} color="bg-serious" /> :
                      <Empty>Chưa có đơn bị từ chối.</Empty>}
                  </Card>
                </div>
                {data.experiments.map((e: any) => <ExperimentCard key={e.id} e={e} />)}
              </>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
