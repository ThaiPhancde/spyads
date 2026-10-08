"use client";
import Link from "next/link";
import { useState } from "react";
import { Card, Empty, Loading, PageHeader, SeverityIcon } from "@/components/ui";
import { api, fmt, qs, useAction, useApi } from "@/lib/api";

const LABELS: Record<string, string> = {
  hidden_winner: "New Hidden Winner", competitor_scaling: "Competitor scaling", new_market: "New market", creative_velocity_spike: "Creative velocity spike",
  price_change: "Price change", store_traffic_spike: "Store traffic spike", negative_comments_increasing: "Negative comments ↑",
  saturation_increasing: "Saturation ↑", ad_rejection_increasing: "Ad rejection ↑", cod_refusal_increasing: "COD refusal ↑",
  roas_dropping: "ROAS dropping", delivery_rate_dropping: "Delivery rate ↓",
};

export default function Alerts() {
  const [type, setType] = useState("");
  const [unread, setUnread] = useState(false);
  const { data, error, reload } = useApi(`/alerts${qs({ type, unread })}`);
  const act = useAction();
  if (!data) return <Loading error={error} />;
  const markAll = () => act.run(async () => { await api("/alerts/read-all", { method: "POST" }); reload(); });
  const mark = (id: number) => act.run(async () => { await api(`/alerts/${id}/read`, { method: "POST" }); reload(); });
  return (
    <div>
      {act.error && <div className="text-sm mb-2" style={{ color: "var(--critical)" }}>Lỗi: {act.error}</div>}
      <PageHeader title="Alert Engine" subtitle={`${data.unread} chưa đọc`}>
        <label className="text-sm flex items-center gap-1.5"><input type="checkbox" checked={unread} onChange={(e) => setUnread(e.target.checked)} /> Chưa đọc</label>
        <button className="btn" onClick={markAll}>Đánh dấu đã đọc tất cả</button>
      </PageHeader>
      <div className="flex flex-wrap gap-2 mb-4">
        <button className="btn text-xs" style={{ fontWeight: !type ? 700 : 400 }} onClick={() => setType("")}>Tất cả</button>
        {Object.entries(LABELS).map(([k, l]) => (
          <button key={k} className="btn text-xs" style={{ fontWeight: type === k ? 700 : 400, background: type === k ? "var(--surface-2)" : undefined }} onClick={() => setType(k)}>
            {l} <span className="text-muted">{data.counts[k] || 0}</span>
          </button>
        ))}
      </div>
      <Card pad={false}>
        {data.rows.length === 0 && <Empty>Không có cảnh báo.</Empty>}
        {data.rows.map((a: any) => (
          <div key={a.id} className="flex gap-3 px-4 py-3 border-b" style={{ borderColor: "var(--grid)", opacity: a.is_read ? 0.6 : 1 }}>
            <SeverityIcon s={a.severity} />
            <div className="flex-1">
              <div className="text-sm font-medium">
                {a.product_id ? <Link className="hover:underline" href={`/products/${a.product_id}`}>{a.title}</Link> :
                  a.advertiser_id ? <Link className="hover:underline" href={`/competitors?focus=${a.advertiser_id}`}>{a.title}</Link> : a.title}
              </div>
              <div className="text-xs text-ink2">{a.message}</div>
              <div className="text-[11px] text-muted mt-0.5">{LABELS[a.type] || a.type} · {a.severity} · {fmt.dt(a.created_at)}</div>
            </div>
            {!a.is_read && <button className="btn text-xs self-start" onClick={() => mark(a.id)}>Đã đọc</button>}
          </div>
        ))}
      </Card>
    </div>
  );
}
