"use client";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ExternalLink } from "lucide-react";
import { Card, PageHeader, Pill, ScoreBar } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EuLoading, Kv, MANUAL_TAGS, Price, PriceChart, SOURCE_LABEL, Thumb, eur } from "@/components/eu-market/shared";
import { AdMatchRow } from "@/components/eu-market/matches";
import { api, fmt, useAction, useApi } from "@/lib/api";

function Detail() {
  const id = useSearchParams().get("id");
  const { data: p, error, reload } = useApi<any>(id ? `/eu-market/products/${id}` : null);
  const act = useAction();
  const [sel, setSel] = useState<number | null>(null);
  if (!id) return <p className="text-sm text-muted-foreground">Thiếu id sản phẩm.</p>;
  if (!p) return <EuLoading error={error} rows={4} />;
  const offers: any[] = p.offers;
  const shown = sel ?? offers.find((o) => (p.price_history[o.id]?.points || []).length > 1)?.id ?? offers[0]?.id;
  const hist = p.price_history[shown] || { points: [], stats: {} };
  const save = (body: any) => act.run(async () => { await api(`/eu-market/products/${p.id}`, { method: "PATCH", body: JSON.stringify(body) }); reload(); });
  const review = (kind: string, mid: number, status: string) =>
    act.run(async () => { await api(`/eu-market/${kind}/${mid}/review`, { method: "POST", body: JSON.stringify({ status }) }); reload(); });
  const t = p.trend, r = p.rarity, cmp = p.price_compare;

  return (
    <div className="space-y-4">
      <div className="flex gap-4">
        <Thumb src={p.image_url} alt={p.title} size={112} />
        <PageHeader title={p.title} subtitle={[p.brand, p.ean && `EAN ${p.ean}`, p.category, `${p.offer_count} offer · ${p.countries.join(" ")}`].filter(Boolean).join(" · ")}>
          {p.compliance_flags.length > 0 && <Pill tone="bad">compliance: {p.compliance_flags.join(", ")}</Pill>}
        </PageHeader>
      </div>
      {act.error && <p className="text-sm text-critical">{act.error}</p>}

      <Card title="Offers — mỗi dòng là dữ liệu thật từ nguồn, kèm thời điểm quan sát" pad={false}>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader><TableRow><TableHead>Nguồn</TableHead><TableHead>QG</TableHead><TableHead>Loại</TableHead><TableHead>Giá</TableHead><TableHead>Seller</TableHead>
              <TableHead>Tình trạng</TableHead><TableHead>Landed cost</TableHead><TableHead>Quan sát đầu / cuối</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {offers.map((o) => {
                const lc = p.landed_cost[o.id];
                return (
                  <TableRow key={o.id} data-state={o.id === shown ? "selected" : undefined} className="cursor-pointer" onClick={() => setSel(o.id)}>
                    <TableCell>{SOURCE_LABEL[o.source] || o.source}</TableCell><TableCell>{o.market_country}</TableCell>
                    <TableCell>{o.kind === "supplier_offer" ? "B2B" : "bán lẻ"}</TableCell><TableCell><Price l={o} /></TableCell>
                    <TableCell className="max-w-40 truncate">{o.seller_name || o.seller_id || "unknown"}</TableCell><TableCell>{o.availability}</TableCell>
                    <TableCell className="tnum text-xs">{lc?.value != null ? `${fmt.n(lc.value, 2)} ${lc.currency}` : <span className="text-muted-foreground">thiếu: {lc?.missing?.join(", ")}</span>}</TableCell>
                    <TableCell className="text-xs">{fmt.dt(o.observed_at)}<br />{fmt.dt(o.last_seen_at)}</TableCell>
                    <TableCell>{o.product_url && <a href={o.product_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-series-1" onClick={(e) => e.stopPropagation()}>mở<ExternalLink className="size-3" /></a>}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </Card>

      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Lịch sử giá (snapshot thật)" className="xl:col-span-2" description="Bấm một offer ở bảng trên để xem lịch sử của nó.">
          <PriceChart points={hist.points} />
          {hist.stats?.change_pct != null && (
            <p className="mt-2 text-xs text-muted-foreground">{hist.stats.snapshots} snapshot · {hist.stats.first} → {hist.stats.last} {hist.stats.currency} ({fmt.n(hist.stats.change_pct, 1)}%) · tăng liên tiếp {hist.stats.consecutive_rises} lần</p>
          )}
        </Card>
        <Card title="So sánh giá giữa nguồn (EUR tham khảo)">
          {Object.keys(cmp).length === 0 && <p className="text-sm text-muted-foreground">Chưa có giá quy đổi được (thiếu giá hoặc tỷ giá ECB).</p>}
          {[["retail_incl_vat", "Bán lẻ (gồm VAT)"], ["wholesale_excl_vat", "Giá sỉ (chưa VAT)"], ["vat_unknown", "Chưa rõ VAT"]].map(([k, l]) => cmp[k] && (
            <div key={k} className="mb-2 space-y-0.5">
              <div className="text-xs font-medium">{l} · {cmp[k].offers} offer</div>
              <Kv k="Thấp nhất" v={eur(cmp[k].min_eur)} /><Kv k="Cao nhất" v={eur(cmp[k].max_eur)} />
              {cmp[k].gap_pct != null && <Kv k="Chênh lệch" v={`${cmp[k].gap_pct}%`} />}
            </div>
          ))}
          {cmp.retail_vs_wholesale && <p className="text-xs text-muted-foreground">Bán lẻ − sỉ: {eur(cmp.retail_vs_wholesale.gross_gap_eur)} — {cmp.retail_vs_wholesale.note}.</p>}
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card title={<span className="flex items-center gap-2">Trend score (tín hiệu nội bộ) <ScoreBar value={t.score} />{t.insufficient_data && <Pill tone="warn">insufficient_data</Pill>}</span>}
              description={`${t.history_days} ngày lịch sử · cửa sổ ${t.window_days} ngày · ${t.note}`}>
          {Object.entries(t.components).map(([k, c]: any) => <Kv key={k} k={k} v={`${c.value} → ${(c.score * 100).toFixed(0)}`} />)}
          {t.missing.length > 0 && <p className="mt-1 text-xs text-muted-foreground">Thiếu dữ liệu: {t.missing.join(", ")}</p>}
        </Card>
        <Card title={<span className="flex items-center gap-2">Rarity <ScoreBar value={r.score} /><Badge variant="outline">{r.label}</Badge></span>}
              description={`${r.note} · độ tin cậy ${r.confidence} (${r.dataset_listings} listing trong kho)`}>
          <ul className="list-disc space-y-0.5 pl-4 text-sm">{r.reasons.map((x: string) => <li key={x}>{x}</li>)}</ul>
          <div className="mt-3 flex flex-wrap gap-1">
            {MANUAL_TAGS.map(([k, l]) => {
              const on = p.marketer_tags.includes(k);
              return <Button key={k} size="xs" variant={on ? "default" : "outline"} aria-pressed={on} disabled={act.busy}
                             onClick={() => save({ marketer_tags: on ? p.marketer_tags.filter((x: string) => x !== k) : [...p.marketer_tags, k] })}>{l}</Button>;
            })}
            <Button size="xs" variant={p.excluded ? "destructive" : "outline"} disabled={act.busy}
                    onClick={() => save(p.excluded ? { excluded: false } : { excluded: true, excluded_reason: prompt("Lý do loại (hàng cấm / giả / nguy hiểm…)") || "manual" })}>
              {p.excluded ? `Đã loại: ${p.excluded_reason || ""} — bỏ loại` : "Loại khỏi gợi ý"}
            </Button>
          </div>
        </Card>
      </div>

      <Card title="Ad Matches — quảng cáo trong kho SpyAds" description="Mạnh = URL / EAN; yếu = trùng từ trong tiêu đề, cần review. Không suy diễn doanh thu / ROAS từ creative.">
        {p.ad_matches.length ? p.ad_matches.map((m: any) => <AdMatchRow key={m.id} m={m} onReview={(s) => review("ad-matches", m.id, s)} busy={act.busy} hideProduct />)
          : <p className="text-sm text-muted-foreground">Chưa ghép được quảng cáo nào (chạy “match” ở Watchlists hoặc Data Sources).</p>}
      </Card>
      <Card title="Nguồn nhập B2B đã ghép">
        {p.supplier_matches.length ? p.supplier_matches.map((m: any) => (
          <div key={m.id} className="flex flex-wrap items-center gap-2 border-b py-2 text-sm last:border-0">
            <Badge variant="outline">{m.match_method} · {m.score}</Badge><Pill tone={m.review_status === "confirmed" ? "good" : m.review_status === "rejected" ? "bad" : "warn"}>{m.review_status}</Pill>
            {m.offer && <><span className="font-medium">{m.offer.title}</span><Price l={m.offer} /></>}
            <span className="ml-auto flex gap-1">
              <Button size="xs" variant="outline" disabled={act.busy} onClick={() => review("supplier-matches", m.id, "confirmed")}>Đúng</Button>
              <Button size="xs" variant="outline" disabled={act.busy} onClick={() => review("supplier-matches", m.id, "rejected")}>Sai</Button>
            </span>
          </div>
        )) : <p className="text-sm text-muted-foreground">Các offer B2B cùng EAN được gom thẳng vào bảng offer ở trên; ở đây là các ghép theo tiêu đề cần review.</p>}
      </Card>
      {p.signals.length > 0 && (
        <Card title="Tín hiệu đã quan sát (raw)" pad={false}>
          <Table><TableHeader><TableRow><TableHead>Loại</TableHead><TableHead>Giá trị</TableHead><TableHead>Nguồn</TableHead><TableHead>QG</TableHead><TableHead>Lúc</TableHead></TableRow></TableHeader>
            <TableBody>{p.signals.slice(-30).reverse().map((s: any, i: number) => (
              <TableRow key={i}><TableCell>{s.type}</TableCell><TableCell className="tnum">{s.value}</TableCell><TableCell>{s.source}</TableCell><TableCell>{s.country}</TableCell><TableCell>{fmt.dt(s.captured_at)}</TableCell></TableRow>
            ))}</TableBody></Table>
        </Card>
      )}
    </div>
  );
}

export default function EuProductDetail() {
  return <Suspense fallback={<EuLoading />}><Detail /></Suspense>;
}
