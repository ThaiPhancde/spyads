"use client";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Card, Loading, PageHeader } from "@/components/ui";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { AdvertiserFilters } from "@/components/workspace/actions";
import { Pager } from "@/components/workspace/common";
import { toast } from "sonner";

export default function Filters() {
  const [q, setQ] = useState(""); const [term, setTerm] = useState(""); const [offset, setOffset] = useState(0); const [busy, setBusy] = useState(false);
  const filters = useApi("/workspace/filters"); const advertisers = useApi(`/advertisers?q=${encodeURIComponent(term)}&offset=${offset}`);
  async function set(id: number, mode: string) { setBusy(true); try { await api(`/workspace/filters/${id}`, { method: "PUT", body: JSON.stringify({ mode }) }); filters.reload(); window.dispatchEvent(new Event("workspace-filters")); } catch (e: any) { toast.error(e.message); } finally { setBusy(false); } }
  return <div className="space-y-4"><PageHeader title="Whitelist & Blacklist" subtitle="Áp dụng riêng cho tài khoản trong Search và Library. Blacklist luôn được loại trừ." /><AdvertiserFilters />
    <Card title="Đã chọn">{filters.error && <Loading error={filters.error} />}{filters.data?.rows.map((f: any) => <div key={f.advertiser_id} className="flex items-center justify-between gap-3 border-b py-2 text-sm"><span>{f.name} · {f.mode}</span><Button disabled={busy} variant="ghost" size="sm" onClick={() => set(f.advertiser_id, "none")}>Bỏ khỏi bộ lọc</Button></div>)}{!filters.data?.rows.length && <p className="text-sm text-muted-foreground">Chưa chọn advertiser.</p>}</Card>
    <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setTerm(q); setOffset(0); }}><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm advertiser để thêm vào bộ lọc" /><Button>Tìm</Button></form>
    {!advertisers.data ? <Loading error={advertisers.error} /> : <><div className="grid gap-3 md:grid-cols-2">{advertisers.data.rows.map((a: any) => <Card key={a.id} title={a.name}><p className="mb-3 text-xs text-muted-foreground">{a.source} · {a.source_advertiser_id || "Chưa có ID"}</p><div className="flex gap-2"><Button disabled={busy} size="sm" variant="outline" onClick={() => set(a.id, "whitelist")}>Whitelist</Button><Button disabled={busy} size="sm" variant="outline" onClick={() => set(a.id, "blacklist")}>Blacklist</Button></div></Card>)}</div><Pager offset={offset} total={advertisers.data.total} onChange={setOffset} /></>}
  </div>;
}
