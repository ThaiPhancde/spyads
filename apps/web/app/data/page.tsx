"use client";
import { useState } from "react";
import { AlertCircle, ChevronDown, Play, Plus, RefreshCw, Settings2, Trash2, Zap } from "lucide-react";
import { toast } from "sonner";
import { Card, Empty, Loading, PageHeader, Pill, Tone } from "@/components/ui";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, fmt, useAction, useApi } from "@/lib/api";
import { getUser, useEvents } from "@/lib/realtime";
import { NetworkBadge, usePlatforms } from "@/components/platforms";
import { cn } from "@/lib/utils";

const CHANNEL_ORDER: [string, string][] = [["ads", "Thư viện quảng cáo"], ["commerce", "Nguồn hàng & store"]];
const HEALTH: Record<string, [Tone, string]> = {
  healthy: ["good", "Healthy"], slow: ["warn", "Slow"], auth_expired: ["bad", "Auth expired"], error: ["bad", "Error"],
  degraded: ["warn", "Degraded"], unknown: ["neutral", "Chưa chạy"],
};

function ConnectorRow({ c, onChange, byKey }: { c: any; onChange: () => void; byKey: any }) {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState<Record<string, any>>(c.config || {});
  const [every, setEvery] = useState(c.every_minutes ?? "");
  const [msg, setMsg] = useState("");
  const act = useAction();
  const save = () => act.run(async () => {
    await api(`/collector/connectors/${c.id}`, { method: "PATCH", body: JSON.stringify({ config: cfg, every_minutes: every === "" ? 0 : Number(every) }) });
    setMsg("Đã lưu"); toast.success(`Đã lưu cấu hình ${c.name}`); onChange();
  });
  const toggle = () => act.run(async () => { await api(`/collector/connectors/${c.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !c.enabled }) }); onChange(); });
  const test = () => act.run(async () => { setMsg("Đang kiểm tra…"); const r = await api(`/collector/connectors/${c.id}/test`, { method: "POST" }); setMsg(JSON.stringify(r)); onChange(); });
  const sync = () => act.run(async () => { await api(`/collector/connectors/${c.id}/sync`, { method: "POST" }); setMsg("Đã đưa vào hàng đợi — kết quả hiện ở feed realtime"); toast(`Đã xếp hàng sync ${c.name}`); });
  const [tone, label] = HEALTH[c.health] || HEALTH.unknown;
  const id = `conn-${c.id}`;
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="border-b px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-[240px] flex-1">
          <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
            <NetworkBadge network={c.network} byKey={byKey} small />{c.name}
            <span className="text-[11px] text-muted-foreground">· {c.adapter} · {c.kind}</span>
          </div>
          <div className="text-[11px] text-muted-foreground">
            Lần chạy: {fmt.dt(c.last_sync_at)} · {c.last_sync_count} bản ghi{c.last_duration_ms ? ` · ${(c.last_duration_ms / 1000).toFixed(1)}s` : ""}
            {c.every_minutes ? ` · tự chạy mỗi ${c.every_minutes} phút` : " · chạy khi tìm kiếm / theo dõi từ khoá"}
          </div>
          {c.last_error && <div className="text-[11px] text-critical">{c.last_error}</div>}
        </div>
        <Pill tone={tone}>{label}</Pill>
        <div className="flex items-center gap-1.5">
          <Switch id={`${id}-on`} size="sm" checked={!!c.enabled} onCheckedChange={toggle} />
          <Label htmlFor={`${id}-on`} className="text-xs">Bật</Label>
        </div>
        <CollapsibleTrigger asChild>
          <Button variant="outline" size="xs"><Settings2 />Cấu hình<ChevronDown className={cn("transition-transform", open && "rotate-180")} /></Button>
        </CollapsibleTrigger>
        <Button variant="outline" size="xs" onClick={test}><Zap />Test</Button>
        <Button variant="outline" size="xs" onClick={sync}><RefreshCw />Sync ngay</Button>
      </div>
      {act.error ? (
        <Alert variant="destructive" className="mt-2"><AlertCircle /><AlertTitle>Lỗi</AlertTitle><AlertDescription className="break-all">{act.error}</AlertDescription></Alert>
      ) : msg && <div className="mt-1 break-all text-[11px] text-ink2">{msg}</div>}
      <CollapsibleContent>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          {(c.config_fields || []).map((f: any) => (
            <div key={f.key} className="grid gap-1">
              <Label htmlFor={`${id}-${f.key}`} className="text-xs text-muted-foreground">{f.label}{f.required ? " *" : ""}</Label>
              {/keywords|mapping|input|body|headers|product_map/.test(f.key) ? (
                <Textarea id={`${id}-${f.key}`} className="h-20 font-mono text-[11px]" value={cfg[f.key] ?? ""} onChange={(e) => setCfg({ ...cfg, [f.key]: e.target.value })} />
              ) : (
                <Input id={`${id}-${f.key}`} type={f.secret ? "password" : "text"} value={cfg[f.key] ?? ""} onChange={(e) => setCfg({ ...cfg, [f.key]: e.target.value })} />
              )}
            </div>
          ))}
          <div className="grid gap-1">
            <Label htmlFor={`${id}-every`} className="text-xs text-muted-foreground">Tự chạy mỗi N phút (để trống = chỉ chạy khi tìm kiếm / có tracked query)</Label>
            <Input id={`${id}-every`} inputMode="numeric" value={every} onChange={(e) => setEvery(e.target.value.replace(/\D/g, ""))} />
          </div>
          <div className="md:col-span-2"><Button size="sm" onClick={save}>Lưu cấu hình</Button></div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** Is every platform flowing? One row per network: records held, new in 24 h, connectors on / healthy, last error. */
function SourcesCard({ sources, byKey }: { sources: any[]; byKey: any }) {
  if (!sources.length) return null;
  return (
    <Card title="Nguồn theo nền tảng" pad={false} className="mb-4">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nền tảng</TableHead><TableHead>Kênh</TableHead><TableHead className="text-right">Bản ghi</TableHead>
              <TableHead className="text-right">Mới 24h</TableHead><TableHead>Connector</TableHead><TableHead>Lần chạy</TableHead><TableHead>Ghi chú</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {CHANNEL_ORDER.flatMap(([ch]) => sources.filter((s) => s.channel === ch)).map((s) => {
              const dot = s.enabled === 0 ? "bg-muted-foreground" : s.healthy === s.enabled ? "bg-good" : s.healthy ? "bg-warning" : "bg-critical";
              return (
                <TableRow key={s.network}>
                  <TableCell><NetworkBadge network={s.network} byKey={byKey} /></TableCell>
                  <TableCell className="text-xs text-ink2">{s.channel_label}</TableCell>
                  <TableCell className="tnum text-right">{fmt.n(s.records)}</TableCell>
                  <TableCell className="tnum text-right">{s.new_24h ? `+${fmt.n(s.new_24h)}` : "—"}</TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    <span className={cn("mr-1 inline-block size-2 rounded-full", dot)} />
                    {s.connectors ? `${s.enabled}/${s.connectors} bật · ${s.healthy} ổn` : "chưa có connector"}
                  </TableCell>
                  <TableCell className="text-xs whitespace-nowrap">{fmt.dt(s.last_sync_at)}</TableCell>
                  <TableCell className="max-w-[360px] text-[11px]">
                    {s.errors.length ? (
                      <Tooltip>
                        <TooltipTrigger asChild><span className="block cursor-help truncate text-critical">{s.errors.join(" · ")}</span></TooltipTrigger>
                        <TooltipContent className="max-w-sm break-all">{s.errors.join(" · ")}</TooltipContent>
                      </Tooltip>
                    ) : s.enabled ? "" : <span className="text-muted-foreground">Tắt — bật ở danh sách connector</span>}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </Card>
  );
}

const mb = (b: number) => (b >= 1073741824 ? `${(b / 1073741824).toFixed(1)} GB` : `${Math.round(b / 1048576)} MB`);

function Metric({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-lg border bg-muted/30 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="tnum text-lg font-semibold">{value}</div>
      {sub && <div className="text-xs text-ink2">{sub}</div>}
    </div>
  );
}

function StorageCard() {
  const { data, reload } = useApi("/storage/usage");
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  if (!data) return <Loading rows={1} />;
  const c = data.config;
  const buckets: any[] = data.buckets || [];
  const run = async (dry: boolean) => {
    setBusy(true);
    try {
      const r = await api(`/storage/cleanup?dry_run=${dry}`, { method: "POST" });
      setPreview(dry ? r : null);
      if (!dry) { toast.success(`Đã dọn — giải phóng ${mb(r.freed_bytes || 0)}`); reload(); }
    } catch (e: any) { toast.error(String(e?.message || e)); }
    finally { setBusy(false); }
  };
  const total = preview ? preview.archived + (preview.trimmed || 0) + preview.raw_deleted + preview.events_deleted : 0;
  return (
    <Card title="Dung lượng & tự dọn dẹp" className="mb-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Metric label="Video hôm nay" value={<>{data.today.videos}{c.video_daily_quota ? ` / ${c.video_daily_quota}` : ""}</>} sub={`kho ${mb(data.video.bytes)} · ${data.video.count} file`} />
        <Metric label="Ảnh" value={mb(data.image.bytes)} sub={`${data.image.count} file`} />
        <Metric label="Dữ liệu thô (raw)" value={mb(data.raw_bytes)} />
        <Metric label="Đã dọn (giữ ảnh bìa)" value={data.archived} />
        <Metric label="Đang ghim" value={data.pinned} />
      </div>
      {buckets.length > 0 && (
        <div className="mt-4 space-y-2">
          <div className="text-xs text-muted-foreground">Ngân sách kho theo nền tảng — tổng {mb(data.max_bytes)} (R2_MAX_GB), chia bằng <code>STORAGE_SHARES</code>. Một nền tảng không bao giờ chiếm chỗ của nền tảng khác.</div>
          {buckets.map((b) => {
            const pct = b.budget_bytes ? Math.min(1, b.bytes / b.budget_bytes) : 0;
            const over = b.bytes > b.budget_bytes;
            return (
              <div key={b.key} className="grid grid-cols-1 items-center gap-1 text-xs sm:grid-cols-[150px_1fr_auto] sm:gap-3">
                <span className="truncate font-medium">{b.label} <span className="text-muted-foreground">{Math.round(b.share * 100)}%</span></span>
                <Progress value={pct * 100} className={cn("h-2.5 bg-muted", over ? "[&>[data-slot=progress-indicator]]:bg-critical" : pct > 0.85 ? "[&>[data-slot=progress-indicator]]:bg-warning" : "[&>[data-slot=progress-indicator]]:bg-series-1")} />
                <span className="tnum whitespace-nowrap text-ink2">{mb(b.bytes)} / {mb(b.budget_bytes)} · {b.videos} video · {b.images} ảnh · hôm nay {b.videos_today}{b.video_quota ? `/${b.video_quota}` : ""} video{over ? " · vượt → dọn video yếu nhất" : ""}</span>
              </div>
            );
          })}
        </div>
      )}
      <Collapsible className="mt-3">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="xs" className="group -ml-2 text-muted-foreground">Cách hoạt động<ChevronDown className="transition-transform group-data-[state=open]:rotate-180" /></Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <Alert className="mt-2 text-xs leading-relaxed">
            <AlertDescription className="block text-ink2">
              <b>Video theo lứa mỗi ngày:</b> mỗi ngày lưu tối đa <b>{c.video_daily_quota || "∞"}</b> video, ưu tiên quảng cáo mạnh nhất (chạy lâu, nhiều biến thể, nhiều vị trí) ở thị trường mục tiêu.
              Hết ngày (0h, UTC{c.day_tz_offset >= 0 ? "+" : ""}{c.day_tz_offset}) video cũ hơn <b>{c.video_keep_days || "∞"}</b> ngày bị xoá để nhường chỗ cho lứa mới — lần làm mới tới: <b>{new Date(data.today.next_rollover + "Z").toLocaleString("vi-VN")}</b>.
              Muốn giữ video nào lâu hơn thì ghim hoặc vote sản phẩm.<br />
              Dọn thêm mỗi 6 giờ: ảnh sau <b>{c.image_days || "∞"}</b> ngày · dữ liệu thô sau <b>{c.raw_days || "∞"}</b> ngày · nhật ký sự kiện sau <b>{c.event_days || "∞"}</b> ngày.
              Thông tin quảng cáo, điểm số, ảnh bìa và lịch sử luôn được giữ. <b>Không bao giờ dọn:</b> video đã ghim, sản phẩm đã vote LOVE/TEST/WATCH và quảng cáo lưu bằng extension.
              Đổi bằng biến môi trường <code>VIDEO_DAILY_QUOTA</code>, <code>VIDEO_KEEP_DAYS</code>, <code>DAY_TZ_OFFSET</code>, <code>IMAGE_RETENTION_DAYS</code>, <code>RAW_RETENTION_DAYS</code>, <code>EVENT_RETENTION_DAYS</code> (0 = tắt).
            </AlertDescription>
          </Alert>
        </CollapsibleContent>
      </Collapsible>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => run(true)} disabled={busy}>Xem trước sẽ dọn gì</Button>
        {preview && (
          <>
            <span className="text-xs text-ink2">Sẽ dọn <b>{preview.archived + (preview.trimmed || 0)}</b> file{preview.trimmed ? ` (${preview.trimmed} do vượt ngân sách nền tảng)` : ""}, giải phóng <b>{mb(preview.freed_bytes)}</b> · giữ lại {preview.kept_protected} file được bảo vệ · xoá {preview.raw_deleted} file raw, {preview.events_deleted} sự kiện cũ</span>
            {total > 0 && (
              <AlertDialog>
                <AlertDialogTrigger asChild><Button variant="destructive" size="sm" disabled={busy}><Trash2 />Dọn ngay</Button></AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Dọn kho ngay?</AlertDialogTitle>
                    <AlertDialogDescription>Sẽ xoá {total} file / sự kiện và giải phóng {mb(preview.freed_bytes)}. Video đã ghim và sản phẩm đã vote được giữ nguyên. Không hoàn tác được.</AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Huỷ</AlertDialogCancel>
                    <AlertDialogAction onClick={() => run(false)}>Dọn ngay</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

const INGEST_DOC = `POST /api/ingest/ads        {"records":[{"source":"pipiads","source_ad_id":"123","platform":"tiktok","country":"SA",
                              "advertiser":"ABC Store","ad_text":"…","cta_type":"SHOP_NOW","landing_page":"https://…",
                              "media":[{"type":"video","url":"https://…mp4","preview_url":"https://…jpg"}],
                              "first_seen":"2026-10-01","active":true,"likes":500,"comments":20,"shares":10}]}
POST /api/ingest/products   {"name":"…","url":"…","country":"SA","video_urls":["…"],"saved_by":"mkt.a"}   ← Chrome extension
`;

export default function Collector() {
  const { data, error, reload } = useApi("/collector");
  const [feed, setFeed] = useState<any[]>([]);
  const [q, setQ] = useState({ connector_id: "", query: "", countries: "SA", every_minutes: "60", page_ids: "" });
  const [exp, setExp] = useState<{ source: string; country: string; file: File | null }>({ source: "pipiads", country: "", file: null });
  const [newAdapter, setNewAdapter] = useState("");
  const { byKey } = usePlatforms();
  useEvents((e) => {
    if (["CONNECTOR_SYNCED", "CONNECTOR_FAILED", "SEARCH_DONE", "PRODUCT_DISCOVERED"].includes(e.type)) {
      setFeed((f) => [e, ...f].slice(0, 30));
      if (e.type.startsWith("CONNECTOR")) reload();
    }
  });
  if (!data) return <Loading error={error} />;
  // spy sources grouped by channel (ads / commerce)
  const grouped: Record<string, any[]> = {};
  data.connectors.forEach((c: any) => {
    const g = byKey[c.network]?.channel || "ads";
    (grouped[g] ||= []).push(c);
  });
  const searchable = data.connectors.filter((c: any) => c.supports_search);

  const addQuery = async () => {
    await api("/collector/queries", { method: "POST", body: JSON.stringify({
      connector_id: Number(q.connector_id || searchable[0]?.id), query: q.query || null,
      page_ids: q.page_ids.split(",").map((x) => x.trim()).filter(Boolean),
      countries: q.countries.split(",").map((x) => x.trim()).filter(Boolean), every_minutes: Number(q.every_minutes || 60), user: getUser().name }) });
    setQ({ ...q, query: "", page_ids: "" }); toast.success("Đã thêm theo dõi"); reload();
  };
  const upload = async () => {
    if (!exp.file) return;
    const fd = new FormData(); fd.append("source", exp.source); if (exp.country) fd.append("country", exp.country); fd.append("file", exp.file);
    await api("/collector/export-upload", { method: "POST", body: fd }); toast.success("Đã upload — đang xử lý"); reload();
  };
  const addConnector = async () => {
    if (!newAdapter) return;
    await api("/collector/connectors", { method: "POST", body: JSON.stringify({ adapter: newAdapter, enabled: false }) });
    setNewAdapter(""); toast.success("Đã thêm connector (đang tắt)"); reload();
  };

  return (
    <div>
      <PageHeader title="Data & Connectors" subtitle="Unified Spy Collector — nguồn → raw storage → normalize → dedup → product cluster → analytics. App chỉ đọc dữ liệu đã chuẩn hoá.">
        <Select value={newAdapter} onValueChange={setNewAdapter}>
          <SelectTrigger size="sm" className="w-56"><SelectValue placeholder="Thêm connector…" /></SelectTrigger>
          <SelectContent>{data.catalogue.map((c: any) => <SelectItem key={c.adapter} value={c.adapter}>{c.name}</SelectItem>)}</SelectContent>
        </Select>
        <Button size="sm" onClick={addConnector} disabled={!newAdapter}><Plus />Thêm</Button>
      </PageHeader>

      <Tabs defaultValue="sources" className="mt-3">
        <TabsList className="mb-3 flex-wrap">
          <TabsTrigger value="sources">Nguồn & connector</TabsTrigger>
          <TabsTrigger value="storage">Dung lượng</TabsTrigger>
          <TabsTrigger value="track">Theo dõi & Upload</TabsTrigger>
          <TabsTrigger value="ingest">Ingest API</TabsTrigger>
        </TabsList>

        <TabsContent value="sources">
          <SourcesCard sources={data.sources || []} byKey={byKey} />
          <Card title="Connectors" pad={false}>
            {data.connectors.length === 0 && <Empty>Chưa có connector — thêm từ danh mục ở góc trên.</Empty>}
            {CHANNEL_ORDER.filter(([g]) => grouped[g]?.length).map(([g, title]) => (
              <div key={g}>
                <div className="px-4 pt-3 pb-1 text-[11px] uppercase tracking-wider text-muted-foreground">{title} · {grouped[g].length}</div>
                {grouped[g].map((c) => <ConnectorRow key={c.id} c={c} onChange={reload} byKey={byKey} />)}
              </div>
            ))}
          </Card>
        </TabsContent>

        <TabsContent value="storage">
          <StorageCard />
          <Card title="Kho dữ liệu">
            <div className="space-y-1 text-sm">
              <div className="flex justify-between gap-3"><span className="text-ink2">Storage</span><span className="truncate">{data.storage.backend}{data.storage.data_dir ? ` · ${data.storage.data_dir}` : ""}</span></div>
              <div className="flex justify-between"><span className="text-ink2">Raw batch files</span><span className="tnum">{data.storage.raw_files ?? "R2"}</span></div>
              {Object.entries(data.creatives).map(([k, v]) => <div key={k} className="flex justify-between"><span className="text-ink2">Creatives {k}</span><span className="tnum">{v as number}</span></div>)}
              <div className="flex justify-between"><span className="text-ink2">Ingest token</span><span>{data.ingest_token_required ? "bắt buộc" : "chưa đặt (INGEST_TOKEN)"}</span></div>
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="track">
          <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card title="Theo dõi từ khoá / page đối thủ (Tier 2)">
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2 grid gap-1">
                  <Label className="text-xs text-muted-foreground">Connector</Label>
                  <Select value={q.connector_id || String(searchable[0]?.id ?? "")} onValueChange={(v) => setQ({ ...q, connector_id: v })}>
                    <SelectTrigger className="w-full"><SelectValue placeholder="Chọn connector" /></SelectTrigger>
                    <SelectContent>{searchable.map((c: any) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="col-span-2 grid gap-1"><Label htmlFor="tq-query" className="text-xs text-muted-foreground">Từ khoá</Label><Input id="tq-query" placeholder="vd: neck massager" value={q.query} onChange={(e) => setQ({ ...q, query: e.target.value })} /></div>
                <div className="col-span-2 grid gap-1"><Label htmlFor="tq-pages" className="text-xs text-muted-foreground">hoặc Page ID đối thủ (phẩy)</Label><Input id="tq-pages" value={q.page_ids} onChange={(e) => setQ({ ...q, page_ids: e.target.value })} /></div>
                <div className="grid gap-1"><Label htmlFor="tq-countries" className="text-xs text-muted-foreground">Market</Label><Input id="tq-countries" placeholder="SA,AE" value={q.countries} onChange={(e) => setQ({ ...q, countries: e.target.value.toUpperCase() })} /></div>
                <div className="grid gap-1"><Label htmlFor="tq-every" className="text-xs text-muted-foreground">Mỗi N phút</Label><Input id="tq-every" inputMode="numeric" value={q.every_minutes} onChange={(e) => setQ({ ...q, every_minutes: e.target.value.replace(/\D/g, "") })} /></div>
              </div>
              <Button size="sm" className="mt-3" onClick={addQuery} disabled={!q.query && !q.page_ids}><Plus />Thêm theo dõi</Button>
            </Card>
            <Card title="Upload file export (Pipiads / Minea / BigSpy)">
              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-1"><Label htmlFor="exp-source" className="text-xs text-muted-foreground">Nguồn</Label><Input id="exp-source" placeholder="pipiads" value={exp.source} onChange={(e) => setExp({ ...exp, source: e.target.value })} /></div>
                <div className="grid gap-1"><Label htmlFor="exp-country" className="text-xs text-muted-foreground">Country mặc định</Label><Input id="exp-country" value={exp.country} onChange={(e) => setExp({ ...exp, country: e.target.value.toUpperCase() })} /></div>
                <div className="col-span-2 grid gap-1"><Label htmlFor="exp-file" className="text-xs text-muted-foreground">File (.csv, .xlsx, .json)</Label><Input id="exp-file" type="file" accept=".csv,.xlsx,.json" onChange={(e) => setExp({ ...exp, file: e.target.files?.[0] || null })} /></div>
              </div>
              <Button size="sm" className="mt-3" onClick={upload} disabled={!exp.file}>Upload & xử lý</Button>
              <div className="mt-2 text-[11px] text-muted-foreground">Cột được nhận diện tự động (Ad ID, Advertiser, Caption, Video URL, Landing page, Country, First seen, Likes…). Video trong file sẽ được tải về kho.</div>
            </Card>
          </div>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card title="Tracked queries" pad={false}>
              {data.tracked_queries.length === 0 ? <Empty>Chưa theo dõi từ khoá nào.</Empty> : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader><TableRow><TableHead>Từ khoá / page</TableHead><TableHead>Market</TableHead><TableHead>Chu kỳ</TableHead><TableHead>Lần chạy</TableHead><TableHead>Kết quả</TableHead><TableHead /></TableRow></TableHeader>
                    <TableBody>{data.tracked_queries.map((t: any) => (
                      <TableRow key={t.id}>
                        <TableCell>{t.query || `pages: ${t.page_ids.join(",")}`}</TableCell><TableCell>{t.countries.join(",")}</TableCell><TableCell className="tnum">{t.every_minutes}′</TableCell>
                        <TableCell className="text-xs">{fmt.dt(t.last_run_at)}</TableCell>
                        <TableCell className="text-xs">{t.last_error ? <span className="text-critical">{t.last_error.slice(0, 80)}</span> : `${t.last_count} ads · ${t.last_new} mới`}</TableCell>
                        <TableCell className="whitespace-nowrap">
                          <div className="flex gap-1">
                            <Button variant="outline" size="xs" onClick={async () => { await api(`/collector/queries/${t.id}/run`, { method: "POST" }); toast("Đã chạy — xem feed realtime"); }}><Play />Chạy</Button>
                            <AlertDialog>
                              <AlertDialogTrigger asChild><Button variant="outline" size="xs"><Trash2 />Xoá</Button></AlertDialogTrigger>
                              <AlertDialogContent>
                                <AlertDialogHeader>
                                  <AlertDialogTitle>Xoá theo dõi này?</AlertDialogTitle>
                                  <AlertDialogDescription>{t.query || `pages: ${t.page_ids.join(",")}`} · {t.countries.join(",")} — sẽ ngừng tự chạy. Dữ liệu đã thu vẫn giữ.</AlertDialogDescription>
                                </AlertDialogHeader>
                                <AlertDialogFooter>
                                  <AlertDialogCancel>Huỷ</AlertDialogCancel>
                                  <AlertDialogAction onClick={async () => { await api(`/collector/queries/${t.id}`, { method: "DELETE" }); reload(); }}>Xoá</AlertDialogAction>
                                </AlertDialogFooter>
                              </AlertDialogContent>
                            </AlertDialog>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}</TableBody>
                  </Table>
                </div>
              )}
            </Card>
            <Card title="Realtime feed">
              {feed.length === 0 ? <div className="text-sm text-muted-foreground">Chờ sự kiện…</div> : (
                <ScrollArea className="h-72">
                  <ul className="space-y-1 pr-3 text-xs">
                    {feed.map((e, i) => <li key={i} className="break-all"><b>{e.type}</b> {new Date(e.at).toLocaleTimeString("vi-VN")} — {JSON.stringify(e.data).slice(0, 180)}</li>)}
                  </ul>
                </ScrollArea>
              )}
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="ingest">
          <Card title="Đẩy dữ liệu vào (Ingest API)">
            <div className="space-y-2 text-xs text-ink2">
              <p>Header <code>X-Ingest-Token: $INGEST_TOKEN</code>. Mọi nguồn dùng chung một schema (Common Data Contract); trường không có thì để <code>null</code>.</p>
              <pre className="overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs">{INGEST_DOC}</pre>
              <p>Push sources: {Object.entries(data.push_sources).map(([k, v]) => `${k} (${v})`).join(" · ")}</p>
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
