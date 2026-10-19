"use client";
import Link from "next/link";
import { Card, Loading, PageHeader, Pill } from "@/components/ui";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmt, useApi } from "@/lib/api";

const STATUS: [string, string][] = [["verified", "MKT xác nhận"], ["auto_confirmed", "Tự xác nhận"], ["suggested", "Gợi ý (chờ MKT)"],
  ["unknown", "Chưa có tên"], ["needs_review", "Cần kiểm duyệt"], ["no_product", "Không phải sản phẩm / không đủ dữ liệu"], ["errors", "Lỗi (≥ 3 lần)"]];
const SOURCE: Record<string, string> = { "—": "Chưa đặt tên", none: "Chưa đặt tên", manual:"MKT đặt", listing: "Listing / schema.org", slug: "Trang sản phẩm", adapter: "Tiêu đề link",
  ad_title: "Nội dung ad", llm: "AI", legacy: "Tên cũ / gần giống", llm_none: "AI: không có sản phẩm", needs_review: "Cần kiểm duyệt", restored: "Khôi phục" };

function Limits({ l }: { l: Record<string, any> }) {
  const rows = Object.entries(l || {});
  if (!rows.length) return <span className="text-muted-foreground">chưa có header</span>;
  return <span className="tnum">{rows.map(([w, v]) => `${w.replace("_", "/")}: ${fmt.n(v.remaining)}${v.limit ? ` / ${fmt.n(v.limit)}` : ""}`).join(" · ")}</span>;
}

/** Product naming dashboard: how many products are named and how, what is waiting, and how much each free AI provider has left today. */
export default function Naming() {
  const { data: s, error } = useApi("/naming/stats");
  if (!s) return <Loading error={error} />;
  const pct = (n: number) => `${Math.round((100 * n) / Math.max(1, s.products))}%`;
  return (
    <div className="space-y-4">
      <PageHeader title="Đặt tên sản phẩm" subtitle="Tên lấy từ bằng chứng trước (title, caption, trang sản phẩm, schema.org, metadata, cụm sản phẩm); AI chỉ đặt tên phần còn lại và không bao giờ sửa tên MKT đã xác nhận." />
      <Card title={`${fmt.n(s.products)} sản phẩm · ${fmt.n(s.named)} có tên (${pct(s.named)})`}>
        <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4 xl:grid-cols-7">
          {STATUS.map(([k, l]) => (
            <div key={k}><div className="tnum text-xl font-semibold">{fmt.n(s[k] ?? 0)}</div><div className="text-xs text-muted-foreground">{l}</div>
              {s.avg_confidence?.[k.toUpperCase()] != null && <div className="text-[11px] text-muted-foreground">confidence TB {s.avg_confidence[k.toUpperCase()]}</div>}</div>
          ))}
        </div>
        <div className="mt-3 text-xs text-muted-foreground">Hàng đợi: {fmt.n(s.queue_rules)} chờ đọc bằng chứng (không AI) · {fmt.n(s.queue_ai)} chờ AI · đặt tên bằng AI: {fmt.n(s.named_by_ai)} · engine hiện tại: <b>{s.engine}</b>
          {" · "}<Link href="/search?name_status=UNKNOWN" className="text-link underline">mở hàng chờ MKT</Link></div>
      </Card>
      <Card title="AI provider — quota thật (đọc từ header) và mức đã dùng hôm nay" pad={false}>
        <Table>
          <TableHeader><TableRow><TableHead>Provider</TableHead><TableHead>Model</TableHead><TableHead>Trạng thái</TableHead><TableHead className="text-right">Request hôm nay</TableHead>
            <TableHead className="text-right">Token hôm nay</TableHead><TableHead>Quota còn lại</TableHead><TableHead>Lỗi gần nhất</TableHead></TableRow></TableHeader>
          <TableBody>{Object.entries(s.providers).map(([n, p]: any) => (
            <TableRow key={n}>
              <TableCell className="font-medium">{n}</TableCell>
              <TableCell className="text-xs">{p.model || "—"}</TableCell>
              <TableCell>{!p.configured ? <Pill>chưa cấu hình</Pill> : p.ready ? <Pill tone="good">sẵn sàng</Pill> : <Pill tone="warn">{p.why || "tạm nghỉ"}</Pill>}
                {p.cooldown_until && <div className="text-[11px] text-muted-foreground">đến {fmt.dt(p.cooldown_until)}</div>}</TableCell>
              <TableCell className="tnum text-right">{fmt.n(p.today?.requests ?? 0)}{p.today?.errors ? <span className="text-critical"> ({p.today.errors} lỗi)</span> : null}</TableCell>
              <TableCell className="tnum text-right">{fmt.n(p.today?.tokens ?? 0)}{p.today?.neurons ? <div className="text-[11px] text-muted-foreground">{fmt.n(p.today.neurons)} neurons</div> : null}</TableCell>
              <TableCell className="text-xs"><Limits l={p.limits} /></TableCell>
              <TableCell className="max-w-[260px] truncate text-xs text-muted-foreground" title={p.last_error || ""}>{p.last_error || "—"}</TableCell>
            </TableRow>))}</TableBody>
        </Table>
      </Card>
      <Card title="Nguồn của tên">
        <div className="flex flex-wrap gap-2 text-xs">{Object.entries(s.by_source).sort((a: any, b: any) => b[1] - a[1]).map(([k, n]: any) => (
          <Pill key={k}>{SOURCE[k] || k} · {fmt.n(n)}</Pill>))}</div>
      </Card>
    </div>
  );
}
