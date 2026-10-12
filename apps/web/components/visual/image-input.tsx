"use client";
import { useEffect, useRef, useState } from "react";
import { Crop, ImagePlus, Link2, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export const ACCEPT = "image/jpeg,image/png,image/webp";
export const MAX_MB = 8;
export type CropBox = { x: number; y: number; w: number; h: number };  // fractions of the picture

/** What the user picked: a file (upload / drop / paste), a URL, or an entity already in SpyAds. */
export type ImageInput =
  | { kind: "file"; file: File; preview: string }
  | { kind: "url"; url: string; preview: string }
  | { kind: "entity"; creative_id?: number; ad_id?: number; product_id?: number; eu_listing_id?: number; preview?: string | null; label?: string };

export function fileOk(f: File): string | null {
  if (!/^image\/(jpeg|png|webp)$/.test(f.type)) return "Chỉ nhận JPG, PNG, WebP.";
  if (f.size > MAX_MB * 1024 * 1024) return `Ảnh quá lớn (> ${MAX_MB} MB).`;
  return null;
}

/** Listen for a pasted picture anywhere on the page. */
export function usePasteImage(onFile: (f: File) => void) {
  useEffect(() => {
    const h = (e: ClipboardEvent) => {
      const it = Array.from(e.clipboardData?.items || []).find((i) => i.type.startsWith("image/"));
      const f = it?.getAsFile();
      if (f) { e.preventDefault(); onFile(f); }
    };
    window.addEventListener("paste", h);
    return () => window.removeEventListener("paste", h);
  }, [onFile]);
}

/** Drop zone + file picker + image URL — spec §3.1 (upload, drag & drop, paste, URL). */
export function ImageUploadDropzone({ onPick, compact, className }: { onPick: (i: ImageInput) => void; compact?: boolean; className?: string }) {
  const [over, setOver] = useState(false);
  const [url, setUrl] = useState("");
  const [err, setErr] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  const take = (f?: File | null) => {
    if (!f) return;
    const bad = fileOk(f);
    if (bad) return setErr(bad);
    setErr("");
    onPick({ kind: "file", file: f, preview: URL.createObjectURL(f) });
  };
  const takeUrl = () => {
    const u = url.trim();
    if (!/^https?:\/\//i.test(u)) return setErr("Dán link ảnh http(s).");
    setErr("");
    onPick({ kind: "url", url: u, preview: u });
  };
  return (
    <div className={className}>
      <div role="button" tabIndex={0} aria-label="Chọn hoặc thả ảnh"
           onClick={() => ref.current?.click()} onKeyDown={(e) => e.key === "Enter" && ref.current?.click()}
           onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
           onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files?.[0]); }}
           className={cn("flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed text-center text-sm transition-colors",
                         compact ? "px-3 py-4" : "px-4 py-8", over ? "border-series-1 bg-series-1/5" : "border-input hover:bg-muted/50")}>
        <ImagePlus className="size-6 text-muted-foreground" />
        <div><b>Thả ảnh</b> vào đây, <b>bấm</b> để chọn, hoặc <b>Ctrl+V</b> dán ảnh</div>
        <div className="text-xs text-muted-foreground">JPG · PNG · WebP · ≤ {MAX_MB} MB</div>
        <input ref={ref} type="file" accept={ACCEPT} className="hidden" onChange={(e) => { take(e.target.files?.[0]); e.target.value = ""; }} />
      </div>
      <div className="mt-2 flex gap-1.5">
        <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="…hoặc dán URL ảnh (https://…)" className="h-8 text-xs"
               onKeyDown={(e) => e.key === "Enter" && takeUrl()} aria-label="URL ảnh" />
        <Button type="button" variant="outline" size="sm" onClick={takeUrl} disabled={!url.trim()}><Link2 /> Dùng URL</Button>
      </div>
      {err && <p className="mt-1 text-xs text-critical">{err}</p>}
    </div>
  );
}

/** Preview of the chosen picture with crop / clear actions. */
export function ImagePreview({ input, crop, onCrop, onClear, size = 160 }: { input: ImageInput; crop?: CropBox | null; onCrop?: () => void; onClear?: () => void; size?: number }) {
  const src = input.preview;
  return (
    <div className="flex items-start gap-3">
      <div className="relative shrink-0 overflow-hidden rounded-md border bg-muted" style={{ width: size, height: size }}>
        {src ? <img src={src} alt="" className="h-full w-full object-contain" referrerPolicy="no-referrer" /> : <div className="flex h-full items-center justify-center text-xs text-muted-foreground">không có ảnh</div>}
        {crop && src && (
          <div className="pointer-events-none absolute border-2 border-series-1 bg-series-1/10"
               style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.w * 100}%`, height: `${crop.h * 100}%` }} />
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5 text-xs">
        <div className="truncate text-muted-foreground" title={input.kind === "file" ? input.file.name : input.kind === "url" ? input.url : input.label}>
          {input.kind === "file" ? `${input.file.name} · ${(input.file.size / 1024).toFixed(0)} KB` : input.kind === "url" ? input.url : input.label || "Ảnh từ SpyAds"}
        </div>
        {crop && <div>Vùng đã chọn: {Math.round(crop.w * 100)}% × {Math.round(crop.h * 100)}%</div>}
        <div className="flex flex-wrap gap-1">
          {onCrop && src && <Button type="button" variant="outline" size="xs" onClick={onCrop}><Crop /> {crop ? "Chọn lại vùng" : "Chọn vật thể (crop)"}</Button>}
          {onClear && <Button type="button" variant="ghost" size="xs" onClick={onClear}><X /> Bỏ ảnh</Button>}
        </div>
      </div>
    </div>
  );
}

/** Drag a rectangle over the picture to search only that object (spec §3.1). Returns fractions; the API does the crop. */
export function ImageCropDialog({ src, open, onOpenChange, value, onChange }: {
  src: string; open: boolean; onOpenChange: (o: boolean) => void; value?: CropBox | null; onChange: (c: CropBox | null) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [sel, setSel] = useState<CropBox | null>(value || null);
  useEffect(() => { if (open) setSel(value || null); }, [open, value]);
  const frac = (e: React.MouseEvent) => {
    const r = box.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  const move = (e: React.MouseEvent) => {
    if (!drag) return;
    const p = frac(e);
    setSel({ x: Math.min(drag.x, p.x), y: Math.min(drag.y, p.y), w: Math.abs(p.x - drag.x), h: Math.abs(p.y - drag.y) });
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Chọn vật thể cần tìm</DialogTitle>
          <DialogDescription>Kéo chuột để khoanh vùng sản phẩm trong ảnh (ảnh có nhiều vật thể). Bỏ trống = dùng cả ảnh.</DialogDescription>
        </DialogHeader>
        <div ref={box} className="relative mx-auto max-h-[65vh] cursor-crosshair select-none overflow-hidden rounded-md border bg-muted"
             onMouseDown={(e) => { e.preventDefault(); setDrag(frac(e)); setSel(null); }} onMouseMove={move}
             onMouseUp={() => setDrag(null)} onMouseLeave={() => setDrag(null)}>
          <img src={src} alt="" className="max-h-[65vh] w-auto" draggable={false} referrerPolicy="no-referrer" />
          {sel && sel.w > 0.01 && sel.h > 0.01 && (
            <div className="pointer-events-none absolute border-2 border-series-1 bg-series-1/15 shadow-[0_0_0_9999px_rgba(0,0,0,.35)]"
                 style={{ left: `${sel.x * 100}%`, top: `${sel.y * 100}%`, width: `${sel.w * 100}%`, height: `${sel.h * 100}%` }} />
          )}
        </div>
        <DialogFooter className="gap-2">
          <Button type="button" variant="ghost" onClick={() => { onChange(null); onOpenChange(false); }}>Dùng cả ảnh</Button>
          <Button type="button" onClick={() => { onChange(sel && sel.w > 0.02 && sel.h > 0.02 ? sel : null); onOpenChange(false); }}><Upload /> Tìm vùng đã chọn</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
