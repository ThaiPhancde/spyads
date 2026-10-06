"use client";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { fmt, useApi } from "@/lib/api";

const DIMS: [string, string][] = [["market", "Market"], ["category", "Category"], ["price_segment", "Price segment"], ["creative_type", "Creative type"],
  ["angle", "Angle"], ["funnel", "Funnel"], ["platform", "Platform"]];

export default function Learning() {
  const { data, error } = useApi("/learning/profile");
  if (!data) return <Loading error={error} />;
  return (
    <div>
      <PageHeader title="Learning Loop" subtitle={`Market data + company data → profile sản phẩm phù hợp với công ty · ${data.total_experiments} experiment đã kết thúc`} />
      <Card title="Winning profiles — tổ hợp market × category × price × creative × funnel" className="mb-4" pad={false}>
        {data.winning_profiles.length === 0 ? <Empty>Chưa có experiment kết thúc.</Empty> : (
          <table className="data"><thead><tr><th>Market</th><th>Category</th><th>Price</th><th>Creative</th><th>Funnel</th><th>Tests</th><th>Win rate</th><th>CVR</th><th>Delivery</th><th>Margin</th><th>Profit</th></tr></thead>
            <tbody>{data.winning_profiles.map((r: any, i: number) => (
              <tr key={i}><td>{r.market}</td><td>{r.category}</td><td>{r.price_segment}</td><td>{r.creative_type}</td><td>{r.funnel}</td>
                <td className="tnum">{r.tests}</td><td className="tnum font-semibold">{fmt.pct(r.win_rate, 0)}</td><td className="tnum">{fmt.pct(r.cvr, 2)}</td>
                <td className="tnum">{fmt.pct(r.delivery_rate, 0)}</td>
                <td className="tnum" style={{ color: (r.margin || 0) < 0 ? "var(--critical)" : "var(--good-text)" }}>{fmt.pct(r.margin, 0)}</td>
                <td className="tnum">{fmt.money(r.profit)}</td></tr>
            ))}</tbody></table>
        )}
      </Card>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {DIMS.map(([k, l]) => (
          <Card key={k} title={`Theo ${l}`} pad={false}>
            <table className="data"><thead><tr><th>{l}</th><th>Tests</th><th>Win rate</th><th>CVR</th><th>Delivery</th><th>Margin</th></tr></thead>
              <tbody>{(data.segments[k] || []).map((r: any) => (
                <tr key={r.value}><td>{r.value}</td><td className="tnum">{r.tests}</td><td className="tnum">{fmt.pct(r.win_rate, 0)}</td><td className="tnum">{fmt.pct(r.cvr, 2)}</td>
                  <td className="tnum">{fmt.pct(r.delivery_rate, 0)}</td><td className="tnum">{fmt.pct(r.margin, 0)}</td></tr>
              ))}</tbody></table>
          </Card>
        ))}
      </div>
      <p className="text-xs text-muted mt-4">Market fit trong Rare Winner Score được lấy từ win rate lịch sử của market/category này — càng nhiều vòng test, hệ thống càng chọn sản phẩm sát với năng lực công ty.</p>
    </div>
  );
}
