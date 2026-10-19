"use client";
import { Eye, Heart, MessageCircle, Share2 } from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { fmt } from "@/lib/api";
import { type Counts, lookupEngagement, useEngagement } from "@/lib/engagement";
import { cn } from "@/lib/utils";

const NA = "N/A";
const num = (v: number | null | undefined) => (v == null ? NA : fmt.n(v));
const WHY: Record<string, string> = {
  ad_library_has_no_post_id: "Quảng cáo trong thư viện Meta không có ID / link bài đăng gốc",
  tiktok_ad_id_is_not_a_video_id: "ID quảng cáo TikTok không phải ID video — không có link video công khai",
  tiktok_short_link: "Link rút gọn TikTok — chưa gỡ được thành ID video",
  no_post_link: "Quảng cáo không trỏ tới bài đăng / video công khai nào",
  needs_click: "Có link bài đăng — bấm để lấy số liệu (dùng 1 lượt Bright Data Posts)",
  page_posts_not_fetched: "Chưa lấy các bài đăng của page — bấm để lấy (1 lượt Bright Data, ~20 bản ghi) rồi khớp quảng cáo với bài theo landing / nội dung / ảnh",
  page_posts_pending: "Bright Data đang thu thập bài đăng của page — tự cập nhật khi xong", no_confident_post_match: "Không bài đăng nào của page khớp đủ chắc (landing + nội dung + ảnh) — N/A, không đoán",
  ambiguous_post_match: "Nhiều bài đăng khớp ngang nhau — N/A, không đoán", no_posts: "Page không có bài đăng nào lấy được (quảng cáo dạng dark post)",
  structured_disabled: "Bright Data Posts (trả phí theo bản ghi) đang tắt", brightdata_disabled: "Bright Data đang tắt", missing_api_token: "Thiếu Bright Data token",
  account_not_active: "Tài khoản Bright Data chưa kích hoạt Web Scraper API (Customer is not active)", posts_budget_zero: "Hạn mức Bright Data Posts = 0", quota_exceeded_daily: "Hết hạn mức Posts hôm nay", quota_exceeded_monthly: "Hết hạn mức Posts tháng này",
  not_found: "Bài đăng riêng tư / đã xoá — nguồn không trả bản ghi", no_metrics_in_record: "Bản ghi không có chỉ số tương tác", timeout: "Bright Data không trả lời kịp",
};

function Metrics({ c, className }: { c: Counts; className?: string }) {
  const cell = (Icon: any, v: number | null, label: string) => (
    <span className="inline-flex items-center gap-0.5" title={`${label}${v == null ? ": nguồn không cung cấp" : ""}`}>
      <Icon className="size-3" /> <span className={cn("tnum", v == null && "text-muted-foreground")}>{num(v)}</span>
    </span>
  );
  return <span className={cn("inline-flex flex-wrap items-center gap-x-2", className)}>{cell(Heart, c.likes, "Like")}{cell(MessageCircle, c.comments, "Comment")}{cell(Share2, c.shares, "Share")}{cell(Eye, c.views, "View")}</span>;
}

/** Like / Comment / Share / View of an ad. Two labelled scopes: "Quảng cáo" (the ad's own numbers from its source) and "Bài gốc" (the public
 *  post / video it was made from, Bright Data) — never summed or mixed. Unknown = N/A, a real 0 = 0. */
export function EngagementLine({ ad, detail }: { ad: any; detail?: boolean }) {
  const e = useEngagement(ad.id);
  const own: Counts = ad.engagement || { likes: ad.likes ?? null, comments: ad.comments ?? null, shares: ad.shares ?? null, views: ad.views ?? null };
  const adNums: Counts = e?.ad || own;
  const post = e?.post;
  const st = e?.status;
  const canAsk = ["needs_provider", "disabled", "quota", "error", "not_found"].includes(st || "");
  return (
    <div className={cn("space-y-0.5 text-[11px] text-ink2", detail && "text-xs")}>
      <div className="flex flex-wrap items-center gap-x-2" title="Số liệu riêng của quảng cáo, do nguồn của nó báo (TikTok Creative Center). N/A = nguồn không cung cấp.">
        <span className="text-muted-foreground">Quảng cáo</span><Metrics c={adNums} />
      </div>
      <div className="flex flex-wrap items-center gap-x-2" title="Số liệu của bài đăng / video gốc (không phải riêng của quảng cáo).">
        <span className="text-muted-foreground">Bài gốc</span>
        {post ? <>
          <Metrics c={post} />
          <span className="text-muted-foreground">{post.platform === "tiktok" ? "TikTok" : "Facebook"}{post.stale ? " · cũ" : ""}{post.observed_at ? ` · ${fmt.date(post.observed_at)}` : ""}</span>
          {post.url && <a href={post.url} target="_blank" rel="noreferrer" className="text-link underline">mở</a>}
        </> : st === "looking" ? <span className="inline-flex items-center gap-1 text-muted-foreground"><Spinner className="size-3" /> đang lấy…</span>
          : <>
            <Metrics c={{ likes: null, comments: null, shares: null, views: null }} />
            <span className="text-muted-foreground" title={e?.reason ? WHY[e.reason] || e.reason : undefined}>{e?.reason ? WHY[e.reason] || e.reason : st === "queued" || st === "unknown" || !st ? "đang kiểm tra…" : ""}</span>
            {canAsk && <button type="button" onClick={() => lookupEngagement(ad.id)} className="rounded border px-1.5 py-0.5 hover:bg-muted">Lấy tương tác</button>}
          </>}
      </div>
    </div>
  );
}
