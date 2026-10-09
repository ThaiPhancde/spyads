"use client";
import { Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { cn } from "@/lib/utils";

// Categorical slots in fixed order (never cycled).
export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const axis = { tickLine: false, axisLine: false, tickMargin: 6, tick: { fontSize: 11 } };
const day = (d: unknown) => String(d).slice(5);

/** Time-series line chart. ≤3 series (validated all-pairs), one y-axis. */
export function TrendChart({ data, x = "date", series, height = 220, percent }: {
  data: any[]; x?: string; series: { key: string; label: string }[]; height?: number; percent?: boolean;
}) {
  const ss = series.slice(0, 3);
  const config = Object.fromEntries(ss.map((s, i) => [s.key, { label: s.label, color: SERIES[i] }])) satisfies ChartConfig;
  const pct = (v: any) => (percent && typeof v === "number" ? `${(v * 100).toFixed(1)}%` : v);
  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height }}>
      <LineChart data={data} margin={{ top: 8, right: 12, left: -8, bottom: 0 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey={x} {...axis} tickFormatter={day} minTickGap={24} />
        <YAxis {...axis} width={44} tickFormatter={(v) => (percent ? `${Math.round(v * 100)}%` : v)} />
        <ChartTooltip content={<ChartTooltipContent indicator="line" formatter={(v, name) => (
          <div className="flex flex-1 justify-between gap-3 leading-none"><span className="text-muted-foreground">{config[String(name)]?.label ?? name}</span><span className="tnum font-medium">{pct(v)}</span></div>
        )} />} />
        {ss.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
        {ss.map((s) => (
          <Line key={s.key} isAnimationActive={false} type="monotone" dataKey={s.key} name={s.key} stroke={`var(--color-${s.key})`} strokeWidth={2} dot={false}
                activeDot={{ r: 4 }} connectNulls />
        ))}
      </LineChart>
    </ChartContainer>
  );
}

const SENTIMENT = {
  positive: { label: "Positive", color: "var(--seq-450)" },
  neutral: { label: "Neutral", color: "var(--axis)" },
  negative: { label: "Negative", color: "var(--series-8)" },
} satisfies ChartConfig;

/** Polarity stack (positive / neutral / negative): diverging blue ↔ red with a gray midpoint. */
export function SentimentStack({ data, x = "week", height = 200 }: { data: any[]; x?: string; height?: number }) {
  return (
    <ChartContainer config={SENTIMENT} className="aspect-auto w-full" style={{ height }}>
      <BarChart data={data} margin={{ top: 8, right: 12, left: -8, bottom: 0 }} barCategoryGap="20%">
        <CartesianGrid vertical={false} />
        <XAxis dataKey={x} {...axis} tickFormatter={day} />
        <YAxis {...axis} width={44} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        {(["positive", "neutral", "negative"] as const).map((k, i) => (
          <Bar key={k} isAnimationActive={false} dataKey={k} stackId="s" fill={`var(--color-${k})`} radius={i === 2 ? [4, 4, 0, 0] : undefined} />
        ))}
      </BarChart>
    </ChartContainer>
  );
}

/** `color` may be a Tailwind bg class ("bg-serious") or a CSS colour (inline, for callers passing var(--…)). */
function fillProps(color?: string) {
  return !color ? { className: "bg-series-1" } : color.startsWith("bg-") ? { className: color } : { style: { background: color } };
}

/** Horizontal magnitude list — single hue, direct labels, no legend needed. */
export function BarList({ rows, format = (v: number) => String(v), color, max }: {
  rows: { label: string; value: number; hint?: string }[]; format?: (v: number) => string; color?: string; max?: number;
}) {
  const m = max ?? Math.max(1e-9, ...rows.map((r) => r.value));
  const fill = fillProps(color);
  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div key={r.label} title={r.hint || `${r.label}: ${format(r.value)}`}>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-ink2">{r.label}</span>
            <span className="tnum font-medium text-foreground">{format(r.value)}</span>
          </div>
          <div className="h-2 rounded-full bg-muted">
            <div className={cn("h-2 rounded-full", fill.className)} style={{ ...fill.style, width: `${Math.max(2, (r.value / m) * 100)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Funnel as ordinal single-hue bars with stage-to-stage conversion. */
export function Funnel({ stages }: { stages: { label: string; value: number; ok?: boolean | null; note?: string; of?: number }[] }) {
  const max = Math.max(1, ...stages.map((s) => s.value));
  return (
    <div className="space-y-1.5">
      {stages.map((s, i) => {
        const denom = s.of ?? (i > 0 ? stages[i - 1].value : 0);
        const conv = i > 0 && denom ? s.value / denom : null;
        return (
          <div key={s.label} className="grid grid-cols-[110px_1fr_70px] items-center gap-2 text-xs" title={s.note}>
            <span className="text-ink2">{s.label}</span>
            <div className="h-5 rounded bg-muted">
              <div className={cn("h-5 rounded", s.ok === false ? "bg-serious" : "bg-series-1")} style={{ width: `${Math.max(1, (s.value / max) * 100)}%` }} />
            </div>
            <span className="tnum text-right">
              {s.value.toLocaleString()}
              {conv !== null && <span className="ml-1 text-muted-foreground">{(conv * 100).toFixed(conv < 0.1 ? 1 : 0)}%</span>}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** Score breakdown — weighted components of a formula score. */
export function Breakdown({ parts }: { parts: Record<string, { weight: number; value: number }> }) {
  const entries = Object.entries(parts || {});
  if (!entries.length) return <div className="text-xs text-muted-foreground">—</div>;
  return (
    <div className="space-y-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[150px_1fr_80px] items-center gap-2 text-xs">
          <span className="truncate text-ink2">{k.replace(/_/g, " ")}</span>
          {v.weight > 0 ? (
            <div className="h-1.5 rounded-full bg-muted">
              <div className="h-1.5 rounded-full bg-series-1" style={{ width: `${v.value}%` }} />
            </div>
          ) : <span />}
          <span className="tnum text-right">
            {v.weight > 0 ? <>{v.value.toFixed(0)} <span className="text-muted-foreground">×{Math.round(v.weight * 100)}%</span></> :
              <span className={v.value < 0 ? "text-critical" : "text-muted-foreground"}>{v.value.toFixed(1)}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}
