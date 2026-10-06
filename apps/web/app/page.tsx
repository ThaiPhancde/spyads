"use client";
import Link from "next/link";
import { useState } from "react";
import { TrendChart } from "@/components/charts";
import { Card, Loading, PageHeader, ProductTable, RecBadge, SeverityIcon, Stat } from "@/components/ui";
import { api, fmt, qs, useApi } from "@/lib/api";

export default function DailyPulse() {
  const [country, setCountry] = useState("");
  const meta = useApi("/meta");
  const { data, error, reload } = useApi(`/dashboard/pulse${qs({ country })}`);
  const [running, setRunning] = useState(false);
  const runPipeline = async () => {
    setRunning(true);
    try { await api("/pipeline/run", { method: "POST" }); reload(); } finally { setRunning(false); }
  };
  if (!data) return <Loading error={error} />;
  const k = data.kpis, t = data.internal_tests;
  const refDelta = t.cod_refusal !== null && t.cod_refusal_prev !== null ? t.cod_refusal - t.cod_refusal_prev : null;
  const hw = data.top_hidden_winners[0];

  return (
    <div>
      <PageHeader title={`Today — ${country || "thị trường mục tiêu"}`} subtitle="Hôm nay thị trường thay đổi gì">
        <select className="input" value={country} onChange={(e) => setCountry(e.target.value)}>
          <option value="">Mọi thị trường mục tiêu</option>
          <option value="ME">Trung Đông</option><option value="US">Mỹ</option><option value="EU">Châu Âu + UK</option><option value="AU">Úc / NZ</option><option value="WW">Toàn cầu</option>
          {meta.data?.countries.map((c: string) => <option key={c}>{c}</option>)}
        </select>
        <button className="btn btn-primary" onClick={runPipeline} disabled={running}>{running ? "Đang chạy pipeline…" : "Chạy pipeline hôm nay"}</button>
      </PageHeader>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3 mb-4">
        <Stat label="Products monitored" value={fmt.n(k.products_monitored)} />
        <Stat label="New products (24h)" value={fmt.n(k.new_products)} />
        <Stat label="New active ads (24h)" value={fmt.n(k.new_active_ads)} />
        <Stat label="New advertisers (24h)" value={fmt.n(k.new_advertisers)} />
        <Stat label="Potential winners" value={fmt.n(k.potential_winners)} />
        <Stat label="Hidden winners" value={fmt.n(k.hidden_winners)} />
        <Stat label="Saturated products" value={fmt.n(k.saturated_products)} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
        <Card title="Internal tests">
          <div className="grid grid-cols-3 gap-3 text-center">
            {[["Testing", t.testing], ["Winning", t.winning], ["Promising", t.promising], ["Failed", t.failed], ["Waiting data", t.waiting_data]].map(([l, v]) => (
              <div key={l as string}><div className="text-xl font-semibold tnum">{v}</div><div className="text-xs text-muted">{l}</div></div>
            ))}
            <div>
              <div className="text-xl font-semibold tnum">{fmt.pct(t.cod_refusal)}</div>
              <div className="text-xs text-muted">COD refusal 14d</div>
              {refDelta !== null && (
                <div className="text-[11px]" style={{ color: refDelta > 0 ? "var(--critical)" : "var(--good-text)" }}>
                  {refDelta > 0 ? "▲" : "▼"} {(Math.abs(refDelta) * 100).toFixed(1)} pt
                </div>
              )}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {Object.entries(data.recommendations).map(([r, n]) => (
              <Link key={r} href={`/products?recommendation=${r}`} className="flex items-center gap-1.5 text-xs"><RecBadge rec={r} /><span className="tnum text-ink2">{n as number}</span></Link>
            ))}
          </div>
        </Card>

        <Card title="⚠ Alerts" action={<Link href="/alerts" className="text-xs text-accent">Xem tất cả →</Link>}>
          <div className="space-y-2.5 max-h-[230px] overflow-y-auto pr-1">
            {data.alerts.length === 0 && <div className="text-sm text-muted">Không có cảnh báo.</div>}
            {data.alerts.map((a: any) => (
              <div key={a.id} className="flex gap-2 text-sm">
                <SeverityIcon s={a.severity} />
                <div>
                  {a.product_id ? <Link href={`/products/${a.product_id}`} className="font-medium hover:underline">{a.title}</Link> : <div className="font-medium">{a.title}</div>}
                  <div className="text-xs text-ink2">{a.message}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="💎 Hidden winner nổi bật">
          {hw ? (
            <Link href={`/products/${hw.id}`} className="block">
              <div className="text-lg font-semibold">{hw.name}</div>
              <div className="text-xs text-muted mb-3">{hw.product_code} · Market: {hw.markets.join(", ")}</div>
              <div className="grid grid-cols-3 gap-y-2 text-sm">
                <div><div className="text-xs text-muted">Advertisers</div><div className="font-semibold tnum">{hw.advertisers}</div></div>
                <div><div className="text-xs text-muted">Growth 7d</div><div className="font-semibold tnum" style={{ color: "var(--good-text)" }}>{fmt.signedPct(hw.growth_7d)}</div></div>
                <div><div className="text-xs text-muted">Win Score</div><div className="font-semibold tnum">{fmt.n(hw.win_score)}</div></div>
                <div><div className="text-xs text-muted">Rarity</div><div className="font-semibold tnum">{fmt.n(hw.rarity_score)}</div></div>
                <div><div className="text-xs text-muted">Opportunity</div><div className="font-semibold tnum">{fmt.n(hw.opportunity_score)}</div></div>
                <div><div className="text-xs text-muted">Confidence</div><div className="font-semibold">{hw.confidence_label}</div></div>
              </div>
              <div className="mt-3 text-xs text-ink2">{hw.win_label} · Confidence: {hw.confidence_label} — không phải “guaranteed winner”.</div>
            </Link>
          ) : <div className="text-sm text-muted">Chưa có hidden winner.</div>}
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <Card title="Active ads — 30 ngày">
          <TrendChart data={data.ads_trend} series={[{ key: "active_ads", label: "Active ads" }]} />
        </Card>
        <Card title="New ads / ngày — 30 ngày">
          <TrendChart data={data.ads_trend} series={[{ key: "new_ads", label: "New ads" }]} />
        </Card>
      </div>

      <Card title="Top opportunities" action={<Link href="/products" className="text-xs text-accent">Product Explorer →</Link>} pad={false}>
        <ProductTable rows={data.top_opportunities} compact />
      </Card>
    </div>
  );
}
