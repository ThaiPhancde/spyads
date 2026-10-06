"use client";
import { useState } from "react";
import { CreativeMedia, DownloadButton, FunnelChips, VOTES, VoteBar } from "@/components/media";
import { Card, Empty, Loading, Pill, RecBadge } from "@/components/ui";
import { api, fmt, useApi } from "@/lib/api";
import { getUser, useEvents } from "@/lib/realtime";

const VEC: [string, string, boolean?][] = [
  ["market_demand", "Market demand"], ["novelty", "Novelty"], ["wave_potential", "Wave potential"],
  ["creative_potential", "Creative potential"], ["market_fit", "Market fit"], ["mkt_appeal", "MKT appeal"],
  ["competition", "Competition", true], ["economics", "Economics"], ["operational_fit", "Operational fit"],
  ["compliance_risk", "Compliance risk", true], ["confidence", "Confidence"], ["company_fit", "Company fit (nội bộ)"],
];

export function DiscoveryPanel({ productId, onChange }: { productId: number; onChange?: () => void }) {
  const { data, error, reload } = useApi(`/products/${productId}/discovery`);
  const meta = useApi("/discovery/meta");
  const [reasons, setReasons] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  useEvents((e) => { if (e.product_id === productId && ["CREATIVE_STORED", "DECISION_CHANGED"].includes(e.type)) reload(); }, [productId]);
  if (!data) return <Loading error={error} />;
  const pv = data.potential || {};
  const v = pv.vector || {};
  const videos = data.creatives.filter((c: any) => c.type === "video");
  const images = data.creatives.filter((c: any) => c.type !== "video");

  const vote = async (decision: string) => {
    const u = getUser();
    if (!u.name) { setMsg("Nhập tên ở thanh bên trái trước khi vote."); return; }
    await api(`/products/${productId}/vote`, { method: "POST", body: JSON.stringify({ user: u.name, team: u.team || null, decision, reasons, note }) });
    setMsg(`Đã vote ${decision}`); setReasons([]); setNote(""); reload(); onChange?.();
  };

  return (
    <div className="space-y-4">
      <Card title={`Video & creative (${videos.length} video · ${images.length} ảnh · ${data.families} creative family)`}>
        {data.creatives.length === 0 ? <Empty>Chưa có media cho sản phẩm này.</Empty> : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
            {[...videos, ...images].slice(0, 24).map((c: any) => (
              <div key={c.id} className="flex flex-col gap-1.5">
                <CreativeMedia c={c} height={280} />
                <div className="text-[11px] text-ink2 flex flex-wrap gap-x-2">
                  <span className="font-medium">{c.ad?.advertiser}</span>
                  <span>{c.ad?.country}</span><span>{c.ad?.days_running ?? "—"} ngày</span>
                  {c.ad?.funnel && <span>funnel {c.ad.funnel}</span>}{c.family_size > 1 && <span>family ×{c.family_size}</span>}
                </div>
                <div className="flex gap-1.5 flex-wrap">
                  <DownloadButton c={c} small />
                  {c.ad?.snapshot_url && <a className="btn text-[11px] px-2 py-1" href={c.ad.snapshot_url} target="_blank" rel="noreferrer">Ad gốc ↗</a>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card title="Product Potential Vector">
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            {data.classification && <Pill tone="info">{data.classification}</Pill>}
            <span className="text-2xl font-semibold tnum">{fmt.n(v.opportunity)}</span><span className="text-xs text-muted">Opportunity</span>
          </div>
          <div className="space-y-1.5">
            {VEC.map(([k, label, inverse]) => (
              <div key={k} className="grid grid-cols-[130px_1fr_36px] items-center gap-2 text-xs">
                <span className="text-ink2">{label}</span>
                {v[k] === null || v[k] === undefined ? <span className="text-muted">chưa có dữ liệu</span> : (
                  <div className="h-1.5 rounded-full" style={{ background: "var(--grid)" }}>
                    <div className="h-1.5 rounded-full" style={{ width: `${v[k]}%`, background: inverse ? "var(--serious)" : "var(--series-1)" }} />
                  </div>
                )}
                <span className="tnum text-right">{fmt.n(v[k])}</span>
              </div>
            ))}
          </div>
          <div className="text-[11px] text-muted mt-3">MKT appeal: {pv.mkt_appeal_source}. {pv.compliance_flags?.length ? `Compliance flags: ${pv.compliance_flags.join(", ")}` : ""}</div>
        </Card>

        <Card title="Theo từng thị trường">
          {Object.keys(data.market_scores).length === 0 ? <Empty>—</Empty> : (
            <table className="data"><thead><tr><th>Market</th><th>Market fit</th><th>Quyết định</th></tr></thead>
              <tbody>{Object.entries(data.market_scores).map(([m, s]: any) => (
                <tr key={m}><td className="font-medium">{m}</td><td className="tnum">{fmt.n(s.market_fit)}</td><td><RecBadge rec={s.decision} /></td></tr>
              ))}</tbody></table>
          )}
          <div className="mt-4 text-xs text-muted mb-1">Funnel của đối thủ đang chạy</div>
          <FunnelChips mix={data.funnel_mix} />
          <div className="mt-4 text-xs text-muted mb-1">Nguồn phát hiện (provenance)</div>
          <div className="flex flex-wrap gap-1">{Object.entries(data.sources).map(([s, n]) => <Pill key={s}>{s} · {n as number}</Pill>)}</div>
          {pv.wave_parts && (
            <>
              <div className="mt-4 text-xs text-muted mb-1">Thành phần Wave</div>
              <div className="text-xs text-ink2">{Object.entries(pv.wave_parts).map(([k, x]) => `${k.replace(/_/g, " ")} ${x}`).join(" · ")}</div>
            </>
          )}
        </Card>

        <Card title="MKT vote (Marketing Team Taste)">
          <div className="flex flex-wrap gap-1 mb-2">
            {(meta.data?.vote_reasons || []).map((r: string) => (
              <button key={r} onClick={() => setReasons((rs) => rs.includes(r) ? rs.filter((x) => x !== r) : [...rs, r])}
                      className="text-[11px] rounded-full px-2 py-0.5 border"
                      style={{ borderColor: reasons.includes(r) ? "var(--series-1)" : "var(--border)", fontWeight: reasons.includes(r) ? 600 : 400 }}>{r}</button>
            ))}
          </div>
          <input className="input w-full mb-2" placeholder="Ghi chú (angle muốn đánh, giá, market…)" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex gap-1 flex-wrap">
            {VOTES.map(([d, icon]) => <button key={d} className="btn text-xs" onClick={() => vote(d)}>{icon} {d}</button>)}
          </div>
          {msg && <div className="text-xs mt-2 text-ink2">{msg}</div>}
          <div className="mt-3 text-xs text-muted">{Object.entries(data.vote_summary).map(([k, n]) => `${k} ${n}`).join(" · ") || "Chưa có vote"}</div>
          <ul className="mt-2 space-y-1 text-xs max-h-40 overflow-y-auto">
            {data.votes.map((x: any, i: number) => (
              <li key={i}><b>{x.user}</b>{x.team ? ` (${x.team})` : ""}: {x.decision} {x.reasons?.length ? `— ${x.reasons.join(", ")}` : ""} {x.note ? `“${x.note}”` : ""}</li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

export { VoteBar };
