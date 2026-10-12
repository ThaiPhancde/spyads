"use client";
import { useState } from "react";
import { SimpleSelect } from "@/components/simple-select";
import { Card, PageHeader, Pill, ScoreBar } from "@/components/ui";
import { Input } from "@/components/ui/input";
import { EU_COUNTRIES, EuLoading, NoData, ProductCard, ProductGrid } from "@/components/eu-market/shared";
import { qs, useApi } from "@/lib/api";

export default function EuTrending() {
  const [country, setCountry] = useState("");
  const [window, setWindow] = useState("30d");
  const [kw, setKw] = useState("");
  const [applied, setApplied] = useState("");
  const { data, error } = useApi<any>(`/eu-market/trending${qs({ country, window, keywords: applied })}`);
  return (
    <div className="space-y-4">
      <PageHeader title="Trending" subtitle="Estimated Trend Signal 0–100 từ dữ liệu đã thu (Kaufland / bol / Etsy / idealo / nguồn khác) — không phải số bán. Mỗi thẻ ghi tín hiệu, nguồn, confidence và cửa sổ quan sát; rank, review, favorites, badge không bao giờ được đổi thành đơn hàng.">
        <SimpleSelect value={country} onChange={setCountry} placeholder="Mọi quốc gia" options={EU_COUNTRIES.map(([c, l]) => [c, `${c} · ${l}`])} />
        <SimpleSelect value={window} onChange={setWindow} options={[["7d", "7 ngày"], ["30d", "30 ngày"], ["90d", "90 ngày"]]} />
        <form onSubmit={(e) => { e.preventDefault(); setApplied(kw); }}><Input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="Ngành / từ khoá (Enter)" className="h-8 w-56" /></form>
      </PageHeader>
      {!data ? <EuLoading error={error} /> : (
        <>
          <Card><p className="text-xs text-muted-foreground">Cách tính: {data.method}. Đã quét {data.scanned} sản phẩm.</p></Card>
          {data.items.length ? (
            <ProductGrid rows={data.items} render={(p) => (
              <ProductCard p={p} extra={
                <div className="space-y-1 text-xs">
                  <div className="flex flex-wrap items-center gap-2">{p.trend.label || "Trend"} <ScoreBar value={p.trend.score} />
                    <Pill tone={p.trend.confidence === "high" ? "good" : p.trend.confidence === "medium" ? "info" : "warn"}>confidence: {p.trend.confidence || "low"}</Pill>
                    {p.trend.insufficient_data ? <Pill tone="warn">insufficient_data · {p.trend.history_days} ngày lịch sử</Pill> : <Pill tone="good">đủ lịch sử</Pill>}
                  </div>
                  <div className="text-muted-foreground">Nguồn: {(p.trend.sources || []).join(", ") || "—"}{p.trend.observation_window && ` · cửa sổ ${p.trend.observation_window.from} → ${p.trend.observation_window.to}`}</div>
                  <ul className="list-disc pl-4 text-muted-foreground">
                    {Object.entries(p.trend.components).map(([k, c]: any) => <li key={k}>{k} = {String(c.value)} <span className="opacity-70">({c.source})</span></li>)}
                  </ul>
                  {p.trend.missing.length > 0 && <div className="text-muted-foreground">thiếu: {p.trend.missing.join(", ")}</div>}
                </div>} />
            )} />
          ) : <NoData>Chưa có sản phẩm để chấm xu hướng — cần listing + vài lần snapshot.</NoData>}
        </>
      )}
    </div>
  );
}
