"use client";
import { MarketPicker } from "@/components/MarketPicker";
import Link from "next/link";
import { useEffect, useState } from "react";
import { CreativeMedia, DownloadButton, PinButton } from "@/components/media";
import { Card, Empty, Loading, PageHeader, Pill } from "@/components/ui";
import { LoadMore } from "@/components/paging";
import { api, fmt, qs, useAction, usePaged } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";

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
        <input className="input w-56" placeholder="Từ khoá trong ad copy…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" value={type} onChange={(e) => setType(e.target.value)}><option value="">Video + ảnh</option><option value="video">Video</option><option value="image">Ảnh</option></select>
        <select className="input" value={funnel} onChange={(e) => setFunnel(e.target.value)}><option value="">Mọi funnel</option><option value="mess">MKT Mess</option><option value="ladi">MKT Ladi</option></select>
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="stored">Đã lưu</option><option value="archived">Đã dọn</option><option value="pending">Đang tải</option><option value="failed">Lỗi</option><option value="expired">Hết hạn</option><option value="">Tất cả</option>
        </select>
      </PageHeader>
      <div className="mb-3"><MarketPicker value={country} onChange={setCountry} /></div>
      {last && (
        <div className="flex flex-wrap gap-2 mb-4 text-xs">
          {Object.entries(last.stats).map(([k, v]) => <Pill key={k} tone={k === "stored" ? "good" : k === "pending" ? "info" : "bad"}>{k}: {v as number}</Pill>)}
          {family && <button className="btn text-xs" onClick={() => setFamily(null)}>× Bỏ lọc family #{family}</button>}
        </div>
      )}
      {act.error && <div className="text-sm mb-2" style={{ color: "var(--critical)" }}>Lỗi: {act.error}</div>}
      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : rows.length === 0 ? <Card><Empty>Chưa có creative. Dùng trang Tìm sản phẩm để quét nguồn.</Empty></Card> : (
        <>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
          {rows.map((c: any) => (
            <div key={c.id} className="card p-2 flex flex-col gap-2">
              <CreativeMedia c={c} height={300} />
              <div className="px-1 text-xs space-y-1">
                <div className="flex justify-between gap-2">
                  <span className="font-medium truncate">{c.ad?.advertiser || c.source}</span>
                  <span className="text-muted whitespace-nowrap">{c.ad?.country} · {c.ad?.platform}</span>
                </div>
                {c.ad?.text && <div className="text-ink2 line-clamp-3">{c.ad.text}</div>}
                <div className="text-muted">
                  Chạy {c.ad?.days_running ?? "—"} ngày · {c.ad?.active ? "đang chạy" : "đã dừng"}{c.ad?.variants ? ` · ${c.ad.variants} biến thể` : ""}
                  {c.duration ? ` · ${Math.round(c.duration)}s` : ""}{c.width ? ` · ${c.width}×${c.height}` : ""}
                </div>
                <div className="text-muted">
                  Nguồn: {c.source} · thu thập {fmt.date(c.collected_at)}
                  {c.ad?.funnel && <> · funnel <b>{c.ad.funnel}</b></>}
                </div>
                <div className="flex flex-wrap gap-1.5 pt-1 items-center">
                  <DownloadButton c={c} small />
                  <PinButton c={c} />
                  {c.family_size > 1 && <button className="btn text-[11px] px-2 py-1" onClick={() => setFamily(c.family_id)}>Family · {c.family_size}</button>}
                  {c.product_id && <Link className="btn text-[11px] px-2 py-1" href={`/products/${c.product_id}`}>Sản phẩm →</Link>}
                  {c.ad?.snapshot_url && <a className="btn text-[11px] px-2 py-1" href={c.ad.snapshot_url} target="_blank" rel="noreferrer">Ad gốc ↗</a>}
                  {c.ad?.landing_url && <a className="btn text-[11px] px-2 py-1" href={c.ad.landing_url} target="_blank" rel="noreferrer">Landing ↗</a>}
                  {["failed", "expired"].includes(c.status) && <button className="btn text-[11px] px-2 py-1" onClick={() => retry(c.id)}>Thử lại</button>}
                </div>
              </div>
            </div>
          ))}
        </div>
        <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </>
      )}
    </div>
  );
}
