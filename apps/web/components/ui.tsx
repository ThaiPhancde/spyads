"use client";
import Link from "next/link";
import { ReactNode } from "react";
import { fmt } from "@/lib/api";

export function Card({ title, action, children, className = "", pad = true }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 px-4 pt-3.5 pb-2">
          <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
          {action}
        </header>
      )}
      <div className={pad ? "px-4 pb-4" : ""}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm text-ink2 mt-0.5">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

export function Stat({ label, value, sub, delta, deltaGood }: { label: string; value: ReactNode; sub?: ReactNode; delta?: string; deltaGood?: boolean }) {
  return (
    <div className="card px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
      {(sub || delta) && (
        <div className="text-xs mt-0.5 text-ink2">
          {delta && <span style={{ color: deltaGood === undefined ? "var(--text-secondary)" : deltaGood ? "var(--good-text)" : "var(--critical)" }} className="font-medium mr-1">{delta}</span>}
          {sub}
        </div>
      )}
    </div>
  );
}

const REC_STYLE: Record<string, { bg: string; icon: string }> = {
  SCALE: { bg: "var(--good)", icon: "▲" },
  TEST: { bg: "var(--series-1)", icon: "◆" },
  WATCH: { bg: "var(--muted)", icon: "◉" },
  HOLD: { bg: "var(--warning)", icon: "❚❚" },
  ITERATE: { bg: "var(--serious)", icon: "↻" },
  STOP: { bg: "var(--critical)", icon: "■" },
  TEST_NOW: { bg: "var(--good)", icon: "🔥" },
  SKIP: { bg: "var(--critical)", icon: "↷" },
  REVIEW: { bg: "var(--serious)", icon: "⚠" },
};

export function RecBadge({ rec }: { rec?: string | null }) {
  if (!rec) return <span className="text-muted">—</span>;
  const s = REC_STYLE[rec] || { bg: "var(--muted)", icon: "" };
  return (
    <span className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold border" style={{ borderColor: s.bg, color: "var(--text-primary)" }}>
      <span style={{ color: s.bg }}>{s.icon}</span>
      {rec.replace("_", " ")}
    </span>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "info" }) {
  const c = { neutral: "var(--muted)", good: "var(--good)", warn: "var(--warning)", bad: "var(--critical)", info: "var(--series-1)" }[tone];
  return (
    <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium" style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}>
      <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: c }} />
      {children}
    </span>
  );
}

export const LIFECYCLE_TONE: Record<string, "neutral" | "good" | "warn" | "bad" | "info"> = {
  DISCOVERED: "neutral", WATCHLIST: "neutral", CANDIDATE: "info", TESTING: "info", FAIL: "bad", HOLD: "warn",
  WIN: "good", SCALE: "good", SATURATED: "warn", RETIRE: "neutral",
};

export function ScoreBar({ value, width = 64, label }: { value?: number | null; width?: number; label?: boolean }) {
  if (value === null || value === undefined) return <span className="text-muted">—</span>;
  const v = Math.max(0, Math.min(100, value));
  return (
    <span className="inline-flex items-center gap-2 tnum">
      <span className="relative inline-block h-1.5 rounded-full" style={{ width, background: "var(--grid)" }}>
        <span className="absolute left-0 top-0 h-1.5 rounded-full" style={{ width: `${v}%`, background: "var(--series-1)" }} />
      </span>
      {label !== false && <span className="text-xs w-6 text-right">{v.toFixed(0)}</span>}
    </span>
  );
}

export function ProductLink({ id, name, code }: { id: number; name: string; code?: string }) {
  return (
    <Link href={`/products/${id}`} className="hover:underline">
      <div className="font-medium text-ink leading-tight">{name}</div>
      {code && <div className="text-[11px] text-muted">{code}</div>}
    </Link>
  );
}

export function Loading({ error }: { error?: string | null }) {
  if (error) return <div className="card p-4 text-sm" style={{ color: "var(--critical)" }}>Lỗi: {error} — kiểm tra API đang chạy.</div>;
  return <div className="text-sm text-muted p-6">Đang tải…</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-sm text-muted py-6 text-center">{children}</div>;
}

export function SeverityIcon({ s }: { s: string }) {
  const m: Record<string, [string, string]> = { critical: ["var(--critical)", "⬤"], warn: ["var(--serious)", "▲"], info: ["var(--series-1)", "●"] };
  const [c, i] = m[s] || m.info;
  return <span style={{ color: c }} className="text-xs" title={s}>{i}</span>;
}

export function ProductTable({ rows, compact }: { rows: any[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Product</th>
            {!compact && <th>Market</th>}
            <th>Advertisers</th>
            <th>Active ads</th>
            <th>Growth 7d</th>
            <th>Win</th>
            {!compact && <th>Rarity</th>}
            <th>Saturation</th>
            <th>Confidence</th>
            <th>Opportunity</th>
            <th>Action</th>
            {!compact && <th>Lifecycle</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="min-w-[200px]"><ProductLink id={r.id} name={r.name} code={`${r.product_code} · ${r.category || ""}`} /></td>
              {!compact && <td className="text-xs">{(r.markets || []).join(", ") || r.country}</td>}
              <td className="tnum">{r.advertisers}</td>
              <td className="tnum">{r.active_ads}</td>
              <td className="tnum" style={{ color: r.growth_7d >= 0.5 ? "var(--good-text)" : undefined }}>{fmt.signedPct(r.growth_7d)}</td>
              <td><ScoreBar value={r.win_score} width={44} /></td>
              {!compact && <td><ScoreBar value={r.rarity_score} width={44} /></td>}
              <td className="text-xs whitespace-nowrap"><ScoreBar value={r.saturation_score} width={44} /><div className="text-muted">{r.saturation_state}</div></td>
              <td className="text-xs whitespace-nowrap">{fmt.n(r.confidence_score)}% <span className="text-muted">{r.confidence_label}</span></td>
              <td><ScoreBar value={r.opportunity_score} width={44} /></td>
              <td><RecBadge rec={r.recommendation} /></td>
              {!compact && <td><Pill tone={LIFECYCLE_TONE[r.lifecycle]}>{r.lifecycle}</Pill></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
