"use client";
import Link from "next/link";
import { useState } from "react";
import { CreativeMedia, DownloadButton } from "@/components/media";
import { fmt } from "@/lib/api";

const PLATFORM: Record<string, string> = {
  facebook: "Facebook", instagram: "Instagram", messenger: "Messenger", audience_network: "Audience Network",
  threads: "Threads", whatsapp: "WhatsApp", tiktok: "TikTok", google: "Google",
};
const FUNNEL: Record<string, [string, string]> = {
  mess: ["💬 MKT Mess", "Quảng cáo dẫn vào inbox / WhatsApp"], ladi: ["🧾 MKT Ladi", "Quảng cáo dẫn tới landing page / shop"],
  form: ["📝 Form", "Quảng cáo thu lead bằng form"], app: ["📱 App", "Quảng cáo cài app"], other: ["Khác", ""],
};

/** One ad, laid out like a card in Meta's Ad Library: status, library id, start date, platforms, advertiser,
 *  copy, media (single / carousel) and the link bar with the CTA. */
function ago(ts: string): string {
  const m = Math.max(0, (Date.now() - new Date(ts + (ts.endsWith("Z") ? "" : "Z")).getTime()) / 60000);
  return m < 60 ? `${Math.round(m)} phút trước` : m < 1440 ? `${Math.round(m / 60)} giờ trước` : `${Math.round(m / 1440)} ngày trước`;
}

export function AdCard({ ad }: { ad: any }) {
  const [open, setOpen] = useState(false);
  const text: string = ad.text || "";
  const long = text.length > 220;
  const media = ad.creatives || [];
  const fn = FUNNEL[ad.funnel] || FUNNEL.other;
  return (
    <div className="card overflow-hidden flex flex-col">
      <div className="px-3 pt-3 text-xs space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1.5 font-medium" style={{ color: ad.active ? "var(--good-text)" : "var(--muted)" }}>
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: ad.active ? "var(--good)" : "var(--muted)" }} />
            {ad.active ? "Đang chạy" : "Đã dừng"}
          </span>
          <span className="text-muted">ID: {ad.external_id}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {ad.force_tier && (
            <span className="rounded px-1.5 py-0.5 font-medium" title="Độ mạnh: ngày chạy + số phiên bản + số nền tảng"
                  style={{ background: "var(--surface-2)", color: ad.force_tier === "legendary" ? "var(--good-text)" : undefined }}>
              {({ legendary: "🏆 Huyền thoại", strong: "💪 Mạnh", regular: "Thường", testing: "Đang test" } as any)[ad.force_tier]} {Math.round(ad.force_score)}
            </span>
          )}
          {ad.last_verified && <span className="text-muted" title={fmt.dt(ad.last_verified)}>✓ xác minh {ago(ad.last_verified)}</span>}
          {ad.reactivated_at && <span style={{ color: "var(--good-text)" }}>↻ chạy lại</span>}
          {!ad.active && ad.inactive_at && <span className="text-muted">dừng {fmt.date(ad.inactive_at)}</span>}
        </div>
        {(ad.impressions_text || ad.spend_text || ad.reach) && (
          <div className="text-ink2">{[ad.impressions_text && `Hiển thị: ${ad.impressions_text}`, ad.spend_text && `Chi tiêu: ${ad.spend_text}`, ad.reach && `Reach EU: ${fmt.n(ad.reach)}`].filter(Boolean).join(" · ")}</div>
        )}
        <div className="text-ink2">
          Bắt đầu chạy: {fmt.date(ad.first_seen)}{ad.days_running !== null && ad.days_running !== undefined ? ` · ${ad.days_running} ngày` : ""}
        </div>
        <div className="flex flex-wrap gap-1">
          {(ad.platforms || []).map((p: string) => <span key={p} className="rounded px-1.5 py-0.5" style={{ background: "var(--surface-2)" }}>{PLATFORM[p] || p}</span>)}
          {(ad.markets?.length ? ad.markets : ad.country ? [ad.country] : []).slice(0, 6).map((m: string) => (
            <span key={m} className="rounded px-1.5 py-0.5" style={{ background: "var(--surface-2)" }}>{m === "ALL" ? "🌍 Toàn cầu" : `📍 ${m}`}</span>
          ))}
        </div>
        {ad.variants > 1 && <div className="text-ink2">Quảng cáo này có {ad.variants} phiên bản</div>}
        {ad.snapshot_url && (
          <a href={ad.snapshot_url} target="_blank" rel="noreferrer" className="btn w-full text-center block mt-1">Xem chi tiết quảng cáo ↗</a>
        )}
      </div>
      <div className="mx-3 my-2.5 h-px" style={{ background: "var(--grid)" }} />

      <div className="px-3 flex items-center gap-2">
        <div className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold shrink-0" style={{ background: "var(--surface-2)" }}>
          {(ad.advertiser || "?").slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0">
          {ad.advertiser_url ? <a href={ad.advertiser_url} target="_blank" rel="noreferrer" className="text-sm font-semibold truncate block hover:underline">{ad.advertiser}</a>
            : <div className="text-sm font-semibold truncate">{ad.advertiser || "—"}</div>}
          <div className="text-[11px] text-muted">Được tài trợ</div>
        </div>
      </div>

      {text && (
        <div className="px-3 pt-2 text-[13px] leading-snug whitespace-pre-wrap break-words">
          {open || !long ? text : `${text.slice(0, 220)}…`}
          {long && <button className="text-accent ml-1" onClick={() => setOpen(!open)}>{open ? "Thu gọn" : "Xem thêm"}</button>}
        </div>
      )}

      <div className="px-3 pt-2">
        {media.length === 0 ? (
          <div className="rounded-lg text-xs text-muted flex items-center justify-center text-center px-3" style={{ height: 160, background: "var(--surface-2)" }}>
            Quảng cáo dạng catalog / động — Meta không trả file media. Xem ở “Xem chi tiết quảng cáo”.
          </div>
        ) : media.length === 1 ? (
          <CreativeMedia c={media[0]} height={320} />
        ) : (
          <div className="flex gap-2 overflow-x-auto snap-x pb-2" style={{ scrollbarWidth: "thin" }}>
            {media.map((c: any) => (
              <div key={c.id} className="snap-start shrink-0" style={{ width: 230 }}><CreativeMedia c={c} height={280} /></div>
            ))}
          </div>
        )}
        {media.length > 1 && <div className="text-[11px] text-muted">{media.length} ảnh / video trong quảng cáo — vuốt ngang để xem</div>}
      </div>

      {(ad.landing_domain || ad.title || ad.cta) && (
        <div className="mx-3 mt-2 rounded-lg px-3 py-2 flex items-center justify-between gap-2" style={{ background: "var(--surface-2)" }}>
          <div className="min-w-0">
            {ad.landing_domain && <div className="text-[10px] uppercase tracking-wide text-muted truncate">{ad.landing_domain}</div>}
            {ad.title && <div className="text-[13px] font-semibold truncate">{ad.title}</div>}
          </div>
          {ad.cta && (ad.landing_url
            ? <a href={ad.landing_url} target="_blank" rel="noreferrer" className="btn text-xs shrink-0">{ad.cta}</a>
            : <span className="btn text-xs shrink-0">{ad.cta}</span>)}
        </div>
      )}

      <div className="px-3 py-3 mt-auto flex flex-wrap gap-1.5 items-center text-[11px]">
        <span className="rounded px-1.5 py-0.5 font-medium" title={fn[1]} style={{ background: "var(--surface-2)" }}>{fn[0]}</span>
        {ad.hook && ad.hook !== "statement" && <span className="rounded px-1.5 py-0.5" style={{ background: "var(--surface-2)" }}>hook: {ad.hook}</span>}
        {ad.angle && ad.angle !== "general" && <span className="rounded px-1.5 py-0.5" style={{ background: "var(--surface-2)" }}>angle: {ad.angle}</span>}
        {ad.product && <Link href={`/products/${ad.product.id}`} className="text-accent hover:underline truncate max-w-[160px]" title={ad.product.name}>Sản phẩm →</Link>}
        <span className="flex-1" />
        {media.filter((c: any) => c.download).map((c: any, i: number) => (
          <DownloadButton key={c.id} c={{ ...c, size: undefined }} small />
        )).slice(0, 3)}
      </div>
    </div>
  );
}
