"use client";
import { useState } from "react";
import { SimpleSelect } from "@/components/simple-select";
import { GlobalImageSearch } from "@/components/visual/global-image-search";
import { Card, PageHeader, ScoreBar } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { EU_COUNTRIES, EuLoading, MANUAL_TAGS, NoData, ProductCard, ProductGrid } from "@/components/eu-market/shared";
import { qs, useApi } from "@/lib/api";

export default function EuRare() {
  const [country, setCountry] = useState("");
  const [tag, setTag] = useState("");
  const [minScore, setMinScore] = useState("40");
  const [conf, setConf] = useState("");
  const { data, error } = useApi<any>(`/eu-market/rare${qs({ country, tag, min_score: minScore, min_confidence: conf })}`);
  return (
    <div className="space-y-4">
      <PageHeader title="Rare Finds" subtitle="Mặt hàng ít tương đương trong phạm vi dữ liệu đã thu (rare_in_dataset) — không khẳng định hiếm trên toàn EU. Hàng cấm / giả / nguy hiểm bị loại khỏi gợi ý.">
        <SimpleSelect value={country} onChange={setCountry} placeholder="Mọi quốc gia" options={EU_COUNTRIES.map(([c, l]) => [c, `${c} · ${l}`])} />
        <SimpleSelect value={tag} onChange={setTag} placeholder="Mọi nhãn" options={MANUAL_TAGS} />
        <SimpleSelect value={minScore} onChange={setMinScore} options={[["20", "Điểm ≥ 20"], ["40", "Điểm ≥ 40"], ["60", "Điểm ≥ 60"]]} />
        <SimpleSelect value={conf} onChange={setConf} placeholder="Mọi độ tin cậy" options={[["medium", "Tin cậy ≥ medium"], ["high", "Tin cậy high"]]} />
        <GlobalImageSearch preset={{ sources: "eu_listing", mode: "similar_product" }} />
      </PageHeader>
      {!data ? <EuLoading error={error} /> : (
        <>
          <Card><p className="text-xs text-muted-foreground">{data.note} Kho hiện có {data.dataset_listings} listing — dưới 200 thì độ tin cậy là “low”.</p></Card>
          {data.items.length ? (
            <ProductGrid rows={data.items} render={(p) => (
              <ProductCard p={p} extra={
                <div className="space-y-1 text-xs">
                  <div className="flex items-center gap-2">Rarity <ScoreBar value={p.rarity.score} /><Badge variant="outline">{p.rarity.label}</Badge>
                    <span className="text-muted-foreground">tin cậy {p.rarity.confidence}</span></div>
                  <ul className="list-disc pl-4 text-muted-foreground">{p.rarity.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul>
                </div>} />
            )} />
          ) : <NoData>Chưa có sản phẩm đạt ngưỡng.</NoData>}
        </>
      )}
    </div>
  );
}
