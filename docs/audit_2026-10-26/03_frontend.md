# 03 — Audit frontend (apps/web) — 2026-10-26

Phạm vi: `apps/web` (Next.js 14.2 app router): trang, component, `lib/api.ts`, nav, middleware, Dockerfile. Chỉ đọc code, không sửa app.
Cách kiểm tra:
- Lấy toàn bộ route backend qua `app.openapi()` (188 path), đối chiếu với 121 path mà frontend gọi bằng `api()/useApi()/usePaged()`.
- Gọi trực tiếp `app.workspace.auth.mkt_write()` + `SYSTEM` để xem mutation nào của UI bị chặn với tài khoản MKT.
- Chạy `node_modules/.bin/tsc --noEmit -p .`.

## 0. Tóm tắt

| Hạng mục | Kết quả |
|---|---|
| Typecheck `tsc --noEmit` | **Sạch** (exit 0, 0 dòng output) |
| Path frontend → endpoint backend | **121/121 có route thật.** 4 path cảnh báo đều là false positive do template string (`/auth/${mode}`, `/clusters/${…}`, `/clusters/candidates/${id}/${kind}`, `/eu-market/${kind}/${mid}/review`) |
| Auth token | Session là cookie HttpOnly. CSRF dùng double-submit cookie. `localStorage` chỉ giữ tên/team để hiển thị. Không thấy token bị lộ |
| Guard trang | `SessionShell` chặn mọi trang không public (`components/workspace/session.tsx:16-41`). `api()` chuyển về /login khi gặp 401 (`lib/api.ts:23`) |
| **Vấn đề lớn nhất** | **14 nút/mutation trên UI luôn trả 403 cho mọi tài khoản** (mọi tài khoản đều là MKT). Danh sách ở F1 |
| Ẩn EU | Phần lớn đã xong (đang là thay đổi chưa commit). Còn 3 chỗ lọt/mâu thuẫn, xem mục 2 |

## 1. Phát hiện

### F1 — HIGH — 14 nút trên UI gọi endpoint mà MKT bị chặn 403 (mâu thuẫn với chính sách chỉ có MKT)
Backend chỉ cho MKT ghi theo whitelist `mkt_write()` (`apps/api/app/workspace/auth.py:121-136`). Mọi request ghi khác bị chặn ở `auth.py:182-183`: "Thao tác hệ thống chỉ chạy bằng dịch vụ nội bộ". Đã kiểm bằng cách gọi trực tiếp `mkt_write` → **403** với:

| UI | Endpoint | File:line |
|---|---|---|
| Daily Pulse "Chạy pipeline hôm nay" | `POST /pipeline/run` | `app/page.tsx:21,32-33` |
| Competitor Radar "Theo dõi / Bỏ theo dõi" | `POST /competitor-radar/{id}/watch` | `app/competitor-radar/page.tsx:28,44` |
| Chi tiết SP "Quét giá & review đối thủ" | `POST /products/{id}/scan` | `app/products/[id]/page.tsx:91,107` |
| Chi tiết SP "AI giải thích…" | `POST /products/{id}/explain` | `app/products/[id]/page.tsx:178,223` |
| Chi tiết SP các nút Lifecycle | `POST /products/{id}/lifecycle` | `app/products/[id]/page.tsx:183,241` |
| Vault "Ghim" | `POST /creatives/{id}/pin` | `components/media.tsx:244` |
| Gộp / Tách / Hoàn tác (trang SP, dialog Gộp/Tách ở /products) | `POST /products/{id}/split`, `/products/merge`, `/products/{id}/merges/{eid}/undo` | `components/product-cluster.tsx:31,41,46` |
| Gom cụm "Hoàn tác" cho merge tay | `/products/{keep}/merges/{eid}/undo` | `app/clusters/page.tsx:49` |
| Sửa / xác nhận / hoàn tác tên SP | `PATCH /products/{id}/name`, `/name/confirm`, `/name/undo` | `components/product-name.tsx:45,46,80` |
| Tìm bằng ảnh "✓ cùng SP / ✗ khác SP" | `POST /products/image-matches/review` | `components/visual/results.tsx:129` |
| Watch ảnh (EU, nút đã ẩn) | `POST /search/images/watch` | `components/visual/watch-dialog.tsx:33`, `web-results.tsx:77` |

Hệ quả: MKT bấm nút thì thấy lỗi đỏ. Subtitle `/products` hứa "Gộp / Tách ở cuối mỗi dòng" (`app/products/page.tsx:31`) và retention hứa "pin what you want to keep" (`apps/api/app/retention.py:19-20`), nhưng cả hai đều không làm được.
Đề xuất (chọn theo nghiệp vụ, mỗi endpoint một dòng):
- (a) Nếu MKT được phép làm (gộp/tách/tên/review ảnh/pin/vote-like), thêm endpoint đó vào `mkt_write`. Attribution lấy từ `db.info["user_id"]` như `routers/clusters.py:86`, không lấy `body.user` (`main.py:549,584,598,624` đang tin `body.user` do client gửi).
- (b) Nếu là việc hệ thống (pipeline/run, lifecycle, explain tốn LLM, scan), ẩn nút trên UI.
Không cần cơ chế role mới.

### F2 — HIGH — Hai khái niệm "theo dõi đối thủ" chồng nhau: /competitors (theo từng user) và /competitor-radar (cờ global, bị 403)
- `/competitors` = "Đối thủ của tôi", follow riêng từng user qua `/api/competitors/{id}/follow` (`app/competitors/page.tsx:16`).
- `/competitor-radar` dùng cờ **global** `Advertiser.is_competitor_watched` (`apps/api/app/main.py:773-779`, `models.py:121`). Bộ lọc "Chỉ đối thủ đang theo dõi" và stat "Đang theo dõi" (`competitor-radar/page.tsx:37-38,68`) đọc cờ global này, không phải follow của user. Nút "Theo dõi" lại bị 403 (F1).
- Nav đặt 2 mục cùng icon `Users` ở 2 nhóm khác nhau (`components/layout/nav.ts:15,40`).

Đề xuất: Radar chỉ giữ phần "ai đang scale". Nút "Theo dõi" gọi `/competitors/{id}/follow` (đã nằm trong whitelist), bộ lọc `watched_only` đọc follow của user. Bỏ cờ global khỏi UI.

### F3 — HIGH — Vault quảng cáo "lưu vĩnh viễn", backend xoá video sau 1 ngày
- `app/vault/page.tsx:51`: "Mọi video / ảnh quảng cáo đã thu thập — lưu vĩnh viễn trong kho". Nav hint: "Video / ảnh đã lưu trong kho" (`nav.ts:30`).
- `apps/api/app/retention.py:5-8,36`: `VIDEO_KEEP_DAYS=1`, ảnh 30 ngày. Cách duy nhất để giữ là pin, nhưng pin bị 403 với MKT (F1).
- Cũng lệch với quyết định "không lưu video hàng loạt, chỉ metadata + thumbnail".
- `/ads` cũng hứa "kèm video/ảnh tải về được" (`app/ads/page.tsx:63`).

Đề xuất: sửa copy ("thumbnail + metadata giữ lâu; video chỉ giữ trong ngày, mở link gốc"). Mở pin cho MKT (thêm `/api/creatives/\d+/pin` vào `mkt_write`) hoặc bỏ nút Ghim.

### F4 — MEDIUM — Quá nhiều trang khám phá sản phẩm làm cùng một việc, nhãn và định nghĩa mâu thuẫn
Có 6 bề mặt liệt kê sản phẩm:

| Route | Nhãn nav | H1 trong trang | API |
|---|---|---|---|
| `/search` | Tìm sản phẩm | Tìm sản phẩm | `/search` |
| `/products` | **Xếp hạng sản phẩm** | **Product Explorer** | `/products` (limit 300, không phân trang) |
| `/product-radar` | Product Radar | Product Radar | `/radar/products` |
| `/radar` | Product Discovery | Product Discovery (13 tab) | `/discovery/tab/*` |
| `/hidden-winners` | Hidden Winners | Hidden Winners | `/products/hidden-winners` |
| `/markets` | Market Radar | Market Radar | `/markets/live` → link sang `/product-radar` |

Mâu thuẫn cụ thể:
- Tab "hidden" ("Hidden Products") của Discovery dùng định nghĩa `advertiser_count ≤ 10 && wave ≥ 45` (`apps/api/app/routers/intel.py:888,921`). Trang Hidden Winners dùng `is_hidden_winner` + status CONFIRMED (`app/hidden-winners/page.tsx:21,28`). Hai tên gần giống nhau nhưng là hai tập khác nhau.
- Tab "favorites"/"saved" của Discovery (`app/radar/page.tsx:14`, `intel.py:917-931`) lặp lại `/workspace/library` (Thư viện của tôi).
- `/products` ghi "`${data.total} sản phẩm`" nhưng chỉ render tối đa 300 dòng, không có phân trang (`app/products/page.tsx:24,47`).
- EU module cũng có một trang "Product Explorer" (`nav.ts:45`).

Đề xuất: gom về 3 trang: Tìm (search), Radar (product-radar có tab market/window, nhận các tab của discovery), Hidden Winners. `/products` đổi thành bảng admin-lite có `Paging` (component đã có, `components/paging.tsx`). Bỏ tab favorites/saved khỏi Discovery.

### F5 — MEDIUM — Header và trang hiển thị hai tiêu đề `<h1>` khác nhau
`SiteHeader` render `<h1>{titleFor(path)}</h1>` từ nhãn nav (`components/layout/site-header.tsx:19`), còn mỗi trang lại render `<h1>` riêng qua `PageHeader` (`components/ui.tsx:37`). Kết quả: mỗi trang có 2 h1 (lỗi a11y), và text hai bên thường mâu thuẫn:
- `/products`: "Xếp hạng sản phẩm" vs "Product Explorer"
- `/marketplace`: "Nguồn hàng & Store" (`nav.ts:29`) vs "Nguồn hàng Trung Quốc" (`app/marketplace/page.tsx:93`)
- `/workspace/library`: "Ads của tôi" (`nav.ts:16`) vs "Thư viện của tôi — Sản phẩm và Ads" (`app/workspace/library/page.tsx:56`; dashboard cũng gọi là "Thư viện của tôi", `app/workspace/page.tsx:24`)
- `/`: "Daily Pulse" vs "Today — …"

Đề xuất: header đổi thành `<div>` hoặc breadcrumb, sửa nhãn nav cho khớp H1 của trang.

### F6 — MEDIUM — Ẩn EU chưa trọn: listing đã lưu từ Google Lens biến mất khỏi kết quả, compare-prices vẫn trả listing EU
- Nút "Lưu" trên kết quả web (`components/visual/web-results.tsx:63-65`, tooltip "Lưu vào SpyAds… ảnh vào chỉ mục nội bộ") ghi vào bảng **EUListing** (`apps/api/app/visual/lens.py:598-625`). Nhưng khi EU ẩn, trang ép `sources=ad,listing` (`app/visual/page.tsx:155`, `KINDS` ở `components/visual/results.tsx:37`), nên listing vừa lưu không bao giờ hiện trong "Trong kho". `KIND_LABEL` lại đặt nhãn `eu_listing: "Listing đã lưu"` (`results.tsx:38`), tức là có ý định cho hiện. Đây là mâu thuẫn.
- `loadPrices` không gửi `sources` cho `/products/compare-prices-by-image` (`app/visual/page.tsx:137-140`), dù backend có nhận param này (`visual/routes.py:320`). Vì vậy bảng so sánh giá vẫn có offer EU (idealo/Etsy/…) với nhãn nguồn EU.

Đề xuất: tách nguồn `google_lens` (listing đã lưu) khỏi nguồn EU connector. Hoặc đơn giản hơn: giữ `eu_listing` trong `KINDS` với nhãn "Listing đã lưu", để backend lọc `source != eu connectors` khi `EU_MARKET_ENABLED=0`. Gửi cùng bộ `sources` cho compare-prices.

### F7 — MEDIUM — Bật lại EU bằng `NEXT_PUBLIC_EU_MARKET=1` sẽ không chạy cho MKT
`/api/eu-market/` nằm trong `SYSTEM` (`apps/api/app/workspace/auth.py:31`, chặn ở `:180-181`). Mọi trang EU sẽ hiện 403 cho mọi tài khoản, kể cả khi bật flag. Màn hình "EU Market đang tạm ẩn" còn hướng dẫn MKT bật biến môi trường (`app/eu-market/layout.tsx:9`), không nên lộ cấu hình cho người dùng. Ghi chú này dành cho lúc user muốn bật lại EU. Hiện tại EU vẫn ẩn, đúng quyết định.

### F8 — MEDIUM (ops) — 167 file build cũ đang bị track trong git (~114 MB trên đĩa)
`git ls-files apps/web | grep .next-(library|search)-test` → 167 file (`.next-library-test` 58M, `.next-search-test` 56M). `.gitignore` chỉ có `.next/`, `.next-workspace-test/`, `.next-validation/`. `apps/web/tsconfig.tsbuildinfo` cũng bị track (đang hiện M trong git status). Thư mục `apps/web` còn 8 file `*.log`.
Đề xuất: `git rm -r --cached apps/web/.next-*-test apps/web/tsconfig.tsbuildinfo`, thêm `.next-*/`, `*.tsbuildinfo`, `*.log` vào `.gitignore`.

### F9 — LOW (security) — Dockerfile web nhúng `ADMIN_TOKEN` vào image mà không dùng
`apps/web/Dockerfile:14-16` khai `ARG ADMIN_TOKEN` + `ENV ADMIN_TOKEN=…` ở stage runtime. Không file nào của web đọc biến này (grep rỗng). `middleware.js:9` còn chủ động xoá header `x-admin-token`. Secret service lộ qua `docker inspect`/`history`. Đề xuất: xoá 2 dòng đó.

### F10 — LOW — /agent và /data là trang chết với mọi tài khoản
Nav đánh dấu `admin: true` nên ẩn khỏi sidebar (`nav.ts:57-58`, `app-sidebar.tsx:45`), nhưng route vẫn mở được bằng URL:
- `/agent` gọi `POST /agent/ask`, nằm trong SYSTEM → 403.
- `/data` gọi `/storage/usage` (SYSTEM → 403) và mọi `/collector/*` ghi (SYSTEM → 403). `useApi("/storage/usage")` bỏ qua `error` (`app/data/page.tsx:160`) nên khối đó loading mãi.

Vì nhóm "Hệ thống" chỉ có 2 item `admin`, sidebar vẫn render **label "Hệ thống" rỗng**: `NAV.filter(g => !g.hidden)` chỉ lọc group, item bị lọc sau (`app-sidebar.tsx:39,45`).
Đề xuất: đặt `hidden: true` cho group "Hệ thống", hoặc lọc group khi không còn item. Trang /agent và /data: render thông báo "chỉ dịch vụ nội bộ", hoặc xoá (CLI đã thay thế).

### F11 — LOW — useApi bỏ qua lỗi → skeleton vô hạn; thông báo lỗi gây hiểu nhầm
- `app/radar/page.tsx:25-26` (RadarChart) và `app/products/[id]/page.tsx:29` bỏ `error`, nên lỗi API hiện thành loading mãi.
- `Loading` luôn ghi "— kiểm tra API đang chạy." (`components/ui.tsx:130`) kể cả khi lỗi là 403 phân quyền.

Đề xuất: truyền `error` vào `<Loading error>`, và đổi câu thông báo theo status (401/403/5xx).

### F12 — LOW (a11y) — Hàng bảng click được nhưng không truy cập được bằng bàn phím
`app/competitor-radar/page.tsx:76`: `<TableRow onClick>` không có `tabIndex`/`onKeyDown`/role, chỉ dùng chuột được. Các trang EU cũng vậy (đang ẩn). Đề xuất: đặt `<button>`/`<Link>` trong ô tên advertiser.
Điểm tốt: không có `<img>` thiếu `alt` (15/15 có alt), nút icon-only đều có `aria-label` (vd `global-image-search.tsx:55`, `product-name.tsx:66-67`), form auth/account dùng `<label>` bọc input.

### F13 — LOW — Payload và import thừa
- Client gửi `user: getUser().name` (lấy từ localStorage, `lib/realtime.ts:60-63`) trong body cluster/merge/name (`app/clusters/page.tsx:41,49`, `components/product-cluster.tsx:31,41,46`, `product-name.tsx:45,80`). `routers/clusters.py:86,99,116` bỏ qua giá trị này (đúng). Nhưng `main.py:549,584,598,624` vẫn tin `body.user` nếu sau này mở whitelist (F1). Xoá field ở client và ở schema.
- `app/layout.tsx:4-7` import `AppSidebar`, `RealtimeToasts`, `SiteHeader`, `SidebarInset/Provider` nhưng không dùng.
- `components/layout/realtime-toasts.tsx:6,11-12` và `lib/realtime.ts:21-22` còn các event COD/order (`REFUSAL_SPIKE`, `DELIVERY_RATE_DROP`, `ORDER_*`). Backend chỉ khai báo trong `apps/api/app/events.py:20`, không thấy chỗ nào emit. Đây là code BI cũ, xoá được.

## 2. EU Market — mọi entry point (trạng thái sau thay đổi chưa commit)

| # | Chỗ | Trạng thái |
|---|---|---|
| 1 | `components/layout/nav.ts:8-9,42-55`: group "EU Market" `hidden: !EU_VISIBLE` | Đã ẩn |
| 2 | `app/eu-market/layout.tsx`: mọi `/eu-market/*` hiện "đang tạm ẩn" | Đã chặn (nên bỏ câu hướng dẫn env, F7) |
| 3 | `nav.ts:70` `titleFor` special-case `/eu-market/products/view` | Vô hại (chỉ khi vào URL trực tiếp) |
| 4 | `components/visual/results.tsx:37` `KINDS` bỏ `eu_listing` | Đã ẩn, nhưng gây F6 |
| 5 | `results.tsx:149-150,248`: link "EU" / lịch sử giá EU | Đã ẩn |
| 6 | `results.tsx:287` IndexCoverage "listing EU" | Đã ẩn |
| 7 | `results.tsx:29` mode "Tìm nguồn hàng" bỏ "B2B EU" | Đã sửa copy |
| 8 | `components/visual/web-results.tsx:113` link SP EU, `:116` nút Watch | Đã ẩn |
| 9 | `web-results.tsx:79` toast "xem EU Market › Watchlists" | Không tới được (watch đã ẩn) |
| 10 | `app/visual/page.tsx:227` nút "Theo dõi" (WatchImageDialog) | Đã ẩn; `watch-dialog.tsx:47,51` còn copy EU nhưng không mở được |
| 11 | `app/visual/page.tsx:197` subtitle bỏ "EU" | Đã sửa |
| 12 | `app/visual/page.tsx:155` lọc nguồn "all" bỏ EU | Đã ẩn; **compare-prices chưa lọc** (F6) |
| 13 | `web-results.tsx:63-65` "Lưu" → ghi EUListing | **Còn**: dữ liệu vào bảng EU, không hiện lại (F6) |
| 14 | `app/visual/page.tsx:233` "Đã lưu listing… (#eu_listing_id)" | Còn, chỉ là text; backend chỉ lưu khi `EU_MARKET_ENABLED` (`visual/routes.py:109-115`) |
| 15 | Region "EU = Châu Âu + UK" (`MarketPicker.tsx:9,45`, `competitor-radar/page.tsx:16`), "Reach EU" (`ad-card.tsx:62`, `ad-detail-sheet.tsx:121`), "TikTok Ad Library (EU/UK)" (`ads/page.tsx:63`) | **Giữ**: đây là thị trường/nguồn ads, không phải module EU Market |
| 16 | `components/eu-market/*`, `app/eu-market/**` | Giữ code (theo quyết định), không còn import từ ngoài module |

## 3. Trang không có trong nav
- `/workspace/activity`: có link từ dashboard (`app/workspace/page.tsx:24`), OK.
- `/products/[id]`, `/competitors/[id]`: trang chi tiết, OK.
- `/agent`, `/data`: chết (F10).
- `/login`, `/register`, `/forgot-password`, `/reset-password`: public, OK.

## 4. Issue cũ — còn mở không?
| Nguồn | Issue | Hiện tại |
|---|---|---|
| AUDIT_2026-10-07 H11 | Poller giữ closure `reload` cũ | **Đã sửa**: `reloadRef` (`app/search/page.tsx:170-171,184`) |
| AUDIT_2026-10-07 H12 | `reload()` vượt `le=200` → 422 | **Đã sửa**: clamp 200/300 (`lib/api.ts:164-169`) |
| AUDIT_2026-10-07 §4 | `realtime.ts` thiếu LIVENESS_CHECKED/AD_STOPPED/AD_REACTIVATED | **Đã sửa** (`lib/realtime.ts:24`) |
| AUDIT_2026-10-07 §4 | `HlsVideo.tsx` retry vô hạn | File không còn |
| AUDIT_2026-10-07 C4 | Apify chạy mặc định | **Đã sửa**: opt-in `mi_paid` (`app/search/page.tsx:134-136`) |
| TEST_REPORT #79/#116 | Không đăng nhập, tên MKT tự khai | **Đã sửa**: session HttpOnly + CSRF (`workspace/auth.py:108-183`, `session.tsx:31`). Tên localStorage giờ chỉ để hiển thị |
| TEST_REPORT #125 | ContentExclusion thiếu UI | **Đã có**: `/workspace/filters` (Whitelist/Blacklist theo user) |
| AUDIT_2026-10-07 §8 | Chưa chạy `next build` | Vẫn chưa kiểm trong audit này (chỉ chạy `tsc`, sạch) |

## 5. Đề xuất cho bản thiết kế (ngắn)
1. **Một bảng quyền duy nhất cho UI**: export `mkt_write` dạng danh sách sang frontend (vd `GET /api/auth/me` trả `can: [...]`), rồi ẩn nút không được phép. Hết lớp lỗi F1.
2. **Gọn IA**: Không gian của tôi (Dashboard, Thư viện, Đối thủ, Thông báo, Bộ lọc) · Khám phá (Tìm, Tìm bằng ảnh, Radar, Hidden Winners) · Dữ liệu (Ads, Nguồn hàng TQ) · Thị trường (Pulse, Market Radar, Gom cụm, Đặt tên). Bỏ `/products` khỏi nav hoặc gộp vào Radar. Bỏ Vault (hoặc gộp thành tab media của Ads), vì video không được giữ lâu.
3. **Theo dõi đối thủ chỉ một khái niệm** (follow theo user), Radar chỉ để khám phá.
4. **Copy khớp retention** (thumbnail giữ lâu, video trong ngày).
5. **Tách "listing đã lưu từ web" khỏi EU** trước khi tắt hẳn EU ở backend.
6. **Repo hygiene**: untrack build cũ, bỏ `ADMIN_TOKEN` khỏi web Dockerfile.
