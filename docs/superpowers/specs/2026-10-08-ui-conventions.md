# ToolSpy web — quy ước UI khi migrate sang shadcn/ui

Nền đã xong (build xanh): Tailwind v4, `components/ui/*` (copy từ demotheme new-york-v4), layout Sidebar/Header/Dark mode,
`components/ui.tsx` (wrapper nghiệp vụ, API cũ giữ nguyên). Việc còn lại là nâng từng màn hình.

## Bắt buộc
- Next 14 / React 18 — không nâng. Không cài thêm dependency (đã có: radix-ui, lucide-react, cmdk, vaul, sonner, next-themes, recharts 3, embla, cva, clsx, tailwind-merge).
- Giữ nguyên: route, `lib/api.ts` (`api`, `useApi`, `usePaged`, `useAction`, `fmt`, `qs`), `lib/realtime.ts`, mọi path/param gọi API, text tiếng Việt, logic nghiệp vụ. Chỉ đổi phần trình bày.
- Component shadcn: import từ `@/components/ui/<name>` (button, badge, card, input, select, checkbox, switch, tabs, table, sheet, dialog, dropdown-menu, popover, tooltip, toggle-group, scroll-area, skeleton, separator, progress, alert, accordion, collapsible, hover-card, command, empty, label, textarea, native-select, kbd, chart, avatar, carousel, item, spinner). `cn` từ `@/lib/utils`. Icon: `lucide-react` (thay emoji/ký tự ⌕ ▶ ✓ … bằng icon; emoji trong label nghiệp vụ như 💬 MKT Mess có thể giữ).
- React 18 + Radix `asChild`: child của `*Trigger asChild` phải là component có forwardRef (`Button`, `Badge`, `Input`, `SidebarMenuButton`, `Link`, thẻ DOM). KHÔNG đặt function component tự viết làm child của `asChild`.
- Màu: dùng token Tailwind — `text-muted-foreground`, `text-ink2` (chữ phụ), `text-link`, `bg-muted`, `border`, `text-good-text`, `bg-good`, `text-critical`, `bg-critical`, `text-warning`, `text-serious`, `text-series-1`…`series-8`. Xoá `style={{ background: "var(--…)" }}` thay bằng class; chỉ giữ inline style cho giá trị động (width %, màu network từ API).
- Nút: `<Button variant="outline|default|ghost|secondary" size="sm|xs|icon-sm">`; thẻ `<Badge variant="outline|secondary">`; group chọn 1 → `ToggleGroup type="single" variant="outline" size="sm"`; `<select className="input">` → `Select` của shadcn (hoặc `NativeSelect` khi >15 option); checkbox → `Checkbox` + `Label`; bảng → `Table*`; ô nhập → `Input`.
- Xoá dần class legacy `.card .btn .input table.data` trong file mình phụ trách (globals.css vẫn giữ tạm cho file chưa migrate).
- Trạng thái: loading → `Loading` (Skeleton) / `Skeleton`; rỗng → `Empty`; lỗi → `Alert variant="destructive"`; thông báo hành động → `toast` từ `sonner` thay cho `setMsg` chữ nhỏ khi hợp lý (vẫn giữ msg inline nếu là trạng thái tiến trình).
- Responsive: mobile 1 cột, không tràn ngang; dùng `flex-wrap`, `min-w-0`, `truncate`.
- Dark mode: không hard-code màu trắng/đen (trừ `text-white` trên nền màu); `bg-black` cho video giữ.
- Kiểm tra: `cd apps/web && npx tsc --noEmit -p tsconfig.json` phải sạch. Không chạy `next build`, không khởi động server (dev server đang chạy sẵn ở :3000, API ở :8000 — có thể `curl http://127.0.0.1:3000/<route>` để chắc trang render 200).
- Không commit git. Không sửa file ngoài phạm vi được giao (file dùng chung do agent khác sở hữu — nếu cần thêm prop, thêm tương thích ngược và ghi rõ trong báo cáo).

## Mẫu tham khảo
- `components/ui.tsx` (Card/Stat/Pill/RecBadge/ProductTable đã viết lại trên shadcn) và `components/layout/*`.
- `demotheme/ui/apps/v4/registry/new-york-v4/blocks/dashboard-01/components/*` (section-cards, data-table, chart-area-interactive) và `demotheme/ui/apps/v4/registry/new-york-v4/examples/*` (ví dụ dùng từng component).
- Ads card nhiều video: giữ `CreativeMedia` (video chỉ tải khi Play), lưới `grid-cols-1 md:2 xl:3 2xl:4`, `LoadMore` (server-side paging) giữ nguyên.
