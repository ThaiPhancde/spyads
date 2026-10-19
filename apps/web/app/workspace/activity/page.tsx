"use client";
import { useState } from "react";
import { fmt, useApi } from "@/lib/api";
import { Card, Loading, PageHeader } from "@/components/ui";
import { EVENT_LABELS, Pager } from "@/components/workspace/common";
export default function Activity() {
  const [offset, setOffset] = useState(0); const { data, error } = useApi(`/workspace/activity?offset=${offset}`);
  return <div className="space-y-3"><PageHeader title="Lịch sử hoạt động của tôi" />{!data ? <Loading error={error} /> : <>{data.rows.map((a: any) => <Card key={a.id}><p className="text-sm">{EVENT_LABELS[a.kind] || a.kind}{a.data.name ? ` · ${a.data.name}` : ""}{a.data.ad_id ? ` · Ads #${a.data.ad_id}` : ""}</p><span className="text-xs text-muted-foreground">{fmt.dt(a.at)}</span></Card>)}<Pager offset={offset} total={data.total} onChange={setOffset} /></>}</div>;
}
