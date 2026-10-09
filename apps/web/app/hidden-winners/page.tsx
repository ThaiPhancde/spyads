"use client";
import Link from "next/link";
import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { MarketPicker } from "@/components/MarketPicker";
import { CreativeMedia, DownloadButton, VoteBar } from "@/components/media";
import { Card, Empty, Loading, PageHeader, RecBadge } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Card as UiCard, CardContent } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { fmt, qs, useApi } from "@/lib/api";
import { useEvents } from "@/lib/realtime";

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
const chip = "text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground";

export default function HiddenWinners() {
  const [tag, setTag] = useState("");
  const [scope, setScope] = useState("");
  const { data, error, reload } = useApi(`/products/hidden-winners${qs({ tag, scope })}`);
  useEvents((e) => { if (["SEARCH_DONE", "CONNECTOR_SYNCED", "LIVENESS_CHECKED"].includes(e.type)) reload(); });
  const labels: Record<string, string> = data?.tag_labels || {};
  return (
    <div className="space-y-4">
      <PageHeader title="Hidden Winners"
                  subtitle="Sản phẩm ít người bán (≤ 5) nhưng quảng cáo đang thắng: chạy lâu, được nhân bản, tăng ads trong tuần. Tính trực tiếp từ dữ liệu Ad Library." />
      <MarketPicker value={scope} onChange={setScope} />
      <div>
        <ToggleGroup type="single" variant="outline" size="sm" spacing={1.5} value={tag || "all"} onValueChange={(v) => setTag(!v || v === "all" ? "" : v)} className="flex-wrap justify-start">
          <ToggleGroupItem value="all" className={chip}>Tất cả {data ? <span className="tnum opacity-70">{data.total}</span> : null}</ToggleGroupItem>
          {Object.entries(labels).map(([k, l]) => (
            <ToggleGroupItem key={k} value={k} disabled={!data?.tags?.[k]} className={chip}>
              {l} <span className="tnum opacity-70">{data?.tags?.[k] || 0}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Điểm = 40% quảng cáo mạnh nhất (số ngày chạy + biến thể + vị trí) · 25% độ hiếm (ít người bán) · 20% ads mới 7 ngày · 15% số biến thể đang chạy.
        </p>
      </div>
      {!data ? <Loading error={error} /> : data.rows.length === 0 ? <Card><Empty>Chưa có hidden winner với bộ lọc này.</Empty></Card> : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {data.rows.map((p: any) => (
            <UiCard key={p.id} className="gap-0 overflow-hidden py-0">
              <div className="p-2 pb-0"><CreativeMedia c={p.cover} height={240} /></div>
              <CardContent className="flex flex-1 flex-col gap-2 p-3">
                <div className="flex items-start justify-between gap-2">
                  <Link href={`/products/${p.id}`} className="line-clamp-2 leading-snug font-semibold hover:underline">{p.name}</Link>
                  <RecBadge rec={p.recommendation} />
                </div>
                <div className="text-[11px] text-muted-foreground">{p.product_code} · {p.category || "—"} · {(p.countries || []).join(", ") || "toàn cầu"}</div>
                <div className="flex items-baseline gap-2">
                  <div className="tnum text-2xl font-semibold">{fmt.n(p.score)}</div>
                  <div className="text-[11px] text-muted-foreground">Hidden Winner score</div>
                </div>
                <div className="flex flex-wrap gap-1">
                  {p.tags.map((t: string) => <Badge key={t} variant="secondary" className="text-[11px]">{labels[t] || t}</Badge>)}
                </div>
                <div className="text-[11px] text-ink2">{p.why.join(" · ")}</div>
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
    </div>
  );
}
