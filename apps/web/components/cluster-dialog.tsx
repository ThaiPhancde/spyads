"use client";
import Link from "next/link";
import { useState } from "react";
import { GitMerge, Scissors } from "lucide-react";
import { toast } from "sonner";
import { Loading, Pill } from "@/components/ui";
import { ProductCluster } from "@/components/product-cluster";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { api, useApi } from "@/lib/api";
import { getUser } from "@/lib/realtime";

const BLOCKER: Record<string, string> = {
  name_conflict: "tên khác nhau", category_conflict: "khác ngành hàng", price_gap: "giá lệch >3×", big_cluster: "cụm quá lớn",
  unknown_bucket: "cụm chưa xác định lớn", ambiguous: "khớp nhiều sản phẩm khác nhau", cannot_link: "đã tách trước đó", verified_names_differ: "tên MKT khác nhau",
  mixed_metadata: "metadata chứa nhiều sản phẩm", model_number_conflict: "khác model hoặc dung lượng", variant_name_conflict: "khác phiên bản",
};
const FAMILY: Record<string, string> = { landing: "trang sản phẩm", copy: "cùng nội dung", phash: "ảnh trùng", clip: "ảnh giống (CLIP)", name: "tên giống", identity: "GTIN / model / SKU có phạm vi" };

/** Why two products look like one: the evidence chips + guards that fired. Shared with the /clusters page. */
export function LinkEvidence({ row }: { row: any }) {
  const s = row.signals || {};
  return (
    <div className="flex flex-wrap items-center gap-1 text-[11px]">
      <Pill tone={row.score >= 0.93 ? "good" : "info"}>{Math.round(row.score * 100)}%</Pill>
      <Pill>{({ duplicate_product: "Cùng sản phẩm", similar_product: "Sản phẩm tương tự", different_product: "Khác sản phẩm", legacy: "Kết quả cũ" } as Record<string, string>)[row.relation_kind] || "Cần kiểm tra"}</Pill>
      {row.status === "pending" && row.score >= 0.93 && !(row.blockers || []).length && <Pill tone="good">đủ chắc để tự gộp ở lượt chạy tới</Pill>}
      {(s.families || []).map((f: string) => <Pill key={f}>{FAMILY[f] || f}{f === "clip" && s.clip ? ` ${Math.round(s.clip * 100)}%` : ""}</Pill>)}
      {(row.blockers || []).map((b: string) => <Pill key={b} tone="warn">{BLOCKER[b] || b}</Pill>)}
    </div>
  );
}

function Suggestions({ id, onChange }: { id: number; onChange: () => void }) {
  const { data, reload } = useApi(`/clusters/candidates?product_id=${id}&status=pending&limit=10`);
  const [busy, setBusy] = useState(false);
  const act = async (lid: number, kind: "accept" | "reject") => {
    setBusy(true);
    try {
      const r = await api(`/clusters/candidates/${lid}/${kind}`, { method: "POST", body: JSON.stringify({ user: getUser().name || null }) });
      toast.success(kind === "accept" ? "Đã gom vào cluster — xem và hoàn tác tại trang Gom cụm sản phẩm" : "Đã ghi nhận: không phải cùng sản phẩm");
      reload(); onChange();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  if (!data?.rows?.length) return <p className="text-xs text-muted-foreground">Hệ thống chưa thấy sản phẩm nào giống sản phẩm này. Có thể gộp tay bằng ô “Gộp vào” bên dưới.</p>;
  return (
    <ul className="divide-y rounded-md border text-xs">
      {data.rows.map((r: any) => {
        const other = r.a.id === id ? r.b : r.a;
        return (
          <li key={r.id} className="space-y-1 p-2">
            <div className="flex items-center justify-between gap-2">
              <Link href={`/products/${other.id}`} className="font-medium hover:underline" target="_blank">{other.name} <span className="text-muted-foreground">#{other.id}</span></Link>
              <span className="whitespace-nowrap text-muted-foreground">{other.unique_ads} ads · {other.advertisers} nhà QC</span>
            </div>
            <LinkEvidence row={r} />
            <div className="flex items-center gap-1">
              <Button size="xs" disabled={busy || !r.can_accept} onClick={() => act(r.id, "accept")}><GitMerge /> {r.can_accept ? "Gom vào cluster" : "Chờ đánh giá lại"}</Button>
              <Button size="xs" variant="ghost" disabled={busy} onClick={() => act(r.id, "reject")}>Không phải</Button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Body({ id, onChange }: { id: number; onChange: () => void }) {
  const { data, error, reload } = useApi(`/products/${id}`);
  if (!data) return <Loading error={error} />;
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 text-xs font-medium">Sản phẩm có thể trùng (gợi ý của hệ thống, MKT xác nhận)</div>
        <Suggestions id={id} onChange={() => { reload(); onChange(); }} />
      </div>
      <ProductCluster p={data.product} ads={data.cluster_ads || []} onChange={() => { reload(); onChange(); }} />
    </div>
  );
}

/** Gộp / Tách buttons of one row in Xếp hạng sản phẩm: suggestions + the full cluster tools (split ads, merge, Hoàn tác). */
export function ClusterButtons({ p, onChange }: { p: any; onChange: () => void }) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="xs" variant="outline" title="Gộp sản phẩm trùng / tách ad lạc cụm / hoàn tác"><GitMerge /><Scissors /></Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] max-w-6xl overflow-y-auto sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>Gộp / Tách — {p.name} <span className="text-sm font-normal text-muted-foreground">#{p.id}</span></DialogTitle>
          <DialogDescription>{p.unique_ads ?? p.active_ads} quảng cáo · {p.advertisers} nhà quảng cáo. Gộp và tách đều hoàn tác được; cặp đã tách sẽ không bị tự gộp lại.</DialogDescription>
        </DialogHeader>
        <Body id={p.id} onChange={onChange} />
      </DialogContent>
    </Dialog>
  );
}
