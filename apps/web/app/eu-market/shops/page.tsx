"use client";
import { Fragment, useState } from "react";
import { SimpleSelect } from "@/components/simple-select";
import { Card, PageHeader } from "@/components/ui";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EU_COUNTRIES, EuLoading, NoData, OfferRow, SOURCE_LABEL, eur } from "@/components/eu-market/shared";
import { fmt, qs, useApi } from "@/lib/api";

function ShopListings({ source, seller }: { source: string; seller: string }) {
  const { data, error } = useApi<any>(`/eu-market/shops/listings${qs({ source, seller })}`);
  if (!data) return <EuLoading error={error} rows={1} />;
  return <div className="space-y-1.5 py-1">{data.items.map((o: any) => (
    <div key={o.id}><div className="text-sm">{o.title}</div><OfferRow o={o} /></div>
  ))}</div>;
}

export default function EuShops() {
  const [source, setSource] = useState("");
  const [country, setCountry] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const { data, error } = useApi<any>(`/eu-market/shops${qs({ source, country, q })}`);
  return (
    <div className="space-y-4">
      <PageHeader title="Competitor Shops" subtitle="Seller xuất hiện trên các listing đã quan sát qua API hợp lệ (buybox Kaufland / best offer bol): số listing, quốc gia, danh mục, khoảng giá, listing mới.">
        <SimpleSelect value={source} onChange={setSource} placeholder="Mọi nguồn" options={[["kaufland", "Kaufland"], ["bol", "bol"]]} />
        <SimpleSelect value={country} onChange={setCountry} placeholder="Mọi quốc gia" options={EU_COUNTRIES.map(([c, l]) => [c, `${c} · ${l}`])} />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tên seller" className="h-8 w-48" />
      </PageHeader>
      {!data ? <EuLoading error={error} /> : data.shops.length ? (
        <Card title={`${data.total} seller`} description={data.note} pad={false}>
          <Table>
            <TableHeader><TableRow><TableHead>Seller</TableHead><TableHead>Nguồn</TableHead><TableHead className="text-right">Listing</TableHead><TableHead className="text-right">Mới 7 ngày</TableHead>
              <TableHead>Quốc gia</TableHead><TableHead>Danh mục chính</TableHead><TableHead className="text-right">Khoảng giá</TableHead><TableHead>Thấy lần cuối</TableHead></TableRow></TableHeader>
            <TableBody>
              {data.shops.map((s: any) => {
                const key = `${s.source}|${s.seller}`;
                return (
                  <Fragment key={key}>
                    <TableRow className="cursor-pointer" onClick={() => setOpen(open === key ? null : key)}>
                      <TableCell className="font-medium">{s.seller}</TableCell><TableCell>{SOURCE_LABEL[s.source] || s.source}</TableCell>
                      <TableCell className="tnum text-right">{s.listings}</TableCell><TableCell className="tnum text-right">{s.new_7d}</TableCell>
                      <TableCell>{s.countries.join(" ")}</TableCell><TableCell className="text-xs">{s.categories.join(", ") || "—"}</TableCell>
                      <TableCell className="tnum text-right">{s.price_min_eur != null ? `${eur(s.price_min_eur)} – ${eur(s.price_max_eur)}` : "—"}</TableCell>
                      <TableCell>{fmt.dt(s.last_seen_at)}</TableCell>
                    </TableRow>
                    {open === key && <TableRow><TableCell colSpan={8}><ShopListings source={s.source} seller={s.seller} /></TableCell></TableRow>}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </Card>
      ) : <NoData>Chưa có seller nào — cần listing có giá từ Kaufland / bol.</NoData>}
    </div>
  );
}
