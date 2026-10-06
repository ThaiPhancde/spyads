"use client";
import { MarketPicker } from "@/components/MarketPicker";
import Link from "next/link";
import { useState } from "react";
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { ProductMediaCard } from "@/components/media";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";

const TAB_ORDER = ["breakout", "experimental", "new", "hidden", "wave", "novelty", "creative", "mess", "ladi", "favorites", "scaling", "saved", "risk"];
// Quadrants: 3 validated categorical slots + neutral gray for "low signal" (≤3 colored series on a scatter).
const QUAD: [string, string, string][] = [
  ["BREAKOUT", "🚀 Breakout (wave cao + đã được xác nhận)", "var(--series-1)"],
  ["EXPERIMENTAL", "💎 Experimental (wave cao, chưa xác nhận)", "var(--series-2)"],
  ["STABLE_WINNER", "🏛 Stable winner (xác nhận, ít sóng mới)", "var(--series-3)"],
  ["LOW_SIGNAL", "Low signal", "var(--axis)"],
];

function RadarChart({ country, funnel }: { country: string; funnel: string }) {
  const { data } = useApi(`/discovery/radar${qs({ country, funnel })}`);
  if (!data) return <Loading />;
  if (!data.points.length) return <Empty>Chưa có sản phẩm được chấm điểm.</Empty>;
  return (
    <>
    <ResponsiveContainer width="100%" height={380}>
      <ScatterChart margin={{ top: 10, right: 20, bottom: 24, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" />
        <XAxis type="number" dataKey="x" domain={[0, 100]} name="Market validation" tick={{ fill: "var(--muted)", fontSize: 11 }} stroke="var(--axis)"
               label={{ value: "Market validation (demand) →", position: "insideBottom", offset: -12, fill: "var(--muted)", fontSize: 11 }} />
        <YAxis type="number" dataKey="y" domain={[0, 100]} name="Wave potential" tick={{ fill: "var(--muted)", fontSize: 11 }} stroke="var(--axis)" width={40}
               label={{ value: "Wave ↑", angle: -90, position: "insideLeft", fill: "var(--muted)", fontSize: 11 }} />
        <ZAxis type="number" dataKey="opportunity" range={[40, 260]} name="Opportunity" />
        <ReferenceLine x={50} stroke="var(--axis)" strokeDasharray="4 4" />
        <ReferenceLine y={55} stroke="var(--axis)" strokeDasharray="4 4" />
        <Tooltip cursor={{ strokeDasharray: "3 3" }}
                 content={({ payload }) => {
                   const p = payload?.[0]?.payload;
                   if (!p) return null;
                   return (
                     <div className="card px-3 py-2 text-xs">
                       <div className="font-semibold">{p.name}</div>
                       <div>Demand {p.x} · Wave {p.y} · Opportunity {p.opportunity}</div>
                       <div className="text-muted">{p.quadrant} · {p.decision}</div>
                     </div>
                   );
                 }} />
        {QUAD.map(([q, label, color]) => (
          <Scatter key={q} name={label} data={data.points.filter((p: any) => p.quadrant === q)} fill={color}
                   stroke="var(--surface-1)" strokeWidth={2}
                   onClick={(p: any) => { if (p?.id) window.location.href = `/products/${p.id}`; }} style={{ cursor: "pointer" }} />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
    <div className="flex flex-wrap justify-center gap-x-5 gap-y-1.5 text-xs mt-1">
      {QUAD.map(([q, label, color]) => (
        <span key={q} className="inline-flex items-center gap-1.5 text-ink2">
          <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />
          {label} <b className="text-ink tnum">{data.counts[q] || 0}</b>
        </span>
      ))}
    </div>
    </>
  );
}

export default function Discovery() {
  const [tab, setTab] = useState("breakout");
  const [country, setCountry] = useState("");
  const team = useTeam();
  const { data, error, reload } = useApi(`/discovery/tab/${tab}${qs({ country, funnel: team })}`);
  useEvents((e) => { if (["DECISION_CHANGED", "PRODUCT_DISCOVERED", "SEARCH_DONE"].includes(e.type)) reload(); });
  return (
    <div>
      <PageHeader title="Product Discovery" subtitle="Không chỉ “sản phẩm bán chạy” — tìm sản phẩm độc lạ, có sóng, có chất liệu creative, hợp market và hợp gu team MKT">
        <Link href="/search" className="btn btn-primary">Tìm sản phẩm mới</Link>
      </PageHeader>
      <div className="mb-3"><MarketPicker value={country} onChange={setCountry} /></div>
      <Card title="Opportunity Radar — wave × market validation" className="mb-4"><RadarChart country={country} funnel={team} /></Card>
      <div className="flex flex-wrap gap-1.5 mb-4">
        {TAB_ORDER.map((t) => (
          <button key={t} onClick={() => setTab(t)} className="btn text-xs" style={{ background: tab === t ? "var(--surface-2)" : undefined, fontWeight: tab === t ? 700 : 400 }}>
            {data?.tabs?.[t] || t}
          </button>
        ))}
      </div>
      {!data ? <Loading error={error} /> : data.rows.length === 0 ? <Card><Empty>Chưa có sản phẩm trong nhóm “{data.title}”.</Empty></Card> : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
          {data.rows.map((p: any) => <ProductMediaCard key={p.id} p={p} onVoted={reload} />)}
        </div>
      )}
    </div>
  );
}
