"use client";
import Link from "next/link";
import { useState } from "react";
import { GitMerge, Scissors, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Card, Pill } from "@/components/ui";
import { NAME_SOURCE, NameStatus } from "@/components/product-name";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, fmt, useAction, useApi } from "@/lib/api";
import { getUser } from "@/lib/realtime";

const AD_TYPE: Record<string, string> = {
  PRODUCT_AD: "Sản phẩm", MULTI_PRODUCT_AD: "Nhiều SP", SERVICE_AD: "Dịch vụ", CONTENT_AD: "Giải trí", BRAND_AD: "Thương hiệu", UNKNOWN: "Chưa rõ",
};

/** MKT tools for one product cluster: why it has this name, which ads it holds, split wrong ads out, merge, undo. */
export function ProductCluster({ p, ads, onChange }: { p: any; ads: any[]; onChange: () => void }) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [splitName, setSplitName] = useState("");
  const [target, setTarget] = useState("");
  const merges = useApi(`/products/${p.id}/merges`);
  const act = useAction();
  const user = () => getUser().name || null;
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const split = () => act.run(async () => {
    const r = await api(`/products/${p.id}/split`, { method: "POST", body: JSON.stringify({ ad_ids: [...sel], name: splitName || null, user: user() }) });
    toast.success(`Đã tách ${sel.size} ad sang sản phẩm mới`, { action: { label: "Mở", onClick: () => window.open(`/products/${r.new_id}`, "_blank") } });
    if (r.duplicate) toast.message(`Tên trùng với ${r.duplicate.name} (${r.duplicate.product_code}) — có thể gộp`);
    setSel(new Set()); setSplitName(""); onChange(); merges.reload();
  });
  const merge = () => act.run(async () => {
    const q = target.trim();
    const found = /^\d+$/.test(q) ? { id: Number(q) } : (await api(`/products?q=${encodeURIComponent(q)}&country=ALL&limit=1`)).rows?.[0];
    if (!found) throw new Error(`Không thấy sản phẩm "${q}"`);
    if (!window.confirm(`Gộp sản phẩm này vào #${found.id}${found.name ? ` (${found.name})` : ""}? Có thể hoàn tác ở sản phẩm đích.`)) return;
    await api(`/products/merge`, { method: "POST", body: JSON.stringify({ source_id: p.id, target_id: found.id, user: user() }) });
    toast.success("Đã gộp — mở sản phẩm đích để hoàn tác nếu cần");
    window.location.href = `/products/${found.id}`;
  });
  const undo = (eid: number) => act.run(async () => {
    await api(`/products/${p.id}/merges/${eid}/undo`, { method: "POST", body: JSON.stringify({ user: user() }) });
    toast.success("Đã hoàn tác"); onChange(); merges.reload();
  });

  const evidenceNames = new Set(ads.map((a) => (a.evidence_name || "").toLowerCase()).filter(Boolean));
  return (
    <Card title="Tên & cụm sản phẩm — bằng chứng, tách / gộp, hoàn tác" className="xl:col-span-3" pad={false}>
      <div className="grid grid-cols-1 gap-4 px-4 pb-3 text-xs lg:grid-cols-3">
        <div>
          <div className="mb-1 flex items-center gap-2 font-medium">Bằng chứng tên <NameStatus status={p.name_status} /></div>
          {(p.name_evidence || []).length ? <ul className="divide-y">{p.name_evidence.map((e: any, i: number) => (
            <li key={i} className="py-1"><span className="text-muted-foreground">{NAME_SOURCE[e.source] || e.source}:</span> {e.value}{e.ad_id ? <span className="text-muted-foreground"> · ad #{e.ad_id}</span> : null}</li>
          ))}</ul> : <div className="text-muted-foreground">Chưa có — tên chưa xác định hoặc do MKT đặt.</div>}
          {evidenceNames.size > 2 && <div className="mt-2 text-serious">Các ad trong cụm cho {evidenceNames.size} tên khác nhau — kiểm tra xem có ad lạc cụm không.</div>}
        </div>
        <div>
          <div className="mb-1 font-medium">Lịch sử tên</div>
          {(p.name_history || []).length ? <ul className="divide-y">{p.name_history.slice(0, 6).map((h: any, i: number) => (
            <li key={i} className="py-1">{h.name_vi || h.name || "—"} <span className="text-muted-foreground">· {h.status || "—"} · {h.by || "?"} · {fmt.dt(h.at)}</span></li>
          ))}</ul> : <div className="text-muted-foreground">Chưa đổi tên lần nào.</div>}
        </div>
        <div>
          <div className="mb-1 font-medium">Gộp / hoàn tác</div>
          <div className="flex gap-1">
            <Input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="ID / mã PRD_… / tên sản phẩm đích" className="h-7 text-xs" />
            <Button type="button" size="xs" variant="outline" onClick={merge} disabled={act.busy || !target.trim()}><GitMerge /> Gộp vào</Button>
          </div>
          <ul className="mt-2 divide-y">{(merges.data || []).map((m: any) => (
            <li key={m.id} className="flex items-center justify-between gap-2 py-1">
              <span>{m.type === "PRODUCT_MERGED" ? `Gộp từ ${m.source_name || m.source_code} (${m.ads} ad)` : <>Tách {m.ads} ad → <Link href={`/products/${m.to_id}`} className="hover:underline">#{m.to_id}</Link></>}
                <span className="text-muted-foreground"> · {m.by || "?"} · {fmt.dt(m.at)}</span></span>
              {m.undone ? <Pill>đã hoàn tác</Pill> : <Button type="button" size="xs" variant="ghost" onClick={() => undo(m.id)} disabled={act.busy}><Undo2 /> Hoàn tác</Button>}
            </li>))}</ul>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t px-4 py-2 text-xs">
        <span className="font-medium">{ads.length} ad trong cụm</span>
        <span className="text-muted-foreground">· chọn các ad không phải sản phẩm này rồi tách</span>
        <Input value={splitName} onChange={(e) => setSplitName(e.target.value)} placeholder="Tên sản phẩm mới (để trống = chưa xác định)" className="h-7 max-w-xs text-xs" />
        <Button type="button" size="xs" onClick={split} disabled={act.busy || !sel.size}><Scissors /> Tách {sel.size || ""} ad</Button>
        {act.error && <span className="text-critical">{act.error}</span>}
      </div>
      <ScrollArea className="max-h-96 overflow-y-auto">
        <Table>
          <TableHeader><TableRow><TableHead className="w-8" /><TableHead>Advertiser</TableHead><TableHead>Tên theo bằng chứng của ad</TableHead><TableHead>Loại ad</TableHead><TableHead>Nội dung</TableHead><TableHead>Thấy lần đầu</TableHead></TableRow></TableHeader>
          <TableBody>{ads.map((a) => (
            <TableRow key={a.id} data-state={sel.has(a.id) ? "selected" : undefined}>
              <TableCell><Checkbox checked={sel.has(a.id)} onCheckedChange={() => toggle(a.id)} aria-label={`Chọn ad ${a.id}`} /></TableCell>
              <TableCell className="max-w-[160px] truncate">{a.advertiser || "—"}</TableCell>
              <TableCell className="max-w-[220px] truncate" title={a.evidence_name || ""}>{a.evidence_name || <span className="text-muted-foreground">—</span>}
                {a.name_source && a.name_source !== "none" && <span className="text-muted-foreground"> · {NAME_SOURCE[a.name_source] || a.name_source}</span>}</TableCell>
              <TableCell>{AD_TYPE[a.ad_type] || a.ad_type || "—"}</TableCell>
              <TableCell className="max-w-md truncate text-xs" title={a.text}>{a.text}</TableCell>
              <TableCell className="whitespace-nowrap">{fmt.date(a.first_seen)}</TableCell>
            </TableRow>))}</TableBody>
        </Table>
      </ScrollArea>
    </Card>
  );
}
