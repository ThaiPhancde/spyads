"use client";
import Link from "next/link";
import { useState } from "react";
import { Card, Empty, Loading, PageHeader, RecBadge } from "@/components/ui";
import { CreativeMedia, DownloadButton, VoteBar } from "@/components/media";
import { MarketPicker } from "@/components/MarketPicker";
import { fmt, qs, useApi } from "@/lib/api";
import { useEvents } from "@/lib/realtime";

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };

export default function HiddenWinners() {
  const [tag, setTag] = useState("");
  const [scope, setScope] = useState("");
  const { data, error, reload } = useApi(`/products/hidden-winners${qs({ tag, scope })}`);
  useEvents((e) => { if (["SEARCH_DONE", "CONNECTOR_SYNCED", "LIVENESS_CHECKED"].includes(e.type)) reload(); });
  const labels: Record<string, string> = data?.tag_labels || {};
  return (
    <div>
      <PageHeader title="Hidden Winners"
                  subtitle="Sản phẩm ít người bán (≤ 5) nhưng quảng cáo đang thắng: chạy lâu, được nhân bản, tăng ads trong tuần. Tính trực tiếp từ dữ liệu Ad Library." />
      <div className="mb-3"><MarketPicker value={scope} onChange={setScope} /></div>
      <div className="flex flex-wrap gap-2 mb-2">
        <button className="btn text-xs" style={{ fontWeight: tag === "" ? 700 : 400, background: tag === "" ? "var(--surface-2)" : undefined }} onClick={() => setTag("")}>
          Tất cả {data ? <span className="text-muted ml-1">{data.total}</span> : null}
        </button>
        {Object.entries(labels).map(([k, l]) => (
          <button key={k} className="btn text-xs" disabled={!data?.tags?.[k]}
                  style={{ fontWeight: tag === k ? 700 : 400, background: tag === k ? "var(--surface-2)" : undefined, opacity: data?.tags?.[k] ? 1 : 0.45 }}
                  onClick={() => setTag(tag === k ? "" : k)}>
            {l} <span className="text-muted ml-1">{data?.tags?.[k] || 0}</span>
          </button>
        ))}
      </div>
      <div className="text-[11px] text-muted mb-4">
        Điểm = 40% quảng cáo mạnh nhất (số ngày chạy + biến thể + vị trí) · 25% độ hiếm (ít người bán) · 20% ads mới 7 ngày · 15% số biến thể đang chạy.
      </div>
      {!data ? <Loading error={error} /> : data.rows.length === 0 ? <Card><Empty>Chưa có hidden winner với bộ lọc này.</Empty></Card> : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {data.rows.map((p: any) => (
            <div key={p.id} className="card overflow-hidden flex flex-col">
              <div className="p-2 pb-0"><CreativeMedia c={p.cover} height={240} /></div>
              <div className="p-3 flex-1 flex flex-col gap-2">
                <div className="flex items-start justify-between gap-2">
                  <Link href={`/products/${p.id}`} className="font-semibold leading-snug hover:underline line-clamp-2">{p.name}</Link>
                  <RecBadge rec={p.recommendation} />
                </div>
                <div className="text-[11px] text-muted">{p.product_code} · {p.category || "—"} · {(p.countries || []).join(", ") || "toàn cầu"}</div>
                <div className="flex items-baseline gap-2">
                  <div className="text-2xl font-semibold tnum">{fmt.n(p.score)}</div>
                  <div className="text-[11px] text-muted">Hidden Winner score</div>
                </div>
                <div className="flex flex-wrap gap-1">
                  {p.tags.map((t: string) => <span key={t} className="text-[11px] rounded px-1.5 py-0.5" style={{ background: "var(--surface-2)" }}>{labels[t] || t}</span>)}
                </div>
                <div className="text-[11px] text-ink2">{p.why.join(" · ")}</div>
                {p.landing_url && <a href={p.landing_url} target="_blank" rel="noreferrer" className="text-[11px] text-accent hover:underline truncate" title={p.landing_url}>🔗 {host(p.landing_url)} ↗</a>}
                <div className="mt-auto flex items-center justify-between gap-2 flex-wrap">
                  <VoteBar productId={p.id} compact />
                  <DownloadButton c={p.cover} small />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
