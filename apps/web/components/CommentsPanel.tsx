"use client";
import { BarList, SentimentStack } from "@/components/charts";
import { Card, Empty } from "@/components/ui";
import { fmt } from "@/lib/api";

export function CommentsPanel({ c }: { c: any }) {
  if (!c.analysed) return <Card><Empty>Chưa có comment.</Empty></Card>;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <Card title={`${fmt.n(c.analysed)} comments analysed`}>
        <div className="space-y-2 text-sm">
          {[["Positive", c.overall.positive, "var(--seq-450)"], ["Neutral", c.overall.neutral, "var(--axis)"], ["Negative", c.overall.negative, "var(--series-8)"]].map(([l, v, col]) => (
            <div key={l as string} className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: col as string }} /><span className="flex-1">{l}</span><span className="tnum font-semibold">{fmt.pct(v, 0)}</span>
            </div>
          ))}
        </div>
        <div className="text-xs text-ink2 mt-4">Purchase intent: {Object.entries(c.purchase_intent).map(([k, v]) => `${k} ${v}`).join(" · ")} · Questions: {c.questions}</div>
        <div className="text-[11px] text-muted-foreground mt-1">Engine: {Object.keys(c.engines).join(", ")}</div>
      </Card>
      <Card title="Top positive"><BarList rows={c.top_positive.map((r: any) => ({ label: r.aspect, value: r.share }))} format={(v) => fmt.pct(v, 0)} /></Card>
      <Card title="Top complaints"><BarList rows={c.top_complaints.map((r: any) => ({ label: r.aspect, value: r.share }))} format={(v) => fmt.pct(v, 0)} color="var(--series-8)" /></Card>
      <Card title="Sentiment theo tuần" className="xl:col-span-2"><SentimentStack data={c.weekly} /></Card>
      <Card title="Ví dụ phàn nàn">
        <div className="space-y-2 text-xs max-h-56 overflow-y-auto">
          {Object.entries(c.examples).filter(([k]) => k.endsWith(":negative")).slice(0, 6).map(([k, v]) => (
            <div key={k}><div className="font-semibold">{k.split(":")[0]}</div>{(v as string[]).map((t) => <div key={t} className="text-ink2">“{t}”</div>)}</div>
          ))}
        </div>
      </Card>
    </div>
  );
}
