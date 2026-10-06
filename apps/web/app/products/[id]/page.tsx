"use client";
import Link from "next/link";
import { useState } from "react";
import { BarList, Breakdown, Funnel, TrendChart } from "@/components/charts";
import { Card, Empty, LIFECYCLE_TONE, Loading, PageHeader, Pill, RecBadge, ScoreBar, Stat } from "@/components/ui";
import { api, fmt, useApi } from "@/lib/api";
import { ExperimentCard } from "@/components/ExperimentCard";
import { CommentsPanel } from "@/components/CommentsPanel";
import { DiscoveryPanel } from "@/components/DiscoveryPanel";

const TABS = ["Video & Discovery", "Overview", "Market & Ads", "Creative Library", "Comments", "Internal & Profit"];

export default function Product360({ params }: { params: { id: string } }) {
  const { data, error, reload } = useApi(`/products/${params.id}`);
  const [tab, setTab] = useState(TABS[0]);
  const [explain, setExplain] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lcMsg, setLcMsg] = useState("");
  if (!data) return <Loading error={error} />;
  const p = data.product, f = data.features, b = data.breakdown;

  const doExplain = async () => {
    setBusy(true);
    try { setExplain((await api(`/products/${p.id}/explain`, { method: "POST" })).explanation); } finally { setBusy(false); }
  };
  const move = async (to: string) => {
    try { await api(`/products/${p.id}/lifecycle`, { method: "POST", body: JSON.stringify({ to, reason: "manual" }) }); setLcMsg(""); reload(); }
    catch (e: any) { setLcMsg(e.message); }
  };

  return (
    <div>
      <PageHeader title={p.name} subtitle={`${p.product_code} · ${p.category || "—"} · Markets: ${p.markets.join(", ") || p.country} · Giá TB ${fmt.n(p.price, 2)} ${p.currency || ""}`}>
        <span className="text-xs text-muted">Lifecycle</span><Pill tone={LIFECYCLE_TONE[p.lifecycle]}>{p.lifecycle}</Pill>
        <span className="text-xs text-muted ml-2">Action</span><RecBadge rec={p.recommendation} />
      </PageHeader>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3 mb-4">
        <Stat label="Win Score" value={fmt.n(p.win_score)} sub={p.win_label} />
        <Stat label="External / Internal" value={<>{fmt.n(p.external_win_score)}<span className="text-muted text-lg"> / {p.internal_win_score === null ? "—" : fmt.n(p.internal_win_score)}</span></>} sub={`Blend ${Math.round((b.blend?.external ?? 1) * 100)}/${Math.round((b.blend?.internal ?? 0) * 100)}`} />
        <Stat label="Rare Winner" value={fmt.n(p.rare_winner_score)} sub={`Rarity ${fmt.n(p.rarity_score)}`} />
        <Stat label="Saturation" value={fmt.n(p.saturation_score)} sub={p.saturation_state} />
        <Stat label="Opportunity" value={fmt.n(p.opportunity_score)} sub={`Raw ${fmt.n(data.derived.opportunity_raw)} × confidence adj.`} />
        <Stat label="Confidence" value={`${fmt.n(p.confidence_score)}%`} sub={p.confidence_label} />
        <Stat label="Customer rejection" value={p.customer_rejection_score === null ? "—" : fmt.n(p.customer_rejection_score)} sub={`Refusal ${fmt.pct(f.refusal_rate)}`} />
      </div>

      <div className="flex gap-1 mb-4 border-b" style={{ borderColor: "var(--grid)" }}>
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className="px-3 py-2 text-sm -mb-px border-b-2"
                  style={{ borderColor: tab === t ? "var(--series-1)" : "transparent", fontWeight: tab === t ? 600 : 400, color: tab === t ? "var(--text-primary)" : "var(--text-secondary)" }}>{t}</button>
        ))}
      </div>

      {tab === "Video & Discovery" && <DiscoveryPanel productId={p.id} onChange={reload} />}

      {tab === "Overview" && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <Card title="Decision engine">
            <div className="flex items-center gap-2 mb-2"><RecBadge rec={p.recommendation} /><span className="text-sm text-ink2">{p.win_label} · Confidence {p.confidence_label}</span></div>
            <ul className="text-sm list-disc pl-5 space-y-1 mb-4">{(p.reasons || []).map((r: string) => <li key={r}>{r}</li>)}</ul>
            <button className="btn btn-primary w-full" onClick={doExplain} disabled={busy}>{busy ? "Đang giải thích…" : "✦ AI giải thích vì sao ra điểm này"}</button>
            {explain && <p className="text-sm mt-3 leading-relaxed whitespace-pre-wrap">{explain}</p>}
            <div className="text-[11px] text-muted mt-2">AI chỉ giải thích — điểm số do công thức tính từ database.</div>
          </Card>
          <Card title="External Win Score — breakdown"><Breakdown parts={b.external} /></Card>
          <Card title="Internal Win Score — breakdown">{Object.keys(b.internal || {}).length ? <Breakdown parts={b.internal} /> : <Empty>Chưa test nội bộ — Final score = 100% External.</Empty>}</Card>
          <Card title="Saturation — breakdown"><Breakdown parts={b.saturation} /></Card>
          <Card title="Rarity & Confidence">
            <Breakdown parts={b.rarity} />
            <div className="h-px my-3" style={{ background: "var(--grid)" }} />
            <Breakdown parts={b.confidence} />
            <div className="text-xs text-ink2 mt-3">Growth velocity {fmt.n(data.derived.growth_velocity)} · Market fit {fmt.n(data.derived.market_fit)} · Margin potential {fmt.n(data.derived.margin_potential)}</div>
          </Card>
          <Card title="Lifecycle">
            <div className="flex flex-wrap gap-1.5 mb-3">
              {data.lifecycle.allowed.map((s: string) => <button key={s} className="btn text-xs" onClick={() => move(s)}>→ {s}</button>)}
            </div>
            {lcMsg && <div className="text-xs mb-2" style={{ color: "var(--critical)" }}>{lcMsg}</div>}
            <ol className="text-xs space-y-1.5 max-h-48 overflow-y-auto">
              {data.lifecycle.events.map((e: any, i: number) => (
                <li key={i}><span className="text-muted">{fmt.dt(e.at)}</span> · {e.from || "—"} → <b>{e.to}</b> <span className="text-ink2">({e.reason})</span></li>
              ))}
              {!data.lifecycle.events.length && <li className="text-muted">Chưa có chuyển trạng thái.</li>}
            </ol>
          </Card>
          <Card title="Entity resolution — các tên khác của sản phẩm" className="xl:col-span-3">
            {p.aliases.length ? (
              <div className="flex flex-wrap gap-2">{p.aliases.map((a: any) => <span key={a.name} className="text-xs rounded px-2 py-1" style={{ background: "var(--surface-2)" }}>{a.name} <span className="text-muted">· {a.source} · match {fmt.n(a.match_score * 100)}%</span></span>)}</div>
            ) : <div className="text-sm text-muted">Không có alias.</div>}
          </Card>
        </div>
      )}

      {tab === "Market & Ads" && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <Card title="Active ads theo ngày"><TrendChart data={data.timeseries} series={[{ key: "active_ads", label: "Active ads" }]} /></Card>
          <Card title="Scores theo ngày"><TrendChart data={data.timeseries} series={[{ key: "external_win", label: "External Win" }, { key: "saturation", label: "Saturation" }, { key: "opportunity", label: "Opportunity" }]} /></Card>
          <Card title="Market performance (active ads)">
            <BarList rows={data.market_performance.map((m: any) => ({ label: m.country, value: m.active_ads }))} />
          </Card>
          <Card title="Signals">
            <div className="grid grid-cols-2 gap-3 text-sm">
              {[["Advertisers", f.advertiser_count], ["New advertisers 7d", f.new_advertisers_7d], ["Active ads", f.active_ads], ["New ads 7d", f.new_ads_7d],
                ["Ads running >30d", f.ads_running_30d], ["Creative duplication", fmt.pct(f.creative_duplication)], ["Stores", f.store_count],
                ["Traffic (est.)", fmt.n(f.estimated_traffic)], ["Traffic growth", `${fmt.n(f.traffic_growth)}%`], ["Search trend", `${fmt.n(p.search_trend)}%`],
                ["Keyword competition", fmt.pct(p.keyword_competition, 0)], ["Price trend 14d", fmt.signedPct(f.price_trend, 1)]].map(([l, v]) => (
                <div key={l as string} className="flex justify-between border-b pb-1" style={{ borderColor: "var(--grid)" }}><span className="text-muted">{l}</span><span className="tnum font-medium">{v as any}</span></div>
              ))}
            </div>
          </Card>
          <Card title={`Advertisers (${data.advertisers.length})`} pad={false}>
            <div className="max-h-80 overflow-y-auto">
              <table className="data"><thead><tr><th>Advertiser</th><th>Country</th><th>Active</th><th>New 7d</th><th>Avg price</th><th>First seen</th></tr></thead>
                <tbody>{data.advertisers.map((a: any) => (
                  <tr key={a.id}><td><Link href={`/competitors?focus=${a.id}`} className="hover:underline">{a.name}</Link></td><td>{a.country}</td><td className="tnum">{a.active_ads}</td><td className="tnum">{a.new_7d}</td><td className="tnum">{fmt.n(a.avg_price, 1)}</td><td>{fmt.date(a.first_seen)}</td></tr>
                ))}</tbody></table>
            </div>
          </Card>
          <Card title={`Stores & traffic (${data.stores.length})`} pad={false}>
            <div className="max-h-80 overflow-y-auto">
              <table className="data"><thead><tr><th>Domain</th><th>Country</th><th>Price</th><th>Traffic</th><th>Growth</th></tr></thead>
                <tbody>{data.stores.map((s: any) => (
                  <tr key={s.domain}><td>{s.domain}</td><td>{s.country}</td><td className="tnum">{fmt.n(s.price, 1)}</td><td className="tnum">{fmt.n(s.traffic)}</td>
                    <td className="tnum" style={{ color: s.traffic_growth > 0 ? "var(--good-text)" : "var(--critical)" }}>{fmt.n(s.traffic_growth)}%</td></tr>
                ))}</tbody></table>
              {!data.stores.length && <Empty>Chưa có dữ liệu store (Similarweb / store tracking).</Empty>}
            </div>
          </Card>
          <Card title="Recent ads" className="xl:col-span-2" pad={false}>
            <div className="max-h-96 overflow-y-auto">
              <table className="data"><thead><tr><th>First seen</th><th>Advertiser</th><th>Country</th><th>Platform</th><th>Source</th><th>Ad text</th><th>Price</th><th>Status</th></tr></thead>
                <tbody>{data.recent_ads.map((a: any) => (
                  <tr key={a.id}><td className="whitespace-nowrap">{fmt.date(a.first_seen)}</td><td>{a.advertiser}</td><td>{a.country}</td><td>{a.platform}</td><td>{a.source}</td>
                    <td className="max-w-md text-xs">{a.text}</td><td className="tnum">{fmt.n(a.price, 1)}</td><td>{a.active ? <Pill tone="good">active</Pill> : <Pill>stopped</Pill>}</td></tr>
                ))}</tbody></table>
            </div>
          </Card>
        </div>
      )}

      {tab === "Creative Library" && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <Card title="Hooks (active ads)"><BarList rows={Object.entries(data.creative_mix.hooks).map(([k, v]) => ({ label: k, value: v as number }))} /></Card>
          <Card title="Angles"><BarList rows={Object.entries(data.creative_mix.angles).map(([k, v]) => ({ label: k, value: v as number }))} /></Card>
          <Card title="Offers"><BarList rows={Object.entries(data.creative_mix.offers).map(([k, v]) => ({ label: k, value: v as number }))} /></Card>
          <Card title="Creatives — gom theo fingerprint (trùng lặp = nhiều advertiser copy cùng creative)" className="xl:col-span-3" pad={false}>
            <div className="overflow-x-auto">
              <table className="data"><thead><tr><th>Creative</th><th>Hook</th><th>Angle</th><th>Offer</th><th>Copies</th><th>Active</th><th>Longest run</th><th>Engagement</th><th>Countries</th></tr></thead>
                <tbody>{data.creatives.map((c: any) => (
                  <tr key={c.fingerprint}><td className="max-w-md text-xs">{c.ad_text}<div className="text-muted">{c.media_type} · {c.platforms.join(", ")}</div></td>
                    <td>{c.hook}</td><td>{c.angle}</td><td className="text-xs">{c.offer}</td><td className="tnum">{c.count}</td><td className="tnum">{c.active}</td>
                    <td className="tnum">{c.longest_days}d</td><td className="tnum">{fmt.n(c.engagement)}</td><td className="text-xs">{c.countries.join(", ")}</td></tr>
                ))}</tbody></table>
            </div>
          </Card>
        </div>
      )}

      {tab === "Comments" && <CommentsPanel c={data.comments} />}

      {tab === "Internal & Profit" && (
        <div className="space-y-4">
          {!f.has_internal ? <Card><Empty>Sản phẩm chưa được test nội bộ. Tạo experiment trong <Link href="/test-lab" className="text-accent">Test Lab</Link>.</Empty></Card> : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
                <Stat label="Spend" value={fmt.money(f.internal_spend)} />
                <Stat label="Orders" value={fmt.n(f.orders)} sub={`Confirm ${fmt.pct(f.confirm_rate)}`} />
                <Stat label="Revenue (booked)" value={fmt.money(f.revenue)} />
                <Stat label="Profit (delivered)" value={fmt.money(f.profit)} sub={`CM ${fmt.pct(f.contribution_margin)}`} />
                <Stat label="ROAS / MER" value={`${fmt.n(f.roas, 2)} / ${fmt.n(f.mer, 2)}`} />
                <Stat label="CPA / AOV" value={`${fmt.money(f.cpa)} / ${fmt.money(f.aov)}`} />
                <Stat label="Delivery rate" value={fmt.pct(f.delivery_rate)} />
                <Stat label="Refusal / Return" value={`${fmt.pct(f.refusal_rate)} / ${fmt.pct(f.return_rate)}`} />
              </div>
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <Card title="Order → delivery funnel">
                  <Funnel stages={[
                    { label: "Orders", value: f.orders }, { label: "Confirmed", value: f.confirmed_orders }, { label: "Shipped", value: f.shipped },
                    { label: "Delivered", value: f.delivered }, { label: "Refused", value: f.refused, ok: (f.refusal_rate || 0) < 0.22, of: f.shipped }, { label: "Returned", value: f.returned, of: f.delivered },
                  ]} />
                </Card>
                <Card title="Lý do khách từ chối (AI phân loại)">
                  {Object.keys(data.orders.refusal_reasons || {}).length ?
                    <BarList rows={Object.entries(data.orders.refusal_reasons).map(([k, v]) => ({ label: k.replace(/_/g, " "), value: v as number }))} color="var(--serious)" /> :
                    <Empty>Chưa có đơn bị từ chối.</Empty>}
                </Card>
              </div>
              {data.experiments.map((e: any) => <ExperimentCard key={e.id} e={e} />)}
            </>
          )}
        </div>
      )}
    </div>
  );
}
