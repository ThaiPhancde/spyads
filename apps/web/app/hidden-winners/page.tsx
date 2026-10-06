"use client";
import Link from "next/link";
import { useState } from "react";
import { Card, Empty, Loading, PageHeader, ProductTable, RecBadge, ScoreBar } from "@/components/ui";
import { fmt, qs, useApi } from "@/lib/api";

const TAGS: [string, string][] = [
  ["fast_growing", "🔥 Fast growing"], ["rare", "💎 Rare"], ["low_competition", "📉 Low competition"],
  ["new_market", "🌍 New market"], ["good_margin", "💰 Good margin"], ["positive_feedback", "💬 Positive feedback"],
];

export default function HiddenWinners() {
  const [tag, setTag] = useState("");
  const { data, error } = useApi(`/products/hidden-winners${qs({ tag })}`);
  return (
    <div>
      <PageHeader title="Hidden Winners" subtitle="Sản phẩm ít người chạy nhưng tín hiệu tăng trưởng tốt — Rare Winner = Win × Rarity × Growth × Market fit × Margin" />
      <div className="flex flex-wrap gap-2 mb-4">
        <button className="btn" style={{ fontWeight: tag === "" ? 700 : 400 }} onClick={() => setTag("")}>Tất cả</button>
        {TAGS.map(([k, l]) => (
          <button key={k} className="btn" style={{ fontWeight: tag === k ? 700 : 400, background: tag === k ? "var(--surface-2)" : undefined }} onClick={() => setTag(k)}>
            {l} {data?.tags?.[k] ? <span className="text-muted ml-1">{data.tags[k]}</span> : null}
          </button>
        ))}
      </div>
      {!data ? <Loading error={error} /> : (
        <>
          {data.rows.length === 0 && <Card><Empty>Chưa có hidden winner với bộ lọc này.</Empty></Card>}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 mb-6">
            {data.rows.map((p: any) => (
              <Link key={p.id} href={`/products/${p.id}`} className="card p-4 block hover:shadow-sm">
                <div className="flex justify-between items-start gap-2">
                  <div>
                    <div className="font-semibold">{p.name}</div>
                    <div className="text-xs text-muted">{p.product_code} · {p.category} · {p.markets.join(", ")}</div>
                  </div>
                  <RecBadge rec={p.recommendation} />
                </div>
                <div className="flex items-baseline gap-2 mt-3">
                  <div className="text-3xl font-semibold tnum">{fmt.n(p.rare_winner_score)}</div>
                  <div className="text-xs text-muted">Rare Winner Score</div>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 mt-3 text-xs">
                  <div className="flex justify-between"><span className="text-muted">Advertisers</span><span className="tnum font-medium">{p.advertisers}</span></div>
                  <div className="flex justify-between"><span className="text-muted">Growth 7d</span><span className="tnum font-medium" style={{ color: "var(--good-text)" }}>{fmt.signedPct(p.growth_7d)}</span></div>
                  <div className="flex justify-between items-center"><span className="text-muted">Win</span><ScoreBar value={p.win_score} width={40} /></div>
                  <div className="flex justify-between items-center"><span className="text-muted">Rarity</span><ScoreBar value={p.rarity_score} width={40} /></div>
                  <div className="flex justify-between items-center"><span className="text-muted">Saturation</span><ScoreBar value={p.saturation_score} width={40} /></div>
                  <div className="flex justify-between items-center"><span className="text-muted">Opportunity</span><ScoreBar value={p.opportunity_score} width={40} /></div>
                </div>
                <div className="flex flex-wrap gap-1 mt-3">
                  {p.tags.map((t: string) => <span key={t} className="text-[11px] rounded px-1.5 py-0.5" style={{ background: "var(--surface-2)" }}>{TAGS.find(([k]) => k === t)?.[1] || t}</span>)}
                </div>
                <div className="text-[11px] text-ink2 mt-3">{p.win_label} · Confidence: {p.confidence_label} ({fmt.n(p.confidence_score)}%)</div>
              </Link>
            ))}
          </div>
          {data.near_misses.length > 0 && (
            <Card title="Sắp đủ điều kiện (near misses) — cần thêm dữ liệu / confidence" pad={false}>
              <ProductTable rows={data.near_misses} compact />
            </Card>
          )}
        </>
      )}
    </div>
  );
}
