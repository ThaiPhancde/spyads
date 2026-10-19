"use client";
import Link from "next/link";
import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { MarketPicker } from "@/components/MarketPicker";
import { LoadMore } from "@/components/paging";
import { CreativeMedia, DownloadButton, VoteBar } from "@/components/media";
import { Card, Empty, Loading, PageHeader, RecBadge } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Card as UiCard, CardContent } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { fmt, qs, usePaged } from "@/lib/api";
import { useEvents } from "@/lib/realtime";

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
const chip = "text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground";

export default function HiddenWinners() {
  const [tag, setTag] = useState("");
  const [scope, setScope] = useState("");
  const [status, setStatus] = useState("CONFIRMED");
  return <HiddenWinnerResults key={`${scope}:${tag}:${status}`} tag={tag} scope={scope} status={status} setTag={setTag} setScope={setScope} setStatus={setStatus} />;
}

function HiddenWinnerResults({ tag, scope, status, setTag, setScope, setStatus }: {
  tag: string; scope: string; status: string; setTag: (value: string) => void; setScope: (value: string) => void; setStatus: (value: string) => void;
}) {
  const { rows, total, last: data, error, loading, hasMore, loadMore, reload } = usePaged(`/products/hidden-winners${qs({ tag, scope, status })}`, 50);
  useEvents((e) => { if (["SEARCH_DONE", "CONNECTOR_SYNCED", "LIVENESS_CHECKED"].includes(e.type)) reload(); });
  const labels: Record<string, string> = data?.tag_labels || {};
  return (
    <div className="space-y-4">
      <PageHeader title="Hidden Winners"
                  subtitle="Nhãn bổ trợ cho Opportunity: 1–3 nhà quảng cáo, có ad chạy ≥7 ngày, có momentum từ snapshot hoặc ≥2 creative khác biệt đã xác nhận, và không thuộc SKIP." />
      <MarketPicker value={scope} onChange={setScope} />
      <ToggleGroup type="single" variant="outline" size="sm" value={status} onValueChange={(v) => { if (v) { setStatus(v); setTag(""); } }} className="flex-wrap justify-start">
        {Object.entries(data?.status_labels || { CONFIRMED: "Hidden Winner đã xác nhận", WATCH: "Chưa xác nhận / Cần theo dõi", REJECTED: "Không đạt điều kiện" }).map(([k, label]) => (
          <ToggleGroupItem key={k} value={k} className={chip}>{String(label)} <span className="tnum opacity-70">{data?.status_counts?.[k] ?? ""}</span></ToggleGroupItem>
        ))}
      </ToggleGroup>
      <div>
        <ToggleGroup type="single" variant="outline" size="sm" spacing={1.5} value={tag || "all"} onValueChange={(v) => setTag(!v || v === "all" ? "" : v)} className="flex-wrap justify-start">
          <ToggleGroupItem value="all" className={chip}>Tất cả {data ? <span className="tnum opacity-70">{data.all_total}</span> : null}</ToggleGroupItem>
          {Object.entries(labels).map(([k, l]) => (
            <ToggleGroupItem key={k} value={k} disabled={!data?.tags?.[k]} className={chip}>
              {l} <span className="tnum opacity-70">{data?.tags?.[k] || 0}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Sắp xếp theo Opportunity. Momentum chỉ tính trên cùng tập quảng cáo qua lịch sử quan sát; quảng cáo mới crawl và ảnh trùng không đủ để xác nhận.
        </p>
      </div>
      {data && <p className="text-sm text-muted-foreground">Đang hiển thị {fmt.n(rows.length)} / {fmt.n(total)} sản phẩm{tag ? " khớp bộ lọc" : ""}.</p>}
      {!data ? <Loading error={error} /> : rows.length === 0 ? <Card><Empty>Chưa có hidden winner với bộ lọc này.</Empty></Card> : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {rows.map((p: any) => (
            <UiCard key={p.id} className="gap-0 overflow-hidden py-0">
              <div className="p-2 pb-0"><CreativeMedia c={p.cover} height={240} /></div>
              <CardContent className="flex flex-1 flex-col gap-2 p-3">
                <div className="flex items-start justify-between gap-2">
                  <Link href={`/products/${p.id}`} className="line-clamp-2 leading-snug font-semibold hover:underline">{p.name}</Link>
                  <RecBadge rec={p.recommendation} />
                </div>
                <div className="text-[11px] text-muted-foreground">{p.product_code} · {p.category || "—"} · {(p.countries || []).join(", ") || "toàn cầu"}</div>
                <div className="flex items-baseline gap-2">
                  <div className="tnum text-2xl font-semibold">{fmt.n(p.opportunity)}</div>
                  <div className="text-[11px] text-muted-foreground">Opportunity</div>
                </div>
                <Badge variant="outline" className="text-[11px]">{p.status_label}</Badge>
                <div className="flex flex-wrap gap-1">
                  {p.tags.map((t: string) => <Badge key={t} variant="secondary" className="text-[11px]">{labels[t] || t}</Badge>)}
                </div>
                <ul className="space-y-1 text-[11px] text-ink2">{(p.checks || []).map((c: any) => <li key={c.key}>{c.passed ? "✓" : "○"} {c.reason}</li>)}</ul>
                {p.landing_url && (
                  <a href={p.landing_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 truncate text-[11px] text-link hover:underline" title={p.landing_url}>
                    <ExternalLink className="size-3 shrink-0" />{host(p.landing_url)}
                  </a>
                )}
                <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
                  <VoteBar productId={p.id} compact />
                  <DownloadButton c={p.cover} small />
                </div>
              </CardContent>
            </UiCard>
          ))}
        </div>
      )}
      {data && error && <p role="alert" className="text-sm text-destructive">Không tải được dữ liệu: {error}</p>}
      {data && <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />}
    </div>
  );
}
