"use client";
import Link from "next/link";
import { useState } from "react";
import { AlertCircle, CheckCheck } from "lucide-react";
import { Card, Empty, Loading, PageHeader, SeverityIcon } from "@/components/ui";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, fmt, qs, useAction, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  hidden_winner: "New Hidden Winner", competitor_scaling: "Competitor scaling", new_market: "New market", creative_velocity_spike: "Creative velocity spike",
  price_change: "Price change", store_traffic_spike: "Store traffic spike", negative_comments_increasing: "Negative comments ↑",
  saturation_increasing: "Saturation ↑",
};
const ALL = "__all";

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
      {act.error && <Alert variant="destructive" className="mb-2"><AlertCircle /><AlertTitle>Lỗi</AlertTitle><AlertDescription>{act.error}</AlertDescription></Alert>}
      <PageHeader title="Alert Engine" subtitle={`${data.unread} chưa đọc`}>
        <div className="flex items-center gap-1.5">
          <Checkbox id="alerts-unread" checked={unread} onCheckedChange={(v) => setUnread(v === true)} />
          <Label htmlFor="alerts-unread" className="text-sm font-normal">Chưa đọc</Label>
        </div>
        <Button variant="outline" size="sm" disabled={act.busy} onClick={markAll}><CheckCheck />Đánh dấu đã đọc tất cả</Button>
      </PageHeader>
      <ToggleGroup type="single" variant="outline" size="sm" spacing={2} className="mb-4 flex-wrap" value={type || ALL} onValueChange={(v) => v && setType(v === ALL ? "" : v)}>
        <ToggleGroupItem value={ALL} className="text-xs data-[state=on]:font-semibold">Tất cả</ToggleGroupItem>
        {Object.entries(LABELS).map(([k, l]) => (
          <ToggleGroupItem key={k} value={k} className="text-xs data-[state=on]:font-semibold">
            {l} <span className="tnum text-muted-foreground">{data.counts[k] || 0}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <Card pad={false}>
        {data.rows.length === 0 && <Empty>Không có cảnh báo.</Empty>}
        {data.rows.map((a: any) => (
          <div key={a.id} className={cn("flex gap-3 border-b px-4 py-3 last:border-b-0", a.is_read && "opacity-60")}>
            <SeverityIcon s={a.severity} />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">
                {a.product_id ? <Link className="hover:underline" href={`/products/${a.product_id}`}>{a.title}</Link> :
                  a.advertiser_id ? <Link className="hover:underline" href={`/competitors?focus=${a.advertiser_id}`}>{a.title}</Link> : a.title}
              </div>
              <div className="text-xs text-ink2">{a.message}</div>
              <div className="mt-0.5 text-[11px] text-muted-foreground">{LABELS[a.type] || a.type} · {a.severity} · {fmt.dt(a.created_at)}</div>
            </div>
            {!a.is_read && <Button variant="outline" size="xs" className="self-start" disabled={act.busy} onClick={() => mark(a.id)}>Đã đọc</Button>}
          </div>
        ))}
      </Card>
    </div>
  );
}
