"use client";
import Link from "next/link";
import { useState } from "react";
import { AlertCircle, ArrowRight, Gem, Play, TriangleAlert } from "lucide-react";
import { TrendChart } from "@/components/charts";
import { REGION_CHIPS } from "@/components/MarketPicker";
import { SimpleSelect } from "@/components/simple-select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Spinner } from "@/components/ui/spinner";
import { Card, Loading, PageHeader, ProductTable, RecBadge, SeverityIcon, Stat } from "@/components/ui";
import { api, fmt, qs, useAction, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function DailyPulse() {
  const [country, setCountry] = useState("");
  const meta = useApi("/meta");
  const { data, error, reload } = useApi(`/dashboard/pulse${qs({ country })}`);
  const act = useAction();
  const running = act.busy;
  const runPipeline = () => act.run(async () => { await api("/pipeline/run", { method: "POST" }); reload(); });
  if (!data) return <Loading error={error} />;
  const k = data.kpis;
  const hw = data.top_hidden_winners[0];
  const regions = REGION_CHIPS.filter(([k]) => k);
  const markets: (string | [string, string])[] = [...regions, ...(meta.data?.countries || []).filter((c: string) => !regions.some(([k]) => k === c))];

  return (
    <div className="space-y-4">
      <PageHeader title={`Today — ${country || "thị trường mục tiêu"}`} subtitle="Hôm nay thị trường thay đổi gì">
        <SimpleSelect value={country} onChange={setCountry} options={markets} placeholder="Mọi thị trường mục tiêu" />
        <Button size="sm" onClick={runPipeline} disabled={running}>
          {running ? <Spinner /> : <Play />}{running ? "Đang chạy pipeline…" : "Chạy pipeline hôm nay"}
        </Button>
      </PageHeader>
      {act.error && <Alert variant="destructive"><AlertCircle /><AlertTitle>Lỗi</AlertTitle><AlertDescription>{act.error}</AlertDescription></Alert>}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Products monitored" value={fmt.n(k.products_monitored)} />
        <Stat label="New products (24h)" value={fmt.n(k.new_products)} />
        <Stat label="New active ads (24h)" value={fmt.n(k.new_active_ads)} />
        <Stat label="New advertisers (24h)" value={fmt.n(k.new_advertisers)} />
        <Stat label="Potential winners" value={fmt.n(k.potential_winners)} />
        <Stat label="Hidden winners" value={fmt.n(k.hidden_winners)} />
        <Stat label="Saturated products" value={fmt.n(k.saturated_products)} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Khuyến nghị">
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.recommendations).map(([r, n]) => (
              <Link key={r} href={`/products?recommendation=${r}`} className="flex items-center gap-1.5 text-xs"><RecBadge rec={r} /><span className="tnum text-ink2">{n as number}</span></Link>
            ))}
          </div>
        </Card>

        <Card title={<span className="inline-flex items-center gap-1.5"><TriangleAlert className="size-3.5 text-warning" />Alerts</span>}
              action={<Link href="/alerts" className="inline-flex items-center gap-1 text-xs text-link">Xem tất cả <ArrowRight className="size-3" /></Link>}>
          <ScrollArea className="h-[230px] pr-3">
            <div className="space-y-2.5">
              {data.alerts.length === 0 && <div className="text-sm text-muted-foreground">Không có cảnh báo.</div>}
              {data.alerts.map((a: any) => (
                <div key={a.id} className="flex gap-2 text-sm">
                  <SeverityIcon s={a.severity} />
                  <div className="min-w-0">
                    {a.product_id ? <Link href={`/products/${a.product_id}`} className="font-medium hover:underline">{a.title}</Link> : <div className="font-medium">{a.title}</div>}
                    <div className="text-xs text-ink2">{a.message}</div>
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </Card>

        <Card title={<span className="inline-flex items-center gap-1.5"><Gem className="size-3.5 text-series-1" />Hidden winner nổi bật</span>}>
          {hw ? (
            <Link href={`/products/${hw.id}`} className="block">
              <div className="text-lg font-semibold">{hw.name}</div>
              <div className="mb-3 text-xs text-muted-foreground">{hw.product_code} · Market: {hw.markets.join(", ")}</div>
              <div className="grid grid-cols-3 gap-y-2 text-sm">
                {[["Advertisers", hw.advertisers], ["Growth 7d", fmt.signedPct(hw.growth_7d), "text-good-text"], ["Win Score", fmt.n(hw.win_score)],
                  ["Rarity", fmt.n(hw.rarity_score)], ["Opportunity", fmt.n(hw.opportunity_score)], ["Confidence", hw.confidence_label]].map(([l, v, c]) => (
                  <div key={l as string}><div className="text-xs text-muted-foreground">{l}</div><div className={cn("tnum font-semibold", c as string)}>{v}</div></div>
                ))}
              </div>
              <div className="mt-3 text-xs text-ink2">{hw.win_label} · Confidence: {hw.confidence_label} — không phải “guaranteed winner”.</div>
            </Link>
          ) : <div className="text-sm text-muted-foreground">Chưa có hidden winner.</div>}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Active ads — 30 ngày">
          <TrendChart data={data.ads_trend} series={[{ key: "active_ads", label: "Active ads" }]} />
        </Card>
        <Card title="New ads / ngày — 30 ngày">
          <TrendChart data={data.ads_trend} series={[{ key: "new_ads", label: "New ads" }]} />
        </Card>
      </div>

      <Card title="Top opportunities" action={<Link href="/products" className="inline-flex items-center gap-1 text-xs text-link">Product Explorer <ArrowRight className="size-3" /></Link>} pad={false}>
        <ProductTable rows={data.top_opportunities} compact />
      </Card>
    </div>
  );
}
