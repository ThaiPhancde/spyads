"use client";
import Link from "next/link";
import { useState } from "react";
import { Card, Loading, PageHeader } from "@/components/ui";
import { fmt, qs, useApi } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";

const FRESH: Record<string, [string, string]> = {
  fresh: ["var(--good)", "Mới (<1h)"], recent: ["var(--series-1)", "Gần đây (<6h)"],
  stale: ["var(--warning)", "Cũ (<24h)"], unreliable: ["var(--critical)", "Không tin cậy (>24h)"],
};

function Trend({ t, g }: { t: string; g: number | null }) {
  const up = t.startsWith("↑") || t === "NEW";
  const down = t.startsWith("↓");
  return (
    <span className="tnum whitespace-nowrap" style={{ color: up ? "var(--good-text)" : down ? "var(--critical)" : "var(--text-secondary)" }}>
      {t}{g !== null && g !== undefined ? ` ${fmt.signedPct(g)}` : ""}
    </span>
  );
}

function Fresh({ f, at }: { f: string; at: string | null }) {
  const [c, l] = FRESH[f] || FRESH.unreliable;
  return (
    <span className="inline-flex items-center gap-1 text-xs whitespace-nowrap" title={at ? `Xác minh lần cuối: ${fmt.dt(at)}` : "Chưa xác minh"}>
      <span className="w-2 h-2 rounded-full" style={{ background: c }} />{l}
    </span>
  );
}

function Table({ rows, muted }: { rows: any[]; muted?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead><tr><th>Nước</th><th>Vùng</th><th>Ads đang chạy</th><th>Sellers</th><th>Sản phẩm</th><th>Ads mới</th><th>Xu hướng</th>
          <th>Ads mạnh</th><th>Mess / Ladi</th><th>Category nổi bật</th><th>Độ tươi</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.country} style={muted ? { opacity: 0.6 } : undefined}>
              <td className="font-medium">
                <Link className="hover:underline" href={`/product-radar?market=${r.country}`}>{r.country === "ALL" ? "🌍 Toàn cầu" : r.country}</Link>
              </td>
              <td className="text-xs text-ink2">{r.region || "—"}</td>
              <td className="tnum">{fmt.n(r.active_ads)}</td>
              <td className="tnum">{r.advertisers}</td>
              <td className="tnum">{r.products}</td>
              <td className="tnum">{r.new_ads} <span className="text-muted text-xs">/ {r.prev_new_ads}</span></td>
              <td><Trend t={r.trend} g={r.growth} /></td>
              <td className="tnum">{r.strong_ads}</td>
              <td className="tnum text-xs">{Math.round(r.mess_share * 100)}% / {Math.round(r.ladi_share * 100)}%</td>
              <td className="text-xs">{r.top_categories.join(", ")}</td>
              <td><Fresh f={r.freshness} at={r.last_verified} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MarketRadar() {
  const [window, setWindow] = useState(7);
  const [showOutside, setShowOutside] = useState(false);
  const team = useTeam();
  const { data, error, reload } = useApi(`/markets/live${qs({ window, funnel: team })}`);
  // live: refresh whenever new ads land or the liveness check finishes
  useEvents((e) => { if (["CONNECTOR_SYNCED", "SEARCH_DONE", "LIVENESS_CHECKED", "AD_STOPPED", "AD_REACTIVATED"].includes(e.type)) reload(); });
  if (!data) return <Loading error={error} />;
  return (
    <div>
      <PageHeader title="Market Radar"
                  subtitle={`Tính trực tiếp từ quảng cáo đang chạy · cập nhật ${fmt.dt(data.generated_at)}${team ? ` · chỉ funnel ${team === "mess" ? "MKT Mess" : "MKT Ladi"}` : ""}`}>
        <div className="flex rounded-lg border overflow-hidden" style={{ borderColor: "var(--border)" }}>
          {[7, 14, 30].map((w) => (
            <button key={w} onClick={() => setWindow(w)} className="text-xs px-3 py-1.5"
                    style={{ background: window === w ? "var(--series-1)" : "var(--surface-1)", color: window === w ? "#fff" : "var(--text-secondary)" }}>{w} ngày</button>
          ))}
        </div>
        <button className="btn text-xs" onClick={reload}>Làm mới</button>
      </PageHeader>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        {data.regions.map((r: any) => (
          <Link key={r.region} href={`/product-radar?market=${r.region}`} className="card px-4 py-3 block hover:shadow-sm">
            <div className="text-xs text-muted">{r.label}</div>
            <div className="text-2xl font-semibold tnum mt-0.5">{fmt.n(r.active_ads)}</div>
            <div className="text-xs text-ink2">ads đang chạy · {r.countries} nước</div>
            <div className="text-xs mt-1">+{r.new_ads} ads mới {window}d <Trend t={r.trend} g={r.growth} /></div>
          </Link>
        ))}
      </div>

      <Card title={`Theo nước — ads mới ${window} ngày gần nhất so với ${window} ngày trước đó (theo ngày bắt đầu chạy, mọi nền tảng quảng cáo)`} pad={false} className="mb-4">
        <Table rows={data.rows} />
      </Card>

      {data.outside.length > 0 && (
        <Card title={<button className="text-left" onClick={() => setShowOutside(!showOutside)}>
          {showOutside ? "▾" : "▸"} Ngoài thị trường mục tiêu ({data.outside.length} nước) — lưu lại nhưng không tính vào tổng hợp
        </button>} pad={false}>
          {showOutside && <Table rows={data.outside} muted />}
        </Card>
      )}
      <p className="text-xs text-muted mt-3">
        “Ads mạnh” = điểm độ mạnh ≥ 45 (chạy lâu · nhiều biến thể · nhiều nền tảng). Độ tươi = lần gần nhất app xác nhận lại với nguồn rằng quảng cáo còn chạy.
        Thị trường mục tiêu cấu hình bằng <code>TARGET_MARKETS</code> trong .env.
      </p>
    </div>
  );
}
