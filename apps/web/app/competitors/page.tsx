"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AlertCircle, Star, X } from "lucide-react";
import { TrendChart } from "@/components/charts";
import { SimpleSelect } from "@/components/simple-select";
import { Card, Loading, PageHeader, Pill, Stat } from "@/components/ui";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, fmt, qs, useAction, useApi } from "@/lib/api";

const MARKETS: [string, string][] = [["PH", "Philippines ★"], ["ME", "Trung Đông"], ["US", "Mỹ"], ["EU", "Châu Âu + UK"], ["AU", "Úc / NZ"], ["VN", "Việt Nam"], ["WW", "Toàn cầu"]];

function Radar() {
  const sp = useSearchParams();
  const meta = useApi("/meta");
  const [country, setCountry] = useState("");
  const [watched, setWatched] = useState(false);
  const { data, error, reload } = useApi(`/competitors${qs({ country, watched_only: watched })}`);
  const [focus, setFocus] = useState<number | null>(sp.get("focus") ? Number(sp.get("focus")) : null);
  const detail = useApi(focus ? `/competitors/${focus}` : null);
  useEffect(() => { if (focus) window.scrollTo({ top: 0, behavior: "smooth" }); }, [focus]);
  const act = useAction();
  const toggleWatch = (id: number, w: boolean) => act.run(async () => { await api(`/competitors/${id}/watch?watched=${w}`, { method: "POST" }); reload(); detail.reload(); });
  const extra = (meta.data?.countries || []).filter((c: string) => !MARKETS.some(([k]) => k === c));

  return (
    <div>
      {act.error && <Alert variant="destructive" className="mb-2"><AlertCircle /><AlertTitle>Lỗi</AlertTitle><AlertDescription>{act.error}</AlertDescription></Alert>}
      <PageHeader title="Competitor Radar" subtitle="Theo dõi advertiser / store — ai đang scale nhanh">
        <SimpleSelect value={country} onChange={setCountry} placeholder="Mọi thị trường mục tiêu" className="w-52" options={[...MARKETS, ...extra]} />
        <div className="flex items-center gap-1.5">
          <Checkbox id="cmp-watched" checked={watched} onCheckedChange={(v) => setWatched(v === true)} />
          <Label htmlFor="cmp-watched" className="text-sm font-normal">Chỉ đối thủ đang theo dõi</Label>
        </div>
      </PageHeader>
      {focus && detail.data && (
        <Card title={<span>{detail.data.advertiser.name} <span className="font-normal text-muted-foreground">· {detail.data.advertiser.country} · {detail.data.advertiser.platform}</span></span>}
              action={<span className="flex gap-2">
                <Button variant="outline" size="sm" className="text-xs" disabled={act.busy} onClick={() => toggleWatch(focus, !detail.data.advertiser.watched)}>
                  <Star className={detail.data.advertiser.watched ? "fill-warning text-warning" : ""} />{detail.data.advertiser.watched ? "Bỏ theo dõi" : "Theo dõi"}
                </Button>
                <Button variant="ghost" size="sm" className="text-xs" onClick={() => setFocus(null)}><X />Đóng</Button></span>} className="mb-4">
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <TrendChart data={detail.data.timeseries} series={[{ key: "active_ads", label: "Active ads" }]} height={180} />
            <div className="max-h-56 overflow-y-auto">
              <Table>
                <TableHeader className="sticky top-0 z-10 bg-card"><TableRow><TableHead>Ngày</TableHead><TableHead>Ad</TableHead><TableHead>Country</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                <TableBody>{detail.data.ads.map((a: any) => (
                  <TableRow key={a.id}><TableCell className="text-xs whitespace-nowrap">{fmt.date(a.first_seen)}</TableCell>
                    <TableCell className="text-xs"><Link className="hover:underline" href={`/products/${a.product_id}`}>{a.text}</Link></TableCell><TableCell>{a.country}</TableCell>
                    <TableCell>{a.active ? <Pill tone="good">active</Pill> : <Pill>stopped</Pill>}</TableCell></TableRow>
                ))}</TableBody>
              </Table>
            </div>
          </div>
        </Card>
      )}
      {!data ? <Loading error={error} /> : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3">
            <Stat label="Advertisers tracked" value={fmt.n(data.total)} />
            <Stat label="Đang scale nhanh (7d ≥ 2× tuần trước)" value={data.scaling} />
            <Stat label="Đang theo dõi" value={data.rows.filter((r: any) => r.watched).length} />
          </div>
          <Card title="Advertisers" pad={false}>
            <Table>
              <TableHeader>
                <TableRow><TableHead className="w-8" /><TableHead>Advertiser</TableHead><TableHead>Country</TableHead><TableHead className="text-right">Active ads</TableHead><TableHead className="text-right">New 7d</TableHead><TableHead className="text-right">Prev 7d</TableHead><TableHead>Growth</TableHead><TableHead className="text-right">Products</TableHead><TableHead>Markets</TableHead><TableHead>Top products</TableHead></TableRow>
              </TableHeader>
              <TableBody>{data.rows.map((r: any) => (
                <TableRow key={r.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setFocus(r.id)}>
                  <TableCell>{r.watched && <Star className="size-3.5 fill-warning text-warning" />}</TableCell>
                  <TableCell><div className="font-medium">{r.name}</div><div className="text-[11px] text-muted-foreground">{r.platform}</div></TableCell>
                  <TableCell>{r.country}</TableCell><TableCell className="tnum text-right">{r.active_ads}</TableCell><TableCell className="tnum text-right">{r.new_ads_7d}</TableCell><TableCell className="tnum text-right">{r.prev_7d}</TableCell>
                  <TableCell>{r.scaling ? <Pill tone="warn">scaling {r.growth_7d !== null && r.growth_7d < 9 ? fmt.signedPct(r.growth_7d) : "new"}</Pill> : <span className="tnum text-xs">{r.growth_7d === null ? "—" : r.growth_7d >= 9 ? "new" : fmt.signedPct(r.growth_7d)}</span>}</TableCell>
                  <TableCell className="tnum text-right">{r.products}</TableCell><TableCell className="text-xs">{r.countries.join(", ")}</TableCell>
                  <TableCell className="text-xs">{r.top_products.map((p: any) => p.name).join(", ")}</TableCell>
                </TableRow>
              ))}</TableBody>
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Radar /></Suspense>;
}
