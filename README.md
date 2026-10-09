# Market Intelligence OS

App nội bộ cho MKT Mess và MKT Ladi: **spy quảng cáo social (Meta, TikTok, Snapchat) để tìm sản phẩm vật lý nhập được từ Trung Quốc**, rồi kiểm chứng giá nhập (AliExpress; 1688/Taobao qua Apify) để quyết định TEST hay SKIP. Không spy Google/Bing, không theo dõi Amazon/Walmart. Hai nhiệm vụ chính:

1. **Tìm sản phẩm.** Tìm theo từ khoá, thị trường và funnel (Mess/Ladi) trong kho dữ liệu, hoặc **tìm trực tiếp trên Meta Ad Library và các nguồn đã kết nối**. Mỗi sản phẩm có video và ảnh quảng cáo thật, **xem và tải về được**.
2. **Xếp hạng và ra quyết định.** Product Potential Vector (novelty, wave, creative, market fit theo từng thị trường, MKT appeal, compliance…) kết hợp dữ liệu nội bộ (ads, đơn, giao hàng, hoàn, lợi nhuận) để ra các quyết định **TEST NOW / TEST / WATCH / REVIEW / SKIP** (trước khi test, theo tín hiệu thị trường) và **SCALE / HOLD / ITERATE / STOP** (sau khi test, theo dữ liệu nội bộ), cập nhật **realtime**.

Thiết kế theo (tài liệu gốc, thắng khi mâu thuẫn): [docs/spy_ads_product_sourcing_architecture.md](docs/spy_ads_product_sourcing_architecture.md). Chi tiết: [product_discovery_intelligence_summary.md](docs/product_discovery_intelligence_summary.md), [spy_app_chat_summary.md](docs/spy_app_chat_summary.md) (connector contract), [competitive_ads_analysis_module.md](docs/competitive_ads_analysis_module.md) (Ad Detail), [market_intelligence_app_blueprint.md](docs/market_intelligence_app_blueprint.md).

**Muốn chạy bằng dữ liệu thật, cần API nào và triển khai thế nào: xem [docs/DATA_SOURCES_AND_DEPLOY.md](docs/DATA_SOURCES_AND_DEPLOY.md).**

## Chạy local

```powershell
./start.ps1      # tạo venv, cài deps, chạy API :8000 và Web :3000
```
Mở http://localhost:3000/search, gõ từ khoá rồi bấm **⚡ Tìm trực tiếp trên nguồn**. Kết quả là dữ liệu thật từ Meta Ad Library, không cần key. Video được tải về `apps/api/data/creatives/`.

App chỉ dùng dữ liệu thật: bộ sinh dữ liệu demo (`seed.py`, `/api/admin/demo-data`) đã bị gỡ.

## Kiến trúc (Unified Spy Collector)

```text
Meta Ad Library · TikTok Ad Library / API · Snapchat · Apify · Pipiads/Minea (API / export) · Chrome extension · Pancake · Carrier
                                + nguồn hàng TQ (AliExpress) · store Shopify đối thủ
        │ connectors (apps/api/app/collector) — chỉ authenticate · fetch · extract · map
        ▼
 Common Data Contract (collector/contract.py) → POST /api/ingest/*
        ▼
 raw storage (raw/<source>/<ngày>) → normalize → dedup + provenance (ad_sources) → product cluster
        ▼                                           ▼
 Creative Vault (media.py: tải ngay · sha256 · thumbnail · dHash family)   analytics (scoring, discovery, decision)
        ▼
 Postgres/SQLite  +  R2/local  →  API  →  Next.js (SSE realtime)
```

| Thành phần | File |
|---|---|
| BaseConnector, rate limit, retry, Retry-After | [collector/base.py](apps/api/app/collector/base.py) |
| Meta Ad Library (public), Meta API (chính thức), Apify Meta, Meta Ads insights | [collector/adapters/meta.py](apps/api/app/collector/adapters/meta.py) |
| Export folder (Pipiads/Minea), Generic REST, Apify actor, TikTok Commercial API | [collector/adapters/generic.py](apps/api/app/collector/adapters/generic.py) |
| TikTok / Snapchat Ad Library · TikTok trending · Shopify store · AliExpress (nguồn hàng TQ) | [ad_libraries.py](apps/api/app/collector/adapters/ad_libraries.py), [free.py](apps/api/app/collector/adapters/free.py), [marketplaces.py](apps/api/app/collector/adapters/marketplaces.py) |
| ConnectorFactory · registry network/channel | [collector/factory.py](apps/api/app/collector/factory.py), [platforms.py](apps/api/app/platforms.py) |
| Ingest: raw, dedup giữa các nguồn, provenance, đặt tên sản phẩm, funnel Mess/Ladi | [ingest.py](apps/api/app/ingest.py) |
| Creative Vault | [media.py](apps/api/app/media.py), [storage.py](apps/api/app/storage.py) (local / Cloudflare R2) |
| Realtime: scheduler theo tier, live search, webhook, chuẩn hoá trạng thái hãng vận chuyển, alert incremental | [realtime.py](apps/api/app/realtime.py), [events.py](apps/api/app/events.py) |
| Product Potential Vector, Taste Model, Market DNA, compliance | [services/discovery.py](apps/api/app/services/discovery.py) |
| API: tìm kiếm, vault, vote, discovery, attribution, collector admin, SSE | [routers/intel.py](apps/api/app/routers/intel.py) |
| Market Win/Saturation/Rarity, lifecycle (từ blueprint đầu) | `services/scoring.py`, `decision.py`, `engine.py` |

## Màn hình

- **Tìm & khám phá:** Tìm sản phẩm · Product Radar (bảng theo thị trường + khung thời gian: xu hướng, ads, sellers, tuổi, điểm, ad mạnh nhất, biến thể scale) (tải thêm từ nguồn, cuộn vô hạn) · Thư viện quảng cáo (từng quảng cáo kiểu Meta Ad Library: carousel, CTA, tải video) · Product Discovery (Opportunity Radar + 13 tab: Breakout, Experimental, High Wave, Creative Goldmine, MKT Favorites, Hợp MKT Mess/Ladi…) · Creative Vault · Hidden Winners
- **Thị trường:** Daily Pulse · Market Radar · Xếp hạng sản phẩm · Competitor Radar
- **Hệ thống:** Alerts · AI Agent · Data & Connectors (health, cấu hình, tracked queries, upload export, hướng dẫn ingest)

Nguyên tắc: điểm số luôn do công thức tính từ database. AI (Claude, tuỳ chọn) chỉ trích xuất dữ liệu và giải thích.

## Thị trường

Mặc định app tổng hợp **Trung Đông (SA, AE, KW, QA, OM, BH, JO, EG, IQ) · Mỹ · Châu Âu + UK · Úc / NZ · Việt Nam · Toàn cầu** — cấu hình bằng `TARGET_MARKETS` trong `.env`.
Quảng cáo ở nước khác (Đông Nam Á…) vẫn được lưu nhưng **không bao giờ lẫn vào số liệu "tất cả"** (radar, xếp hạng, tìm kiếm); muốn xem thì gõ mã nước ở ô "Nước khác".

## Realtime

| Việc | Chu kỳ |
|---|---|
| Quét từ khoá theo dõi (`python -m app.bootstrap_markets --no-run` tạo bộ từ khoá cho từng thị trường) | 60–180 phút / từ khoá |
| Xác minh quảng cáo còn chạy (hỏi lại Meta danh sách ads đang chạy của từng advertiser; 2 lần liên tiếp không thấy mới đánh dấu "đã dừng") | 20 phút |
| Market Radar, Product Radar | tính trực tiếp mỗi lần mở trang + tự làm mới khi có dữ liệu mới |
| Chấm lại điểm toàn bộ + snapshot lịch sử | 6 giờ |
| Xoá lứa video cũ, bắt đầu lứa mới (giữ ảnh bìa + thông tin) | 0h mỗi ngày (+ dọn ảnh/raw mỗi 6 giờ) |

## Video: Video on Demand (HLS) + CDN

Mỗi video quảng cáo được chuyển thành **HLS nhiều độ phân giải** (360p + 540p, đoạn 4 giây, mỗi độ phân giải là 1 file fMP4 đọc theo byte-range) rồi lưu lên R2; bản gốc bị xoá.
- Trình duyệt chỉ tải khi bấm Play, rồi tải dần từng đoạn để xem (giữ khoảng 12 giây phía trước). Mạng nhanh thì đoạn sau dùng 540p, mạng yếu tự hạ xuống 360p (hls.js; Safari dùng HLS gốc).
- "Tải video" trả về file 540p (MP4 xem được bình thường).
- **Video theo lứa mỗi ngày:** mỗi ngày lưu tối đa `VIDEO_DAILY_QUOTA` (3000) video, chỉ quảng cáo **đang chạy ở thị trường mục tiêu**, quảng cáo mạnh nhất (chạy lâu, nhiều biến thể, nhiều vị trí) được tải trước. Hết hạn mức thì video còn lại chờ sang ngày sau (chờ quá 2 ngày thì bỏ, vì link gốc đã hết hạn).
- **0h mỗi ngày** (giờ UTC+`DAY_TZ_OFFSET`, mặc định giờ VN) xoá video cũ hơn `VIDEO_KEEP_DAYS` (1 = chỉ giữ lứa hôm nay) khỏi R2, giữ ảnh bìa + thông tin, rồi bắt đầu tải lứa mới. **Không xoá:** video đã ghim 📌, sản phẩm đã vote LOVE/TEST/WATCH, quảng cáo lưu bằng extension.
- `R2_MAX_GB` (mặc định **20**) là trần cứng: chạm trần thì ngừng nhận media mới (≈ 2500 video/ngày). Cloudflare tính tiền theo trung bình dung lượng đỉnh mỗi ngày, 10 GB đầu miễn phí → chạy R2 ở trần 20 GB tốn ≈ 0,15 USD/tháng; để demo thì dùng `STORAGE_BACKEND=local` (0 đồng).
- Phân phối: `R2_PUBLIC_URL` = link r2.dev (bị giới hạn tốc độ, dùng để thử). Production: gắn **custom domain** vào bucket trong Cloudflare → file được cache ở CDN gần người xem; đổi `R2_PUBLIC_URL` sang domain đó.
- Chuyển kho cũ: `python -m app.vod_migrate --plan`, `--transcode`, `--upload`.

## Đẩy dữ liệu lên Cloudflare R2

1. Trong Cloudflare Dashboard → **R2 Object Storage** → bật R2 cho tài khoản (bắt buộc; hiện API trả "Please enable R2").
2. `.env` ở thư mục gốc đã có `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `CLOUDFLARE_API_TOKEN` (file này không được đưa lên git).
3. `cd apps/api` rồi `.venv\Scripts\python -m app.r2_sync --setup` — kiểm tra token, tạo bucket, ghi/đọc thử.
4. `.venv\Scripts\python -m app.r2_sync --upload` — đẩy video/ảnh/raw đang có lên R2 (chạy lại được, bỏ qua file đã có).
5. Đổi `STORAGE_BACKEND=local` → `STORAGE_BACKEND=r2` trong `.env`, khởi động lại app. Từ đó media mới ghi thẳng lên R2.

## Thêm dữ liệu khi không có API

1. **Meta Ad Library:** không cần API, bấm "Tìm trực tiếp trên nguồn" (chọn 50–1000 quảng cáo, "Tải thêm" để lấy tiếp từ chỗ dừng).
2. **Chrome extension** ([apps/extension](apps/extension)): `chrome://extensions` → bật Developer mode → Load unpacked → chọn thư mục `apps/extension`. Mở biểu tượng extension, điền địa chỉ app (vd `http://localhost:8000`), token và tên. Vào facebook.com/ads/library, tìm và cuộn: quảng cáo hiện ra tự được lưu (góc phải dưới có bộ đếm). Trên trang sản phẩm bất kỳ: bấm "Lưu trang này vào kho".
3. **File export** (Pipiads, Minea…): upload trong Data & Connectors.

## Gỡ dữ liệu của nguồn đã bỏ (Google, Bing, Amazon, Walmart, Shopify)

`cd apps/api` rồi `.venv\Scripts\python -m app.purge_removed` (chỉ đếm, không đổi gì) → kiểm tra số liệu → thêm `--apply` để xoá thật: ads, creative + file media (local / R2), connector, raw, sản phẩm không còn ad nào (giữ sản phẩm có vote / đơn / test). Chạy một lần ở máy local và một lần với `DATABASE_URL` + biến `R2_*` của server.

## Deploy

Không gắn với nhà cung cấp nào. Có `Dockerfile` trong `apps/api` (kèm ffmpeg) và `apps/web`, chạy được trên VPS, Docker host hay PaaS bất kỳ. Biến môi trường: [.env.example](.env.example). Các bước: [docs/DATA_SOURCES_AND_DEPLOY.md](docs/DATA_SOURCES_AND_DEPLOY.md#4-triển-khai-ở-nơi-khác).

## Bảo mật (từ 07/10/2026)

- Mọi request ghi (POST/PATCH/DELETE) vào API cần header `X-Admin-Token` = `ADMIN_TOKEN` trong `.env`; web (`apps/web/middleware.js`) tự gắn header khi proxy `/api/*`, nên chỉ cần đặt cùng `ADMIN_TOKEN` cho cả API và web. Thiếu `ADMIN_TOKEN` → API trả 503 cho mọi request ghi.
- Ingest/webhook dùng `INGEST_TOKEN` (header `X-Ingest-Token`), bắt buộc.
- CORS chỉ cho `WEB_ORIGIN` (mặc định `http://localhost:3000`).
- Nguồn trả phí (Apify) chỉ chạy khi bật "Dùng nguồn trả phí" trên trang tìm kiếm.
- Audit + nhật ký sửa: [docs/AUDIT_2026-10-07.md](docs/AUDIT_2026-10-07.md).
