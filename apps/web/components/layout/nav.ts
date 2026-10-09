import {
  Activity, ArrowRightLeft, Bell, Bot, Database, FlaskConical, Gem, Globe, LayoutGrid, type LucideIcon, MessageSquareText,
  PlaySquare, Radar, RefreshCw, Search, ShoppingCart, Table2, Truck, Users, Zap,
} from "lucide-react";

export type NavItem = { href: string; label: string; icon: LucideIcon; hint?: string };
export type NavGroup = { group: string; items: NavItem[] };

/** Single source of truth for the sidebar + the header title. Routes are unchanged from the pre-shadcn app. */
export const NAV: NavGroup[] = [
  { group: "Khám phá sản phẩm", items: [
    { href: "/search", label: "Tìm sản phẩm", icon: Search, hint: "Tìm trong kho hoặc quét trực tiếp mọi nguồn" },
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
  { group: "Nội bộ (Company Fit)", items: [
    { href: "/test-lab", label: "Test Lab", icon: FlaskConical },
    { href: "/logistics", label: "COD & Vận đơn", icon: Truck },
    { href: "/attribution", label: "Attribution", icon: ArrowRightLeft },
    { href: "/comments", label: "Comment Intelligence", icon: MessageSquareText },
    { href: "/learning", label: "Learning Loop", icon: RefreshCw },
  ]},
  { group: "Hệ thống", items: [
    { href: "/alerts", label: "Alerts", icon: Bell },
    { href: "/agent", label: "AI Agent", icon: Bot },
    { href: "/data", label: "Data & Connectors", icon: Database, hint: "Nguồn crawl, API, lịch đồng bộ, dung lượng" },
  ]},
];

export const isActive = (href: string, path: string) => (href === "/" ? path === "/" : path.startsWith(href));

export function titleFor(path: string): string {
  if (path.startsWith("/products/")) return "Chi tiết sản phẩm";
  for (const g of NAV) for (const it of g.items) if (isActive(it.href, path)) return it.label;
  return "ToolSpy";
}

