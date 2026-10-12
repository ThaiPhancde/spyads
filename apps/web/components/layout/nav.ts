import {
  Activity, BadgeEuro, Bell, Bot, Camera, Database, Euro, Eye, Gauge, Gem, Globe, LayoutGrid, type LucideIcon, Megaphone,
  PlaySquare, PlugZap, Radar, Search, ShoppingCart, Sparkles, Store, Table2, TrendingUp, Truck, Users, Zap,
} from "lucide-react";

export type NavItem = { href: string; label: string; icon: LucideIcon; hint?: string; children?: NavItem[] };
export type NavGroup = { group: string; items: NavItem[] };

/** Single source of truth for the sidebar + the header title. Routes are unchanged from the pre-shadcn app. */
export const NAV: NavGroup[] = [
  { group: "Khám phá sản phẩm", items: [
    { href: "/search", label: "Tìm sản phẩm", icon: Search, hint: "Tìm trong kho hoặc quét trực tiếp mọi nguồn" },
    { href: "/visual", label: "Tìm bằng ảnh", icon: Camera, hint: "Ảnh trùng, sản phẩm giống, quảng cáo, nguồn hàng, so sánh giá từ một tấm ảnh" },
    { href: "/product-radar", label: "Product Radar", icon: Zap },
    { href: "/radar", label: "Product Discovery", icon: Radar },
    { href: "/hidden-winners", label: "Hidden Winners", icon: Gem },
  ]},
  { group: "Nguồn dữ liệu", items: [
    { href: "/ads", label: "Thư viện quảng cáo", icon: LayoutGrid, hint: "Mọi ad đã thu thập: Meta, TikTok, Snapchat" },
    { href: "/marketplace", label: "Nguồn hàng & Store", icon: ShoppingCart },
    { href: "/vault", label: "Creative Vault", icon: PlaySquare, hint: "Video / ảnh đã lưu trong kho" },
  ]},
  { group: "Thị trường", items: [
    { href: "/", label: "Daily Pulse", icon: Activity },
    { href: "/markets", label: "Market Radar", icon: Globe },
    { href: "/products", label: "Xếp hạng sản phẩm", icon: Table2 },
    { href: "/competitors", label: "Competitor Radar", icon: Users },
  ]},
  { group: "EU Market", items: [
    { href: "/eu-market", label: "EU Market", icon: Euro, hint: "Mua bán, xu hướng, giá, hàng hiếm, nguồn hàng châu Âu", children: [
      { href: "/eu-market", label: "Overview", icon: Gauge },
      { href: "/eu-market/products", label: "Product Explorer", icon: Search },
      { href: "/eu-market/trending", label: "Trending", icon: TrendingUp },
      { href: "/eu-market/prices", label: "Price Intelligence", icon: BadgeEuro },
      { href: "/eu-market/rare", label: "Rare Finds", icon: Sparkles },
      { href: "/eu-market/suppliers", label: "Wholesale & Suppliers", icon: Truck },
      { href: "/eu-market/shops", label: "Competitor Shops", icon: Store },
      { href: "/eu-market/ad-matches", label: "Ad Matches", icon: Megaphone },
      { href: "/eu-market/watchlists", label: "Watchlists & Alerts", icon: Eye },
      { href: "/eu-market/sources", label: "Data Sources", icon: PlugZap },
    ]},
  ]},
  { group: "Hệ thống", items: [
    { href: "/alerts", label: "Alerts", icon: Bell },
    { href: "/agent", label: "AI Agent", icon: Bot },
    { href: "/data", label: "Data & Connectors", icon: Database, hint: "Nguồn crawl, API, lịch đồng bộ, dung lượng" },
  ]},
];

const strip = (p: string) => (p.length > 1 ? p.replace(/\/+$/, "") : p);  // trailingSlash: true → "/eu-market/"
export const isActive = (href: string, path: string) => (href === "/" ? path === "/" : strip(path).startsWith(href));
/** Sub-items: the section root ("/eu-market" = Overview) only matches exactly, the rest by prefix. */
export const isSubActive = (href: string, parent: string, path: string) =>
  href === parent ? strip(path) === href : isActive(href, path);

export function titleFor(path: string): string {
  if (path.startsWith("/products/")) return "Chi tiết sản phẩm";
  if (strip(path) === "/eu-market/products/view") return "EU Market · Chi tiết sản phẩm";
  for (const g of NAV) for (const it of g.items) {
    const sub = it.children?.find((c) => isSubActive(c.href, it.href, path));
    if (sub) return `${it.label} · ${sub.label}`;
    if (isActive(it.href, path)) return it.label;
  }
  return "ToolSpy";
}

