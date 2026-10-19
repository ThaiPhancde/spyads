"use client";
import Link from "next/link";
import { useState } from "react";
import { api, fmt, useApi } from "@/lib/api";
import { Card, Loading, PageHeader } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { EVENT_LABELS, Pager } from "@/components/workspace/common";
import { toast } from "sonner";

export default function Notifications() {
  const [unread, setUnread] = useState(false); const [offset, setOffset] = useState(0); const [busy, setBusy] = useState(false);
  const { data, error, reload } = useApi(`/workspace/notifications?unread=${unread}&offset=${offset}`);
  return <div className="space-y-3"><PageHeader title="Thông báo của tôi"><Button variant="outline" onClick={() => { setUnread(!unread); setOffset(0); }}>{unread ? "Xem tất cả" : "Chỉ chưa đọc"}</Button><Button variant="ghost" onClick={reload}>Cập nhật</Button></PageHeader>
    {!data ? <Loading error={error} /> : <>{data.rows.map((n: any) => <Card key={n.id} className={n.read ? "opacity-70" : "border-primary/40"}><div className="flex items-start justify-between gap-3"><div><Link href={`/competitors/${n.advertiser_id}`} className="font-medium hover:underline">{!n.read && "● "}{n.advertiser}: {EVENT_LABELS[n.kind] || n.kind}</Link><p className="text-xs text-muted-foreground">{fmt.dt(n.at)}</p><p className="mt-2 text-sm">{n.data.fields?.join(", ") || n.data.message}</p></div><Button size="sm" variant="outline" disabled={busy} onClick={async () => { setBusy(true); try { await api(`/workspace/notifications/${n.id}`, { method: "PATCH", body: JSON.stringify({ read: !n.read }) }); reload(); } catch (e: any) { toast.error(e.message); } finally { setBusy(false); } }}>{n.read ? "Chưa đọc" : "Đã đọc"}</Button></div></Card>)}{!data.rows.length && <p className="py-5 text-muted-foreground">Chưa có thông báo.</p>}<Pager offset={offset} total={data.total} onChange={setOffset} /></>}
  </div>;
}
