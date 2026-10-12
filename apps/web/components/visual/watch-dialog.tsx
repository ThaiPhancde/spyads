"use client";
import { useEffect, useState } from "react";
import { Eye } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { getUser } from "@/lib/realtime";

/** Watch the product cluster of the searched picture (spec §8 Watchlists): alerts when a new ad / listing / EU offer
 *  matches the picture, plus the EU price rules on the EU products found as same-SKU. */
export function WatchImageDialog({ open, onOpenChange, query, rows, keywords, thumb }: {
  open: boolean; onOpenChange: (o: boolean) => void; query: { phash: string; dhash: string } | null; rows: any[]; keywords: string[]; thumb?: string | null;
}) {
  const same = rows.filter((r) => r.tier !== "VISUALLY_SIMILAR");
  const [name, setName] = useState("");
  const [kw, setKw] = useState("");
  const [drop, setDrop] = useState("10");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    setName((same[0]?.product_name || same[0]?.title || keywords[0] || "Ảnh theo dõi").slice(0, 80));
    setKw(keywords.join("\n"));
  }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    if (!query) return;
    setBusy(true);
    try {
      const r = await api("/search/images/watch", { method: "POST", body: JSON.stringify({
        name, owner: getUser().name || "team", phash: query.phash, dhash: query.dhash, thumb,
        keywords: kw.split(/[\n|,]/).map((s) => s.trim()).filter(Boolean),
        eu_product_ids: [...new Set(same.map((r) => r.eu_product_id).filter(Boolean))],
        rules: drop ? { price_drop_pct: Number(drop) } : {} }) });
      toast.success(`Đã theo dõi “${r.name}” (#${r.id}) — xem Watchlists & Alerts`);
      onOpenChange(false);
    } catch (e: any) { toast.error(String(e.message || e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Theo dõi cụm sản phẩm theo ảnh</DialogTitle>
          <DialogDescription>Báo khi kho có quảng cáo / listing / offer EU mới khớp ảnh này ({same.length} khớp hiện tại), và khi giá thật của offer EU cùng SKU giảm.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1"><Label htmlFor="wname">Tên</Label><Input id="wname" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="wkw">Từ khoá (mỗi dòng một — dùng cho EU sync)</Label><Textarea id="wkw" rows={3} value={kw} onChange={(e) => setKw(e.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="wdrop">Báo khi giá giảm ≥ %</Label><Input id="wdrop" className="w-24" inputMode="numeric" value={drop} onChange={(e) => setDrop(e.target.value.replace(/\D/g, ""))} /></div>
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Huỷ</Button>
          <Button type="button" onClick={save} disabled={busy || !name.trim() || !query}>{busy ? <Spinner /> : <Eye />} Theo dõi</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
