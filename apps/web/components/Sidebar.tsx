"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { getUser, LiveEvent, setUser, useEvents, useLiveStatus } from "@/lib/realtime";

const NAV = [
  { group: "Tìm & khám phá", items: [
    { href: "/search", label: "Tìm sản phẩm", icon: "⌕" },
    { href: "/product-radar", label: "Product Radar", icon: "⚡" },
    { href: "/ads", label: "Thư viện quảng cáo", icon: "▦" },
    { href: "/radar", label: "Product Discovery", icon: "◎" },
    { href: "/vault", label: "Creative Vault (video)", icon: "▶" },
    { href: "/hidden-winners", label: "Hidden Winners", icon: "◆" },
  ]},
  { group: "Thị trường", items: [
    { href: "/", label: "Daily Pulse", icon: "◉" },
    { href: "/markets", label: "Market Radar", icon: "◍" },
    { href: "/products", label: "Xếp hạng sản phẩm", icon: "▤" },
    { href: "/competitors", label: "Competitor Radar", icon: "◈" },
  ]},
  { group: "Nội bộ (Company Fit)", items: [
    { href: "/test-lab", label: "Test Lab", icon: "⚗" },
    { href: "/logistics", label: "COD & Vận đơn", icon: "⛟" },
    { href: "/attribution", label: "Attribution", icon: "⇢" },
    { href: "/comments", label: "Comment Intelligence", icon: "✎" },
    { href: "/learning", label: "Learning Loop", icon: "↻" },
  ]},
  { group: "Hệ thống", items: [
    { href: "/alerts", label: "Alerts", icon: "⚑" },
    { href: "/agent", label: "AI Agent", icon: "✦" },
    { href: "/data", label: "Data & Connectors", icon: "⇄" },
  ]},
];

const TOAST_TYPES = new Set(["ALERT", "DECISION_CHANGED", "REFUSAL_SPIKE", "DELIVERY_RATE_DROP", "SEARCH_DONE", "CONNECTOR_FAILED"]);

function toastText(e: LiveEvent): string {
  const d = e.data || {};
  if (e.type === "DECISION_CHANGED") return `${d.name}: ${d.from} → ${d.to}`;
  if (e.type === "REFUSAL_SPIKE") return `COD refusal tăng ${Math.round(d.from * 100)}% → ${Math.round(d.to * 100)}%`;
  if (e.type === "DELIVERY_RATE_DROP") return `Delivery giảm ${Math.round(d.from * 100)}% → ${Math.round(d.to * 100)}%`;
  if (e.type === "SEARCH_DONE") return d.error && !d.found ? `Tìm kiếm lỗi: ${d.error}` : `Tìm xong: ${d.found} ads, ${d.products} sản phẩm`;
  if (e.type === "CONNECTOR_FAILED") return `${d.connector}: ${d.error}`;
  return d.title || e.type;
}

export default function Sidebar() {
  const path = usePathname();
  const live = useLiveStatus();
  const [unread, setUnread] = useState<number>(0);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [team, setTeam] = useState("");
  const [toasts, setToasts] = useState<(LiveEvent & { k: number })[]>([]);
  useEffect(() => { const u = getUser(); setName(u.name); setTeam(u.team); }, []);
  useEffect(() => { api("/alerts?limit=1").then((d) => setUnread(d.unread)).catch(() => {}); }, [path]);
  useEvents((e) => {
    if (e.type === "ALERT") setUnread((n) => n + 1);
    if (!TOAST_TYPES.has(e.type)) return;
    const k = Date.now() + Math.random();
    setToasts((t) => [{ ...e, k }, ...t].slice(0, 4));
    setTimeout(() => setToasts((t) => t.filter((x) => x.k !== k)), 8000);
  });
  const toggleTheme = () => {
    const el = document.documentElement;
    const cur = el.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    el.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch {}
  };
  useEffect(() => {
    try { const t = localStorage.getItem("theme"); if (t) document.documentElement.setAttribute("data-theme", t); } catch {}
  }, []);
  useEffect(() => setOpen(false), [path]);

  return (
    <>
      <button className="md:hidden fixed top-3 right-3 z-30 btn" onClick={() => setOpen(!open)}>☰</button>
      <aside className={`${open ? "fixed inset-y-0 left-0 z-20" : "hidden"} md:sticky md:top-0 md:block h-screen w-60 shrink-0 border-r overflow-y-auto`}
             style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}>
        <div className="px-5 pt-5 pb-3">
          <div className="text-[15px] font-semibold tracking-tight">Market Intelligence OS</div>
          <div className="text-[11px] text-muted mt-0.5 flex items-center gap-1.5">
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: live === "live" ? "var(--good)" : live === "offline" ? "var(--critical)" : "var(--warning)" }} />
            {live === "live" ? "Realtime đang kết nối" : live === "offline" ? "Mất kết nối realtime" : "Đang kết nối…"}
          </div>
        </div>
        <div className="px-4 pb-3 space-y-1.5">
          <input className="input w-full text-xs" placeholder="Tên bạn (để vote / lưu)" value={name}
                 onChange={(e) => { setName(e.target.value); setUser(e.target.value, team); }} />
          <div className="flex rounded-lg border overflow-hidden text-[11px]" style={{ borderColor: "var(--border)" }}>
            {[["", "Tất cả"], ["mess", "MKT Mess"], ["ladi", "MKT Ladi"]].map(([k, l]) => (
              <button key={k} className="flex-1 py-1" onClick={() => { setTeam(k); setUser(name, k); }}
                      style={{ background: team === k ? "var(--series-1)" : "var(--surface-1)", color: team === k ? "#fff" : "var(--text-secondary)" }}>{l}</button>
            ))}
          </div>
          <div className="text-[10px] text-muted leading-snug">
            {team === "mess" ? "Đang lọc toàn app: chỉ quảng cáo chốt qua inbox / WhatsApp / Messenger." :
             team === "ladi" ? "Đang lọc toàn app: chỉ quảng cáo dẫn về landing page / website." :
             "Chọn team để lọc mọi trang theo funnel bạn chạy."}
          </div>
        </div>
        <nav className="px-3 pb-6">
          {NAV.map((g) => (
            <div key={g.group} className="mb-4">
              <div className="px-2 text-[11px] uppercase tracking-wider text-muted mb-1">{g.group}</div>
              {g.items.map((it) => {
                const active = it.href === "/" ? path === "/" : path.startsWith(it.href);
                return (
                  <Link key={it.href} href={it.href}
                        className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[13px]"
                        style={{ background: active ? "var(--surface-2)" : undefined, fontWeight: active ? 600 : 400, color: active ? "var(--text-primary)" : "var(--text-secondary)" }}>
                    <span className="w-4 text-center text-muted">{it.icon}</span>
                    <span className="flex-1">{it.label}</span>
                    {it.href === "/alerts" && unread > 0 && (
                      <span className="text-[10px] rounded-full px-1.5 py-0.5 font-semibold" style={{ background: "var(--critical)", color: "#fff" }}>{unread}</span>
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
          <button onClick={toggleTheme} className="btn w-full mt-2 text-xs">Đổi giao diện sáng / tối</button>
        </nav>
      </aside>
      <div className="fixed bottom-4 right-4 z-40 space-y-2 w-80 max-w-[calc(100vw-32px)]">
        {toasts.map((t) => (
          <Link key={t.k} href={t.product_id ? `/products/${t.product_id}` : "/alerts"} className="card block px-3 py-2 text-xs shadow-lg">
            <div className="font-semibold">{t.type.replace(/_/g, " ")}</div>
            <div className="text-ink2">{toastText(t)}</div>
          </Link>
        ))}
      </div>
    </>
  );
}
