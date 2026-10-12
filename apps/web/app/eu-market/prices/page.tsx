"use client";
import Link from "next/link";
import { Fragment, useState } from "react";
import { SimpleSelect } from "@/components/simple-select";
import { GlobalImageSearch } from "@/components/visual/global-image-search";
import { Card, PageHeader, Stat } from "@/components/ui";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EU_COUNTRIES, EuLoading, NoData, PriceChart, SOURCE_LABEL, Thumb, productHref } from "@/components/eu-market/shared";
import { fmt, qs, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function EuPrices() {
  const [country, setCountry] = useState("");
  const [window, setWindow] = useState("90d");
  const [open, setOpen] = useState<number | null>(null);
  const { data, error } = useApi<any>(`/eu-market/prices${qs({ country, window })}`);
  return (
    <div className="space-y-4">
      <PageHeader title="Price Intelligence" subtitle="Biến động giá theo listing, cùng currency, chỉ từ snapshot thật (ghi khi giá đổi hoặc tối đa 1 lần/ngày). Giá quy đổi EUR dùng tỷ giá ECB có ngày.">
        <SimpleSelect value={country} onChange={setCountry} placeholder="Mọi quốc gia" options={EU_COUNTRIES.map(([c, l]) => [c, `${c} · ${l}`])} />
        <SimpleSelect value={window} onChange={setWindow} options={[["30d", "30 ngày"], ["90d", "90 ngày"], ["180d", "180 ngày"]]} />
        <GlobalImageSearch preset={{ mode: "compare_prices" }} label="So sánh giá bằng ảnh" />
      </PageHeader>
      {!data ? <EuLoading error={error} /> : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            <Stat label="Listing có giá" value={fmt.n(data.listings_with_any_price)} />
            <Stat label="Listing có ≥2 snapshot" value={fmt.n(data.listings_with_history)} sub={data.note} />
            <Stat label="Listing đổi giá" value={fmt.n(data.movers.length)} />
          </div>
          {data.movers.length ? (
            <Card title="Biến động lớn nhất" pad={false}>
              <Table>
                <TableHeader><TableRow><TableHead>Sản phẩm</TableHead><TableHead>Nguồn</TableHead><TableHead className="text-right">Đầu → cuối</TableHead>
                  <TableHead className="text-right">Δ%</TableHead><TableHead className="text-right">Min / max</TableHead><TableHead className="text-right">Snapshot</TableHead></TableRow></TableHeader>
                <TableBody>
                  {data.movers.map((m: any) => (
                    <Fragment key={m.id}>
                      <TableRow className="cursor-pointer" onClick={() => setOpen(open === m.id ? null : m.id)}>
                        <TableCell><Link href={productHref(m.product_id)} className="flex items-center gap-2 hover:underline" onClick={(e) => e.stopPropagation()}>
                          <Thumb src={m.thumbnail_url} alt={m.title} size={36} /><span className="line-clamp-2 max-w-md">{m.title}</span></Link></TableCell>
                        <TableCell>{SOURCE_LABEL[m.source] || m.source} {m.market_country}</TableCell>
                        <TableCell className="tnum text-right">{fmt.n(m.stats.first, 2)} → {fmt.n(m.stats.last, 2)} {m.stats.currency}</TableCell>
                        <TableCell className={cn("tnum text-right font-medium", m.stats.change_pct < 0 ? "text-good-text" : "text-critical")}>{m.stats.change_pct > 0 ? "+" : ""}{m.stats.change_pct}%</TableCell>
                        <TableCell className="tnum text-right">{fmt.n(m.stats.min, 2)} / {fmt.n(m.stats.max, 2)}</TableCell>
                        <TableCell className="tnum text-right">{m.stats.snapshots}</TableCell>
                      </TableRow>
                      {open === m.id && <TableRow><TableCell colSpan={6}><PriceChart points={m.points} /></TableCell></TableRow>}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>
            </Card>
          ) : <NoData>Chưa có listing nào có ≥2 snapshot giá trong cửa sổ này. Snapshot được ghi khi đồng bộ watchlist —</NoData>}
        </>
      )}
    </div>
  );
}
