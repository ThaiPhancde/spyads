"use client";
import Link from "next/link";
import { BarList } from "@/components/charts";
import { Card, PageHeader, Stat } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EuLoading, NoData, ProductGrid, SOURCE_LABEL, StateBadge } from "@/components/eu-market/shared";
import { fmt, useApi } from "@/lib/api";

export default function EuOverview() {
  const { data, error } = useApi<any>("/eu-market/overview");
  if (!data) return <EuLoading error={error} rows={4} />;
  const src = data.sources;
  return (
    <div className="space-y-4">
      <PageHeader title="EU Market — Overview"
                  subtitle="Sản phẩm rao bán, nguồn nhập B2B, giá và xu hướng tại châu Âu — chỉ từ API được cấp quyền. Listing không phải quảng cáo; không có số bán/doanh thu giả định.">
        <Button asChild size="sm"><Link href="/eu-market/products">Tìm sản phẩm</Link></Button>
        <Button asChild size="sm" variant="outline"><Link href="/eu-market/sources">Data Sources</Link></Button>
      </PageHeader>
      {src.configured === 0 && (
        <Card title="Chưa có nguồn EU nào được kết nối">
          <p className="text-sm text-muted-foreground">
            {src.available} connector đã sẵn sàng (Kaufland, bol, BigBuy) nhưng chưa có credentials. Thêm biến môi trường vào <code>.env</code> của
            server (không gửi key qua chat, không commit) rồi khởi động lại API — chi tiết ở <Link href="/eu-market/sources" className="underline">Data Sources</Link>.
          </p>
        </Card>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 2xl:grid-cols-6">
        <Stat label="Sản phẩm (đã gom nhóm)" value={fmt.n(data.products)} sub={`${fmt.n(data.new_products_7d)} mới trong 7 ngày`} />
        <Stat label="Listing bán lẻ" value={fmt.n(data.by_kind?.marketplace_listing || 0)} />
        <Stat label="Offer nguồn nhập (B2B)" value={fmt.n(data.by_kind?.supplier_offer || 0)} sub={`${data.suppliers} nhà cung cấp`} />
        <Stat label="Snapshot giá" value={fmt.n(data.snapshots)} sub={data.fx_rate_date ? `Tỷ giá ECB ${data.fx_rate_date}` : "chưa tải tỷ giá ECB"} />
        <Stat label="Ad match" value={fmt.n(data.ad_matches?.confirmed || 0)} sub={`${data.ad_matches?.pending || 0} chờ review`} />
        <Stat label="Nguồn đã kết nối" value={`${src.configured}/${src.available}`} sub={`${src.planned} nguồn planned (P4)`} />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Card title="Listing theo nguồn">
          {Object.keys(data.by_source).length
            ? <BarList rows={Object.entries(data.by_source).map(([k, v]) => ({ label: SOURCE_LABEL[k] || k, value: v as number }))} />
            : <p className="text-sm text-muted-foreground">—</p>}
        </Card>
        <Card title="Listing theo quốc gia">
          {Object.keys(data.by_country).length
            ? <BarList rows={Object.entries(data.by_country).map(([k, v]) => ({ label: k, value: v as number }))} />
            : <p className="text-sm text-muted-foreground">—</p>}
        </Card>
      </div>
      <Card title="Sản phẩm mới quan sát (7 ngày)">
        {data.recent_products.length ? <ProductGrid rows={data.recent_products} /> : <NoData>Chưa có listing nào — kết nối một nguồn ở</NoData>}
      </Card>
      <Card title="Lần đồng bộ gần nhất" pad={false}>
        <Table>
          <TableHeader><TableRow><TableHead>Nguồn</TableHead><TableHead>Từ khoá</TableHead><TableHead>QG</TableHead><TableHead>Trạng thái</TableHead>
            <TableHead className="text-right">Đọc / ghi</TableHead><TableHead>Bắt đầu</TableHead></TableRow></TableHeader>
          <TableBody>
            {data.last_runs.map((r: any) => (
              <TableRow key={r.id}>
                <TableCell>{SOURCE_LABEL[r.connector] || r.connector}</TableCell><TableCell>{r.query || "—"}</TableCell><TableCell>{r.country || "—"}</TableCell>
                <TableCell><StateBadge state={r.status} />{r.error_detail && <span className="ml-1 text-xs text-muted-foreground">{r.error_detail.slice(0, 80)}</span>}</TableCell>
                <TableCell className="tnum text-right">{r.items_read} / {r.items_written}</TableCell><TableCell>{fmt.dt(r.started_at)}</TableCell>
              </TableRow>
            ))}
            {!data.last_runs.length && <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground">Chưa chạy lần nào.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
