"use client";
import Link from "next/link";
import { useState } from "react";
import { GitMerge, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { LinkEvidence } from "@/components/cluster-dialog";
import { Card, Empty, Loading, PageHeader, Pill } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, fmt, useApi } from "@/lib/api";
import { getUser } from "@/lib/realtime";

const TABS: [string, string][] = [["groups", "Product Clusters"], ["duplicates", "Ads trùng"], ["pending", "Cần xác nhận"], ["similar", "Sản phẩm tương tự"], ["auto_clustered", "Tự động gom"], ["accepted", "Đã xác nhận"], ["blocked", "Đã chặn"], ["auto_merged", "Lịch sử gộp cũ"], ["impure", "Nghi lẫn sản phẩm"]];
const user = () => getUser().name || null;

function Thumb({ id }: { id?: number | null }) {
  return id ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={`/api/creatives/${id}/thumb`} alt="" className="h-24 w-24 shrink-0 rounded object-cover" loading="lazy" />
    : <div className="h-24 w-24 shrink-0 rounded bg-muted" />;
}

function Side({ p, keep }: { p: any; keep: boolean }) {
  return (
    <div className="flex min-w-0 flex-1 gap-2">
      <Thumb id={p.cover_creative_id} />
      <div className="min-w-0 text-xs">
        <Link href={`/products/${p.id}`} target="_blank" className="block truncate font-medium hover:underline">{p.name}</Link>
        <div className="text-muted-foreground">#{p.id} · {p.name_status === "UNKNOWN" ? "chưa có tên" : p.name_status}{p.category ? ` · ${p.category}` : ""}</div>
        <div className="mt-1">{p.unique_ads} quảng cáo{p.unique_ads !== p.active_ads ? ` (${p.active_ads} dòng)` : ""} · <b>{p.advertisers}</b> nhà quảng cáo</div>
        <div className="text-muted-foreground">Opp {fmt.n(p.opportunity_score)} · {p.recommendation}</div>
        {keep && <Pill tone="good" className="mt-1">đại diện</Pill>}
      </div>
    </div>
  );
}

function Pair({ row, onDone }: { row: any; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const act = async (kind: "accept" | "reject") => {
    setBusy(true);
    try {
      await api(`/clusters/candidates/${row.id}/${kind}`, { method: "POST", body: JSON.stringify({ user: user() }) });
      toast.success(kind === "accept" ? "Đã gom cluster; Ads và sản phẩm gốc được giữ nguyên" : "Đã ghi nhận: không phải cùng sản phẩm");
      onDone();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  const undo = async () => {
    setBusy(true);
    try {
      await api(row.algorithm_version ? `/clusters/events/${row.event_id}/undo` : `/products/${row.keep_id}/merges/${row.event_id}/undo`, { method: "POST", body: JSON.stringify({ user: user() }) });
      toast.success("Đã hoàn tác — cặp này sẽ không bị tự gộp lại"); onDone();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-2 border-b p-3 last:border-0">
      <div className="flex flex-wrap gap-3">
        <Side p={row.a} keep={row.keep_id === row.a.id} />
        <Side p={row.b} keep={row.keep_id === row.b.id} />
      </div>
      <LinkEvidence row={row} />
      <div className="flex flex-wrap items-center gap-2">
        {row.status === "pending" && <>
          <Button size="sm" disabled={busy || !row.can_accept} onClick={() => act("accept")}><GitMerge /> {row.can_accept ? "Gom cluster" : "Chờ worker đánh giá lại"}</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => act("reject")}>Không phải cùng sản phẩm</Button>
        </>}
        {row.status === "blocked" && <Button size="sm" variant="outline" disabled={busy} onClick={() => act("reject")}>Ẩn</Button>}
        {["auto_clustered", "auto_merged", "accepted"].includes(row.status) && (row.event_id
          ? <Button size="sm" variant="outline" disabled={busy} onClick={undo}><Undo2 /> Hoàn tác</Button> : null)}
        {row.decided_at && <span className="text-xs text-muted-foreground">{row.decided_by || "?"} · {fmt.dt(row.decided_at)}</span>}
      </div>
    </div>
  );
}

function Impure() {
  const { data, error, reload } = useApi("/clusters/impure");
  if (!data) return <Loading error={error} />;
  if (!data.rows.length) return <Empty>Không thấy cụm nào lẫn sản phẩm khác nhau.</Empty>;
  return (
    <div>
      {data.rows.map((r: any) => (
        <div key={r.product_id} className="space-y-1 border-b p-3 text-xs last:border-0">
          <Link href={`/products/${r.product_id}`} target="_blank" className="font-medium hover:underline">{r.product.name} <span className="text-muted-foreground">#{r.product_id} · {r.product.ads} ad</span></Link>
          <div className="text-muted-foreground">Các ad trong cụm cho {r.groups.length} tên khác nhau và không có gì nối chúng (trang, nội dung, ảnh). Mở sản phẩm → “Tách” để chọn ad.</div>
          <div className="flex flex-wrap gap-1">{r.groups.map((g: any, i: number) => <Pill key={i}>{g.name} · {g.ads} ad</Pill>)}</div>
        </div>
      ))}
      <div className="p-3"><Button size="sm" variant="ghost" onClick={reload}>Quét lại</Button></div>
    </div>
  );
}

function ClusterGroups({ duplicates = false }: { duplicates?: boolean }) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [adOffset, setAdOffset] = useState(0);
  const { data, error } = useApi(`/clusters/${duplicates ? "duplicate-ads" : "groups"}?limit=40&offset=${offset}`);
  const detail = useApi(selected ? `/clusters/groups/${selected}?limit=40&offset=${adOffset}` : null);
  if (!data) return <Loading error={error} />;
  return <div className="space-y-3 p-3 text-sm">
    <p>{duplicates ? "Cùng ID quảng cáo do nền tảng cấp. Các Ads gốc vẫn được giữ lại." : "Mỗi cluster liên kết các Ads cùng sản phẩm. Điểm tin cậy là điểm bằng chứng; dữ liệu cũ chưa đánh giá hiển thị —."}</p>
    {!data.rows.length && <Empty>Chưa có dữ liệu.</Empty>}
    {data.rows.map((r: any) => duplicates
      ? <div key={`${r.a_id}-${r.b_id}`} className="border-b py-2">Ad #{r.a_id} ↔ Ad #{r.b_id} · {Math.round(r.confidence * 100)}% · {r.evidence.network}</div>
      : <button key={r.cluster_id} className="flex w-full justify-between gap-3 border-b py-2 text-left hover:underline" onClick={() => { setSelected(r.cluster_id); setAdOffset(0); }}>
          <span>#{r.cluster_id} · {r.name}</span><span>{r.ads} Ads · {r.products} sản phẩm gốc · {r.confidence == null ? "—" : `${Math.round(r.confidence * 100)}%`}</span>
        </button>)}
    <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={!offset} onClick={() => setOffset(offset - 40)}>Trước</Button><span>{Math.min(offset + data.rows.length, data.total)} / {data.total}</span><Button size="sm" variant="outline" disabled={offset + 40 >= data.total} onClick={() => setOffset(offset + 40)}>Tiếp</Button></div>
    {selected && <Card title={`Ads trong cluster #${selected}`}>
      {!detail.data ? <Loading error={detail.error} /> : <>
        {detail.data.rows.map((a: any) => <div key={a.ad_id} className="border-b py-2"><Link href={`/products/${a.product_id}`} className="hover:underline">Ad #{a.ad_id} · {a.title || a.external_id}</Link><span className="text-muted-foreground"> · Product #{a.product_id} · Advertiser {a.advertiser_id ?? "—"} · {a.confidence == null ? "chưa đánh giá" : `${Math.round(a.confidence * 100)}%`}</span></div>)}
        <div className="mt-2 flex gap-2"><Button size="sm" disabled={!adOffset} onClick={() => setAdOffset(adOffset - 40)}>Trước</Button><span>{adOffset + detail.data.rows.length} / {detail.data.total}</span><Button size="sm" disabled={adOffset + 40 >= detail.data.total} onClick={() => setAdOffset(adOffset + 40)}>Tiếp</Button></div>
      </>}
    </Card>}
  </div>;
}

export default function Clusters() {
  const [tab, setTab] = useState("groups");
  const [offset, setOffset] = useState(0);
  const stats = useApi("/clusters/stats");
  const list = useApi(["impure", "groups", "duplicates"].includes(tab) ? null : `/clusters/candidates?status=${tab}&limit=60&offset=${offset}`);
  const s = stats.data;
  const done = () => { list.reload(); stats.reload(); };
  return (
    <div className="space-y-4">
      <PageHeader title="Gom cụm sản phẩm" subtitle="Kết hợp tên, ảnh, CLIP, trang sản phẩm và SKU/metadata. Cặp chưa chắc chắn chờ xác nhận; sản phẩm tương tự được giữ riêng. Gom cluster có thể hoàn tác và giữ nguyên dữ liệu gốc." />
      {s && (
        <Card title="Mức độ phân mảnh">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <span><b className="tnum">{fmt.n(s.products)}</b> sản phẩm</span>
            <span><b className="tnum">{fmt.n(s.clusters)}</b> cluster · {fmt.n(s.clustered_ads)} Ads liên kết</span>
            <span><b className="tnum">{s.single_ad_pct}%</b> chỉ có 1 ad ({fmt.n(s.single_ad_products)})</span>
            <span><b className="tnum">{fmt.n(s.unnamed_products)}</b> chưa có tên ({fmt.n(s.unnamed_in_target)} trong thị trường mục tiêu)</span>
            <span><b className="tnum">{fmt.n(s.links?.pending || 0)}</b> cặp chờ xác nhận</span>
          </div>
        </Card>
      )}
      <ToggleGroup className="flex-wrap" type="single" variant="outline" size="sm" value={tab} onValueChange={(v) => { if (v) { setTab(v); setOffset(0); } }}>
        {TABS.map(([k, l]) => <ToggleGroupItem key={k} value={k} className="text-xs">{l}{k !== "impure" && s?.links?.[k] != null ? ` (${s.links[k]})` : ""}</ToggleGroupItem>)}
      </ToggleGroup>
      <Card pad={false}>
        {tab === "groups" || tab === "duplicates" ? <ClusterGroups key={tab} duplicates={tab === "duplicates"} /> : tab === "impure" ? <Impure /> : !list.data ? <Loading error={list.error} /> : list.data.rows.length === 0 ? <Empty>Không có cặp nào.</Empty> : (
          list.data.rows.map((r: any) => <Pair key={r.id} row={r} onDone={done} />)
        )}
        {list.data && <div className="flex items-center gap-2 p-3"><Button size="sm" disabled={!offset} onClick={() => setOffset(offset - 60)}>Trước</Button><span>{offset + list.data.rows.length} / {list.data.total}</span><Button size="sm" disabled={offset + 60 >= list.data.total} onClick={() => setOffset(offset + 60)}>Tiếp</Button></div>}
      </Card>
    </div>
  );
}
