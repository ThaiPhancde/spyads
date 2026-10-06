"use client";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

// Categorical slots in fixed order (never cycled) — see globals.css tokens.
export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const axis = { stroke: "var(--axis)", tick: { fill: "var(--muted)", fontSize: 11 }, tickLine: false };
const tooltipStyle = {
  contentStyle: { background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, color: "var(--text-primary)" },
  labelStyle: { color: "var(--text-secondary)", marginBottom: 4 },
  cursor: { stroke: "var(--axis)", strokeWidth: 1 },
};

/** Time-series line chart. ≤3 series (validated all-pairs), one y-axis. */
export function TrendChart({ data, x = "date", series, height = 220, percent }: {
  data: any[]; x?: string; series: { key: string; label: string }[]; height?: number; percent?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, left: -8, bottom: 0 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={x} {...axis} tickFormatter={(d) => String(d).slice(5)} minTickGap={24} />
        <YAxis {...axis} axisLine={false} width={44} tickFormatter={(v) => (percent ? `${Math.round(v * 100)}%` : v)} />
        <Tooltip {...tooltipStyle} formatter={(v: any) => (percent && typeof v === "number" ? `${(v * 100).toFixed(1)}%` : v)} />
        {series.length > 1 && <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }} />}
        {series.slice(0, 3).map((s, i) => (
          <Line key={s.key} isAnimationActive={false} type="monotone" dataKey={s.key} name={s.label} stroke={SERIES[i]} strokeWidth={2} dot={false}
                activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Polarity stack (positive / neutral / negative): diverging blue ↔ red with a gray midpoint. */
export function SentimentStack({ data, x = "week", height = 200 }: { data: any[]; x?: string; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 12, left: -8, bottom: 0 }} barCategoryGap="20%">
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={x} {...axis} tickFormatter={(d) => String(d).slice(5)} />
        <YAxis {...axis} axisLine={false} width={44} />
        <Tooltip {...tooltipStyle} cursor={{ fill: "var(--surface-2)" }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar isAnimationActive={false} dataKey="positive" name="Positive" stackId="s" fill="var(--seq-450)" stroke="var(--surface-1)" strokeWidth={1} />
        <Bar isAnimationActive={false} dataKey="neutral" name="Neutral" stackId="s" fill="var(--axis)" stroke="var(--surface-1)" strokeWidth={1} />
        <Bar isAnimationActive={false} dataKey="negative" name="Negative" stackId="s" fill="var(--series-8)" stroke="var(--surface-1)" strokeWidth={1} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Horizontal magnitude list — single hue, direct labels, no legend needed. */
export function BarList({ rows, format = (v: number) => String(v), color = "var(--series-1)", max }: {
  rows: { label: string; value: number; hint?: string }[]; format?: (v: number) => string; color?: string; max?: number;
}) {
  const m = max ?? Math.max(1e-9, ...rows.map((r) => r.value));
  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div key={r.label} title={r.hint || `${r.label}: ${format(r.value)}`}>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-ink2">{r.label}</span>
            <span className="tnum text-ink font-medium">{format(r.value)}</span>
          </div>
          <div className="h-2 rounded-full" style={{ background: "var(--grid)" }}>
            <div className="h-2 rounded-full" style={{ width: `${Math.max(2, (r.value / m) * 100)}%`, background: color }} />
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
            <div className="h-5 rounded" style={{ background: "var(--surface-2)" }}>
              <div className="h-5 rounded" style={{ width: `${Math.max(1, (s.value / max) * 100)}%`, background: s.ok === false ? "var(--serious)" : "var(--seq-450)" }} />
            </div>
            <span className="tnum text-right">
              {s.value.toLocaleString()}
              {conv !== null && <span className="text-muted ml-1">{(conv * 100).toFixed(conv < 0.1 ? 1 : 0)}%</span>}
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
  if (!entries.length) return <div className="text-xs text-muted">—</div>;
  return (
    <div className="space-y-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[150px_1fr_80px] items-center gap-2 text-xs">
          <span className="text-ink2 truncate">{k.replace(/_/g, " ")}</span>
          {v.weight > 0 ? (
            <div className="h-1.5 rounded-full" style={{ background: "var(--grid)" }}>
              <div className="h-1.5 rounded-full" style={{ width: `${v.value}%`, background: "var(--series-1)" }} />
            </div>
          ) : <span />}
          <span className="tnum text-right">
            {v.weight > 0 ? <>{v.value.toFixed(0)} <span className="text-muted">×{Math.round(v.weight * 100)}%</span></> :
              <span style={{ color: v.value < 0 ? "var(--critical)" : "var(--muted)" }}>{v.value.toFixed(1)}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}
