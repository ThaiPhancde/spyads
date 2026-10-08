# Dữ liệu thật, API cần có và triển khai (host tuỳ chọn + Cloudflare R2)

Tài liệu này trả lời: **muốn app chạy bằng dữ liệu thật từ Facebook, TikTok, spy tool và dữ liệu nội bộ thì cần những gì, dùng API nào, và triển khai thế nào.**

**Phạm vi (07/10/2026):** app chỉ săn **sản phẩm vật lý nhập được từ Trung Quốc** bán qua quảng cáo social (Meta, TikTok, Snapchat). Quảng cáo công cụ tìm kiếm (Google, Bing) và sàn bán lẻ phương Tây (Amazon, Walmart) đã bị gỡ khỏi app. Nguồn Trung Quốc (AliExpress; 1688 / Taobao / Pinduoduo / Alibaba qua Apify khi cần) chỉ là lớp **SOURCE** để kiểm chứng giá nhập và biên lợi nhuận — xem `spy_ads_product_sourcing_architecture.md` §1 (Phạm vi), §10, §10.1.

---

## 1. Nguyên tắc (theo `spy_app_chat_summary.md`)

```text
Nguồn (Meta, TikTok, Pipiads, Minea, Pancake, hãng vận chuyển…)
   → Connector (chỉ: authenticate · fetch · extract · map)
   → POST /api/ingest/*  hoặc  /api/webhooks/*      (một Common Data Contract chung)
   → Raw storage (raw/<source>/<ngày>/*.jsonl — lưu nguyên bản để audit / xử lý lại)
   → Normalize → Dedup (cùng ad từ nhiều nguồn) → Product cluster → Creative Vault (tải video về)
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
| **TikTok Trending videos** (Creator Center, theo `reference/tiktok`) | Endpoint công khai `tiktok.com/creator_studio/inspiration/trending/video/v2` | Không cần key | ✅ Connector `tiktok_trending` (180 phút). Video viral theo region (US, GB, DE, FR, AU) × vertical, có MP4, views, likes. Lọc video có dấu hiệu bán hàng (`product_only`) | Chưa hỗ trợ SA/AE. Đây là video organic, không phải quảng cáo: dùng làm tín hiệu "đang viral" |
| **TikTok Creative Center · Top Ads** | `ads.tiktok.com/creative_radar_api/v1/top_ads/v2/list` | Tài khoản TikTok for Business **free**: dán headers (cookie, user-sign, timestamp, web-id, anonymous-user-id) copy từ DevTools khi đã đăng nhập | ⚠️ Preset `http` "TikTok Creative Center · Top Ads", **tắt sẵn**. Mapping chưa thử với session thật | Headers hết hạn thì health báo `auth_expired`, phải copy lại |
| **Shopify store đối thủ** | `https://<store>/products.json` (mọi store Shopify đều có) | Không cần key | ✅ Connector `shopify_store` (12 giờ): sản phẩm mới tạo trong N ngày, giá, ảnh. Không khai store thì tự lấy domain landing page từ ads đã thu | Thay cho phần "shop spy" của Minea/PiPiAds |
| **TikTok Ad Library** (`library.tiktok.com/api/v1/search`, cách của CheckFirstHQ/Tikadrchivist) | Endpoint công khai, không login, session `X-CCL-STR` | Không cần proxy; chỉ điền `proxy` khi bị `GEO_BLOCKED` | ✅ Connector `tiktok_ad_library` (tìm kiếm) | Chỉ có ads phân phối ở EU/EEA/UK/CH. Có video, landing page, CTA, ngày chạy, estimated audience |
| **Snapchat Ads Library** | API chính thức `adsapi.snapchat.com/v1/ads_library/ads/search` | Không cần key | ⚠️ Connector `snapchat_ads_library`, **tắt sẵn** | Chỉ tìm theo **tên brand** (không theo từ khoá) và hay trả `429` cho IP ngoài EU. Bật khi cần soi brand cụ thể |
| **AliExpress** (nguồn hàng TQ) | JSON nhúng trong trang tìm kiếm công khai (cookie USD) | Không cần key | ✅ Connector `aliexpress_search` (tìm kiếm): giá nhập, giá gốc, **số đã bán**, ngày lên kệ | Là **mốc giá vốn**: `supplier_price_usd` → biên gộp ước tính (1 − giá nhập × 1,6 / giá bán) |
| **1688 · Taobao · Pinduoduo · Alibaba** (nguồn hàng TQ) | — | — | ⏸ Trả login wall / captcha cho request thường (thử 06/10/2026) | Khi cần thì đi qua Apify (`apify_actor`, `source=1688` …). Chỉ là lớp SOURCE, không phải kho quảng cáo |
| **Pinterest Ads Repository · LinkedIn Ad Library · X** | — | — | ⏸ Chưa làm | Pinterest cần resource API nội bộ (EU), LinkedIn là B2B (không hợp COD), X chỉ có DSA EU |
| **TikTok Creative Center Top Ads** | — | — | ⚠️ Không giả chữ ký `user-sign` (repo `tiktok-creative-center-api` dùng `tiktok-user-sign`): chỉ dùng preset dán headers từ tài khoản của bạn | — |
| **Chrome extension "Save to Intelligence"** ([apps/extension](../apps/extension)) | MKT duyệt Meta Ad Library như bình thường; extension bắt dữ liệu trang đã tải (gồm link video) và gửi vào kho. Có nút lưu trang sản phẩm bất kỳ | `INGEST_TOKEN` (nếu đã đặt) | ✅ Đã viết, 7 test offline với dữ liệu thật pass. **Chưa chạy thử trong Chrome thật** | **Cách vào dữ liệu khi không có API.** Không gọi Meta thay bạn, chỉ đọc những gì trang đã tải cho tài khoản của bạn |

### 2.2 Dữ liệu nội bộ (Company Fit)

| Nguồn | API | Trong app | Tier |
|---|---|---|---|
| **Meta Ads (tài khoản công ty)** | Marketing API `GET /act_<id>/insights?level=ad` | ✅ Connector `meta_ads`. Cần **System User token** (quyền `ads_read`) và danh sách ad account. Map sản phẩm bằng cách ghi mã `PRD_0000123` trong tên campaign/ad, hoặc khai `product_map` | Tier 1 (5 phút) |
| **TikTok Ads** | TikTok Business API (`/report/integrated/get/`) | ⚠️ Chưa có connector riêng. Đẩy qua `POST /api/webhooks/ad-metrics` (ví dụ dùng n8n) | Tier 1 |
| **Pancake POS / CRM** | Webhook đơn hàng (Pancake hỗ trợ webhook khi đơn đổi trạng thái) | ✅ `POST /api/webhooks/orders`. Cần một sample payload thật để map trường, hoặc map trong n8n | Tier 0 (< 5 giây) |
| **Hãng vận chuyển** (Aramex, SMSA, J&T, Naqel, GHN, GHTK, Viettel Post…) | Webhook tracking hoặc tracking API | ✅ `POST /api/webhooks/shipments`. Trạng thái của mọi hãng được chuẩn hoá về `DELIVERED / REFUSED / UNREACHABLE / FAILED / RETURNED…` (`realtime.py: STAGE_MAP`) | Tier 0 |
| **Comment / inbox** | Meta Graph API (page của công ty), webhook của Pancake | ✅ `POST /api/webhooks/comments` | Tier 0 |

### 2.2b Ba kênh dữ liệu (`apps/api/app/platforms.py`)

Mọi connector đổ về **một** pipeline (`AdRecord` → ingest → product), nhưng mỗi bản ghi mang `network` và `channel`:

| Kênh | Network | Dùng để |
|---|---|---|
| `ads` | meta, tiktok, snapchat (pinterest: chỉ khi nhập qua export) | Mọi chỉ số quảng cáo: active ads, advertiser, ads mới, saturation, Market Radar, Competitors, alert |
| `commerce` | aliexpress (nguồn hàng TQ), shopify (store đối thủ) | **Giá nhập** và số đã bán (AliExpress), giá bán lẻ của đối thủ (Shopify). Không bao giờ được đếm là "ads" |
| `organic` | tiktok_organic | Độ viral (views). Không phải quảng cáo |

**Chỉ sản phẩm nhập được từ TQ:** sản phẩm không phải hàng vật lý generic (dịch vụ, app, thực phẩm/TPCN, thuốc, hàng brand chỉ brand bán) nhận category `non_product` (`enrichment.NOT_IMPORTABLE` + bước LLM) và bị loại khỏi Radar, Hidden winners, Products, Dashboard.

Điểm `cross_platform` (12% của External Win) **chỉ tính khi sản phẩm thật sự có ở nguồn hàng / viral**: không có dữ liệu thì không bị trừ điểm. Lọc "Có trên ≥ 2 nền tảng" ở trang Tìm sản phẩm.

**Ngân sách kho theo nền tảng:** `STORAGE_SHARES=meta:45,tiktok:30,ads_other:10,commerce:15` chia cả `R2_MAX_GB` lẫn `VIDEO_DAILY_QUOTA`. Ngăn nào vượt thì lần dọn kế tiếp gỡ video yếu nhất (force thấp, cũ nhất) của chính ngăn đó; video đã ghim / sản phẩm được bảo vệ không bị đụng. Dung lượng đếm theo **file thật** (trước đây đếm theo dòng nên bị gấp ~2 lần và chặn mọi lượt tải). Ảnh > 200 KB được nén thành JPEG ≤ 1080 px.

### 2.3 Hạ tầng

| Thành phần | Dùng | Biến môi trường |
|---|---|---|
| **Cloudflare R2** | Lưu video/ảnh (Creative Vault) và raw data | `STORAGE_BACKEND=r2`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, (tuỳ chọn) `R2_PUBLIC_URL` |
| **PostgreSQL** (tuỳ chọn, mặc định SQLite) | Database chính | `DATABASE_URL=postgresql+psycopg://…` |
| **Claude API** (tuỳ chọn) | Trích tên sản phẩm, lọc quảng cáo không phải sản phẩm, phân tích comment, lý do từ chối, AI Agent | `ANTHROPIC_API_KEY` |

**Vì sao phải tải video về ngay:** link video của Facebook/TikTok (fbcdn…) là link có chữ ký và **hết hạn sau vài giờ đến vài ngày**. App tải video ngay khi thu thập, đặt tên theo `sha256` để không lưu trùng, tạo thumbnail và perceptual hash để gom creative family. Video và ảnh được lưu vĩnh viễn trên R2, người dùng xem và tải về từ đó.

---

## 3. Realtime đang hoạt động thế nào

| Tier | Mục tiêu | Trong app |
|---|---|---|
| 0 | < 5 giây | Webhook orders/shipments/comments → chấm lại điểm **chỉ cho sản phẩm liên quan** → alert (refusal/delivery tăng bất thường) → đẩy SSE lên trình duyệt. Đo được khoảng 0,2 giây mỗi webhook |
| 1 | 1–5 phút | Connector `meta_ads` chạy theo `every_minutes` |
| 2 | 15–60 phút | Tracked queries (từ khoá / page đối thủ) và các connector spy được scheduler chạy lại |
| 3 | 6–24 giờ | Chấm lại điểm toàn bộ + daily snapshot |

Giao diện nhận sự kiện qua `GET /api/events/stream` (Server-Sent Events): toast, kết quả tìm kiếm, video vừa tải xong, quyết định đổi (ví dụ SCALE → HOLD).

Scheduler và event bus chạy trong tiến trình API, vì vậy chỉ chạy **1 replica**. Khi cần nhiều instance: chuyển sang Redis pub/sub cùng Celery hoặc RQ (giữ nguyên các hàm job trong `realtime.py`).

---

## 4. Triển khai ở nơi khác

App gồm 2 tiến trình, không phụ thuộc nhà cung cấp nào (VPS, Docker host, PaaS):

1. **API** (`apps/api`, `Dockerfile` có sẵn ffmpeg, lắng nghe `$PORT`, mặc định 8000). Biến môi trường tối thiểu: `INGEST_TOKEN`; thêm `DATABASE_URL` nếu dùng Postgres; `STORAGE_BACKEND=r2` cùng các biến `R2_*` để lưu video lên Cloudflare (hoặc gắn ổ đĩa bền vào `DATA_DIR` và để `STORAGE_BACKEND=local`); `ANTHROPIC_API_KEY` (tuỳ chọn).
2. **Web** (`apps/web`, `Dockerfile` có sẵn). Đặt `API_URL=<địa chỉ API mà web gọi tới>` **lúc build** và `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` nếu muốn khoá bản demo.
   Chỉ cần một domain công khai trỏ vào Web; Web chuyển `/api/*` sang API. Webhook của Pancake hoặc hãng vận chuyển gọi `https://<domain>/api/webhooks/...` kèm header `X-Ingest-Token`.
3. **Cloudflare R2**: tạo bucket và API token (Object Read & Write). Muốn link video công khai và nhanh thì gắn custom domain hoặc bật r2.dev rồi đặt `R2_PUBLIC_URL`; nếu không, app dùng presigned URL có hạn 1 giờ.
4. **Chỉ chạy 1 bản sao của API**: scheduler và kênh realtime nằm trong tiến trình API. Khi cần nhiều bản, chuyển sang Redis (xem mục 3).

---

## 5. Bạn cần chuẩn bị gì

Không cần gửi key qua chat. Hãy nhập vào biến môi trường của máy chủ hoặc màn **Data & Connectors**; trường bí mật được ẩn khi hiển thị.

| Mức | Cần | Để làm gì |
|---|---|---|
| **Bắt buộc để chạy ở máy chủ** | Postgres (khuyến nghị), bucket R2 + access key, `INGEST_TOKEN`, Basic Auth | Dữ liệu và video tồn tại lâu dài |
| **Tìm sản phẩm thật** | Không cần gì thêm (Meta Ad Library public đã chạy). Volume lớn: `APIFY_TOKEN` và/hoặc proxy | Tìm kiếm, theo dõi từ khoá, theo dõi page đối thủ |
| **TikTok** | `APIFY_TOKEN` + actor TikTok bạn chọn, hoặc quyền TikTok Commercial Content API | Quảng cáo TikTok |
| **Spy tool** | Pipiads/Minea: key Enterprise, hoặc chỉ cần file export | Gộp dữ liệu nhiều nguồn |
| **Company Fit** | Meta **System User token** (ads_read) + ad account id; **sample webhook payload của Pancake**; danh sách hãng vận chuyển và mẫu trạng thái của họ | ROAS → đơn → giao → hoàn → lợi nhuận thật, quyết định SCALE/HOLD theo thời gian thực |
| **AI** (tuỳ chọn) | `ANTHROPIC_API_KEY` | Tên sản phẩm chuẩn, lọc quảng cáo dịch vụ, phân tích comment |

---

## 6. Giới hạn hiện tại

- Meta Ad Library public **không** trả lượt like/comment/share của từng quảng cáo, nên Wave Potential dựa vào nội dung creative (hook, demo, before/after), số biến thể, tốc độ ra ads mới và comment nội bộ.
- Khi chưa có `ANTHROPIC_API_KEY`: tên sản phẩm lấy từ slug của landing page, headline hoặc câu đầu của ad copy. Quảng cáo dịch vụ (spa, khoá học…) có thể lẫn vào kết quả tìm theo từ khoá.
- Chưa có: OCR và chuyển giọng nói thành chữ cho video, embedding video, connector riêng cho TikTok Ads, connector 1688 / Taobao (qua Apify).
- Extension chỉ bắt tự động trên **Meta Ad Library**. Các trang khác (Pipiads, Minea, TikTok…) dùng nút "Lưu trang này", chỉ lấy được tiêu đề, ảnh, video có link tải trực tiếp và giá; video dạng blob/stream thì không lưu được.
- Ngưỡng điểm và quyết định là giá trị khởi điểm. Learning Loop và Taste Model sẽ chính xác dần khi có vote của MKT và kết quả test thật.
