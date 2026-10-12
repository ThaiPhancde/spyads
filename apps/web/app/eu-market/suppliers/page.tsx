"use client";
import Link from "next/link";
import { useState } from "react";
import { Card, PageHeader, Pill } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EuLoading, NoData, Price, SOURCE_LABEL, Thumb, productHref } from "@/components/eu-market/shared";
import { api, fmt, qs, useAction, useApi } from "@/lib/api";

const TYPE: Record<string, string> = { manufacturer: "Nhà sản xuất", distributor: "Nhà phân phối", wholesale_marketplace: "Sàn sỉ", dropshipper: "Dropshipper", liquidation: "Hàng thanh lý" };

export default function EuSuppliers() {
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const { data, error } = useApi<any>(`/eu-market/suppliers${qs({ q: applied, limit: 60 })}`);
  const pending = useApi<any>("/eu-market/supplier-matches?status=pending");
  const act = useAction();
  const review = (id: number, status: string) => act.run(async () => {
    await api(`/eu-market/supplier-matches/${id}/review`, { method: "POST", body: JSON.stringify({ status }) });
    pending.reload();
  });
  return (
    <div className="space-y-4">
      <PageHeader title="Wholesale & Suppliers" subtitle="Nguồn nhập B2B từ API được cấp quyền. Giá sỉ thường CHƯA gồm VAT — không so trực tiếp với giá lẻ gồm VAT. Ghép sản phẩm: EAN = tự xác nhận, tiêu đề = cần review.">
        <form onSubmit={(e) => { e.preventDefault(); setApplied(q); }}><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm offer B2B (Enter)" className="h-8 w-64" /></form>
      </PageHeader>
      {!data ? <EuLoading error={error} /> : (
        <>
          <Card title="Nhà cung cấp" pad={false}>
            <Table>
              <TableHeader><TableRow><TableHead>Tên</TableHead><TableHead>Loại</TableHead><TableHead>Kho</TableHead><TableHead>MOQ</TableHead><TableHead>Lead time</TableHead>
                <TableHead>VAT</TableHead><TableHead>Điều kiện truy cập</TableHead><TableHead className="text-right">Offer</TableHead></TableRow></TableHeader>
              <TableBody>
                {data.suppliers.map((s: any) => (
                  <TableRow key={s.id}>
                    <TableCell>{s.website ? <a href={s.website} target="_blank" rel="noopener noreferrer" className="hover:underline">{s.name}</a> : s.name}</TableCell>
                    <TableCell>{TYPE[s.supplier_type] || s.supplier_type}</TableCell><TableCell>{s.country || "unknown"}</TableCell>
                    <TableCell>{s.moq ?? "unknown"}</TableCell><TableCell>{s.lead_time_days ?? "unknown"}</TableCell>
                    <TableCell className="text-xs">{s.vat_note || "unknown"}</TableCell><TableCell className="text-xs">{s.access_terms || "—"}</TableCell>
                    <TableCell className="tnum text-right">{fmt.n(s.offers)}</TableCell>
                  </TableRow>
                ))}
                {!data.suppliers.length && <TableRow><TableCell colSpan={8}><NoData>Chưa có nguồn nhập nào — BigBuy cần BIGBUY_API_KEY, xem</NoData></TableCell></TableRow>}
              </TableBody>
            </Table>
          </Card>
          {pending.data?.items?.length > 0 && (
            <Card title={`Ghép sản phẩm cần review (${pending.data.items.length})`}>
              {act.error && <p className="text-sm text-critical">{act.error}</p>}
              {pending.data.items.map((m: any) => (
                <div key={m.id} className="flex flex-wrap items-center gap-2 border-b py-2 text-sm last:border-0">
                  <Link href={productHref(m.product_id)} className="text-series-1 hover:underline">Sản phẩm #{m.product_id}</Link>
                  <Badge variant="outline">{m.match_method} · {m.score}</Badge>
                  {m.offer && <><span>{m.offer.title}</span><Price l={m.offer} /></>}
                  <span className="text-xs text-muted-foreground">{JSON.stringify(m.evidence)}</span>
                  <span className="ml-auto flex gap-1">
                    <Button size="xs" variant="outline" disabled={act.busy} onClick={() => review(m.id, "confirmed")}>Đúng</Button>
                    <Button size="xs" variant="outline" disabled={act.busy} onClick={() => review(m.id, "rejected")}>Sai</Button>
                  </span>
                </div>
              ))}
            </Card>
          )}
          <Card title={`Offer B2B (${data.total})`} pad={false}>
            <Table>
              <TableHeader><TableRow><TableHead>Sản phẩm</TableHead><TableHead>Nguồn</TableHead><TableHead>Giá sỉ</TableHead><TableHead>EAN</TableHead><TableHead>Tồn</TableHead><TableHead>Cập nhật</TableHead></TableRow></TableHeader>
              <TableBody>
                {data.offers.map((o: any) => (
                  <TableRow key={o.id}>
                    <TableCell><Link href={productHref(o.product_id)} className="flex items-center gap-2 hover:underline"><Thumb src={o.thumbnail_url} alt={o.title} size={36} /><span className="line-clamp-2 max-w-md">{o.title}</span></Link></TableCell>
                    <TableCell>{SOURCE_LABEL[o.source] || o.source}</TableCell><TableCell><Price l={o} /></TableCell><TableCell className="tnum">{o.ean || "unknown"}</TableCell>
                    <TableCell>{o.availability === "in_stock" ? <Pill tone="good">còn hàng</Pill> : o.availability}</TableCell><TableCell>{fmt.dt(o.last_seen_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}
