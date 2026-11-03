# Audit backend `apps/api/app` — 26/10/2026

Phạm vi: toàn bộ routers, services, collector, ingest, engine/scoring, worker, workspace (auth), pricing, visual, engagement,
allocation / behavior / preferences / recommend / library / personal_search. Chỉ đọc code, không sửa app.

## 0. Kiểm tra đã chạy

| Kiểm tra | Kết quả |
|---|---|
| `.venv/Scripts/python -c "import app.main"` | OK. Đủ 202 operation trong OpenAPI. Có 1 cảnh báo: `Duplicate Operation ID serve_media_api_media__key__get` (`routers/intel.py:813`, `api_route` GET+HEAD) |
| Bảng route → lớp auth (dựng từ `app.openapi()` + `workspace/auth.py`) | 13 route ghi mà UI cho MKT bấm bị chặn (xem B1). Toàn bộ `/api/eu-market/*` là SYSTEM, nên MKT luôn bị 403 |
| Probe bằng TestClient trên SQLite tạm (đăng ký → đăng nhập MKT → gọi API) | `register 201` (tài khoản active ngay), `PATCH /api/products/1/name 403`, `POST .../split 403`, `.../merge 403`, `.../lifecycle 403`, `.../scan 403`, `competitor-radar/1/watch 403`, `creatives/1/pin 403`, `search/images/watch 403`, `agent/ask 403`, `eu-market/overview 403` |
| Secret trong git | `git log -- .env`: rỗng. `git grep` theo mẫu key (sk-, gsk_, AIza, token=…): rỗng. `.env` đã nằm trong `.gitignore` |
| Secret trong log (`apps/api/*.log`) | Không thấy `api_key=` / `token=`. httpx bị hạ xuống WARNING ở cả API (`main.py:117`) lẫn worker (`worker.py:15`) |
| SQL đọc trên PG live | Không chạy được: kết nối bị từ chối (password auth) với chuỗi tôi tự parse từ `.env`. Tôi không cố thêm. Các finding dưới đây dựa trên code |

## 1. Lớp auth hiện tại (tóm tắt)

- `SessionGuard` (`workspace/auth.py:186-205`) chặn mọi `/api/*`. Ngoại lệ: `PUBLIC` (`auth.py:26`), `MACHINE` (`auth.py:28`, tự kiểm token riêng), và service token `X-Admin-Token` (`auth.py:147-152`, so bằng hmac, không dùng được trên vùng private).
- GET: MKT nào đã đăng nhập cũng gọi được. Dữ liệu cá nhân (allocations, behavior, library, preferences, workspace, search jobs) đều lọc theo `user["id"]`. Đã kiểm `routers/allocations.py:76-96`, `routers/behavior.py:31-61`, `routers/library.py:19-28`, `workspace/routes.py:110-410`, `intel.py:694-731`. **Không thấy lỗ đọc chéo dữ liệu giữa các MKT.**
- Ghi: chỉ các path trong allow-list `mkt_write()` (`auth.py:105-124`). Mọi path khác trả 403 "Thao tác hệ thống chỉ chạy bằng dịch vụ nội bộ".
- CSRF dùng double-submit cộng hash phiên và kiểm Origin (`auth.py:164-178`). Next middleware xoá `x-admin-token` của trình duyệt (`apps/web/middleware.js:10`).
- Claim allocation dùng PK `product_allocation_locks.product_id`, chèn khoá theo thứ tự id (`allocation.py:146-154`). **Race claim kép đã được chặn đúng.**

## 2. Findings

### B1 — HIGH — Allow-list ghi cho MKT chặn chính các nút MKT dùng hằng ngày
- Bằng chứng: `auth.py:105-124` không có các path `/api/products/{id}/name`, `/name/confirm`, `/name/undo`, `/split`, `/merge`, `/merges/{eid}/undo`, `/lifecycle`, `/explain`, `/scan`, `/api/competitor-radar/{id}/watch`, `/api/creatives/{id}/pin`, `/api/products/image-matches/review`, `/api/search/images/watch`. `/api/agent/ask` còn rơi vào prefix SYSTEM `/api/agent/` (`auth.py:31`). Handler nằm ở `main.py:517-666`, `main.py:773`, `main.py:846`, `intel.py:1349`, `visual/routes.py:380`.
- UI đang gọi các route này: `components/product-name.tsx:45,80`, `components/product-cluster.tsx:31,41,46`, `app/products/[id]/page.tsx:91,178,183`, `app/competitor-radar/page.tsx:28`, `components/media.tsx:244`, `components/visual/results.tsx:129`, `components/visual/web-results.tsx:77`, `app/agent/page.tsx:37`, `app/page.tsx:21` (pipeline/run).
- Mâu thuẫn: MKT **được** accept/reject merge cụm (thay đổi toàn cục, `auth.py:107-108`), nhưng **không được** đặt tên, tách cụm hay hoàn tác merge của sản phẩm. Hàng đợi "Chưa xác định sản phẩm" (UNKNOWN), vốn dành cho MKT đặt tên, vì thế không dùng được.
- Đề xuất: chia rõ hai nhóm. (a) Hành động MKT: naming, split, merge-undo, scan, explain, pin, image-match review. Đưa vào `mkt_write`, lấy `user` từ `request.state.user` thay vì `body.user` tự khai (`main.py:574,590,616`). (b) Hành động hệ thống: pipeline/run, import csv/manual, agent/ask, lifecycle nếu muốn giữ. Giữ ở service token và **ẩn nút trên UI**.

### B2 — HIGH — Ai cũng tự đăng ký được và active ngay, trong khi app có lúc lộ ra Internet qua quick tunnel
- Bằng chứng: `workspace/accounts.py:58-70` tạo `User(role="MKT", status="active")`, không có invite, không giới hạn domain email, không xác minh email. Probe trả `register 201` rồi đăng nhập được ngay. `run_tunnel.cmd` mở `cloudflared tunnel --url http://localhost:3000` (URL trycloudflare công khai).
- Hệ quả: người lạ có URL sẽ đọc được toàn bộ catalogue, ads, giá, đối thủ. Họ cũng tiêu được quota trả phí dùng chung: `POST /api/prices/lookup` gọi `jobs.lookup_now(..., paid=True)` (`pricing/jobs.py:157-167`), `POST /api/engagement/lookup`, `POST /api/search/images/live` (SerpApi Lens), `POST /api/search/live` với `paid=true` hoặc `adapters` tuỳ ý (`intel.py:635,661-668`). Hiện chỉ có trần quota toàn cục, không có trần riêng cho từng người.
- Đề xuất: biến `REGISTRATION_MODE=closed|domain|invite`, mặc định `closed` (CLI `create-user` đã có ở `workspace/cli.py`). Có thể allow-list domain (`@levelupvn.com`) hoặc để `status="pending"`. Đặt trần/ngày cho từng user ở các endpoint trả phí.

### B3 — MEDIUM — Rate limit tính theo IP nên vừa dùng chung giữa mọi người vừa bị giả mạo được
- Bằng chứng: `auth.py:85-87` (`request.client.host`), dùng cho register/login/forgot/reset (`accounts.py:60,79,199,226`). Visual dùng bucket theo IP trong bộ nhớ process (`visual/routes.py:46-56`, 30 lượt/phút). Next rewrite (`apps/web/next.config.js` `rewrites`) khiến mọi request tới backend đều mang IP 127.0.0.1. Một người tìm ảnh 30 lần/phút là khoá luôn cả team, và 8 lượt đăng ký/15 phút cũng là trần chung. Trong khi đó `Dockerfile:10` chạy `--proxy-headers --forwarded-allow-ips='*'`, nên client nào cũng tự đặt được `X-Forwarded-For` để né limit. Điều này ngược với comment ở `auth.py:86`.
- Đề xuất: endpoint đã đăng nhập thì key theo `user_id`. `forwarded-allow-ips` chỉ để IP của Next/proxy. Bucket ảnh chuyển sang `rate_limit()` dùng DB như auth.

### B4 — MEDIUM — Fetch URL lấy từ dữ liệu bên thứ ba không chặn địa chỉ nội bộ (SSRF)
- Bằng chứng: `services/spy.py:112-115` (`curl_cffi ... allow_redirects=True`, không kiểm host) được scheduler spy và `/scan` gọi. `pricing/extract.py:34-54` chỉ kiểm host đầu và host cuối, còn các hop redirect ở giữa đã được request trước khi kiểm. `pricing/extract.py:84` (`shopify_json`) không kiểm gì. `media.py:81,295,394` và `creative_enrich.py:149` đều `follow_redirects=True` mà không kiểm. Landing URL và CDN URL do advertiser hoặc nguồn bên ngoài kiểm soát.
- Codebase đã có bản fetch đúng: `visual/service.py:124-160` kiểm public-IP ở mọi hop.
- Đề xuất: một hàm `safe_get()` dùng chung, tách từ `fetch_image` (kiểm từng hop, giới hạn byte), áp cho spy, pricing, media, creative_enrich.

### B5 — MEDIUM — Chạy hai process (API + worker) nhưng event bus chỉ sống trong một process
- Bằng chứng: `.env:109 DISABLE_SCHEDULER=1`. `events.py:1-5` ghi rõ "In-process event bus", fan-out vào queue của process hiện tại. Event worker phát ra (`realtime.py:97` CONNECTOR_SYNCED, `:476` STORAGE_ROLLOVER, `:488` LIVENESS_CHECKED, `media.py:328` CREATIVE_STORED từ hàng đợi định kỳ) không bao giờ tới SSE của API và không xoá cache `_cache` của API (`intel.py:46`). `GET /api/collector/scheduler` (`intel.py:1511-1519`) đọc `realtime._running/_last_*` trong RAM của API, nơi scheduler không chạy. Bug #10 trong `docs/TOOLSPY_FULL_TEST_REPORT.md:111` **vẫn còn mở**.
- Đề xuất: `publish()` gọi thêm `pg_notify('toolspy_events', json)`. Một thread `LISTEN` trong API đẩy vào `_fanout` và listeners. Worker ghi heartbeat (running jobs, last_*) vào một bảng 1 dòng để endpoint scheduler đọc.

### B6 — MEDIUM — Allocation độc quyền: trạng thái claimed/testing/tested không bao giờ hết hạn và không có trần cho mỗi MKT
- Bằng chứng: chỉ `reserved` hết hạn (`allocation.py:28-29,119-134`). Luồng `reserved→claimed` đi thẳng được (`allocation.py:23-24,210-216`), sau đó khoá giữ mãi. `claim()` không giới hạn số allocation đang active của một user (`allocation.py:163-196`), router cũng không có rate limit (`routers/allocations.py:38-42`). `cli.py lock` thu hồi session nhưng không release allocation (`workspace/cli.py:52-56`).
- Hệ quả: một MKT, hoặc một tài khoản lạ (B2), có thể ôm cả kho sản phẩm. Tài khoản đã bị khoá vẫn giữ sản phẩm.
- Đề xuất: `ALLOCATION_MAX_ACTIVE` cho mỗi user. Idle TTL cho `claimed` (ví dụ 7 ngày không có status/result thì release, sweep trong `expire()`). Release toàn bộ khi lock tài khoản.

### B7 — MEDIUM — Ẩn EU Market: backend vẫn bật và module khác vẫn ghi vào bảng EU
- Bằng chứng: `eu_market/jobs.py:34` mặc định `EU_MARKET_ENABLED=1`, và `.env:115=1`, nên `main.py:87-91` vẫn mount route. Nhưng mọi `/api/eu-market/` là SYSTEM (`auth.py:31`), nên trang `/eu-market` chỉ nhận 403 khi MKT dùng (probe xác nhận). Bridge Allegro mount vô điều kiện (`main.py:81-83`), và `auth.py:28,141` mở MACHINE cho `/api/eu-market/allegro/callback/`, `/api/agent/v1/`, `/api/imports/allegro`.
- Module visual phụ thuộc EU: import model EU ngay khi load (`visual/routes.py:30-31`). "Lưu kết quả web" ghi `EUListing` (`visual/lens.py:596-642`). "Theo dõi ảnh" ghi `EUWatchlist` (`visual/routes.py:380-402`). Bảng tỷ giá lấy từ `eu_market.service.fx_table`. Sau khi ẩn EU, dữ liệu này vẫn được tạo ra nhưng không màn hình nào hiển thị.
- Đề xuất (giữ code EU): đặt `EU_MARKET_ENABLED=0`. Gate thêm bridge router và các mục MACHINE theo cờ. Nút "lưu kết quả web" và "theo dõi ảnh" chuyển sang bảng lõi, hoặc tắt khi cờ = 0. Ẩn nav và trang `/eu-market` ở web.

### B8 — MEDIUM — Một số request GET quét gần như cả bảng
- Bằng chứng: `services/hidden.py:74-96` quét toàn bộ ads, creatives và cả `ProductImage.embedding` mỗi lần gọi. Hàm này được gọi trong pulse (`main.py:245`) và hidden-winners (`main.py:314`). Cache 60 s chỉ để trong process. `_competitor_rows` nạp hết ads, advertisers và products (`main.py:724-729`). `_pulse` nạp hết products (`main.py:229`). `product_360` nạp mọi ad và mọi comment của sản phẩm, không có limit (`main.py:434,671-674`). `list_creatives` chạy GROUP BY trên toàn bảng mỗi request (`intel.py:757-758`). Danh sách `IN` khổng lồ (`hidden.py:84,88`: `Product.id.in_(by_p)`, `Creative.ad_id.in_(active_ids)`) sẽ chạm trần 65 535 bind param của psycopg khi dữ liệu lớn lên.
- Lệch số liệu: KPI `hidden_winners` của pulse tính live (`main.py:245`), còn cờ trên card dùng verdict đã lưu (`engine.py:141-144`). Giữa hai lần rescore, hai con số khác nhau.
- Đề xuất: đọc `features.hidden_verdict` đã lưu thay vì tính lại. Các đếm dùng aggregate SQL. Dùng subquery thay cho list `IN`. `product_360` thêm limit cho ads/comments.

### B9 — MEDIUM — Logic lặp: hai pipeline giá landing page và hai hệ tỷ giá
- Bằng chứng: `services/spy.py:118-135` (fetch_landing, có fallback Shopify `.json` riêng) và `pricing/extract.py:26-98` (`_fetch` + `shopify_json`) làm cùng một việc với hai bộ quy tắc khác nhau. FX tĩnh `FX_TO_USD` (`services/enrichment.py:285-288`) đối lập với tỷ giá ECB trong DB (`eu_market/service.py:193`, visual đang dùng). `to_usd` coi mọi tiền tệ lạ là USD (`enrichment.py:303-306`). JPY/INR/KRW/HKD/SGD/CHF/TRY không có trong bảng nên bị nhân ×1, làm lệch median giá bán và margin trong `mkt_summary` (`main.py:344-357`). Mục MEDIUM "FX thiếu" của `docs/AUDIT_2026-10-07.md` mới sửa được một phần.
- Đề xuất: spy gọi `pricing.extract`. Chỉ giữ một nguồn FX: rate DB, fallback bảng tĩnh. Tiền tệ không biết thì trả `None`, không trả ×1.

### B10 — LOW — Hai khái niệm "theo dõi đối thủ" song song
- Bằng chứng: cờ toàn cục `Advertiser.is_competitor_watched` (`main.py:739,773-784`; MKT bị chặn ghi, xem B1) tồn tại song song với `Follow` của từng user (`workspace/routes.py:74-108`). Lọc `watched_only` ở radar dùng cờ toàn cục.
- Đề xuất: bỏ watch toàn cục, cho `watched_only` dùng `Follow` của user hiện tại.

### B11 — LOW — Các điểm cứng hoá còn thiếu
- `/docs`, `/redoc`, `/openapi.json` nằm ngoài guard vì guard chỉ bọc `/api/` (`auth.py:191`). Docker bind `0.0.0.0` (`Dockerfile:10`), nên khi deploy ai cũng xem được schema.
- `require_token` so `x_ingest_token != INGEST_TOKEN` (`intel.py:39`), không dùng `hmac.compare_digest` như bridge (`bridge.py:56`).
- Mật khẩu tối thiểu 6 ký tự (`auth.py:46`), quá yếu cho app có lúc public (B2).
- Đề xuất: `docs_url=None` khi không phải dev; dùng hmac; tối thiểu 10 ký tự.

### B12 — LOW — Cấu hình và tài liệu mâu thuẫn với code
- `start.ps1` dòng cuối bảo chạy `app.workspace.cli create-admin`, nhưng CLI chỉ có `create-user|reset-password|lock|unlock` (`workspace/cli.py:22`).
- `apps/web/next.config.js` (EXPORT=1) ghi "FastAPI phục vụ cùng origin", nhưng `main.py` không có `StaticFiles` hay `mount`.
- `test_search_keywords.py:4` còn nhắc `AdminGuard` đã bị thay.
- Duplicate operationId của `serve_media` (`intel.py:813`).
- `apps/api` còn `market_intel.db` cùng 2 bản backup SQLite (một bản sao dữ liệu nằm ngoài PG) và khoảng 40 file `*.log` (không track).
- Đề xuất: sửa chữ, bỏ hoặc hoàn thiện EXPORT mode, dọn file cũ.

### B13 — LOW — Việc tồn đọng từ các audit cũ
- **Vẫn mở:** `markdown_price` heuristic chọn nhầm giá (`pricing/extract.py:115-125`, bug #8 của FULL_TEST_REPORT). Scheduler status sai process (bug #10, gộp vào B5). FX thiếu (gộp vào B9).
- **Đã đóng (đã kiểm trong code):**
  - C1 không auth: `SessionGuard`.
  - H8 ingest token fail-open: nay fail-closed (`intel.py:36-40`).
  - `/api/events?limit=` không giới hạn: nay `le=500` (`intel.py:245`).
  - competitors không có offset: nay có (`main.py:713`).
  - GET download ghi DB: nay read-only (`intel.py:773`).
  - `ConnectorFactory.get` nằm ngoài try: nay đã vào try (`realtime.py:64-65`).

## 3. Những điểm đã kiểm và **không** phải lỗi
- Đọc chéo dữ liệu MKT qua `user_id`: không có, tham số `user_id` bị bỏ qua (`routers/behavior.py:31-33`).
- Path traversal ở `/api/media/{key}`: đã chặn (`storage.py:23-26`, prefix `creatives/|thumbs/`).
- SSE lọc payload cho MKT (`intel.py:229-233`). `/api/events` trả `[]` cho MKT (`intel.py:246-247`).
- Không còn source eBay/Amazon. Các regex nhắc tới amazon/shopify chỉ dùng để nhận diện landing page (`ingest.py:45`, `services/naming.py`).
- Không phát hiện module chết. Các file không được import đều là CLI có `__main__` (backfill_names, bootstrap_markets, migrate_to_pg, repair_*, purge_*, r2_sync, migrate_pgvector, workspace/cli).
