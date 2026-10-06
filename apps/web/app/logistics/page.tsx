"use client";
import Link from "next/link";
import { useState } from "react";
import { BarList, Funnel, TrendChart } from "@/components/charts";
import { Card, Loading, PageHeader, ScoreBar, Stat } from "@/components/ui";
import { fmt, qs, useApi } from "@/lib/api";

export default function Logistics() {
  const meta = useApi("/meta");
  const [country, setCountry] = useState("");
  const [days, setDays] = useState("60");
  const { data, error } = useApi(`/logistics${qs({ country, days })}`);
  if (!data) return <Loading error={error} />;
  const f = data.funnel, ar = data.ad_rejection;
  return (
    <div>
      <PageHeader title="COD Refusal & Ad Rejection" subtitle="Tách riêng: khách từ chối nhận hàng (COD) ≠ quảng cáo bị nền tảng từ chối">
        <select className="input" value={country} onChange={(e) => setCountry(e.target.value)}><option value="">Country</option>{meta.data?.countries.map((c: string) => <option key={c}>{c}</option>)}</select>
        <select className="input" value={days} onChange={(e) => setDays(e.target.value)}>{["14", "30", "60", "90", "365"].map((d) => <option key={d} value={d}>{d} ngày</option>)}</select>
      </PageHeader>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Stat label="Orders" value={fmt.n(f.orders)} sub={`Confirm ${fmt.pct(f.confirm_rate)}`} />
        <Stat label="Delivered" value={fmt.n(f.delivered)} sub={`Delivery ${fmt.pct(f.delivery_rate)}`} />
        <Stat label="COD refusal rate" value={fmt.pct(f.refusal_rate)} sub="Refused ÷ delivery attempts" />
        <Stat label="Ad rejection rate" value={fmt.pct(ar.rate)} sub={`${ar.rejected}/${ar.submitted} creatives`} />
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
        <Card title="COD funnel">
          <Funnel stages={[
            { label: "Orders", value: f.orders }, { label: "Confirmed", value: f.confirmed }, { label: "Shipped", value: f.shipped },
            { label: "Delivered", value: f.delivered }, { label: "Refused", value: f.refused, ok: false, of: f.shipped }, { label: "Other failed", value: f.other_failed, ok: false, of: f.shipped },
          ]} />
        </Card>
        <Card title="Lý do khách từ chối (AI phân loại từ ghi chú call center / carrier)">
          <BarList rows={data.refusal_reasons.map((r: any) => ({ label: r.reason.replace(/_/g, " "), value: r.share, hint: `${r.count} đơn` }))} format={(v) => fmt.pct(v, 0)} color="var(--serious)" />
        </Card>
        <Card title="Refusal rate theo tuần">
          <TrendChart data={data.weekly} x="week" series={[{ key: "refusal_rate", label: "Refusal" }]} percent />
        </Card>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="Theo sản phẩm — Customer Rejection Score" pad={false}>
          <table className="data"><thead><tr><th>Product</th><th>Orders</th><th>Confirm</th><th>Delivery</th><th>Refusal</th><th>Return</th><th>Rejection score</th></tr></thead>
            <tbody>{data.products.map((p: any) => (
              <tr key={p.id}><td><Link className="hover:underline" href={`/products/${p.id}`}>{p.name}</Link></td><td className="tnum">{p.orders}</td>
                <td className="tnum">{fmt.pct(p.confirm_rate, 0)}</td><td className="tnum">{fmt.pct(p.delivery_rate, 0)}</td>
                <td className="tnum" style={{ color: (p.refusal_rate || 0) > 0.22 ? "var(--critical)" : undefined }}>{fmt.pct(p.refusal_rate, 0)}</td>
                <td className="tnum">{fmt.pct(p.return_rate, 0)}</td><td><ScoreBar value={p.customer_rejection_score} /></td></tr>
            ))}</tbody></table>
        </Card>
        <Card title="Ad rejection theo experiment" pad={false}>
          <table className="data"><thead><tr><th>Product</th><th>Experiment</th><th>Submitted</th><th>Rejected</th><th>Rate</th></tr></thead>
            <tbody>{ar.by_product.map((r: any) => (
              <tr key={r.experiment}><td>{r.product}</td><td className="text-xs">{r.experiment}</td><td className="tnum">{r.submitted}</td><td className="tnum">{r.rejected}</td>
                <td className="tnum" style={{ color: (r.rate || 0) > 0.3 ? "var(--critical)" : undefined }}>{fmt.pct(r.rate, 0)}</td></tr>
            ))}</tbody></table>
          <div className="text-xs text-muted p-3">Policy reasons (medical claims, before/after…) được ghi khi import ad với rejected=true + rejection_reason.</div>
        </Card>
      </div>
    </div>
  );
}
