"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Camera } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ImageInput, ImageUploadDropzone, usePasteImage } from "@/components/visual/image-input";
import { qs } from "@/lib/api";

export const STASH_KEY = "toolspy_visual_image";

/** Keep a picked file for the /visual page (session only, never sent anywhere but our API). */
export async function stashImage(f: File): Promise<void> {
  const dataUrl = await new Promise<string>((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = rej; r.readAsDataURL(f); });
  try { sessionStorage.setItem(STASH_KEY, JSON.stringify({ name: f.name, type: f.type, dataUrl })); } catch { /* quota: the page falls back to asking again */ }
}

export function takeStashedImage(): File | null {
  try {
    const raw = sessionStorage.getItem(STASH_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(STASH_KEY);
    const { name, type, dataUrl } = JSON.parse(raw);
    const bin = atob(dataUrl.split(",")[1]);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new File([arr], name || "image", { type });
  } catch { return null; }
}

export type VisualPreset = { mode?: string; sources?: string; keywords?: string; country?: string; origin?: "hybrid" | "local" | "web" };

/** Link to the image search page for a picture already in SpyAds (ad / creative / product / EU listing). */
export function visualHref(p: { creative_id?: number; ad_id?: number; product_id?: number; eu_listing_id?: number; image_url?: string } & VisualPreset) {
  return `/visual${qs(p)}`;
}

/** Camera button (spec §8 Global Search): opens the picker, then lands on /visual with the picture. */
export function GlobalImageSearch({ preset, variant = "outline", size = "sm", label, iconOnly, className }: {
  preset?: VisualPreset; variant?: any; size?: any; label?: string; iconOnly?: boolean; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const go = async (i: ImageInput) => {
    if (i.kind === "file") { await stashImage(i.file); router.push(visualHref({ ...preset })); }
    else if (i.kind === "url") router.push(visualHref({ image_url: i.url, ...preset }));
    setOpen(false);
  };
  usePasteImage((f) => { if (open) go({ kind: "file", file: f, preview: "" }); });
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button type="button" variant={variant} size={size} className={className} onClick={() => setOpen(true)} aria-label="Tìm bằng ảnh">
            <Camera />{!iconOnly && (label || "Tìm bằng ảnh")}
          </Button>
        </TooltipTrigger>
        <TooltipContent>Tìm sản phẩm / quảng cáo / giá bằng ảnh (upload, dán, URL)</TooltipContent>
      </Tooltip>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Tìm bằng ảnh</DialogTitle>
            <DialogDescription>Tìm ảnh trùng, sản phẩm giống, quảng cáo liên quan, nguồn hàng và so sánh giá từ một tấm ảnh.</DialogDescription>
          </DialogHeader>
          <ImageUploadDropzone onPick={go} />
        </DialogContent>
      </Dialog>
    </>
  );
}
