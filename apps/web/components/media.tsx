"use client";
import { NetworkStrip, usePlatforms } from "@/components/platforms";
import { HlsVideo } from "@/components/HlsVideo";
import Link from "next/link";
import { useState } from "react";
import { api, fmt, useAction } from "@/lib/api";
import { getUser } from "@/lib/realtime";
import { RecBadge } from "@/components/ui";

const FUNNEL_LABEL: Record<string, string> = { mess: "Mess", ladi: "Ladi", form: "Form", app: "App", other: "Khác", shop: "Shop" };

export function FunnelChips({ mix }: { mix: Record<string, number> }) {
  const total = Object.values(mix || {}).reduce((a, b) => a + b, 0);
  if (!total) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {Object.entries(mix).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
        <span key={k} className="text-[10px] rounded px-1.5 py-0.5 font-medium" style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}>
          {FUNNEL_LABEL[k] || k} {Math.round((v / total) * 100)}%
        </span>
      ))}
    </span>
  );
}

/** Video / image player for a stored creative + download button. */
export function CreativeMedia({ c, height = 220, autoPlay = false }: { c: any; height?: number; autoPlay?: boolean }) {
  if (!c) return <div className="flex items-center justify-center text-xs text-muted rounded-lg" style={{ height, background: "var(--surface-2)" }}>Media đang chờ tải…</div>;
  if (c.stream_url && c.type === "image") {
    return (
      <div className="relative">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={c.stream_url} alt="" loading="lazy" referrerPolicy="no-referrer" className="w-full rounded-lg object-contain" style={{ height, background: "var(--surface-2)" }} />
        <span className="absolute top-2 left-2 text-[10px] rounded px-1.5 py-0.5" style={{ background: "rgba(0,0,0,.65)", color: "#fff" }}>ảnh từ link gốc (đang tải về kho)</span>
      </div>
    );
  }
  if (c.stream_url) {  // not stored (yet / any more) but the original link still works → stream it directly, nothing is saved
    return (
      <div className="relative">
        <video src={c.stream_url} poster={c.thumb || undefined} controls preload="none" playsInline
               className="w-full rounded-lg bg-black object-contain" style={{ height }} />
        <span className="absolute top-2 left-2 text-[10px] rounded px-1.5 py-0.5" style={{ background: "rgba(0,0,0,.65)", color: "#fff" }}>
          đang phát từ link gốc (chưa lưu)
        </span>
      </div>
    );
  }
  if (c.status !== "stored" && c.thumb) {  // archived / expired / failed: still show the frame so MKT can tell the product
    return (
      <div className="relative rounded-lg overflow-hidden" style={{ height, background: "var(--surface-2)" }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={c.thumb} alt="" loading="lazy" className="w-full h-full object-contain opacity-70" />
        <div className="absolute inset-x-0 bottom-0 text-[11px] text-center py-1.5 px-2" style={{ background: "rgba(0,0,0,.65)", color: "#fff" }}>
          {c.status === "archived" ? "Video đã dọn khỏi kho để tiết kiệm dung lượng — ghim 📌 để giữ lại lần sau" : c.status === "expired" ? "Link video gốc đã hết hạn — chỉ còn ảnh bìa" : "Video chưa tải được — đang hiện ảnh bìa"}
        </div>
      </div>
    );
  }
  if (c.status !== "stored") {
    return (
      <div className="flex flex-col items-center justify-center text-xs text-muted rounded-lg gap-1 px-3 text-center" style={{ height, background: "var(--surface-2)" }}>
        <span>{c.status === "pending" ? "Đang tải media về kho…" : c.status === "archived" ? "Đã dọn khỏi kho" : c.status === "expired" ? "Link gốc đã hết hạn" : "Tải media lỗi"}</span>
        {c.error && <span className="text-[10px]">{c.error}</span>}
      </div>
    );
  }
  if (c.type === "video" && c.hls) return <HlsVideo src={c.hls} poster={c.thumb} height={height} fallback={c.url} />;
  return c.type === "video" ? (
    <video src={c.url} poster={c.thumb || undefined} controls preload="none" playsInline autoPlay={autoPlay} muted={autoPlay}
           className="w-full rounded-lg bg-black object-contain" style={{ height }} />
  ) : (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={c.url} alt="" loading="lazy" className="w-full rounded-lg object-contain" style={{ height, background: "var(--surface-2)" }} />
  );
}

export function DownloadButton({ c, small }: { c: any; small?: boolean }) {
  if (!c?.download) return null;
  return (
    <a href={c.download} download={c.file_name} className={`btn ${small ? "text-[11px] px-2 py-1" : "text-xs"}`} title="Tải file gốc về máy">
      ⬇ Tải {c.type === "video" ? "video" : "ảnh"}{c.size ? ` · ${(c.size / 1048576).toFixed(1)}MB` : ""}
    </a>
  );
}

export const VOTES: [string, string][] = [["LOVE", "🔥"], ["TEST", "👍"], ["WATCH", "👀"], ["NORMAL", "😐"], ["SKIP", "👎"]];

export function VoteBar({ productId, market, onVoted, compact }: { productId: number; market?: string; onVoted?: () => void; compact?: boolean }) {
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const vote = async (decision: string) => {
    const u = getUser();
    if (!u.name) { setErr("Nhập tên ở thanh bên trái trước khi vote"); return; }
    try {
      await api(`/products/${productId}/vote`, { method: "POST", body: JSON.stringify({ user: u.name, team: u.team || null, market: market || null, decision, reasons: [] }) });
      setDone(decision); setErr(""); onVoted?.();
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div>
      <div className="flex gap-1">
        {VOTES.map(([d, icon]) => (
          <button key={d} onClick={() => vote(d)} title={d}
                  className={`rounded-md border ${compact ? "px-1.5 py-0.5 text-xs" : "px-2 py-1 text-sm"}`}
                  style={{ borderColor: done === d ? "var(--series-1)" : "var(--border)", background: done === d ? "var(--surface-2)" : "var(--surface-1)" }}>
            {icon}{!compact && <span className="ml-1 text-[11px]">{d}</span>}
          </button>
        ))}
      </div>
      {err && <div className="text-[11px] mt-1" style={{ color: "var(--critical)" }}>{err}</div>}
    </div>
  );
}

const hostOf = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };

const QUADRANT: Record<string, string> = { BREAKOUT: "🚀 Breakout", EXPERIMENTAL: "💎 Experimental", STABLE_WINNER: "🏛 Stable winner", LOW_SIGNAL: "· Low signal" };

/** Product card for search / discovery grids: video first. */
export function ProductMediaCard({ p, onVoted }: { p: any; onVoted?: () => void }) {
  const v = p.vector || {};
  const { byKey } = usePlatforms();
  return (
    <div className="card overflow-hidden flex flex-col">
      <div className="p-2 pb-0"><CreativeMedia c={p.cover} height={260} /></div>
      <div className="p-3 flex-1 flex flex-col gap-2">
        <div className="flex items-start justify-between gap-2">
          <Link href={`/products/${p.id}`} className="font-semibold leading-snug hover:underline line-clamp-2">{p.name}</Link>
          <RecBadge rec={p.recommendation} />
        </div>
        <div className="text-[11px] text-muted">
          {p.product_code} · {p.category || "—"} · {(p.markets || []).join(", ") || p.country || "—"}
        </div>
        <div className="flex flex-wrap gap-1 items-center">
          {p.classification && <span className="text-[11px] rounded px-1.5 py-0.5" style={{ background: "var(--surface-2)" }}>{QUADRANT[p.classification] || p.classification}</span>}
          <FunnelChips mix={p.funnel_mix} />
        </div>
        <div className="grid grid-cols-4 gap-1 text-center text-[11px]">
          {[["Opp.", v.opportunity ?? p.opportunity], ["Wave", v.wave_potential], ["Novelty", v.novelty], ["Creative", v.creative_potential]].map(([l, x]) => (
            <div key={l as string} className="rounded py-1" style={{ background: "var(--surface-2)" }}>
              <div className="font-semibold tnum text-sm">{fmt.n(x)}</div><div className="text-muted">{l}</div>
            </div>
          ))}
        </div>
        {p.landing_url && (
          <a href={p.landing_url} target="_blank" rel="noreferrer" title={p.landing_url}
             className="text-[11px] text-accent hover:underline truncate">🔗 {hostOf(p.landing_url)} ↗</a>
        )}
        <NetworkStrip networks={p.networks} byKey={byKey} />
        {(p.listings > 0 || p.organic_views > 0) && (
          <div className="text-[11px] text-ink2 flex flex-wrap gap-x-2">
            {p.reviews_total ? <span>★ {fmt.n(p.reviews_total)} đánh giá</span> : null}
            {p.sold_total ? <span>🛒 {fmt.n(p.sold_total)} đã bán</span> : null}
            {p.supplier_price_usd ? <span title="Giá thấp nhất trên AliExpress — mốc giá nhập">💲 nhập ~${p.supplier_price_usd}</span> : null}
            {p.organic_views ? <span>▶ {fmt.n(p.organic_views)} view viral</span> : null}
          </div>
        )}
        <div className="text-[11px] text-ink2">
          {p.advertisers} advertiser · {p.active_ads} ads active · +{p.new_ads_7d} ads 7d
          {p.matched_ads !== undefined && <> · {p.matched_ads} ads khớp</>}
          {p.matched_keywords?.length > 1 && <> · khớp {p.matched_keywords.length} từ: {p.matched_keywords.join(", ")}</>}
        </div>
        <div className="mt-auto flex items-center justify-between gap-2 flex-wrap">
          <VoteBar productId={p.id} compact onVoted={onVoted} />
          <DownloadButton c={p.cover} small />
        </div>
      </div>
    </div>
  );
}


export function PinButton({ c, onChange }: { c: any; onChange?: () => void }) {
  const [pinned, setPinned] = useState<boolean>(!!c.pinned);
  const act = useAction();
  if (c.status !== "stored") return null;
  const toggle = () => act.run(async () => {
    const next = !pinned;
    await api(`/creatives/${c.id}/pin?pinned=${next}`, { method: "POST" });
    setPinned(next); onChange?.();
  });
  return (
    <button className="btn text-[11px] px-2 py-1" onClick={toggle} disabled={act.busy}
            title={act.error ? `Lỗi: ${act.error}` : pinned ? "Đang ghim — không bị tự dọn" : "Ghim để không bị tự dọn sau vài ngày"}
            style={act.error ? { borderColor: "var(--critical)" } : pinned ? { borderColor: "var(--series-1)", background: "var(--surface-2)" } : undefined}>
      📌 {act.error ? "Ghim lỗi" : pinned ? "Đã ghim" : "Ghim"}
    </button>
  );
}
