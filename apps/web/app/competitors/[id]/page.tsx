"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, fmt, useApi } from "@/lib/api";
import { Card, Loading, PageHeader } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { AdActions, FollowButton } from "@/components/workspace/actions";
import { EVENT_LABELS, Pager, STATUS_LABELS } from "@/components/workspace/common";

export default function Competitor({ params }: { params: { id: string } }) {
  const root = `/competitors/${params.id}`; const detail = useApi(root); const [tab, setTab] = useState("overview"); const [offset, setOffset] = useState(0); const [busy, setBusy] = useState(false);
  const listPath = tab === "timeline" ? "timeline" : tab === "products" ? "products" : `ads?tab=${tab === "overview" ? "all" : tab}`;
  const list = useApi(detail.data?.followed ? `${root}/${listPath}${listPath.includes("?") ? "&" : "?"}offset=${offset}` : null);
  const [snapshotId, setSnapshotId] = useState<number | null>(null);
  const [snapshotOffset, setSnapshotOffset] = useState(0);
  const snapshots = useApi(snapshotId ? `${root}/ads/${snapshotId}/snapshots?offset=${snapshotOffset}` : null);
  useEffect(() => { const timer = setInterval(() => detail.reload(), 30_000); return () => clearInterval(timer); }, [root]);
  if (!detail.data) return <Loading error={detail.error} />;
  const d = detail.data, a = d.advertiser;
  async function action(path: string, body?: any) { setBusy(true); try { await api(root + path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) }); detail.reload(); list.reload(); } catch (e: any) { toast.error(e.message); } finally { setBusy(false); } }
  return <div className="space-y-4"><Link href="/competitors" className="text-sm underline">← Đối thủ của tôi</Link><PageHeader title={a.name} subtitle={`${a.platform || ""} · ${a.country || "Chưa rõ thị trường"} · ${a.source || "Nguồn cũ"}`}><FollowButton id={a.id} after={() => { detail.reload(); list.reload(); }} /></PageHeader>
    <Card><p className="text-sm">{a.capability.detail}</p><p className="mt-2 text-xs text-muted-foreground">ID nguồn: {a.source_advertiser_id || "Chưa xác minh"}</p>{a.page_url && <a href={a.page_url} className="text-sm underline" target="_blank" rel="noreferrer">Trang advertiser</a>}
      {d.followed && <div className="mt-4 flex flex-wrap items-center gap-3 text-sm"><label>Chu kỳ <select aria-label="Chu kỳ kiểm tra" value={d.interval_hours} disabled={busy} onChange={(e) => action("/follow", { interval_hours: Number(e.target.value) })} className="rounded border bg-background px-2 py-1">{[6, 12, 24].map((h) => <option key={h} value={h}>{h} giờ</option>)}</select></label><Button size="sm" disabled={busy || !a.capability.supported || d.scan.source_status === "running"} onClick={() => action("/refresh")}>Kiểm tra ngay</Button><span>{STATUS_LABELS[d.scan.source_status]}</span></div>}
      {d.followed && <div className="mt-3 space-y-1 text-xs text-muted-foreground"><p>Kiểm tra: {fmt.dt(d.scan.last_checked_at)} · Thành công: {fmt.dt(d.scan.last_success_at)}</p><p>Lần tiếp theo: {fmt.dt(d.scan.next_check_at)}</p><p>{d.scan.error || (d.scan.source_status === "success" && d.scan.last_changes === 0 ? "Không phát hiện thay đổi" : "")}</p></div>}
    </Card>
    {!d.followed ? <Card>Follow advertiser này để đồng bộ dữ liệu đã có và bắt đầu lịch theo dõi.</Card> : <>
      <div className="flex flex-wrap gap-2">{[["overview", "Tổng quan"], ["active", "Ads đang hoạt động"], ["new", "Ads mới"], ["timeline", "Lịch sử hoạt động"], ["products", "Sản phẩm đang quảng cáo"]].map(([k, label]) => <Button key={k} variant={tab === k ? "default" : "outline"} onClick={() => { setTab(k); setOffset(0); }}>{label}</Button>)}</div>
      {tab === "overview" && <p className="text-sm">{d.counts.active || 0} active · {d.counts.inactive || 0} đã xác nhận dừng · {d.counts.unknown || 0} chưa xác định. Dữ liệu cũ giữ trạng thái chưa xác định đến khi có bằng chứng từ nguồn.</p>}
      {tab === "new" && <Button size="sm" variant="outline" disabled={busy} onClick={() => action("/seen")}>Đánh dấu đã xem Ads mới</Button>}
      {!list.data ? <Loading error={list.error} /> : <><div className={tab === "timeline" ? "space-y-3 border-l-2 pl-5" : "grid gap-4 md:grid-cols-2 xl:grid-cols-3"}>{list.data.rows.map((r: any) => tab === "timeline" ? <Card key={r.id}><b className="text-sm">{EVENT_LABELS[r.kind] || r.kind}</b><p className="text-xs text-muted-foreground">{fmt.dt(r.at)}</p>{r.kind === "scan_complete" && <p className="mt-2 text-sm">{r.data.changes ? `${r.data.changes} thay đổi` : "Không phát hiện thay đổi"}{!r.data.complete && " · chỉ có dữ liệu một phần"}</p>}<p className="text-sm">{r.data.fields?.join(", ") || r.data.message}</p>{r.data.ad_id && <Link href={`/ads?ad_id=${r.data.ad_id}`} className="text-xs underline">Xem Ads #{r.data.ad_id}</Link>}</Card>
        : tab === "products" ? <Card key={r.id}><Link href={`/products/${r.id}`} className="font-medium hover:underline">{r.name}</Link></Card>
        : <Card key={r.id} title={r.data.title || `Ads ${r.source_ad_id}`}><p className="text-xs">{r.status === "active" ? "Đang hoạt động" : r.status === "inactive" ? "Nguồn xác nhận dừng" : "Không xác định"} · {r.baseline ? "Dữ liệu đã có" : "Phát hiện mới"}</p><p className="my-3 line-clamp-5 whitespace-pre-wrap text-sm">{r.data.text}</p>{r.data.media?.[0] && (r.data.media_type === "video" ? <video controls preload="none" className="my-2 max-h-64 w-full rounded" src={r.data.media[0]} /> : <img loading="lazy" alt={r.data.title || "Creative quảng cáo"} className="my-2 max-h-64 w-full rounded object-contain" src={r.data.media[0]} />)}<p className="text-xs text-muted-foreground">{(r.data.markets || []).join(", ")} · {fmt.dt(r.last_seen_at)}</p>
          <p className="my-2 text-xs">{Object.entries(r.data.metrics || {}).map(([k, v]) => `${k}: ${fmt.n(v)}`).join(" · ") || "Nguồn chưa cung cấp tương tác"}</p>{r.data.snapshot_url && <a className="block text-sm underline" href={r.data.snapshot_url} target="_blank" rel="noreferrer">Xem quảng cáo gốc</a>}{r.data.landing_url && <a className="block truncate text-xs underline" href={r.data.landing_url} target="_blank" rel="noreferrer">{r.data.landing_url}</a>}{r.ad_id && <AdActions id={r.ad_id} />}<Button variant="ghost" size="sm" onClick={() => { setSnapshotId(r.id); setSnapshotOffset(0); }}>Xem snapshot & số liệu</Button></Card>)}</div>
        {!list.data.rows.length && <p className="py-6 text-sm text-muted-foreground">Chưa có dữ liệu trong mục này.</p>}<Pager offset={offset} total={list.data.total} onChange={setOffset} /></>}
      {snapshotId && <Card title="Lịch sử snapshot" action={<Button variant="ghost" size="sm" onClick={() => setSnapshotId(null)}>Đóng</Button>}>{snapshots.error && <p role="alert">{snapshots.error}</p>}{snapshots.data?.rows.map((s: any) => <div className="border-b py-3 text-sm" key={s.id}><p>{fmt.dt(s.observed_at)} · {s.source} · {s.data.status}</p><p>{s.data.text}</p><p className="break-all">{s.data.landing_url}</p><p>{Object.entries(s.data.metrics || {}).map(([k, v]) => `${k}: ${fmt.n(v)}`).join(" · ") || "Không có số liệu tương tác"}</p></div>)}{snapshots.data && <Pager offset={snapshotOffset} total={snapshots.data.total} onChange={setSnapshotOffset} />}</Card>}
    </>}
  </div>;
}
