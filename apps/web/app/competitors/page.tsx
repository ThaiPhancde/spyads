"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { TrendChart } from "@/components/charts";
import { Card, Loading, PageHeader, Pill, Stat } from "@/components/ui";
import { api, fmt, qs, useAction, useApi } from "@/lib/api";

function Radar() {
  const sp = useSearchParams();
  const meta = useApi("/meta");
  const [country, setCountry] = useState("");
  const [watched, setWatched] = useState(false);
  const { data, error, reload } = useApi(`/competitors${qs({ country, watched_only: watched })}`);
  const [focus, setFocus] = useState<number | null>(sp.get("focus") ? Number(sp.get("focus")) : null);
  const detail = useApi(focus ? `/competitors/${focus}` : null);
  useEffect(() => { if (focus) window.scrollTo({ top: 0, behavior: "smooth" }); }, [focus]);
  const act = useAction();
  const toggleWatch = (id: number, w: boolean) => act.run(async () => { await api(`/competitors/${id}/watch?watched=${w}`, { method: "POST" }); reload(); detail.reload(); });

  return (
    <div>
      {act.error && <div className="text-sm mb-2" style={{ color: "var(--critical)" }}>Lỗi: {act.error}</div>}
      <PageHeader title="Competitor Radar" subtitle="Theo dõi advertiser / store — ai đang scale nhanh">
        <select className="input" value={country} onChange={(e) => setCountry(e.target.value)}><option value="">Mọi thị trường mục tiêu</option><option value="PH">Philippines ★</option><option value="ME">Trung Đông</option><option value="US">Mỹ</option><option value="EU">Châu Âu + UK</option><option value="AU">Úc / NZ</option><option value="VN">Việt Nam</option><option value="WW">Toàn cầu</option>{meta.data?.countries.map((c: string) => <option key={c}>{c}</option>)}</select>
        <label className="text-sm flex items-center gap-1.5"><input type="checkbox" checked={watched} onChange={(e) => setWatched(e.target.checked)} /> Chỉ đối thủ đang theo dõi</label>
      </PageHeader>
      {focus && detail.data && (
        <Card title={<span>{detail.data.advertiser.name} <span className="text-muted font-normal">· {detail.data.advertiser.country} · {detail.data.advertiser.platform}</span></span>}
              action={<span className="flex gap-2">
                <button className="btn text-xs" onClick={() => toggleWatch(focus, !detail.data.advertiser.watched)}>{detail.data.advertiser.watched ? "★ Bỏ theo dõi" : "☆ Theo dõi"}</button>
                <button className="btn text-xs" onClick={() => setFocus(null)}>Đóng</button></span>} className="mb-4">
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <TrendChart data={detail.data.timeseries} series={[{ key: "active_ads", label: "Active ads" }]} height={180} />
            <div className="max-h-56 overflow-y-auto">
              <table className="data"><thead><tr><th>Ngày</th><th>Ad</th><th>Country</th><th>Status</th></tr></thead>
                <tbody>{detail.data.ads.map((a: any) => (
                  <tr key={a.id}><td className="text-xs whitespace-nowrap">{fmt.date(a.first_seen)}</td>
                    <td className="text-xs"><Link className="hover:underline" href={`/products/${a.product_id}`}>{a.text}</Link></td><td>{a.country}</td>
                    <td>{a.active ? <Pill tone="good">active</Pill> : <Pill>stopped</Pill>}</td></tr>
                ))}</tbody></table>
            </div>
          </div>
        </Card>
      )}
      {!data ? <Loading error={error} /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
            <Stat label="Advertisers tracked" value={fmt.n(data.total)} />
            <Stat label="Đang scale nhanh (7d ≥ 2× tuần trước)" value={data.scaling} />
            <Stat label="Đang theo dõi" value={data.rows.filter((r: any) => r.watched).length} />
          </div>
          <Card title="Advertisers" pad={false}>
            <div className="overflow-x-auto">
              <table className="data">
                <thead><tr><th></th><th>Advertiser</th><th>Country</th><th>Active ads</th><th>New 7d</th><th>Prev 7d</th><th>Growth</th><th>Products</th><th>Markets</th><th>Top products</th></tr></thead>
                <tbody>{data.rows.map((r: any) => (
                  <tr key={r.id} className="cursor-pointer" onClick={() => setFocus(r.id)}>
                    <td>{r.watched ? "★" : ""}</td>
                    <td><div className="font-medium">{r.name}</div><div className="text-[11px] text-muted">{r.platform}</div></td>
                    <td>{r.country}</td><td className="tnum">{r.active_ads}</td><td className="tnum">{r.new_ads_7d}</td><td className="tnum">{r.prev_7d}</td>
                    <td>{r.scaling ? <Pill tone="warn">scaling {r.growth_7d !== null && r.growth_7d < 9 ? fmt.signedPct(r.growth_7d) : "new"}</Pill> : <span className="tnum text-xs">{r.growth_7d === null ? "—" : r.growth_7d >= 9 ? "new" : fmt.signedPct(r.growth_7d)}</span>}</td>
                    <td className="tnum">{r.products}</td><td className="text-xs">{r.countries.join(", ")}</td>
                    <td className="text-xs">{r.top_products.map((p: any) => p.name).join(", ")}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Radar /></Suspense>;
}
