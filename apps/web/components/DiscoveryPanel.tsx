"use client";
import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { CreativeMedia, DownloadButton, FunnelChips, VOTES, VoteBar } from "@/components/media";
import { Card, Empty, Loading, Pill, RecBadge, ScoreBar } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, fmt, useApi } from "@/lib/api";
import { getUser, useEvents } from "@/lib/realtime";

const VEC: [string, string, boolean?][] = [
  ["market_demand", "Market demand"], ["novelty", "Novelty"], ["wave_potential", "Wave potential"],
  ["creative_potential", "Creative potential"], ["market_fit", "Market fit"], ["mkt_appeal", "MKT appeal"],
  ["competition", "Competition", true], ["economics", "Economics"],
  ["compliance_risk", "Compliance risk", true], ["confidence", "Confidence"],
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
    if (!u.name) { setMsg("Nhập tên ở thanh bên trái trước khi vote."); toast.warning("Nhập tên ở thanh bên trái trước khi vote."); return; }
    try {
      await api(`/products/${productId}/vote`, { method: "POST", body: JSON.stringify({ user: u.name, team: u.team || null, decision, reasons, note }) });
      setMsg(`Đã vote ${decision}`); toast.success(`Đã vote ${decision}`); setReasons([]); setNote(""); reload(); onChange?.();
    } catch (e: any) { setMsg(`Lỗi: ${e.message}`); toast.error("Vote thất bại", { description: e.message }); }
  };

  return (
    <div className="space-y-4">
      <Card title={`Video & creative (${videos.length} video · ${images.length} ảnh · ${data.families} creative family)`}>
        {data.creatives.length === 0 ? <Empty>Chưa có media cho sản phẩm này.</Empty> : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[...videos, ...images].slice(0, 24).map((c: any) => (
              <div key={c.id} className="flex min-w-0 flex-col gap-1.5">
                <CreativeMedia c={c} height={280} />
                <div className="flex flex-wrap gap-x-2 text-[11px] text-ink2">
                  <span className="font-medium">{c.ad?.advertiser}</span>
                  <span>{c.ad?.country}</span><span>{c.ad?.days_running ?? "—"} ngày</span>
                  {c.ad?.funnel && <span>funnel {c.ad.funnel}</span>}{c.family_size > 1 && <span>family ×{c.family_size}</span>}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <DownloadButton c={c} small />
                  {c.ad?.snapshot_url && <Button variant="outline" size="xs" asChild><a href={c.ad.snapshot_url} target="_blank" rel="noreferrer">Ad gốc <ExternalLink /></a></Button>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Product Potential Vector">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {data.classification && <Pill tone="info">{data.classification}</Pill>}
            <span className="tnum text-2xl font-semibold">{fmt.n(v.opportunity)}</span><span className="text-xs text-muted-foreground">Opportunity</span>
          </div>
          <div className="space-y-1.5">
            {VEC.map(([k, label, inverse]) => (
              <div key={k} className="grid grid-cols-[130px_1fr] items-center gap-2 text-xs">
                <span className="truncate text-ink2">{label}</span>
                {v[k] === null || v[k] === undefined ? <span className="text-muted-foreground">chưa có dữ liệu</span>
                  : <ScoreBar value={v[k]} width={140} tone={inverse ? "bg-serious" : "bg-series-1"} />}
              </div>
            ))}
          </div>
          <div className="mt-3 text-[11px] text-muted-foreground">MKT appeal: {pv.mkt_appeal_source}. {pv.compliance_flags?.length ? `Compliance flags: ${pv.compliance_flags.join(", ")}` : ""}</div>
        </Card>

        <Card title="Theo từng thị trường">
          {Object.keys(data.market_scores).length === 0 ? <Empty>—</Empty> : (
            <div className="max-h-80 overflow-y-auto">
            <Table>
              <TableHeader><TableRow><TableHead>Market</TableHead><TableHead className="text-right">Market fit</TableHead><TableHead>Quyết định</TableHead></TableRow></TableHeader>
              <TableBody>{Object.entries(data.market_scores).map(([m, s]: any) => (
                <TableRow key={m}><TableCell className="font-medium">{m}</TableCell><TableCell className="tnum text-right">{fmt.n(s.market_fit)}</TableCell><TableCell><RecBadge rec={s.decision} /></TableCell></TableRow>
              ))}</TableBody>
            </Table>
            </div>
          )}
          <div className="mt-4 mb-1 text-xs text-muted-foreground">Funnel của đối thủ đang chạy</div>
          <FunnelChips mix={data.funnel_mix} />
          <div className="mt-4 mb-1 text-xs text-muted-foreground">Nguồn phát hiện (provenance)</div>
          <div className="flex flex-wrap gap-1">{Object.entries(data.sources).map(([s, n]) => <Pill key={s}>{s} · {n as number}</Pill>)}</div>
          {pv.wave_parts && (
            <>
              <div className="mt-4 mb-1 text-xs text-muted-foreground">Thành phần Wave</div>
              <div className="text-xs text-ink2">{Object.entries(pv.wave_parts).map(([k, x]) => `${k.replace(/_/g, " ")} ${x}`).join(" · ")}</div>
            </>
          )}
        </Card>

        <Card title="MKT vote (Marketing Team Taste)">
          <ToggleGroup type="multiple" variant="outline" size="sm" spacing={1} value={reasons} onValueChange={setReasons} className="mb-2 flex-wrap">
            {(meta.data?.vote_reasons || []).map((r: string) => <ToggleGroupItem key={r} value={r} className="h-7 rounded-full px-2.5 text-[11px]">{r}</ToggleGroupItem>)}
          </ToggleGroup>
          <Input className="mb-2" placeholder="Ghi chú (angle muốn đánh, giá, market…)" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex flex-wrap gap-1">
            {VOTES.map(([d, icon]) => <Button key={d} variant="outline" size="xs" onClick={() => vote(d)}>{icon} {d}</Button>)}
          </div>
          {msg && <div className="mt-2 text-xs text-ink2">{msg}</div>}
          <div className="mt-3 text-xs text-muted-foreground">{Object.entries(data.vote_summary).map(([k, n]) => `${k} ${n}`).join(" · ") || "Chưa có vote"}</div>
          <ScrollArea className="mt-2 max-h-40 overflow-y-auto">
            <ul className="space-y-1 text-xs">
              {data.votes.map((x: any, i: number) => (
                <li key={i}><b>{x.user}</b>{x.team ? ` (${x.team})` : ""}: {x.decision} {x.reasons?.length ? `— ${x.reasons.join(", ")}` : ""} {x.note ? `“${x.note}”` : ""}</li>
              ))}
            </ul>
          </ScrollArea>
        </Card>
      </div>
    </div>
  );
}

export { VoteBar };
