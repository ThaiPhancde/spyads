"use client";
import Link from "next/link";
import { useState } from "react";
import { ChevronDown, Globe, RefreshCw } from "lucide-react";
import { Card, Loading, PageHeader } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Table as UiTable, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { fmt, qs, useApi } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";
import { cn } from "@/lib/utils";

const FRESH: Record<string, [string, string]> = {
  fresh: ["bg-good", "Mới (<1h)"], recent: ["bg-series-1", "Gần đây (<6h)"],
  stale: ["bg-warning", "Cũ (<24h)"], unreliable: ["bg-critical", "Không tin cậy (>24h)"],
};

function Trend({ t, g }: { t: string; g: number | null }) {
  const up = t.startsWith("↑") || t === "NEW";
  const down = t.startsWith("↓");
  return (
    <span className={cn("tnum whitespace-nowrap", up ? "text-good-text" : down ? "text-critical" : "text-ink2")}>
      {t}{g !== null && g !== undefined ? ` ${fmt.signedPct(g)}` : ""}
    </span>
  );
}

function Fresh({ f, at }: { f: string; at: string | null }) {
  const [c, l] = FRESH[f] || FRESH.unreliable;
  return (
    <Badge variant="outline" className="gap-1.5 font-normal whitespace-nowrap" title={at ? `Xác minh lần cuối: ${fmt.dt(at)}` : "Chưa xác minh"}>
      <span className={cn("inline-block size-2 rounded-full", c)} />{l}
    </Badge>
  );
}

function Table({ rows, muted }: { rows: any[]; muted?: boolean }) {
  return (
    <div className="max-h-[70vh] overflow-x-auto">
      <UiTable>
        <TableHeader className="sticky top-0 z-10 bg-card">
          <TableRow>
            <TableHead>Nước</TableHead><TableHead>Vùng</TableHead><TableHead className="text-right">Ads đang chạy</TableHead><TableHead className="text-right">Sellers</TableHead>
            <TableHead className="text-right">Sản phẩm</TableHead><TableHead className="text-right">Ads mới</TableHead><TableHead>Xu hướng</TableHead>
            <TableHead className="text-right">Ads mạnh</TableHead><TableHead className="text-right">Mess / Ladi</TableHead><TableHead>Category nổi bật</TableHead><TableHead>Độ tươi</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.country} className={cn(muted && "opacity-60")}>
              <TableCell className="font-medium">
                <Link className="inline-flex items-center gap-1 hover:underline" href={`/product-radar?market=${r.country}`}>
                  {r.country === "ALL" ? <><Globe className="size-3.5 text-muted-foreground" /> Toàn cầu</> : r.country}
                </Link>
              </TableCell>
              <TableCell className="text-xs text-ink2">{r.region || "—"}</TableCell>
              <TableCell className="tnum text-right">{fmt.n(r.active_ads)}</TableCell>
              <TableCell className="tnum text-right">{r.advertisers}</TableCell>
              <TableCell className="tnum text-right">{r.products}</TableCell>
              <TableCell className="tnum text-right">{r.new_ads} <span className="text-xs text-muted-foreground">/ {r.prev_new_ads}</span></TableCell>
              <TableCell><Trend t={r.trend} g={r.growth} /></TableCell>
              <TableCell className="tnum text-right">{r.strong_ads}</TableCell>
              <TableCell className="tnum text-right text-xs">{Math.round(r.mess_share * 100)}% / {Math.round(r.ladi_share * 100)}%</TableCell>
              <TableCell className="text-xs">{r.top_categories.join(", ")}</TableCell>
              <TableCell><Fresh f={r.freshness} at={r.last_verified} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </UiTable>
    </div>
  );
}

export default function MarketRadar() {
  const [window, setWindow] = useState(7);
  const [showOutside, setShowOutside] = useState(false);
  const team = useTeam();
  const { data, error, reload } = useApi(`/markets/live${qs({ window, funnel: team })}`);
  // live: refresh whenever new ads land or the liveness check finishes
  useEvents((e) => { if (["CONNECTOR_SYNCED", "SEARCH_DONE", "LIVENESS_CHECKED", "AD_STOPPED", "AD_REACTIVATED"].includes(e.type)) reload(); });
  if (!data) return <Loading error={error} />;
  return (
    <div>
      <PageHeader title="Market Radar"
                  subtitle={`Tính trực tiếp từ quảng cáo đang chạy · cập nhật ${fmt.dt(data.generated_at)}${team ? ` · chỉ funnel ${team === "mess" ? "MKT Mess" : "MKT Ladi"}` : ""}`}>
        <ToggleGroup type="single" variant="outline" size="sm" value={String(window)} onValueChange={(v) => v && setWindow(Number(v))}>
          {[7, 14, 30].map((w) => <ToggleGroupItem key={w} value={String(w)} className="text-xs data-[state=on]:font-semibold">{w} ngày</ToggleGroupItem>)}
        </ToggleGroup>
        <Button variant="outline" size="sm" className="text-xs" onClick={reload}><RefreshCw />Làm mới</Button>
      </PageHeader>

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        {data.regions.map((r: any) => (
          <Link key={r.region} href={`/product-radar?market=${r.region}`} className="block rounded-xl border bg-card px-4 py-3 text-card-foreground shadow-sm transition-colors hover:bg-muted/50">
            <div className="text-xs text-muted-foreground">{r.label}</div>
            <div className="tnum mt-0.5 text-2xl font-semibold">{fmt.n(r.active_ads)}</div>
            <div className="text-xs text-ink2">ads đang chạy · {r.countries} nước</div>
            <div className="mt-1 text-xs">+{r.new_ads} ads mới {window}d <Trend t={r.trend} g={r.growth} /></div>
          </Link>
        ))}
      </div>

      <Card title={`Theo nước — ads mới ${window} ngày gần nhất so với ${window} ngày trước đó (theo ngày bắt đầu chạy, mọi nền tảng quảng cáo)`} pad={false} className="mb-4">
        <Table rows={data.rows} />
      </Card>

      {data.outside.length > 0 && (
        <Collapsible open={showOutside} onOpenChange={setShowOutside}>
          <Card pad={false} title={
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="group/out -ml-2 h-auto gap-1.5 px-2 py-1 text-[13px] font-semibold whitespace-normal text-left">
                <ChevronDown className="shrink-0 transition-transform group-data-[state=open]/out:rotate-180" />
                Ngoài thị trường mục tiêu ({data.outside.length} nước) — lưu lại nhưng không tính vào tổng hợp
              </Button>
            </CollapsibleTrigger>
          }>
            <CollapsibleContent><Table rows={data.outside} muted /></CollapsibleContent>
          </Card>
        </Collapsible>
      )}
      <p className="mt-3 text-xs text-muted-foreground">
        “Ads mạnh” = điểm độ mạnh ≥ 45 (chạy lâu · nhiều biến thể · nhiều nền tảng). Độ tươi = lần gần nhất app xác nhận lại với nguồn rằng quảng cáo còn chạy.
        Thị trường mục tiêu cấu hình bằng <code>TARGET_MARKETS</code> trong .env.
      </p>
    </div>
  );
}
