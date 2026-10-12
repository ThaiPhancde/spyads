"use client";
import { useState } from "react";
import { ExternalLink, HeartPulse, RefreshCw } from "lucide-react";
import { Card, PageHeader } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EuLoading, SOURCE_LABEL, StateBadge } from "@/components/eu-market/shared";
import { api, fmt, useAction, useApi } from "@/lib/api";

export default function EuSources() {
  const { data, error, reload } = useApi<any>("/eu-market/sources");
  const runs = useApi<any>("/eu-market/admin/sync-runs?limit=40");
  const act = useAction();
  const [imp, setImp] = useState<any>(null);
  const [health, setHealth] = useState<Record<string, any>>({});
  const check = (s: string) => act.run(async () => { const r = await api(`/eu-market/sources/${s}/healthcheck`, { method: "POST" }); setHealth((h) => ({ ...h, [s]: r })); });
  const importAllegro = () => act.run(async () => { setImp(await api("/eu-market/imports/allegro", { method: "POST" })); reload(); runs.reload(); });
  const fx = () => act.run(async () => { await api("/eu-market/admin/fx", { method: "POST" }); reload(); });
  if (!data) return <EuLoading error={error} rows={4} />;
  const allegro = data.sources.find((s: any) => s.source === "allegro");
  const avail = data.sources.filter((s: any) => s.status === "available");
  const planned = data.sources.filter((s: any) => s.status !== "available");
  return (
    <div className="space-y-4">
      <PageHeader title="Data Sources" subtitle="Public data first: trang sản phẩm công khai (JSON-LD) không cần tài khoản seller. Kaufland / bol / BigBuy là connector seller tuỳ chọn. Credentials đặt trong .env của server (không gửi qua chat, không commit, không xuống trình duyệt). Không vượt CAPTCHA / đăng nhập / anti-bot.">
        <Button size="sm" variant="outline" onClick={fx} disabled={act.busy}><RefreshCw />Tải tỷ giá ECB</Button>
      </PageHeader>
      {act.error && <p className="text-sm text-critical">{act.error}</p>}
      <p className="text-sm text-muted-foreground">Tỷ giá tham khảo: {data.fx_rate_date ? `ECB ngày ${data.fx_rate_date}` : "chưa tải (giá quy đổi EUR sẽ hiện “—”)"}</p>
      {allegro && (
        <Card title="Allegro · import từ file JSON">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <StateBadge state={allegro.import?.state === "file_found" ? "ready" : allegro.import?.state || "no_data"} />
            <span className="text-muted-foreground">
              {allegro.import?.state === "file_found"
                ? `${fmt.n(allegro.import.rows)} dòng trong file · ghi ${fmt.dt(allegro.import.modified_at)} · ${fmt.n(allegro.listings)} listing trong kho`
                : allegro.import?.message || "Chưa có dữ liệu."}
            </span>
            <code className="text-xs">{allegro.import?.path}</code>
            <Button size="sm" onClick={importAllegro} disabled={act.busy || allegro.import?.state !== "file_found"}>Import vào kho</Button>
          </div>
          {imp && <p className="mt-2 text-xs text-muted-foreground">Đọc {imp.rows} dòng · hợp lệ {imp.valid} · bỏ qua {imp.skipped_invalid} (thiếu offer_id/tên) · trùng trong file {imp.duplicates_in_file} · ghi {imp.written}. Import lại không tạo bản sao (khoá: allegro + offer_id).</p>}
        </Card>
      )}
      <Card title="Connector đã triển khai" pad={false}>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader><TableRow><TableHead>Nguồn</TableHead><TableHead>Trạng thái</TableHead><TableHead>Quốc gia</TableHead><TableHead>Khả năng</TableHead>
              <TableHead>Biến môi trường cần có</TableHead><TableHead className="text-right">Listing</TableHead><TableHead>Lần chạy cuối</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {avail.map((s: any) => (
                <TableRow key={s.source} className="align-top">
                  <TableCell><div className="font-medium">{s.label} {s.public_data && <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px] text-good-text">public data</Badge>}{s.optional_seller && <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px]">seller · tuỳ chọn</Badge>}</div><div className="max-w-64 text-xs whitespace-normal text-muted-foreground">{s.notes}</div>
                    <a href={s.docs_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-series-1">docs<ExternalLink className="size-3" /></a></TableCell>
                  <TableCell><StateBadge state={s.state} />
                    {health[s.source] && <div className="mt-1 text-xs">{health[s.source].ok ? "✓ kết nối được" : `✗ ${health[s.source].error}: ${health[s.source].detail || ""}`}</div>}</TableCell>
                  <TableCell className="text-xs">{s.countries.join(" ")}</TableCell>
                  <TableCell><div className="flex flex-wrap gap-1">{s.capabilities.map((c: string) => <Badge key={c} variant="outline">{c}</Badge>)}</div></TableCell>
                  <TableCell className="text-xs">{s.env_vars.map((v: string) => <div key={v}><code>{v}</code></div>)}
                    <span className={s.configured ? "text-good-text" : "text-muted-foreground"}>{s.configured ? "đã đặt" : "chưa đặt"}</span></TableCell>
                  <TableCell className="tnum text-right">{fmt.n(s.listings)}</TableCell>
                  <TableCell className="text-xs">{s.last_run ? <>{fmt.dt(s.last_run.started_at)} · {s.last_run.status}<br />{s.last_run.items_written} ghi{s.last_run.error_detail && <div className="max-w-56 truncate text-critical" title={s.last_run.error_detail}>{s.last_run.error_detail}</div>}</> : "—"}
                    {s.last_ok_at && <div className="text-muted-foreground">OK gần nhất {fmt.dt(s.last_ok_at)}</div>}</TableCell>
                  <TableCell><Button size="xs" variant="outline" disabled={act.busy || !s.configured} onClick={() => check(s.source)}><HeartPulse />Kiểm tra</Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>
      <Card title="Nguồn chưa gọi được — access_restricted (chỉ có Seller / Partner API) hoặc unsupported_capability (không có API / feed hợp lệ)" pad={false}>
        <Table>
          <TableHeader><TableRow><TableHead>Nguồn</TableHead><TableHead>Trạng thái</TableHead><TableHead>Loại</TableHead><TableHead>Quốc gia</TableHead><TableHead>Ghi chú</TableHead><TableHead>Biến môi trường dự kiến</TableHead></TableRow></TableHeader>
          <TableBody>
            {planned.map((s: any) => (
              <TableRow key={s.source} className="align-top">
                <TableCell><a href={s.docs_url} target="_blank" rel="noopener noreferrer" className="font-medium hover:underline">{s.label}</a></TableCell>
                <TableCell><StateBadge state={s.state} /></TableCell>
                <TableCell>{s.kind === "supplier" ? "B2B" : "Marketplace"}</TableCell><TableCell className="text-xs">{s.countries.join(" ")}</TableCell>
                <TableCell className="max-w-md text-xs whitespace-normal text-muted-foreground">{s.notes}</TableCell>
                <TableCell className="text-xs">{s.env_vars.length ? s.env_vars.map((v: string) => <div key={v}><code>{v}</code></div>) : "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      <Card title="Lịch sử đồng bộ" pad={false}>
        <Table>
          <TableHeader><TableRow><TableHead>Bắt đầu</TableHead><TableHead>Nguồn</TableHead><TableHead>Từ khoá</TableHead><TableHead>QG</TableHead><TableHead>Trạng thái</TableHead>
            <TableHead className="text-right">Đọc / ghi</TableHead><TableHead>Cursor</TableHead></TableRow></TableHeader>
          <TableBody>
            {(runs.data?.items || []).map((r: any) => (
              <TableRow key={r.id}>
                <TableCell>{fmt.dt(r.started_at)}</TableCell><TableCell>{SOURCE_LABEL[r.connector] || r.connector}</TableCell><TableCell>{r.query || "—"}</TableCell>
                <TableCell>{r.country || "—"}</TableCell><TableCell><StateBadge state={r.status} /></TableCell>
                <TableCell className="tnum text-right">{r.items_read} / {r.items_written}</TableCell><TableCell className="text-xs">{r.next_cursor || "—"}</TableCell>
              </TableRow>
            ))}
            {!runs.data?.items?.length && <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground">Chưa có lần đồng bộ nào.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
