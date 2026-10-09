"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { MarketPicker } from "@/components/MarketPicker";
import { LoadMore } from "@/components/paging";
import { Card, Empty, Loading, PageHeader, RecBadge } from "@/components/ui";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { fmt, qs, usePaged } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";
import { cn } from "@/lib/utils";

const TIER: Record<string, [string, string]> = {
  legendary: ["bg-good", "Huyền thoại"], strong: ["bg-series-1", "Mạnh"], regular: ["bg-muted-foreground", "Thường"], testing: ["bg-border", "Đang test"],
};
const SORTS: [string, string][] = [["score", "Điểm"], ["trend", "Xu hướng"], ["new", "Ads mới"], ["ads", "Số ads"], ["sellers", "Sellers"], ["force", "Ads mạnh nhất"], ["age", "Mới xuất hiện"]];

function Radar() {
  const sp = useSearchParams();
  const [market, setMarket] = useState(sp.get("market") || "");
  const [window, setWindow] = useState(7);
  const [sort, setSort] = useState("score");
  const team = useTeam();
  const path = `/radar/products${qs({ scope: market, window, funnel: team, sort })}`;
  const { rows, total, loading, error, hasMore, loadMore, reload } = usePaged(path, 50);
  useEvents((e) => { if (["CONNECTOR_SYNCED", "LIVENESS_CHECKED", "SEARCH_DONE"].includes(e.type)) reload(); });

  return (
    <div className="space-y-4">
      <PageHeader title="Product Radar" subtitle="Sản phẩm đang chạy theo thị trường + khung thời gian — xu hướng tính trực tiếp từ ngày bắt đầu chạy của quảng cáo trên mọi nền tảng (Meta, TikTok, Snapchat)">
        <ToggleGroup type="single" variant="outline" size="sm" value={String(window)} onValueChange={(v) => v && setWindow(Number(v))}>
          {[7, 14, 30].map((w) => <ToggleGroupItem key={w} value={String(w)} className="text-xs">{w} ngày</ToggleGroupItem>)}
        </ToggleGroup>
        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger size="sm" className="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>{SORTS.map(([k, l]) => <SelectItem key={k} value={k}>Sắp xếp: {l}</SelectItem>)}</SelectContent>
        </Select>
      </PageHeader>
      <MarketPicker value={market} onChange={setMarket} />
      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : rows.length === 0 ? <Card><Empty>Chưa có sản phẩm đang chạy ở thị trường này.</Empty></Card> : (
        <Card pad={false} title={`${total} sản phẩm đang chạy${team ? ` · funnel ${team}` : ""}`}>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-card">
                <TableRow>
                  <TableHead />
                  <TableHead>Sản phẩm</TableHead><TableHead>Xu hướng ({window}d)</TableHead><TableHead className="text-right">Ads</TableHead>
                  <TableHead className="text-right">Sellers</TableHead><TableHead className="text-right">Tuổi</TableHead><TableHead className="text-right">Điểm</TableHead>
                  <TableHead>Ad mạnh nhất</TableHead><TableHead>Biến thể scale</TableHead><TableHead>Mess / Ladi</TableHead><TableHead>Thị trường</TableHead><TableHead>Hành động</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r: any) => {
                  const [tc, tl] = TIER[r.top_tier] || TIER.testing;
                  const up = r.trend.startsWith("↑") || r.trend === "NEW";
                  return (
                    <TableRow key={r.id}>
                      <TableCell className="w-14">
                        {r.thumb ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={r.thumb} alt="" className="size-12 rounded object-cover" loading="lazy" />
                          : <div className="size-12 rounded bg-muted" />}
                      </TableCell>
                      <TableCell className="min-w-[220px] whitespace-normal">
                        <Link href={`/products/${r.id}`} className="leading-snug font-medium hover:underline">{r.name}</Link>
                        <div className="text-[11px] text-muted-foreground">{r.code} · {r.category || "—"}</div>
                      </TableCell>
                      <TableCell className={cn("tnum whitespace-nowrap", up && "text-good-text", r.trend.startsWith("↓") && "text-critical")}>
                        {r.trend} <span className="text-xs">{r.growth !== null ? fmt.signedPct(r.growth) : ""}</span>
                        <div className="text-[11px] text-muted-foreground">+{r.new_ads} mới / {r.prev_new_ads} trước</div>
                      </TableCell>
                      <TableCell className="tnum text-right">{r.ads}</TableCell>
                      <TableCell className="tnum text-right">{r.sellers}</TableCell>
                      <TableCell className="tnum text-right whitespace-nowrap">{r.age_days} ngày</TableCell>
                      <TableCell className="tnum text-right font-semibold">{fmt.n(r.score)}</TableCell>
                      <TableCell className="text-xs whitespace-nowrap">
                        <span className={cn("mr-1 inline-block size-2 rounded-full", tc)} />{tl} <span className="tnum text-muted-foreground">{fmt.n(r.force_max)}</span>
                        {r.strong_ads > 1 && <div className="text-[11px] text-muted-foreground">{r.strong_ads} ads mạnh</div>}
                      </TableCell>
                      <TableCell className="tnum">{r.max_variation > 1 ? <span title="Cùng page + cùng nội dung chạy song song = đang scale">×{r.max_variation}</span> : "—"}</TableCell>
                      <TableCell className="tnum text-xs">{r.mess} / {r.ladi}</TableCell>
                      <TableCell className="text-xs">{r.markets.map((m: string) => (m === "ALL" ? "🌍" : m)).join(", ")}</TableCell>
                      <TableCell><RecBadge rec={r.decision} /></TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </Card>
      )}
      <p className="text-xs text-muted-foreground">
        Độ mạnh của quảng cáo = ngày chạy (tối đa 180, 60đ) + số phiên bản (tối đa 10, 25đ) + số nền tảng (tối đa 4, 15đ), ×0,7 nếu đã dừng — theo sonda-imperial.
        Huyền thoại ≥70 · Mạnh ≥45 · Thường ≥20. “Biến thể scale” = số quảng cáo cùng page, cùng nội dung đang chạy song song.
      </p>
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Radar /></Suspense>;
}
