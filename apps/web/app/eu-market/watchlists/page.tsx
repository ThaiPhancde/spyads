"use client";
import { GlobalImageSearch } from "@/components/visual/global-image-search";
import { useState } from "react";
import { Play, Trash2 } from "lucide-react";
import { Card, PageHeader, Pill } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { CountryChips, EuLoading, SOURCE_LABEL } from "@/components/eu-market/shared";
import { api, fmt, useAction, useApi } from "@/lib/api";

const EMPTY = { name: "", owner: "", keywords: "", countries: [] as string[], sources: [] as string[], price_drop_pct: "10", source_gap_pct: "", consecutive_rises: "" };

export default function EuWatchlists() {
  const { data, error, reload } = useApi<any>("/eu-market/watchlists");
  const alerts = useApi<any>("/alerts?type=EU_*&limit=100");
  const act = useAction();
  const [f, setF] = useState(EMPTY);
  const [editing, setEditing] = useState<number | null>(null);
  const [msg, setMsg] = useState("");

  const body = (w: any = f) => ({
    name: w.name.trim(), owner: w.owner.trim() || "team", keywords: w.keywords.split(/[\n|,]/).map((s: string) => s.trim()).filter(Boolean),
    countries: w.countries, sources: w.sources, active: true,
    rules: Object.fromEntries(["price_drop_pct", "source_gap_pct", "consecutive_rises"].filter((k) => w[k]).map((k) => [k, Number(w[k])])),
  });
  const submit = () => act.run(async () => {
    if (!f.name.trim()) throw new Error("Đặt tên cho watchlist");
    await api(editing ? `/eu-market/watchlists/${editing}` : "/eu-market/watchlists", { method: editing ? "PUT" : "POST", body: JSON.stringify(body()) });
    setF(EMPTY); setEditing(null); reload();
  });
  const toggle = (w: any) => act.run(async () => {
    await api(`/eu-market/watchlists/${w.id}`, { method: "PUT", body: JSON.stringify({ ...w, active: !w.active }) }); reload();
  });
  const del = (w: any) => act.run(async () => { if (confirm(`Xoá watchlist “${w.name}”?`)) { await api(`/eu-market/watchlists/${w.id}`, { method: "DELETE" }); reload(); } });
  const runNow = () => act.run(async () => { await api("/eu-market/admin/run-jobs", { method: "POST" }); setMsg("Đã chạy nền: tỷ giá → đồng bộ watchlist → snapshot giá → ghép → cảnh báo. Xem tiến độ ở Data Sources."); });
  const edit = (w: any) => {
    setEditing(w.id);
    setF({ name: w.name, owner: w.owner, keywords: (w.keywords || []).join("\n"), countries: w.countries || [], sources: w.sources || [],
           price_drop_pct: String(w.rules?.price_drop_pct ?? ""), source_gap_pct: String(w.rules?.source_gap_pct ?? ""), consecutive_rises: String(w.rules?.consecutive_rises ?? "") });
  };
  const euAlerts: any[] = alerts.data?.rows || [];

  return (
    <div className="space-y-4">
      <PageHeader title="Watchlists & Alerts" subtitle="Từ khoá theo dõi được đồng bộ định kỳ (EU_SYNC_EVERY_MIN, mặc định 6 giờ) khi có nguồn đã kết nối; cảnh báo giá dựa trên snapshot thật.">
        <GlobalImageSearch label="Theo dõi bằng ảnh" />
        <Button size="sm" variant="outline" onClick={runNow} disabled={act.busy}><Play />Chạy đồng bộ ngay</Button>
      </PageHeader>
      {msg && <p className="text-sm text-muted-foreground">{msg}</p>}
      {act.error && <p className="text-sm text-critical">{act.error}</p>}
      <div className="grid gap-3 xl:grid-cols-[minmax(0,420px)_1fr]">
        <Card title={editing ? `Sửa watchlist #${editing}` : "Watchlist mới"}>
          <div className="space-y-2">
            <Input placeholder="Tên (vd. Vòng tay phong thuỷ DE/FR)" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
            <Input placeholder="Marketer (tên của bạn)" value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })} />
            <Textarea rows={3} placeholder={"Từ khoá, mỗi dòng một\nGlücksarmband\nbracelet porte-bonheur"} value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} />
            <CountryChips value={f.countries} onChange={(countries) => setF({ ...f, countries })} />
            <div className="flex flex-wrap gap-1">{["kaufland", "bol"].map((s) => (
              <Button key={s} size="xs" variant={f.sources.includes(s) ? "default" : "outline"} aria-pressed={f.sources.includes(s)}
                      onClick={() => setF({ ...f, sources: f.sources.includes(s) ? f.sources.filter((x) => x !== s) : [...f.sources, s] })}>{SOURCE_LABEL[s]}</Button>
            ))}<span className="self-center text-xs text-muted-foreground">(trống = mọi nguồn đã kết nối)</span></div>
            <div className="grid grid-cols-3 gap-2">
              <Label className="grid gap-1 text-xs">Giảm giá ≥ %<Input type="number" min={1} value={f.price_drop_pct} onChange={(e) => setF({ ...f, price_drop_pct: e.target.value })} /></Label>
              <Label className="grid gap-1 text-xs">Chênh nguồn ≥ %<Input type="number" min={1} value={f.source_gap_pct} onChange={(e) => setF({ ...f, source_gap_pct: e.target.value })} /></Label>
              <Label className="grid gap-1 text-xs">Tăng liên tiếp ≥<Input type="number" min={2} value={f.consecutive_rises} onChange={(e) => setF({ ...f, consecutive_rises: e.target.value })} /></Label>
            </div>
            <div className="flex gap-2">
              <Button onClick={submit} disabled={act.busy}>{editing ? "Lưu" : "Tạo watchlist"}</Button>
              {editing && <Button variant="ghost" onClick={() => { setEditing(null); setF(EMPTY); }}>Huỷ</Button>}
            </div>
          </div>
        </Card>
        <Card title="Watchlists">
          {!data ? <EuLoading error={error} rows={2} /> : data.items.length ? data.items.map((w: any) => (
            <div key={w.id} className="flex flex-wrap items-start gap-3 border-b py-2.5 last:border-0">
              <Switch checked={w.active} onCheckedChange={() => toggle(w)} aria-label="Bật / tắt" />
              <div className="min-w-0 flex-1 text-sm">
                <button className="font-medium hover:underline" onClick={() => edit(w)}>{w.name}</button>
                <span className="ml-2 text-xs text-muted-foreground">{w.owner} · đồng bộ {w.last_synced_at ? fmt.dt(w.last_synced_at) : "chưa"}</span>
                <div className="text-xs text-muted-foreground">{w.keywords.join(" · ") || "—"} · {w.countries.join(" ") || "mặc định"} · {w.sources.join(", ") || "mọi nguồn"}</div>
                <div className="mt-1 flex flex-wrap gap-1">{Object.entries(w.rules || {}).map(([k, v]: any) => <Pill key={k} tone="info">{k} {v}</Pill>)}</div>
              </div>
              <Button size="icon-xs" variant="ghost" aria-label="Xoá" onClick={() => del(w)}><Trash2 /></Button>
            </div>
          )) : <p className="text-sm text-muted-foreground">Chưa có watchlist.</p>}
        </Card>
      </div>
      <Card title={`Cảnh báo EU (${euAlerts.length})`}>
        {euAlerts.length ? euAlerts.map((a: any) => (
          <div key={a.id} className="border-b py-2 text-sm last:border-0">
            <span className="font-medium">{a.title}</span> <span className="text-xs text-muted-foreground">· {fmt.dt(a.created_at)}</span>
            <div className="text-xs text-muted-foreground">{a.message}</div>
          </div>
        )) : <p className="text-sm text-muted-foreground">Chưa có cảnh báo. Cảnh báo cần ≥2 snapshot giá của listing thuộc watchlist.</p>}
      </Card>
    </div>
  );
}
