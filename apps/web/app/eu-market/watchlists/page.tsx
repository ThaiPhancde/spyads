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

const EMPTY = { name: "", owner: "", keywords: "", countries: [] as string[], sources: [] as string[] };

export default function EuWatchlists() {
  const { data, error, reload } = useApi<any>("/eu-market/watchlists");
  const act = useAction();
  const [f, setF] = useState(EMPTY);
  const [editing, setEditing] = useState<number | null>(null);
  const [msg, setMsg] = useState("");

  const body = (w: any = f) => ({
    name: w.name.trim(), owner: w.owner.trim() || "team", keywords: w.keywords.split(/[\n|,]/).map((s: string) => s.trim()).filter(Boolean),
    countries: w.countries, sources: w.sources, active: true,
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
    setF({ name: w.name, owner: w.owner, keywords: (w.keywords || []).join("\n"), countries: w.countries || [], sources: w.sources || []});
  };

  return (
    <div className="space-y-4">
      <PageHeader title="Watchlists" subtitle="Từ khoá theo dõi được đồng bộ định kỳ (EU_SYNC_EVERY_MIN, mặc định 6 giờ) khi có nguồn đã kết nối.">
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
              </div>
              <Button size="icon-xs" variant="ghost" aria-label="Xoá" onClick={() => del(w)}><Trash2 /></Button>
            </div>
          )) : <p className="text-sm text-muted-foreground">Chưa có watchlist.</p>}
        </Card>
      </div>
    </div>
  );
}
