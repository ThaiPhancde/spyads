"use client";
import Link from "next/link";
import { useState } from "react";
import { Search } from "lucide-react";
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { MarketPicker } from "@/components/MarketPicker";
import { ProductMediaCard } from "@/components/media";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { qs, useApi } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";

const TAB_ORDER = ["breakout", "experimental", "new", "hidden", "wave", "novelty", "creative", "mess", "ladi", "favorites", "scaling", "saved", "risk"];
// Quadrants: 3 validated categorical slots + neutral gray for "low signal" (≤3 coloured series on a scatter).
const QUAD: [string, string, string][] = [
  ["BREAKOUT", "🚀 Breakout (wave cao + đã được xác nhận)", "var(--series-1)"],
  ["EXPERIMENTAL", "💎 Experimental (wave cao, chưa xác nhận)", "var(--series-2)"],
  ["STABLE_WINNER", "🏛 Stable winner (xác nhận, ít sóng mới)", "var(--series-3)"],
  ["LOW_SIGNAL", "Low signal", "var(--axis)"],
];
const tick = { fill: "var(--muted-foreground)", fontSize: 11 };

function RadarChart({ country, funnel }: { country: string; funnel: string }) {
  const { data } = useApi(`/discovery/radar${qs({ country, funnel })}`);
  if (!data) return <Loading />;
  if (!data.points.length) return <Empty>Chưa có sản phẩm được chấm điểm.</Empty>;
  return (
    <>
      <ResponsiveContainer width="100%" height={380}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 24, left: 0 }}>
          <CartesianGrid stroke="var(--border)" />
          <XAxis type="number" dataKey="x" domain={[0, 100]} name="Market validation" tick={tick} stroke="var(--axis)"
                 label={{ value: "Market validation (demand) →", position: "insideBottom", offset: -12, ...tick }} />
          <YAxis type="number" dataKey="y" domain={[0, 100]} name="Wave potential" tick={tick} stroke="var(--axis)" width={40}
                 label={{ value: "Wave ↑", angle: -90, position: "insideLeft", ...tick }} />
          <ZAxis type="number" dataKey="opportunity" range={[40, 260]} name="Opportunity" />
          <ReferenceLine x={50} stroke="var(--axis)" strokeDasharray="4 4" />
          <ReferenceLine y={55} stroke="var(--axis)" strokeDasharray="4 4" />
          <Tooltip cursor={{ strokeDasharray: "3 3" }}
                   content={({ payload }) => {
                     const p = payload?.[0]?.payload;
                     if (!p) return null;
                     return (
                       <div className="rounded-lg border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
                         <div className="font-semibold">{p.name}</div>
                         <div className="tnum">Demand {p.x} · Wave {p.y} · Opportunity {p.opportunity}</div>
                         <div className="text-muted-foreground">{p.quadrant} · {p.decision}</div>
                       </div>
                     );
                   }} />
          {QUAD.map(([q, label, color]) => (
            <Scatter key={q} name={label} data={data.points.filter((p: any) => p.quadrant === q)} fill={color}
                     stroke="var(--card)" strokeWidth={2}
                     onClick={(p: any) => { if (p?.id) window.location.href = `/products/${p.id}`; }} style={{ cursor: "pointer" }} />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
      <div className="mt-1 flex flex-wrap justify-center gap-x-5 gap-y-1.5 text-xs">
        {QUAD.map(([q, label, color]) => (
          <span key={q} className="inline-flex items-center gap-1.5 text-ink2">
            <span className="inline-block size-2.5 shrink-0 rounded-full" style={{ background: color }} />
            {label} <b className="tnum text-foreground">{data.counts[q] || 0}</b>
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
    <div className="space-y-4">
      <PageHeader title="Product Discovery" subtitle="Không chỉ “sản phẩm bán chạy” — tìm sản phẩm độc lạ, có sóng, có chất liệu creative, hợp market và hợp gu team MKT">
        <Button asChild><Link href="/search"><Search />Tìm sản phẩm mới</Link></Button>
      </PageHeader>
      <MarketPicker value={country} onChange={setCountry} />
      <Card title="Opportunity Radar — wave × market validation"><RadarChart country={country} funnel={team} /></Card>
      <ToggleGroup type="single" variant="outline" size="sm" spacing={1.5} value={tab} onValueChange={(v) => v && setTab(v)} className="flex-wrap justify-start">
        {TAB_ORDER.map((t) => (
          <ToggleGroupItem key={t} value={t} className="text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
            {data?.tabs?.[t] || t}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      {!data ? <Loading error={error} /> : data.rows.length === 0 ? <Card><Empty>Chưa có sản phẩm trong nhóm “{data.title}”.</Empty></Card> : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {data.rows.map((p: any) => <ProductMediaCard key={p.id} p={p} onVoted={reload} />)}
        </div>
      )}
    </div>
  );
}
