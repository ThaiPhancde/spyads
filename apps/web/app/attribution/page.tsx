"use client";
import { useState } from "react";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { fmt, qs, useApi } from "@/lib/api";
import { useEvents } from "@/lib/realtime";

const BY: [string, string][] = [["ad_external_id", "Ad"], ["campaign_id", "Campaign"], ["creative_ref", "Creative"], ["sales_agent", "Sales"], ["country", "Market"]];

export default function Attribution() {
  const [by, setBy] = useState("ad_external_id");
  const [days, setDays] = useState(60);
  const { data, error, reload } = useApi(`/attribution${qs({ by, days })}`);
  useEvents((e) => { if (e.type.startsWith("ORDER_") || e.type.startsWith("SHIPMENT_")) reload(); });
  return (
    <div>
      <PageHeader title="Attribution chain" subtitle="Creative → Ad → Campaign → Order → Shipment → Delivered. CPA thấp chưa chắc tốt — xem Cost / Delivered.">
        <div className="flex rounded-lg border overflow-hidden" style={{ borderColor: "var(--border)" }}>
          {BY.map(([k, l]) => (
            <button key={k} onClick={() => setBy(k)} className="text-xs px-3 py-1.5"
                    style={{ background: by === k ? "var(--series-1)" : "var(--card)", color: by === k ? "#fff" : "var(--ink2)" }}>{l}</button>
          ))}
        </div>
        <select className="input" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} ngày</option>)}</select>
      </PageHeader>
      {!data ? <Loading error={error} /> : data.rows.length === 0 ? (
        <Card><Empty>Chưa có đơn hàng. Kết nối Pancake / CRM qua <code>POST /api/webhooks/orders</code> (kèm campaign_id, ad_external_id, creative_ref) và spend qua Meta Ads connector.</Empty></Card>
      ) : (
        <Card pad={false}>
          <div className="overflow-x-auto">
            <table className="data">
              <thead><tr><th>{BY.find(([k]) => k === by)?.[1]}</th><th>Spend</th><th>Orders</th><th>CPA</th><th>Confirmed</th><th>Delivered</th><th>Refused</th><th>Returned</th><th>Delivery</th><th>Refusal</th><th>Cost / Delivered</th><th>Revenue delivered</th></tr></thead>
              <tbody>{data.rows.map((r: any) => (
                <tr key={r.key}>
                  <td className="font-medium">{r.key}</td><td className="tnum">{fmt.money(r.spend)}</td><td className="tnum">{r.orders}</td><td className="tnum">{fmt.money(r.cpa)}</td>
                  <td className="tnum">{r.confirmed}</td><td className="tnum">{r.delivered}</td><td className="tnum">{r.refused}</td><td className="tnum">{r.returned}</td>
                  <td className="tnum">{fmt.pct(r.delivery_rate)}</td>
                  <td className="tnum" style={{ color: (r.refusal_rate || 0) > 0.22 ? "var(--critical)" : undefined }}>{fmt.pct(r.refusal_rate)}</td>
                  <td className="tnum font-semibold">{fmt.money(r.cost_per_delivered)}</td><td className="tnum">{fmt.money(r.revenue_delivered)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
