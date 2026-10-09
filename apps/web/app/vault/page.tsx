"use client";
import { MarketPicker } from "@/components/MarketPicker";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowRight, ExternalLink, Layers, RotateCw, X } from "lucide-react";
import { CreativeMedia, DownloadButton, PinButton } from "@/components/media";
import { Card, Empty, Loading, PageHeader, Pill } from "@/components/ui";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card as UiCard } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { LoadMore } from "@/components/paging";
import { api, fmt, qs, useAction, usePaged } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";

// Radix Select rejects "" as an item value — "all" stands in for "" (every option) in the query string.
const TYPES: [string, string][] = [["all", "Video + ảnh"], ["video", "Video"], ["image", "Ảnh"]];
const FUNNELS: [string, string][] = [["all", "Mọi funnel"], ["mess", "MKT Mess"], ["ladi", "MKT Ladi"]];
const STATUSES: [string, string][] = [["stored", "Đã lưu"], ["archived", "Đã dọn"], ["pending", "Đang tải"], ["failed", "Lỗi"], ["expired", "Hết hạn"], ["all", "Tất cả"]];
const un = (v: string) => (v === "all" ? "" : v);

function Filter({ value, onChange, options, className }: { value: string; onChange: (v: string) => void; options: [string, string][]; className?: string }) {
  return (
    <Select value={value || "all"} onValueChange={(v) => onChange(un(v))}>
      <SelectTrigger size="sm" className={className}><SelectValue /></SelectTrigger>
      <SelectContent>{options.map(([k, l]) => <SelectItem key={k} value={k}>{l}</SelectItem>)}</SelectContent>
    </Select>
  );
}

export default function Vault() {
  const [type, setType] = useState("video");
  const [funnel, setFunnel] = useState("");
  const team = useTeam();
  useEffect(() => { setFunnel(team); }, [team]);
  const [country, setCountry] = useState("");
  const [q, setQ] = useState("");
  const [qd, setQd] = useState("");  // q debounced 300 ms: one request per pause, not per keystroke
  useEffect(() => { const t = setTimeout(() => setQd(q), 300); return () => clearTimeout(t); }, [q]);
  const [family, setFamily] = useState<number | null>(null);
  const [status, setStatus] = useState("stored");
  const { rows, total, last, loading, error, hasMore, loadMore, reload } = usePaged(`/creatives${qs({ type, funnel, country, q: qd, status, family_id: family })}`, 36);
  useEvents((e) => { if (e.type === "CREATIVE_STORED") reload(); });
  const act = useAction();
  const retry = (id: number) => act.run(async () => { await api(`/creatives/${id}/retry`, { method: "POST" }); reload(); });

  return (
    <div>
      <PageHeader title="Creative Vault" subtitle="Mọi video / ảnh quảng cáo đã thu thập — lưu vĩnh viễn trong kho (link CDN gốc sẽ hết hạn), xem & tải về, gom theo creative family">
        <Input className="h-8 w-56" placeholder="Từ khoá trong ad copy…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Filter value={type} onChange={setType} options={TYPES} />
        <Filter value={funnel} onChange={setFunnel} options={FUNNELS} />
        <Filter value={status} onChange={setStatus} options={STATUSES} />
      </PageHeader>
      <div className="mb-3"><MarketPicker value={country} onChange={setCountry} /></div>
      {last && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
          {Object.entries(last.stats).map(([k, v]) => <Pill key={k} tone={k === "stored" ? "good" : k === "pending" ? "info" : "bad"}>{k}: {v as number}</Pill>)}
          {family && (
            <Badge variant="outline" className="gap-1 pr-1">
              Family #{family}
              <button type="button" aria-label="Bỏ lọc family" onClick={() => setFamily(null)} className="rounded-full hover:bg-muted"><X className="size-3" /></button>
            </Badge>
          )}
        </div>
      )}
      {act.error && <Alert variant="destructive" className="mb-2"><AlertCircle /><AlertTitle>Lỗi</AlertTitle><AlertDescription>{act.error}</AlertDescription></Alert>}
      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : rows.length === 0 ? <Card><Empty>Chưa có creative. Dùng trang Tìm sản phẩm để quét nguồn.</Empty></Card> : (
        <>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {rows.map((c: any) => (
            <UiCard key={c.id} className="flex flex-col gap-2 py-2">
              <div className="px-2"><CreativeMedia c={c} height={300} /></div>
              <div className="space-y-1 px-3 pb-1 text-xs">
                <div className="flex justify-between gap-2">
                  <span className="truncate font-medium">{c.ad?.advertiser || c.source}</span>
                  <span className="whitespace-nowrap text-muted-foreground">{c.ad?.country} · {c.ad?.platform}</span>
                </div>
                {c.ad?.text && <div className="line-clamp-3 text-ink2">{c.ad.text}</div>}
                <div className="text-muted-foreground">
                  Chạy {c.ad?.days_running ?? "—"} ngày · {c.ad?.active ? "đang chạy" : "đã dừng"}{c.ad?.variants ? ` · ${c.ad.variants} biến thể` : ""}
                  {c.duration ? ` · ${Math.round(c.duration)}s` : ""}{c.width ? ` · ${c.width}×${c.height}` : ""}
                </div>
                <div className="text-muted-foreground">
                  Nguồn: {c.source} · thu thập {fmt.date(c.collected_at)}
                  {c.ad?.funnel && <> · funnel <b>{c.ad.funnel}</b></>}
                </div>
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  <DownloadButton c={c} small />
                  <PinButton c={c} />
                  {c.family_size > 1 && <Button variant="outline" size="xs" onClick={() => setFamily(c.family_id)}><Layers /> Family · {c.family_size}</Button>}
                  {c.product_id && <Button asChild variant="outline" size="xs"><Link href={`/products/${c.product_id}`}>Sản phẩm <ArrowRight /></Link></Button>}
                  {c.ad?.snapshot_url && <Button asChild variant="outline" size="xs"><a href={c.ad.snapshot_url} target="_blank" rel="noreferrer">Ad gốc <ExternalLink /></a></Button>}
                  {c.ad?.landing_url && <Button asChild variant="outline" size="xs"><a href={c.ad.landing_url} target="_blank" rel="noreferrer">Landing <ExternalLink /></a></Button>}
                  {["failed", "expired"].includes(c.status) && <Button variant="outline" size="xs" disabled={act.busy} onClick={() => retry(c.id)}><RotateCw /> Thử lại</Button>}
                </div>
              </div>
            </UiCard>
          ))}
        </div>
        <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </>
      )}
    </div>
  );
}
