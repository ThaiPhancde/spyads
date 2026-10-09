# ToolSpy web — redesign giao diện trên shadcn/ui (theo `demotheme`)

Ngày: 2026-10-08 · Phạm vi: `apps/web` (Next 14 · React 18). Backend FastAPI giữ nguyên.

## Mục tiêu
- Giao diện thống nhất theo shadcn/ui "new-york-v4" đúng như `demotheme/ui` (sidebar inset, card, badge, sheet, tabs, table, chart…).
- Không rebuild: giữ nguyên toàn bộ luồng dữ liệu (`lib/api.ts`, `lib/realtime.ts`, route, query string), nâng cấp từng màn hình.
- Ưu tiên: Thư viện quảng cáo (Ads Discovery + filters) → Tìm sản phẩm (multi-keyword) → Chi tiết sản phẩm / phân tích → Dashboard → Data & Connectors (Settings).

## Quyết định kỹ thuật
| Vấn đề | Quyết định | Lý do |
|---|---|---|
| Nguồn component | Copy từ `demotheme/ui/apps/v4/registry/new-york-v4/ui` vào `components/ui` | Đúng "demotheme"; không phụ thuộc CLI (shadcn CLI 4.x chỉ sinh CSS Tailwind v4) |
| Tailwind | Nâng 3.4 → 4.x (`@tailwindcss/postcss`) | Component v4 dùng `@theme`, `data-slot`, `oklch`; app nhỏ (3.7k dòng) nên migrate rẻ |
| React / Next | Giữ 18.3 / 14.2 | Theo yêu cầu; mọi dep (radix-ui, cmdk, vaul, sonner, lucide) đều hỗ trợ React 18 |
| `ref` trên React 18 | `Button`, `Input`, `Badge`, `Textarea` bọc `forwardRef` | Component v4 bỏ forwardRef (React 19). Radix `asChild` cần ref để định vị popover/tooltip |
| Theme | `next-themes` attribute=class, `.dark` variant | Giống demotheme; bỏ code toggle `data-theme` tự viết |
| Token màu | shadcn tokens (neutral) + giữ dataviz tokens `--series-*`, `--good/--warning/--critical`, `--seq-*` | Chart & badge nghiệp vụ vẫn dùng palette cũ |
| Toast realtime | `sonner` | Thay toast tự viết trong Sidebar |
| Bảng | shadcn `Table`; sort/filter vẫn qua API | TanStack Table (giai đoạn 4 trong mẫu) bỏ qua — dữ liệu đã phân trang/sort server-side |
| Lưới ads nhiều video | Giữ `usePaged` + `LoadMore` (IntersectionObserver) | Đã là server-side paging; virtualized list thêm khi >1k card trên 1 trang |

## Cấu trúc thư mục
```
apps/web/
  app/globals.css          # Tailwind v4 + tokens
  app/shadcn.css           # copy packages/shadcn/src/tailwind.css (variants, keyframes)
  components/ui/*          # shadcn (copy từ demotheme)
  components/layout/       # app-sidebar.tsx, site-header.tsx, theme-toggle.tsx, realtime-toasts.tsx
  components/ads/          # ad-card.tsx, ad-detail-sheet.tsx, listing-card.tsx
  components/filters/      # market-picker.tsx, network-chips.tsx, tag-input.tsx, funnel-toggle.tsx
  components/charts/       # trend-chart.tsx … (shadcn ChartContainer)
  components/ui.tsx        # wrapper nghiệp vụ (Card/Stat/Pill/RecBadge/ScoreBar/ProductTable) viết lại trên shadcn, GIỮ API cũ
  hooks/use-mobile.ts  lib/utils.ts (cn)
```
Route giữ nguyên (`/ads`, `/search`, `/products/[id]`, `/data`…) để không vỡ link/bookmark.

## Lộ trình
1. Nền: deps, Tailwind v4, tokens, `components/ui`, layout (Sidebar + Header + Dark mode), `ui.tsx` wrapper. `npm run build` xanh.
2. Ads Discovery: `/ads` filter bar (ToggleGroup/Select/Checkbox/Input), `AdCard` mới, Sheet chi tiết ad.
3. Tìm sản phẩm: TagInput đa từ khoá, filters, ProductMediaCard, Vault.
4. Chi tiết sản phẩm: Tabs/Sheet/Accordion/ScrollArea; Discovery/Experiment/Comments panel.
5. Dashboard + Market/Competitor/Products (Table) + chart theo `ChartContainer`.
6. Data & Connectors (Switch/Collapsible/Progress/Alert/Dialog) + các trang nhỏ còn lại.
7. Kiểm tra: `tsc`, `next build`, chụp màn hình các trang chính (Playwright), responsive mobile.
