"use client";
import Link from "next/link";
import { Funnel } from "@/components/charts";
import { Card, Pill } from "@/components/ui";
import { fmt } from "@/lib/api";

const STATUS_TONE: Record<string, "good" | "info" | "bad" | "neutral" | "warn"> = { WIN: "good", PROMISING: "info", FAILED: "bad", WAITING: "neutral" };

export function ExperimentCard({ e, onEdit }: { e: any; onEdit?: () => void }) {
  const m = e.metrics;
  const stageOk = (name: string) => {
    const d = (e.diagnosis || []).find((x: any) => x.stage === name);
    return d ? d.ok : null;
  };
  return (
    <Card
      title={<span className="flex flex-wrap items-center gap-2">{e.name} <Pill tone={STATUS_TONE[e.status]}>{e.status}</Pill>{e.running && <Pill tone="info">running</Pill>}</span>}
      action={<span className="flex items-center gap-2">{onEdit && <button className="btn text-xs" onClick={onEdit}>Cập nhật số liệu</button>}
        {e.product_name && <Link className="text-xs text-accent" href={`/products/${e.product_id}`}>{e.product_name} →</Link>}</span>}
    >
      <div className="text-xs text-ink2 mb-3">
        {e.market} · {e.platform} · {e.creative_type} · angle {e.angle} · offer {e.offer} · {e.funnel} · từ {fmt.date(e.started_at)}{e.ended_at ? ` đến ${fmt.date(e.ended_at)}` : ""}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-1">
          <Funnel stages={[
            { label: "Clicks", value: e.clicks, ok: stageOk("CTR") },
            { label: "Landing views", value: e.landing_views },
            { label: "Add to cart", value: e.atc, ok: stageOk("Add to cart") },
            { label: "Checkout", value: e.checkout, ok: stageOk("Checkout") },
            { label: "Purchase", value: e.purchase, ok: stageOk("Purchase") },
            { label: "Confirmed", value: e.confirmed_orders, ok: stageOk("Confirmation") },
            { label: "Delivered", value: e.delivered, ok: stageOk("Delivery") },
          ]} />
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs content-start">
          {[["Impressions", fmt.n(e.impressions)], ["Spend", fmt.money(e.spend)], ["Revenue", fmt.money(e.revenue)], ["ROAS", fmt.n(m.roas, 2)], ["CTR", fmt.pct(m.ctr, 2)], ["CPC", `$${fmt.n(m.cpc, 2)}`],
            ["CVR", fmt.pct(m.cvr, 2)], ["CPA", fmt.money(m.cpa)], ["Refused", e.refused], ["Returned", e.returned], ["Delivery", fmt.pct(m.delivery_rate)],
            ["Refusal", fmt.pct(m.refusal_rate)], ["Ad rejected", `${e.ads_rejected}/${e.ads_submitted}`]].map(([l, v]) => (
            <div key={l as string} className="rounded-lg px-2 py-1.5" style={{ background: "var(--muted)" }}>
              <div className="text-muted">{l}</div><div className="font-semibold tnum text-sm">{v as any}</div>
            </div>
          ))}
        </div>
        <div>
          <div className="text-xs text-muted mb-1">Phân tích funnel</div>
          <div className="text-sm font-semibold mb-2">Decision: {e.decision || "—"}</div>
          {e.failure_types.length > 0 && <div className="flex flex-wrap gap-1 mb-2">{e.failure_types.map((f: string) => <Pill key={f} tone="bad">{f}</Pill>)}</div>}
          <ul className="space-y-1 text-xs">
            {(e.diagnosis || []).map((d: any, i: number) => (
              <li key={i} className="flex gap-1.5">
                <span style={{ color: d.ok ? "var(--good)" : "var(--critical)" }}>{d.ok ? "✓" : "✗"}</span>
                <span><b>{d.stage}</b> {d.value !== null && d.stage !== "Data" ? fmt.pct(d.value, 1) : ""} <span className="text-muted">(bench {d.stage === "Data" ? d.benchmark : fmt.pct(d.benchmark, 1)})</span>{!d.ok && d.note ? <span className="text-ink2"> — {d.note}</span> : null}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}
