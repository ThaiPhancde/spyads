"use client";
import Link from "next/link";
import { useState } from "react";
import { BadgeCheck, BadgeEuro, Database, ExternalLink, Eye, Globe, ImageOff, Megaphone, Save, Truck } from "lucide-react";
import { toast } from "sonner";
import { Card, Pill } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { TierBadge } from "@/components/visual/results";
import { visualHref } from "@/components/visual/global-image-search";
import { api, fmt } from "@/lib/api";
import { getUser } from "@/lib/realtime";
import { cn } from "@/lib/utils";

/** Thumbnail that opens the large picture on click (spec: "bấm vào thumbnail để xem lớn"). */
export function ZoomThumb({ src, big, alt = "", className }: { src?: string | null; big?: string | null; alt?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const [bad, setBad] = useState(false);
  if (!src || bad) return <div className={cn("flex items-center justify-center bg-muted text-muted-foreground", className)}><ImageOff className="size-5" /></div>;
  return (
    <>
      <button type="button" className={cn("block overflow-hidden bg-muted", className)} onClick={() => setOpen(true)} title="Xem ảnh lớn">
        <img src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setBad(true)} className="h-full w-full object-contain" />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle className="line-clamp-2 pr-6 text-sm">{alt || "Ảnh"}</DialogTitle><DialogDescription className="sr-only">Ảnh lớn</DialogDescription></DialogHeader>
          <img src={big || src} alt={alt} referrerPolicy="no-referrer" className="mx-auto max-h-[75vh] w-auto object-contain" onError={(e) => { (e.target as HTMLImageElement).src = src; }} />
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Plain-words reason for a failed price check (rows saved before `reason` existed only have the code). */
export const CHECK_REASON: Record<string, string> = {
  access_restricted: "Shop chặn truy cập tự động hoặc robots.txt không cho phép — mở trang để xem giá",
  unsupported_capability: "Trang không công bố dữ liệu sản phẩm / giá (có thể là bài viết, video hoặc trang danh mục)",
  no_price: "Trang có thông tin sản phẩm nhưng không công bố giá", rate_limited: "Shop đang giới hạn tốc độ — thử lại sau",
  temporarily_unavailable: "Không tải được trang (lỗi mạng hoặc shop không phản hồi)", bad_response: "Shop trả lỗi khi mở trang",
};

export function WebPrice({ r }: { r: any }) {
  if (r.price == null) return <span className="text-muted-foreground">giá: không có trong kết quả</span>;
  return (
    <span className="tnum">
      <b>{fmt.n(r.price, 2)} {r.currency || ""}</b>{!r.currency && r.price_raw && <span className="text-muted-foreground"> ({r.price_raw} — tiền tệ chưa rõ)</span>}
      {r.price_verified
        ? <Badge variant="outline" className="ml-1 gap-0.5 px-1 py-0 text-[10px] text-good-text"><BadgeCheck className="size-3" />Price Verified</Badge>
        : <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px]" title="Giá quan sát trong kết quả tìm kiếm Google Lens — chưa chắc là giá hiện tại">Price Unverified</Badge>}
    </span>
  );
}

/** One web result (Google Lens via SerpApi): its own picture, shop, price as observed, verification + actions. */
export function WebResultCard({ r, jobId, index, cached, onChanged }: { r: any; jobId: number; index: number; cached: boolean; onChanged: (row: any) => void }) {
  const [busy, setBusy] = useState("");
  const call = async (what: string, fn: () => Promise<any>) => { setBusy(what); try { return await fn(); } catch (e: any) { toast.error(String(e.message || e)); } finally { setBusy(""); } };
  const save = () => call("save", async () => {
    const s = await api(`/search/images/results/${jobId}/save`, { method: "POST", body: JSON.stringify({ index }) });
    onChanged({ ...r, eu_listing_id: s.eu_listing_id, eu_product_id: s.eu_product_id, in_database: true });
    if (!s.already) toast.success(`Đã lưu vào SpyAds (listing #${s.eu_listing_id})${s.image_indexed ? " — ảnh đã vào chỉ mục" : ""}`);
    return s;
  });
  const verify = () => call("verify", async () => {
    const v = await api(`/search/images/results/${jobId}/verify`, { method: "POST", body: JSON.stringify({ index }) });
    onChanged(v);
    const pc = v.price_check || {};
    pc.ok ? toast.success(`Giá trên trang: ${fmt.n(v.price, 2)} ${v.currency || ""}`) : toast.message(pc.reason || "Không xác minh được giá");
  });
  const watch = () => call("watch", async () => {
    const s = r.eu_listing_id ? { eu_listing_id: r.eu_listing_id, eu_product_id: r.eu_product_id } : await save();
    if (!s?.eu_listing_id) return;
    await api("/search/images/watch", { method: "POST", body: JSON.stringify({ name: (r.title || r.shop_name || "Web result").slice(0, 80), owner: getUser().name || "team",
      eu_listing_id: s.eu_listing_id, eu_product_ids: s.eu_product_id ? [s.eu_product_id] : [], thumb: r.thumbnail_url, rules: { price_drop_pct: 10 } }) });
    toast.success("Đã theo dõi — xem EU Market › Watchlists");
  });
  const img = r.thumbnail_url || r.image_url;
  return (
    <Card className="flex h-full flex-col gap-0 overflow-hidden py-0" pad={false}>
      <ZoomThumb src={img} big={r.image_url} alt={r.title || ""} className="aspect-square w-full" />
      <div className="flex flex-1 flex-col gap-1.5 p-2.5 text-xs">
        <div className="flex flex-wrap items-center gap-1">
          <TierBadge tier={r.tier} similarity={r.similarity ?? undefined} />
          <Pill tone="info" className="gap-1"><Globe className="size-3" />{cached ? "Cached Web Result" : "Live Web Result"}</Pill>
          {r.in_database && <Pill tone="good" className="gap-1"><Database className="size-3" />In Database</Pill>}
          {r.match_type === "exact_match" && <Badge variant="outline" className="px-1 py-0 text-[10px]" title="Google Lens: trang chứa đúng ảnh này">Lens exact</Badge>}
          {r.keyword_hits?.length > 0 && <Badge variant="outline" className="px-1 py-0 text-[10px] text-good-text">khớp: {r.keyword_hits.join(", ")}</Badge>}
        </div>
        <div className="line-clamp-2 leading-snug font-medium" title={r.title || ""}>{r.title || <span className="text-muted-foreground">(không có tiêu đề)</span>}</div>
        <div className="flex items-center gap-1 text-muted-foreground">
          {r.source_icon && <img src={r.source_icon} alt="" className="size-3.5" referrerPolicy="no-referrer" />}
          <span className="truncate" title={r.marketplace || ""}>{r.shop_name || "—"}{r.marketplace && r.shop_name !== r.marketplace ? ` · ${r.marketplace}` : ""}</span>
        </div>
        <div><WebPrice r={r} /></div>
        {r.price_check && (r.price_check.ok
          ? <div className="text-good-text">✓ Đọc từ trang sản phẩm{r.price_check.observed_before != null && r.price_check.observed_before !== r.price ? ` (kết quả tìm kiếm ghi ${fmt.n(r.price_check.observed_before, 2)})` : " — khớp giá tìm kiếm"}</div>
          : <div className="text-warning" title={r.price_check.detail || ""}>✗ {r.price_check.reason || CHECK_REASON[r.price_check.code] || r.price_check.code}</div>)}
        {(r.rating || r.in_stock != null) && (
          <div className="text-muted-foreground">{r.rating ? `★ ${r.rating}${r.reviews ? ` (${fmt.n(r.reviews)})` : ""} · ` : ""}{r.in_stock === true ? "còn hàng" : r.in_stock === false ? "hết hàng" : ""}</div>
        )}
        <div className="text-muted-foreground" title="Thời điểm tìm thấy (kết quả web)">{fmt.dt(r.verified_at || r.observed_at)} · {r.verification_status === "visual_verified" ? `đã so ảnh${r.cosine != null ? ` (CLIP ${Math.round(r.cosine * 100)}%)` : ""}` : "chưa so ảnh"}</div>
        <div className="mt-auto flex flex-wrap items-center gap-1 pt-1">
          {r.product_url && <Button variant="outline" size="xs" asChild><a href={r.product_url} target="_blank" rel="noreferrer" title={r.product_url}>Mở <ExternalLink /></a></Button>}
          {r.product_url && !r.price_verified && (!r.price_check || r.price_check.code === "rate_limited" || r.price_check.code === "temporarily_unavailable") && (r.price != null || r.in_stock != null || r.rating) && (
            <Button variant="outline" size="xs" onClick={verify} disabled={!!busy} title="Đọc giá trực tiếp trên trang sản phẩm (dữ liệu công khai của shop, không tốn lượt API). Shop chặn truy cập tự động thì không đọc được.">
              {busy === "verify" ? <Spinner /> : <BadgeEuro />} {r.price != null ? "Xác minh giá" : "Lấy giá từ trang"}</Button>
          )}
          {!r.in_database && <Button variant="outline" size="xs" onClick={save} disabled={!!busy} title="Lưu vào SpyAds để nghiên cứu tiếp (ảnh vào chỉ mục nội bộ)">{busy === "save" ? <Spinner /> : <Save />} Lưu</Button>}
          {r.eu_product_id && <Button variant="ghost" size="xs" asChild><Link href={`/eu-market/products/view?id=${r.eu_product_id}`} title="Sản phẩm đã lưu · lịch sử giá">SP</Link></Button>}
          {img && <Button variant="ghost" size="xs" asChild><Link href={visualHref({ image_url: img, mode: "ads_match", origin: "local" } as any)} title="Quảng cáo liên quan (trong kho)"><Megaphone /></Link></Button>}
          {img && <Button variant="ghost" size="xs" asChild><Link href={visualHref({ image_url: img, mode: "find_suppliers", origin: "local" } as any)} title="Tìm nguồn hàng (trong kho)"><Truck /></Link></Button>}
          <Button variant="ghost" size="xs" onClick={watch} disabled={!!busy} title="Lưu + theo dõi giá / offer mới">{busy === "watch" ? <Spinner /> : <Eye />}</Button>
        </div>
      </div>
    </Card>
  );
}

export function QuotaPanel({ q, onRefresh }: { q: any; onRefresh?: () => void }) {
  if (!q) return null;
  const a = q.account || {};
  const why: Record<string, string> = { missing_api_key: "chưa có SERPAPI_API_KEY trong .env của server", live_search_disabled: "IMAGE_SEARCH_LIVE_ENABLED=false",
    image_search_disabled: "GLOBAL_IMAGE_SEARCH_ENABLED=false", unsupported_provider: "IMAGE_SEARCH_PROVIDER không phải serpapi_lens" };
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border bg-muted/30 px-3 py-1.5 text-xs">
      <span className="font-medium">Google Lens (SerpApi)</span>
      {q.live_unavailable ? <Pill tone="warn">Tìm Internet tắt: {why[q.live_unavailable] || q.live_unavailable}</Pill> : <Pill tone="good">sẵn sàng</Pill>}
      <span title="Ngân sách do SpyAds đặt (giới hạn cứng ở backend)">tháng <b className="tnum">{q.month_used}/{q.monthly_budget}</b> · hôm nay <b className="tnum">{q.day_used}/{q.daily_budget}</b></span>
      <span>cache hit <b className="tnum">{q.month_cache_hits}</b> · lỗi <b className="tnum">{q.month_errors}</b> · bị chặn <b className="tnum">{q.month_refused}</b> · kết quả web <b className="tnum">{fmt.n(q.month_discovered_results)}</b></span>
      {a.this_month_usage != null && <span title={`Account API, đồng bộ ${fmt.dt(a.synced_at)}`}>SerpApi thật: đã dùng <b className="tnum">{a.this_month_usage}</b>, còn <b className="tnum">{a.plan_searches_left}</b>/{a.searches_per_month} ({a.plan_name}, gia hạn {a.plan_renewal_date})</span>}
      {a.error && <span className="text-critical">Account API: {a.error}</span>}
      {onRefresh && <button type="button" className="underline" onClick={onRefresh}>làm mới</button>}
    </div>
  );
}
