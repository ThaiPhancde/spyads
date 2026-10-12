# Dữ liệu thật, API cần có và triển khai (host tuỳ chọn + Cloudflare R2)

Tài liệu này trả lời: **muốn app chạy bằng dữ liệu thật từ Facebook, TikTok, spy tool và dữ liệu nội bộ thì cần những gì, dùng API nào, và triển khai thế nào.**

**Phạm vi (07/10/2026):** app chỉ săn **sản phẩm vật lý nhập được từ Trung Quốc** bán qua quảng cáo social (Meta, TikTok, Snapchat). Quảng cáo công cụ tìm kiếm (Google, Bing) và sàn bán lẻ phương Tây (Amazon, Walmart) đã bị gỡ khỏi app. Nguồn Trung Quốc (AliExpress; 1688 / Taobao / Pinduoduo / Alibaba qua Apify khi cần) chỉ là lớp **SOURCE** để kiểm chứng giá nhập và biên lợi nhuận — xem `spy_ads_product_sourcing_architecture.md` §1 (Phạm vi), §10, §10.1.

---

## 1. Nguyên tắc (theo `spy_app_chat_summary.md`)

```text
Nguồn (Meta, TikTok, Pipiads/Minea (export), AliExpress, 1688…)
   → Connector (chỉ: authenticate · fetch · extract · map)
   → POST /api/ingest/*                              (một Common Data Contract chung)
   → Raw storage (raw/<source>/<ngày>/*.jsonl — lưu nguyên bản để audit / xử lý lại)
   → Normalize → Dedup (cùng ad từ nhiều nguồn) → Product cluster → Creative Vault (ảnh bìa, video tải theo yêu cầu)
   → Analytics (điểm số, quyết định) → App
```

- App **không** gọi trực tiếp Pipiads/Minea/Meta. Frontend chỉ đọc API nội bộ.
- Thêm nguồn mới = viết một class trong `apps/api/app/collector/adapters/` và đăng ký trong `factory.py`.
- Không dùng "master key", cookie/session lấy trộm hay reverse private API của spy tool. Chỉ dùng: API chính thức, API thương mại (gói Enterprise), file export, webhook, hoặc dữ liệu công khai.
- Trường nào nguồn không cung cấp thì để `null`, không suy đoán.

---

## 2. Các nguồn và API

### 2.1 Quảng cáo đối thủ (thị trường)

| Nguồn | Cách lấy | Cần gì | Trạng thái trong app | Ghi chú |
|---|---|---|---|---|
| **Meta Ad Library (public)** | GraphQL công khai của trang Ad Library, qua thư viện `meta-ads-collector` (MIT) | Không cần key. Chạy volume lớn thì nên có proxy residential | ✅ **Đã chạy với dữ liệu thật**: tìm theo từ khoá, quốc gia, page đối thủ; có video HD/SD, ảnh, CTA, landing page, ngày chạy, số biến thể | Không chính thức: Meta có thể đổi `doc_id` bất kỳ lúc nào, khi đó health check báo lỗi. Hãy giữ volume vừa phải và tự đánh giá điều khoản của Meta |
| **Apify — Facebook Ads Library scraper** (`curious_coder~facebook-ads-library-scraper`) | Apify chạy scraper, trả JSON cùng cấu trúc | `APIFY_TOKEN` (tài khoản Apify, trả theo lượng dùng) | ✅ Connector `apify_meta` | Nên dùng cho production volume, Apify xử lý chống chặn |
| **Meta Ad Library API (chính thức)** `graph.facebook.com/vXX/ads_archive` | REST chính thức | App Meta đã verify, xác minh danh tính (ID), token có quyền ads_read | ✅ Connector `meta_graph` | **Giới hạn:** quảng cáo thương mại chỉ có cho quảng cáo chạy tại EU/UK (DSA). Không có link video, chỉ có `ad_snapshot_url` |
| **TikTok Commercial Content API** | REST chính thức `open.tiktokapis.com/v2/research/adlib/…` | Đăng ký và được duyệt quyền truy cập, `client_key` / `client_secret` | ✅ Connector `tiktok_commercial` | Chỉ có quảng cáo hiển thị ở EU/EEA/UK/CH |
| **TikTok ads ngoài EU / Creative Center top ads** | Apify actor (nhiều actor cộng đồng) | `APIFY_TOKEN` + id actor | ✅ Connector `apify_actor` (tự nhận diện cột; có thể map tay bằng JSON) | Chất lượng phụ thuộc actor. Hãy thử 1–2 actor rồi chốt |
| **Pipiads** | API Enterprise (hỏi Pipiads) hoặc **Export CSV/XLSX** | Key Enterprise, hoặc file export từ tài khoản | ✅ `http` (Generic REST + mapping) và `export` (thư mục `data/import/pipiads/` hoặc nút Upload) | Không có API thì dùng Export. Không crawl qua cookie |
| **Minea** | Export (hoặc Enterprise API nếu có) | File export | ✅ `export` (minea) | Như trên |
| **BigSpy / AdSpy / Foreplay…** | API của từng tool nếu có, nếu không thì Export | Key / file | ✅ `http` hoặc `export` | Chỉ cần cấu hình, không phải sửa code |
| **TikTok Creative Center · Top Ads** | Bản **headless** (Playwright điều khiển Edge/Chrome cài trên máy chạy API, không cần đăng nhập, ≈150 ads/nước ở period 180): bật sẵn, 12 giờ/lần. Bản dán headers từ tài khoản TikTok for Business (`user-sign`, cookie…) tắt sẵn | Trình duyệt trên host API | ✅ Nguồn TikTok duy nhất có PH/SA/AE (kể cả Apify `fetch_cat` ~0,11 USD/1k nếu cần volume) | Không giả chữ ký `user-sign`. Container mặc định không có trình duyệt nên connector headless không chạy được ở đó |
| ~~Shopify store đối thủ~~ (đã gỡ 07/10/2026) | `https://<store>/products.json` (mọi store Shopify đều có) | Không cần key | ✅ Connector `shopify_store` (12 giờ): sản phẩm mới tạo trong N ngày, giá, ảnh. Không khai store thì tự lấy domain landing page từ ads đã thu | Thay cho phần "shop spy" của Minea/PiPiAds |
| **TikTok Ad Library** (`library.tiktok.com/api/v1/search`, cách của CheckFirstHQ/Tikadrchivist) | Endpoint công khai, không login, session `X-CCL-STR` | Không cần proxy; chỉ điền `proxy` khi bị `GEO_BLOCKED` | ✅ Connector `tiktok_ad_library` (tìm kiếm) | Chỉ có ads phân phối ở EU/EEA/UK/CH. Có video, landing page, CTA, ngày chạy, estimated audience |
| **Snapchat Ads Library** | API chính thức `adsapi.snapchat.com/v1/ads_library/ads/search` | Không cần key | ⚠️ Connector `snapchat_ads_library`, **tắt sẵn** | Chỉ tìm theo **tên brand** (không theo từ khoá) và hay trả `429` cho IP ngoài EU. Bật khi cần soi brand cụ thể |
| **AliExpress** (nguồn hàng TQ) | JSON nhúng trong trang tìm kiếm công khai (cookie USD) | Không cần key | ✅ Connector `aliexpress_search` (tìm kiếm): giá nhập, giá gốc, **số đã bán**, ngày lên kệ | Là **mốc giá vốn**: `supplier_price_usd` → biên gộp ước tính (1 − giá nhập × 1,6 / giá bán) |
| **1688** (nguồn hàng TQ) | API 1688 AI chính thức ký HMAC (AK): miễn phí, ≤20 offer, có số bán 30 ngày; fallback Apify khi AK lỗi/ít kết quả. **Taobao/Tmall** qua Apify | `ALI_1688_AK` (lấy trong app 1688 AI) hoặc `APIFY_TOKEN` | ✅ Connector `ali1688`, `apify_1688`, `apify_taobao` | Chi tiết: [ToolSpy_1688_Hybrid_Connector.md](ToolSpy_1688_Hybrid_Connector.md). Không scrape 1688.com trực tiếp (login wall/captcha) |
| **Pinterest Ads Repository · LinkedIn Ad Library · X** | — | — | ⏸ Chưa làm | Pinterest cần resource API nội bộ (EU), LinkedIn là B2B (không hợp COD), X chỉ có DSA EU |
| **Chrome extension "Save to Intelligence"** ([apps/extension](../apps/extension)) | MKT duyệt Meta Ad Library như bình thường; extension bắt dữ liệu trang đã tải (gồm link video) và gửi vào kho. Có nút lưu trang sản phẩm bất kỳ | `INGEST_TOKEN` (nếu đã đặt) | ✅ Đã viết, 7 test offline với dữ liệu thật pass. **Chưa chạy thử trong Chrome thật** | **Cách vào dữ liệu khi không có API.** Không gọi Meta thay bạn, chỉ đọc những gì trang đã tải cho tài khoản của bạn |

### 2.1b Giá bán của đối thủ và review khách (`apps/api/app/services/spy.py`)

Ad library (Meta / TikTok / Google) **không** trả giá bán lẫn comment. Thẻ "Thông tin cho MKT" trên trang sản phẩm vì vậy lấy từ:

| Dữ liệu | Nguồn | Cách lấy | Ghi trên row |
|---|---|---|---|
| Giá đối thủ đang bán | nội dung ad (regex ₱ / $ / 199k / 299.000đ / SAR…) | lúc ingest + quét lại | `ads.price`, `price_source=ad_text` |
| Giá đối thủ đang bán | **landing page** của ad (Shopify `/products/x.json`, JSON-LD `offers`, `og:price`) | `curl_cffi` giả Chrome (httpx bị 429), 1 fetch / URL, cache 14 ngày (`landing_checked_at`) | `price_source=landing`, `original_price` = compare-at |
| Giá bán lẻ sàn (Walmart / Amazon) | listing commerce | adapter | hiện riêng (`retail_usd`), **không** gộp vào giá đối thủ |
| Review khách | AliExpress feedback API công khai (listing nguồn hàng của sản phẩm) | 2 trang × 20 review / listing, 14 ngày quét lại (`reviews_checked_at`) | `comments.source=review`, `rating` (sao) → `overall` |
| Review khách | JSON-LD `review` trên landing page (Judge.me / Loox…) | cùng lượt fetch landing | `comments.source=landing_review` |

`sell_usd` = median giá của **các ad có giá** (không bao giờ là median giá sàn); API trả thêm `price_coverage` (bao nhiêu ad có giá,
nguồn nào, bao nhiêu ad dẫn về inbox nên không có trang giá) và `sell_by_advertiser` (giá từng đối thủ + nguồn) để MKT tự kiểm.
Chạy: nút "Quét giá & review đối thủ" (`POST /api/products/{id}/scan`, ~30 s) hoặc scheduler mỗi `SPY_EVERY_MIN` (10) phút,
`SPY_URLS_PER_RUN` (40) URL ưu tiên sản phẩm WATCHLIST / TESTING / CANDIDATE. Ad dẫn về inbox / app store (`SKIP_HOST`) chỉ có giá khi copy ghi giá.

### 2.2 Dữ liệu nội bộ (Company Fit): đã gỡ

Meta Ads insights, đơn hàng Pancake, vận đơn, comment/inbox qua webhook, Test Lab, Attribution, Learning Loop **đã bị gỡ khỏi app** (backend ở commit `c60bc91`, giao diện ngày 09/10/2026). Hệ thống hiện chỉ dùng tín hiệu thị trường (ads, nguồn hàng, giá, review công khai). Vòng lặp "quyết định → kết quả test" chưa còn; xem [SYSTEM_ANALYSIS.md §14](SYSTEM_ANALYSIS.md#14-nhược-điểm-và-rủi-ro).

### 2.2b Ba kênh dữ liệu (`apps/api/app/platforms.py`)

Mọi connector đổ về **một** pipeline (`AdRecord` → ingest → product), nhưng mỗi bản ghi mang `network` và `channel`:

| Kênh | Network | Dùng để |
|---|---|---|
| `ads` | meta, tiktok, snapchat (pinterest: chỉ khi nhập qua export) | Mọi chỉ số quảng cáo: active ads, advertiser, ads mới, saturation, Market Radar, Competitors, alert |
| `commerce` | aliexpress (nguồn hàng TQ), shopify (store đối thủ) | **Giá nhập** và số đã bán (AliExpress), giá bán lẻ của đối thủ (Shopify). Không bao giờ được đếm là "ads" |

**Chỉ sản phẩm nhập được từ TQ:** sản phẩm không phải hàng vật lý generic (dịch vụ, app, thực phẩm/TPCN, thuốc, hàng brand chỉ brand bán) nhận category `non_product` (`enrichment.NOT_IMPORTABLE` + bước LLM) và bị loại khỏi Radar, Hidden winners, Products, Dashboard.

Điểm `cross_platform` (12% của External Win) **chỉ tính khi sản phẩm thật sự có ở nguồn hàng / viral**: không có dữ liệu thì không bị trừ điểm. Lọc "Có trên ≥ 2 nền tảng" ở trang Tìm sản phẩm.

**Ngân sách kho theo nền tảng:** `STORAGE_SHARES=meta:45,tiktok:30,ads_other:10,commerce:15` chia cả `R2_MAX_GB` lẫn `VIDEO_DAILY_QUOTA`. Ngăn nào vượt thì lần dọn kế tiếp gỡ video yếu nhất (force thấp, cũ nhất) của chính ngăn đó; video đã ghim / sản phẩm được bảo vệ không bị đụng. Dung lượng đếm theo **file thật** (trước đây đếm theo dòng nên bị gấp ~2 lần và chặn mọi lượt tải). Ảnh > 200 KB được nén thành JPEG ≤ 1080 px.

### 2.3 Hạ tầng

| Thành phần | Dùng | Biến môi trường |
|---|---|---|
| **Cloudflare R2** | Lưu video/ảnh (Creative Vault) và raw data | `STORAGE_BACKEND=r2`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, (tuỳ chọn) `R2_PUBLIC_URL` |
| **PostgreSQL** (đang dùng; SQLite chỉ hợp 1 người tìm một lúc) | Database chính | `DATABASE_URL=postgresql+psycopg://…` · chuyển dữ liệu SQLite → Postgres: `python -m app.migrate_to_pg market_intel.db` |
| **Claude API** (tuỳ chọn) | Trích tên sản phẩm, lọc quảng cáo không phải sản phẩm, phân tích comment, lý do từ chối, AI Agent | `ANTHROPIC_API_KEY` |

**Creative:** link video của Facebook/TikTok có chữ ký và hết hạn sau vài giờ, nên app lưu ngay **một ảnh bìa ≤480px** cho mỗi creative; video phát từ nguồn khi bấm Play. Tải toàn bộ file chỉ khi người dùng yêu cầu hoặc `VIDEO_DOWNLOAD=1` (sha256, dHash, gom creative family). Không có HLS.

---

## 3. Realtime đang hoạt động thế nào

Scheduler chạy ở **tiến trình riêng** `python -m app.worker` (API đặt `DISABLE_SCHEDULER=1`), tick 30 giây, tối đa `MAX_PARALLEL_JOBS` (3) việc song song. Tìm kiếm trực tiếp của người dùng chạy trong API, mỗi lượt một thread, chung `LIVE_SEARCH_SLOTS` (12) lượt tải nguồn cùng lúc — không phải chờ job theo lịch:

| Việc | Chu kỳ |
|---|---|
| Tracked queries / connector có lịch | theo `every_minutes` (PH 30 phút) |
| Xác minh ads còn chạy (liveness) | 20 phút |
| Quét giá landing page + review đối thủ | 10 phút |
| Alerts | 15 phút |
| Lấy ảnh bìa creative | 2 phút |
| Chấm lại toàn bộ + snapshot | 6 giờ |
| Dọn kho | 6 giờ + sau 0h giờ VN |

Giao diện nhận sự kiện qua `GET /api/events/stream` (SSE): kết quả tìm kiếm hiện dần, toast sản phẩm mới, quyết định đổi (ví dụ TEST → WATCH).

Chạy **1 API + 1 worker**. Event bus (SSE) nằm trong bộ nhớ API: tiến trình tìm kiếm đẩy tiến độ ngay, còn sự kiện của worker (đồng bộ nguồn, alert) chỉ được lưu vào bảng `events`, không đẩy realtime. Muốn nhiều bản API: chuyển event bus sang Postgres LISTEN/NOTIFY hoặc Redis.

## 4. Triển khai ở nơi khác

App gồm 3 tiến trình + Postgres, không phụ thuộc nhà cung cấp nào (VPS, Docker host, PaaS). Docker không bắt buộc: một VPS cài Postgres + Python + Node là đủ.

1. **API** (`apps/api`, `Dockerfile` có sẵn ffmpeg, lắng nghe `$PORT`, mặc định 8000). Biến môi trường tối thiểu: `ADMIN_TOKEN`, `INGEST_TOKEN`, `WEB_ORIGIN`; `DATABASE_URL` (Postgres), `DISABLE_SCHEDULER=1`; `STORAGE_BACKEND=r2` cùng các biến `R2_*` để lưu video lên Cloudflare (hoặc gắn ổ đĩa bền vào `DATA_DIR` và để `STORAGE_BACKEND=local`); `ANTHROPIC_API_KEY` (tuỳ chọn).
2. **Web** (`apps/web`, `Dockerfile` có sẵn). Đặt `API_URL=<địa chỉ API mà web gọi tới>` **lúc build** và `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` nếu muốn khoá bản demo.
   Chỉ cần một domain công khai trỏ vào Web; Web chuyển `/api/*` sang API. Extension gọi `https://<domain>/api/ingest/...` kèm header `X-Ingest-Token`.
3. **Cloudflare R2**: tạo bucket và API token (Object Read & Write). Muốn link video công khai và nhanh thì gắn custom domain hoặc bật r2.dev rồi đặt `R2_PUBLIC_URL`; nếu không, app dùng presigned URL có hạn 1 giờ.
4. **Worker** (`python -m app.worker`, cùng image/biến môi trường với API): đúng **1 bản**. API cũng chỉ 1 bản (event bus trong bộ nhớ, xem mục 3).

---

## 5. Bạn cần chuẩn bị gì

Không cần gửi key qua chat. Hãy nhập vào biến môi trường của máy chủ hoặc màn **Data & Connectors**; trường bí mật được ẩn khi hiển thị.

| Mức | Cần | Để làm gì |
|---|---|---|
| **Bắt buộc để chạy ở máy chủ** | Postgres (khuyến nghị), bucket R2 + access key, `ADMIN_TOKEN`, `INGEST_TOKEN`, Basic Auth | Dữ liệu và video tồn tại lâu dài |
| **Tìm sản phẩm thật** | Không cần gì thêm (Meta Ad Library public đã chạy). Volume lớn: `APIFY_TOKEN` và/hoặc proxy | Tìm kiếm, theo dõi từ khoá, theo dõi page đối thủ |
| **TikTok** | `APIFY_TOKEN` + actor TikTok bạn chọn, hoặc quyền TikTok Commercial Content API | Quảng cáo TikTok |
| **Spy tool** | Pipiads/Minea: key Enterprise, hoặc chỉ cần file export | Gộp dữ liệu nhiều nguồn |
| **AI** (tuỳ chọn) | `ANTHROPIC_API_KEY` | Tên sản phẩm chuẩn, lọc quảng cáo dịch vụ, phân tích comment |

### 5b. EU Market (`apps/api/app/eu_market/`, menu **EU Market**)

Đặc tả: `docs/SpyAds_EU_Market_Implementation.md`. Module riêng, bảng riêng (`eu_*`), không trộn với bảng ads. Mọi listing có `source`, `market_country`, `observed_at`, `data_access`, `confidence`. Không lưu số bán, doanh thu hay conversion.

**Public data first (09/10/2026, xem `docs/IMAGE_SEARCH.md` §P4):** không cần tài khoản seller, giấy phép EU hay gói trả phí để dùng EU Market. Trạng thái nguồn: `available` → `ready` / `missing_credentials` / mã lỗi lần chạy cuối; `access_restricted` (chỉ có Seller/Partner API); `unsupported_capability` (không có API/feed hợp lệ); `rate_limited`, `temporarily_unavailable` khi gọi.

| Nguồn | Trạng thái | Biến `.env` | Dùng làm gì |
|---|---|---|---|
| **Trang sản phẩm công khai (JSON-LD)** | available, không cần key | — | Dán URL (Product Explorer, hoặc ô URL ở "Tìm bằng ảnh") → listing + ảnh vào chỉ mục. Đọc đúng `schema.org/Product` + `Offer` như shop công bố; tôn trọng robots.txt; trang trả 403/CAPTCHA → `access_restricted`, không vượt. |
| Kaufland Seller API v2 | tuỳ chọn (seller) (DE AT CZ SK PL FR IT) | `KAUFLAND_CLIENT_KEY`, `KAUFLAND_SECRET_KEY` (tuỳ chọn `KAUFLAND_USER_AGENT`) | `/products/search` + giá buybox (seller, ship) |
| bol Retailer API v10 | đã triển khai (NL BE) | `BOL_CLIENT_ID`, `BOL_CLIENT_SECRET` | `/products/list` + best offer, URL, ảnh, số rating |
| BigBuy (B2B) | đã triển khai, không có search | `BIGBUY_API_KEY` (tuỳ chọn `BIGBUY_SANDBOX=1`) | Sync catalog theo trang (10 lần/giờ), sau đó tìm cục bộ. Giá sỉ chưa VAT |
| Allegro, eMAG, OTTO, Cdiscount, ManoMano, Zalando, Faire, Ankorstore, Orderchamp | `access_restricted` | — | Chỉ có Seller / Partner API. Hiện ở Data Sources, không bao giờ gọi |
| OnBuy, Merkandi, Europages, SoloStocks | `unsupported_capability` | — | Không xác minh được API / feed hợp lệ. Không scrape |

- Endpoint của Kaufland và bol được đối chiếu với swagger/OpenAPI chính thức. Riêng BigBuy, spec không mô tả tên trường trong response, nên phần mapping được viết phòng thủ và giữ payload gốc. **Cần xác nhận mapping ở lần gọi thật đầu tiên.**
- Tỷ giá quy đổi lấy từ ECB (`eurofxref-daily.xml`, miễn phí, có ngày công bố). Giá quy đổi luôn ghi kèm ngày tỷ giá.
- Lịch chạy: scheduler gọi `jobs.run_all` mỗi `EU_SYNC_EVERY_MIN` phút (mặc định 360), **chỉ khi có ít nhất một nguồn đã cấu hình**. Thứ tự: tỷ giá, đồng bộ từ khoá của watchlist, snapshot giá, ghép nhà cung cấp và ads, cảnh báo. Có thể chạy tay: `cd apps/api && .venv/Scripts/python -m app.eu_market.jobs all|sync|snapshot|fx|match`.
- Các biến khác: `EU_MARKET_ENABLED` (mặc định 1), `EU_MARKET_COUNTRIES` (mặc định `DE,FR,IT,ES,NL,BE,PL`), `EU_SNAPSHOT_MAX` (200), `KAUFLAND_BUYBOX_TOP` (15), `BOL_ENRICH_TOP` (12), `BIGBUY_PAGE_SIZE` (1000).
- Kiểm thử offline: `cd apps/api && .venv/Scripts/python test_eu_market.py`.

---

## 6. Giới hạn hiện tại

- Meta Ad Library public **không** trả lượt like/comment/share của từng quảng cáo, nên Wave Potential dựa vào nội dung creative (hook, demo, before/after), số biến thể, tốc độ ra ads mới và review công khai.
- Khi chưa có `ANTHROPIC_API_KEY`: tên sản phẩm lấy từ slug trang `/products/…`, tiêu đề link, hoặc `<advertiser> (chưa đặt tên)`; không bao giờ dùng ad copy. Tên giống headline hoặc câu CTA bị giới hạn ở nhãn WATCH. Quảng cáo dịch vụ (spa, khoá học…) có thể lẫn vào kết quả tìm theo từ khoá.
- Chưa có: OCR và chuyển giọng nói thành chữ cho video, embedding video.
- Extension chỉ bắt tự động trên **Meta Ad Library**. Các trang khác (Pipiads, Minea, TikTok…) dùng nút "Lưu trang này", chỉ lấy được tiêu đề, ảnh, video có link tải trực tiếp và giá; video dạng blob/stream thì không lưu được.
- Ngưỡng điểm và quyết định được hiệu chỉnh trên DB thật ngày 09/10/2026 (ranking v2, xem README). Taste Model chỉ có tác dụng khi team vote đủ (≥3 vote cho mỗi đặc trưng); hiện mới có 2 vote.

## Allegro (EU Market, import từ file)

Grok Bot ghi `apps/api/data/imports/allegro.json` (mảng, hoặc object có mảng trong `items/offers/products/listings/results/data`; mỗi dòng cần `offer_id` + `title`, tuỳ chọn `price`, `seller`, `url`, `image`, `ean`, `brand`, `category`). Vào **EU Market → Data Sources → Allegro → Import vào kho** (hoặc `POST /api/eu-market/imports/allegro`, cần `X-Admin-Token`). Khoá chống trùng: `allegro` + `offer_id` + `PL`; import lại chỉ cập nhật giá/seller và thêm snapshot khi giá đổi. Chưa có file → trạng thái `no_data`, không có dữ liệu giả. Listing hiển thị là marketplace (`kind=marketplace_listing`), tách khỏi bảng quảng cáo; mã: `eu_market/allegro_import.py`, test: `test_allegro_import.py`.

### Allegro qua Grok Bot (tìm bằng trình duyệt)

Crawler HTTP (`apps/api/scripts/allegro_crawler.py`) bị DataDome trả 403 nên **tạm tắt** (bật lại bằng `ALLEGRO_HTTP_CRAWLER_ENABLED=1`). Tìm Allegro giờ đi qua Grok Bot:

1. MKT bấm **Tìm trên Allegro** trong EU Market → Product Explorer (từ khoá = dòng đầu tiên).
2. `POST /api/eu-market/allegro/search` tạo job (bảng `eu_bot_jobs`) và POST tới `GROK_ROUTINE_WEBHOOK_URL` (`Authorization: Bearer GROK_ROUTINE_WEBHOOK_KEY`) payload: `job_id, keyword, limit, market=PL, deadline_at, callback.url, instructions, result_schema`.
3. Bot mở allegro.pl, đọc tối đa `limit` offer, rồi POST một JSON tới `callback.url` (`/api/eu-market/allegro/callback/{job_id}?token=…`) với header `X-Callback-Secret: ALLEGRO_CALLBACK_SECRET`:
   `{"status": "ok|empty|captcha|blocked|error", "message": "...", "items": [{"offer_id": "17…", "title": "...", "price": 59.99, "currency": "PLN", "seller": "...", "url": "https://allegro.pl/oferta/…", "image": "https://…"}]}`
4. API kiểm token riêng của job + secret, validate (offer_id phải là số, link không thuộc allegro.pl bị thay bằng link `/oferta/{id}`), lưu bằng importer có sẵn (khoá chống trùng `allegro + offer_id + PL`), frontend poll `GET /api/eu-market/allegro/jobs/{id}` và hiển thị sản phẩm.

Trạng thái job: `sent → done | empty | captcha | blocked | failed | timeout`. Cùng từ khoá đang chạy → dùng lại job; đã xong trong `ALLEGRO_BOT_CACHE_HOURS` (12h) → trả cache (nút "Chạy lại" bỏ cache). Không có callback sau `ALLEGRO_BOT_TIMEOUT_MIN` (15 phút) → `timeout`. Bot **không** giải CAPTCHA / vượt chặn: gặp thì gửi `captcha` hoặc `blocked`, UI hiển thị rõ.

**Callback phải gọi được từ cloud**: localhost không dùng được. Chạy local thì mở Cloudflare Tunnel `cloudflared tunnel --url http://localhost:8000` (hoặc tunnel có tên + domain riêng) và đặt URL `https://…` vào `PUBLIC_API_BASE_URL`. Chỉ đường dẫn `/api/eu-market/allegro/callback/*` được miễn `X-Admin-Token`; mọi API ghi khác vẫn cần token admin.
Mã: `app/eu_market/allegro_bot.py`, `components/eu-market/allegro-bot.tsx`; test offline: `test_allegro_bot.py`.
