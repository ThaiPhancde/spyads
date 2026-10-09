"use client";
import Link from "next/link";
import { ReactNode } from "react";
import { AlertCircle, ArrowUp, CircleDot, Diamond, Flame, SkipForward, TriangleAlert } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card as UiCard, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty as UiEmpty, EmptyDescription, EmptyHeader } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmt } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Business wrappers on top of components/ui. The props API is kept from the pre-shadcn app so pages migrate one at a time. */

export function Card({ title, action, children, className = "", pad = true, description }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; pad?: boolean; description?: ReactNode;
}) {
  return (
    <UiCard className={cn("gap-3 py-4", className)}>
      {(title || action) && (
        <CardHeader className="px-4">
          {title && <CardTitle className="text-[13px] font-semibold">{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
          {action && <CardAction>{action}</CardAction>}
        </CardHeader>
      )}
      <CardContent className={pad ? "px-4" : "px-0"}>{children}</CardContent>
    </UiCard>
  );
}

export function PageHeader({ title, subtitle, children }: { title: ReactNode; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-2 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 max-w-3xl text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

/** KPI tile (dashboard-01 section-cards): label on top, big tabular number, optional delta badge + footnote. */
export function Stat({ label, value, sub, delta, deltaGood, className }: {
  label: ReactNode; value: ReactNode; sub?: ReactNode; delta?: string; deltaGood?: boolean; className?: string;
}) {
  return (
    <UiCard className={cn("gap-1 py-4", className)}>
      <CardHeader className="px-4">
        <CardDescription className="text-xs">{label}</CardDescription>
        <CardTitle className="tnum text-2xl font-semibold">{value}</CardTitle>
        {delta && (
          <CardAction>
            <Badge variant="outline" className={cn("tnum", deltaGood === true && "text-good-text", deltaGood === false && "text-critical")}>{delta}</Badge>
          </CardAction>
        )}
      </CardHeader>
      {sub && <CardContent className="px-4 text-xs text-muted-foreground">{sub}</CardContent>}
    </UiCard>
  );
}

const REC_STYLE: Record<string, { color: string; icon: typeof ArrowUp }> = {
  TEST: { color: "text-series-1", icon: Diamond },
  WATCH: { color: "text-muted-foreground", icon: CircleDot },
  TEST_NOW: { color: "text-good", icon: Flame },
  SKIP: { color: "text-critical", icon: SkipForward },
  REVIEW: { color: "text-serious", icon: TriangleAlert },
};

export function RecBadge({ rec }: { rec?: string | null }) {
  if (!rec) return <span className="text-muted-foreground">—</span>;
  const s = REC_STYLE[rec] || { color: "text-muted-foreground", icon: CircleDot };
  return (
    <Badge variant="outline" className="gap-1 font-semibold">
      <s.icon className={s.color} />
      {rec.replace("_", " ")}
    </Badge>
  );
}

export type Tone = "neutral" | "good" | "warn" | "bad" | "info";
const DOT: Record<Tone, string> = { neutral: "bg-muted-foreground", good: "bg-good", warn: "bg-warning", bad: "bg-critical", info: "bg-series-1" };

export function Pill({ children, tone = "neutral", className }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <Badge variant="secondary" className={cn("gap-1.5 font-medium", className)}>
      <span className={cn("inline-block size-1.5 rounded-full", DOT[tone])} />
      {children}
    </Badge>
  );
}

export const LIFECYCLE_TONE: Record<string, Tone> = {
  DISCOVERED: "neutral", WATCHLIST: "neutral", CANDIDATE: "info", TESTING: "info", FAIL: "bad", HOLD: "warn",
  WIN: "good", SCALE: "good", SATURATED: "warn", RETIRE: "neutral",
};

export function ScoreBar({ value, width = 64, label, tone = "bg-series-1" }: { value?: number | null; width?: number; label?: boolean; tone?: string }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>;
  const v = Math.max(0, Math.min(100, value));
  return (
    <span className="tnum inline-flex items-center gap-2">
      <span className="relative inline-block h-1.5 overflow-hidden rounded-full bg-muted" style={{ width }}>
        <span className={cn("absolute top-0 left-0 h-1.5 rounded-full", tone)} style={{ width: `${v}%` }} />
      </span>
      {label !== false && <span className="w-6 text-right text-xs">{v.toFixed(0)}</span>}
    </span>
  );
}

export function ProductLink({ id, name, code }: { id: number; name: string; code?: string }) {
  return (
    <Link href={`/products/${id}`} className="hover:underline">
      <div className="leading-tight font-medium text-foreground">{name}</div>
      {code && <div className="text-[11px] text-muted-foreground">{code}</div>}
    </Link>
  );
}

/** Loading skeleton, or an error alert when the API call failed. */
export function Loading({ error, rows = 3 }: { error?: string | null; rows?: number }) {
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertCircle />
        <AlertTitle>Không tải được dữ liệu</AlertTitle>
        <AlertDescription>{error} — kiểm tra API đang chạy.</AlertDescription>
      </Alert>
    );
  }
  return (
    <div className="space-y-3 py-2" aria-busy="true" aria-label="Đang tải">
      <Skeleton className="h-8 w-1/3" />
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
    </div>
  );
}

export function Empty({ children, title }: { children: ReactNode; title?: ReactNode }) {
  return (
    <UiEmpty className="py-8">
      <EmptyHeader>
        {title && <div className="text-sm font-medium">{title}</div>}
        <EmptyDescription>{children}</EmptyDescription>
      </EmptyHeader>
    </UiEmpty>
  );
}

export function SeverityIcon({ s }: { s: string }) {
  const m: Record<string, string> = { critical: "bg-critical", warn: "bg-serious", info: "bg-series-1" };
  return <span className={cn("mt-1.5 inline-block size-2 shrink-0 rounded-full", m[s] || m.info)} title={s} />;
}

export function ProductTable({ rows, compact }: { rows: any[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Product</TableHead>
            {!compact && <TableHead>Market</TableHead>}
            <TableHead className="text-right">Advertisers</TableHead>
            <TableHead className="text-right">Active ads</TableHead>
            <TableHead className="text-right">Growth 7d</TableHead>
            <TableHead>Win</TableHead>
            {!compact && <TableHead>Rarity</TableHead>}
            <TableHead>Saturation</TableHead>
            <TableHead>Confidence</TableHead>
            <TableHead>Opportunity</TableHead>
            <TableHead>Action</TableHead>
            {!compact && <TableHead>Lifecycle</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id}>
              <TableCell className="min-w-[200px]"><ProductLink id={r.id} name={r.name} code={`${r.product_code} · ${r.category || ""}`} /></TableCell>
              {!compact && <TableCell className="text-xs">{(r.markets || []).join(", ") || r.country}</TableCell>}
              <TableCell className="tnum text-right">{r.advertisers}</TableCell>
              <TableCell className="tnum text-right">{r.active_ads}</TableCell>
              <TableCell className={cn("tnum text-right", r.growth_7d >= 0.5 && "text-good-text")}>{fmt.signedPct(r.growth_7d)}</TableCell>
              <TableCell><ScoreBar value={r.win_score} width={44} /></TableCell>
              {!compact && <TableCell><ScoreBar value={r.rarity_score} width={44} /></TableCell>}
              <TableCell className="text-xs whitespace-nowrap"><ScoreBar value={r.saturation_score} width={44} /><div className="text-muted-foreground">{r.saturation_state}</div></TableCell>
              <TableCell className="text-xs whitespace-nowrap">{fmt.n(r.confidence_score)}% <span className="text-muted-foreground">{r.confidence_label}</span></TableCell>
              <TableCell><ScoreBar value={r.opportunity_score} width={44} /></TableCell>
              <TableCell><RecBadge rec={r.recommendation} /></TableCell>
              {!compact && <TableCell><Pill tone={LIFECYCLE_TONE[r.lifecycle]}>{r.lifecycle}</Pill></TableCell>}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {rows.length === 0 && <Empty>Không có sản phẩm khớp bộ lọc.</Empty>}
    </div>
  );
}
