"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { MarketPicker } from "@/components/MarketPicker";
import { LoadMore } from "@/components/paging";
import { Card, Empty, Loading, PageHeader, RecBadge } from "@/components/ui";
import { fmt, qs, usePaged } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";

const TIER: Record<string, [string, string]> = {
  legendary: ["var(--good)", "Huyền thoại"], strong: ["var(--series-1)", "Mạnh"], regular: ["var(--muted)", "Thường"], testing: ["var(--axis)", "Đang test"],
};
const SORTS: [string, string][] = [["score", "Điểm"], ["trend", "Xu hướng"], ["new", "Ads mới"], ["ads", "Số ads"], ["sellers", "Sellers"], ["force", "Ads mạnh nhất"], ["age", "Mới xuất hiện"]];

function Radar() {
  const sp = useSearchParams();
  const [market, setMarket] = useState(sp.get("market") || "");
  const [window, setWindow] = useState(7);
  const [sort, setSort] = useState("score");
  const team = useTeam();
  const path = `/radar/products${qs({ scope: market, window, funnel: team, sort })}`;
  const { rows, total, loading, error, hasMore, loadMore, reload } = usePaged(path, 50);
  useEvents((e) => { if (["CONNECTOR_SYNCED", "LIVENESS_CHECKED", "SEARCH_DONE"].includes(e.type)) reload(); });

  return (
    <div>
      <PageHeader title="Product Radar" subtitle="Sản phẩm đang chạy theo thị trường + khung thời gian — xu hướng tính trực tiếp từ ngày bắt đầu chạy của quảng cáo trên mọi nền tảng (Meta, TikTok, Snapchat)">
        <div className="flex rounded-lg border overflow-hidden" style={{ borderColor: "var(--border)" }}>
          {[7, 14, 30].map((w) => (
            <button key={w} onClick={() => setWindow(w)} className="text-xs px-3 py-1.5"
                    style={{ background: window === w ? "var(--series-1)" : "var(--surface-1)", color: window === w ? "#fff" : "var(--text-secondary)" }}>{w} ngày</button>
          ))}
        </div>
        <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}>{SORTS.map(([k, l]) => <option key={k} value={k}>Sắp xếp: {l}</option>)}</select>
      </PageHeader>
      <div className="mb-4"><MarketPicker value={market} onChange={setMarket} /></div>
      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : rows.length === 0 ? <Card><Empty>Chưa có sản phẩm đang chạy ở thị trường này.</Empty></Card> : (
        <Card pad={false} title={`${total} sản phẩm đang chạy${team ? ` · funnel ${team}` : ""}`}>
          <div className="overflow-x-auto">
            <table className="data">
              <thead><tr><th></th><th>Sản phẩm</th><th>Xu hướng ({window}d)</th><th>Ads</th><th>Sellers</th><th>Tuổi</th><th>Điểm</th>
                <th>Ad mạnh nhất</th><th>Biến thể scale</th><th>Mess / Ladi</th><th>Thị trường</th><th>Hành động</th></tr></thead>
              <tbody>
                {rows.map((r: any) => {
                  const [tc, tl] = TIER[r.top_tier] || TIER.testing;
                  const up = r.trend.startsWith("↑") || r.trend === "NEW";
                  return (
                    <tr key={r.id}>
                      <td className="w-14">
                        {r.thumb ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={r.thumb} alt="" className="w-12 h-12 object-cover rounded" loading="lazy" />
                          : <div className="w-12 h-12 rounded" style={{ background: "var(--surface-2)" }} />}
                      </td>
                      <td className="min-w-[220px]">
                        <Link href={`/products/${r.id}`} className="font-medium hover:underline leading-snug">{r.name}</Link>
                        <div className="text-[11px] text-muted">{r.code} · {r.category || "—"}</div>
                      </td>
                      <td className="tnum whitespace-nowrap" style={{ color: up ? "var(--good-text)" : r.trend.startsWith("↓") ? "var(--critical)" : undefined }}>
                        {r.trend} <span className="text-xs">{r.growth !== null ? fmt.signedPct(r.growth) : ""}</span>
                        <div className="text-[11px] text-muted">+{r.new_ads} mới / {r.prev_new_ads} trước</div>
                      </td>
                      <td className="tnum">{r.ads}</td>
                      <td className="tnum">{r.sellers}</td>
                      <td className="tnum whitespace-nowrap">{r.age_days} ngày</td>
                      <td className="tnum font-semibold">{fmt.n(r.score)}</td>
                      <td className="whitespace-nowrap text-xs">
                        <span className="inline-block w-2 h-2 rounded-full mr-1" style={{ background: tc }} />{tl} <span className="tnum text-muted">{fmt.n(r.force_max)}</span>
                        {r.strong_ads > 1 && <div className="text-[11px] text-muted">{r.strong_ads} ads mạnh</div>}
                      </td>
                      <td className="tnum">{r.max_variation > 1 ? <span title="Cùng page + cùng nội dung chạy song song = đang scale">×{r.max_variation}</span> : "—"}</td>
                      <td className="tnum text-xs">{r.mess} / {r.ladi}</td>
                      <td className="text-xs">{r.markets.map((m: string) => (m === "ALL" ? "🌍" : m)).join(", ")}</td>
                      <td><RecBadge rec={r.decision} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </Card>
      )}
      <p className="text-xs text-muted mt-3">
        Độ mạnh của quảng cáo = ngày chạy (tối đa 180, 60đ) + số phiên bản (tối đa 10, 25đ) + số nền tảng (tối đa 4, 15đ), ×0,7 nếu đã dừng — theo sonda-imperial.
        Huyền thoại ≥70 · Mạnh ≥45 · Thường ≥20. “Biến thể scale” = số quảng cáo cùng page, cùng nội dung đang chạy song song.
      </p>
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Radar /></Suspense>;
}
