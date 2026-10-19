"use client";
import Link from "next/link";
import { useState } from "react";
import { Check, Info, Pencil, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Pill, type Tone } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { api, useAction } from "@/lib/api";
import { getUser } from "@/lib/realtime";
import { cn } from "@/lib/utils";

/** Naming (docs/PRODUCT_NAMING.md): product name ≠ advertiser / CTA. Status says how sure we are and who said so. */
export const NAME_STATUS: Record<string, [string, Tone, string]> = {
  VERIFIED: ["Đã xác nhận", "good", "MKT đã xác nhận — crawler không bao giờ đổi tên này"],
  AUTO_CONFIRMED: ["Tự xác nhận", "info", "Tên lấy từ listing sàn / trang sản phẩm — đo được ≥ 90% đúng"],
  SUGGESTED: ["Đề xuất", "warn", "Tên lấy từ nội dung quảng cáo / AI — khoảng 2/3 đúng, cần MKT xác nhận"],
  UNKNOWN: ["Chưa xác định", "neutral", "Chưa đủ bằng chứng để đặt tên — không tự bịa"],
};
export const NAME_SOURCE: Record<string, string> = {
  listing: "Listing sàn", slug: "Trang sản phẩm", adapter: "Tiêu đề link", ad_title: "Nội dung ad", llm: "AI (có bằng chứng)",
  manual: "MKT đặt", legacy: "Tên cũ", restored: "Khôi phục", legacy_hint: "Tên cũ (gợi ý)", cluster: "Sản phẩm giống (gợi ý)",
};
export const UNKNOWN_LABEL = "Chưa xác định sản phẩm";

export function NameStatus({ status }: { status?: string }) {
  const s = NAME_STATUS[status || "SUGGESTED"] || NAME_STATUS.SUGGESTED;
  return <span title={s[2]}><Pill tone={s[1]} className="text-[10px]">{s[0]}</Pill></span>;
}

/** Name + status + MKT actions (sửa nhanh · xác nhận · bằng chứng · hoàn tác). `p` = a product card from the API. */
export function ProductName({ p, onChanged, href, className, big }: { p: any; onChanged?: (p: any) => void; href?: string; className?: string; big?: boolean }) {
  const [cur, setCur] = useState(p);
  const unknown = cur.name_status === "UNKNOWN";
  const act = useAction();
  const done = (r: any, msg: string) => {
    setCur({ ...cur, ...r.product });
    onChanged?.(r.product);
    toast.success(msg);
    if (r.duplicate) toast.message(`Đã có sản phẩm trùng tên: ${r.duplicate.name} (${r.duplicate.product_code})`,
      { description: "Mở sản phẩm để gộp nếu đúng là một.", action: { label: "Mở", onClick: () => window.open(`/products/${r.duplicate.id}`, "_blank") } });
  };
  const confirm = () => act.run(async () => done(await api(`/products/${cur.id}/name/confirm`, { method: "POST", body: JSON.stringify({ user: getUser().name || null }) }), "Đã xác nhận tên"));
  const undo = () => act.run(async () => done(await api(`/products/${cur.id}/name/undo`, { method: "POST" }), "Đã hoàn tác tên"));
  const title = unknown && !cur.name_provisional ? UNKNOWN_LABEL : cur.name;
  const nameEl = <span className={cn("leading-snug font-semibold", big && "text-xl", unknown && "font-normal text-muted-foreground italic")}>{title}</span>;
  return (
    <span className={cn("block min-w-0", className)}>
      <span className="flex flex-wrap items-center gap-1">
        {href ? <Link href={href} className="line-clamp-2 hover:underline">{nameEl}</Link> : nameEl}
      </span>
      <span className="mt-1 flex flex-wrap items-center gap-1 text-[11px] font-normal tracking-normal text-muted-foreground">
        <NameStatus status={cur.name_status} />
        {cur.name_provisional && <span title="Chưa biết sản phẩm nào — tên tạm theo shop, không dùng để gom cụm / tìm kiếm / chấm điểm">· tên tạm theo shop</span>}
        {!unknown && cur.name_source && <span title="Nguồn tên">{NAME_SOURCE[cur.name_source] || cur.name_source}</span>}
        {cur.name_vi && cur.canonical_name && cur.canonical_name !== cur.name_vi && <span className="truncate" title="Tên gốc (dùng để gom cụm / tìm kiếm)">· {cur.canonical_name}</span>}
        {unknown && cur.name_hint && <span title="Tên cũ trước khi chuẩn hoá — bấm sửa để dùng">· gợi ý: {cur.name_hint}</span>}
        <span className="ml-auto inline-flex items-center gap-0.5">
          <EditName p={cur} onSaved={(r) => done(r, "Đã lưu tên")} />
          {(cur.name_status === "SUGGESTED" || cur.name_status === "AUTO_CONFIRMED") && (
            <Button type="button" variant="ghost" size="icon-xs" onClick={confirm} disabled={act.busy} title="Xác nhận tên này đúng" aria-label="Xác nhận tên"><Check /></Button>
          )}
          {cur.manual_override && <Button type="button" variant="ghost" size="icon-xs" onClick={undo} disabled={act.busy} title="Hoàn tác lần sửa tên gần nhất" aria-label="Hoàn tác tên"><Undo2 /></Button>}
          <Evidence p={cur} />
        </span>
      </span>
      {act.error && <span className="mt-1 block text-[11px] text-critical">{act.error}</span>}
    </span>
  );
}

function EditName({ p, onSaved }: { p: any; onSaved: (r: any) => void }) {
  const [open, setOpen] = useState(false);
  const [vi, setVi] = useState(p.name_vi || "");
  const [orig, setOrig] = useState(p.canonical_name || p.name_hint || "");
  const act = useAction();
  const save = () => act.run(async () => {
    const r = await api(`/products/${p.id}/name`, { method: "PATCH", body: JSON.stringify({ name_vi: vi, name: orig || null, user: getUser().name || null }) });
    setOpen(false);
    onSaved(r);
  });
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="ghost" size="icon-xs" title="Sửa tên sản phẩm" aria-label="Sửa tên sản phẩm"><Pencil /></Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 space-y-2 text-xs" align="end">
        <label className="block space-y-1"><span className="font-medium">Tên tiếng Việt (MKT đọc)</span>
          <Input value={vi} onChange={(e) => setVi(e.target.value)} placeholder="VD: Vòng tay tỳ hưu đá obsidian" autoFocus /></label>
        <label className="block space-y-1"><span className="font-medium">Tên gốc / tiếng Anh <span className="font-normal text-muted-foreground">(gom cụm, tìm kiếm)</span></span>
          <Input value={orig} onChange={(e) => setOrig(e.target.value)} placeholder="VD: Pixiu Obsidian Bracelet" /></label>
        <div className="text-muted-foreground">Tên MKT đặt được khoá: crawler không ghi đè. Không nhập tên shop / CTA.</div>
        {act.error && <div className="text-critical">{act.error}</div>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" size="xs" onClick={() => setOpen(false)}>Huỷ</Button>
          <Button type="button" size="xs" onClick={save} disabled={act.busy || !(vi.trim() || orig.trim())}>Lưu</Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Evidence({ p }: { p: any }) {
  return (
    <HoverCard openDelay={150}>
      <HoverCardTrigger asChild>
        <Button type="button" variant="ghost" size="icon-xs" aria-label="Bằng chứng nhận diện"><Info /></Button>
      </HoverCardTrigger>
      <HoverCardContent className="w-80 space-y-1 text-xs" align="end">
        <div className="font-medium">Bằng chứng nhận diện</div>
        <div className="text-muted-foreground">
          {(NAME_STATUS[p.name_status] || NAME_STATUS.SUGGESTED)[2]}
          {p.name_confidence != null && p.name_status !== "UNKNOWN" ? ` · độ chính xác nguồn ~${Math.round(p.name_confidence * 100)}%` : ""}
        </div>
        {(p.name_evidence || []).length ? (
          <ul className="divide-y">{p.name_evidence.map((e: any, i: number) => (
            <li key={i} className="py-1"><span className="text-muted-foreground">{NAME_SOURCE[e.source] || e.source}:</span> {e.value}{e.ad_id ? <span className="text-muted-foreground"> · ad #{e.ad_id}</span> : null}</li>
          ))}</ul>
        ) : <div className="text-muted-foreground">{p.name_evidence ? "Không có bằng chứng — mở sản phẩm xem ad." : "Mở sản phẩm để xem bằng chứng."}</div>}
      </HoverCardContent>
    </HoverCard>
  );
}
