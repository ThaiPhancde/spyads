# ToolSpy (Market Intelligence OS)

App nội bộ cho MKT Mess và MKT Ladi: **spy quảng cáo social (Meta, TikTok, Snapchat) để tìm sản phẩm vật lý nhập được từ Trung Quốc**, rồi kiểm chứng giá nhập (AliExpress, 1688, Taobao) để quyết định có nên test hay không. Thị trường ưu tiên số 1 là **Philippines**.

Hai việc chính:

1. **Tìm sản phẩm.** Tìm trong kho theo từ khoá, thị trường, funnel (Mess/Ladi), hoặc **quét trực tiếp mọi nguồn** đang bật. Mỗi sản phẩm là một cụm quảng cáo của nhiều đối thủ, kèm creative (ảnh bìa lưu sẵn, video phát từ nguồn và tải về được), giá bán đối thủ và giá nhập.
2. **Xếp hạng và khuyến nghị.** Công thức xác định (AI không chấm điểm) ra nhãn **TEST_NOW / TEST / WATCH / REVIEW / SKIP**, cập nhật ngay sau mỗi lần thu thập.

Ngoài phạm vi: quảng cáo Google/Bing, sàn Amazon/Walmart, spy store Shopify, video TikTok organic, dữ liệu nội bộ sau test (đơn, vận đơn, attribution). Dữ liệu của các nguồn này đã được xoá khỏi DB ngày 09/10/2026.

Phân tích nghiệp vụ và thiết kế đầy đủ: **[docs/SYSTEM_ANALYSIS.md](docs/SYSTEM_ANALYSIS.md)**. Nguồn dữ liệu và triển khai: [docs/DATA_SOURCES_AND_DEPLOY.md](docs/DATA_SOURCES_AND_DEPLOY.md).

## Chạy local

```powershell
./start.ps1      # nạp .env, tạo venv, cài deps, chạy API :8000 và Web :3000
```

Mở http://localhost:3000/search, gõ từ khoá (nhiều từ khoá: `a | b`), chọn nước rồi bấm **⚡ Tìm trực tiếp trên nguồn**. Meta Ad Library không cần key. Nguồn trả phí (Apify) chỉ chạy khi tích **"Dùng nguồn trả phí"**.

`.env` tối thiểu: `ADMIN_TOKEN`, `INGEST_TOKEN`. Danh sách đầy đủ biến môi trường: [SYSTEM_ANALYSIS.md §11.2](docs/SYSTEM_ANALYSIS.md#112-biến-môi-trường-chính).

## Kiến trúc

```text
Meta Ad Library · TikTok Creative Center / Ad Library · Snapchat (EU) · AliExpress · 1688 (AK) · Apify · export Pipiads/Minea · Chrome extension
        │ connectors (apps/api/app/collector) — chỉ authenticate · fetch · extract · map
        ▼
 AdRecord (collector/contract.py) → ingest.py: raw batch → dedup + provenance → gom cụm sản phẩm → funnel / giá / hook
        ▼                                            ▼
 media.py: ảnh bìa ≤480px (video phát từ nguồn)      services/: features → scores → potential vector → quyết định
        ▼                                            ▼
 Postgres (SQLite cho 1 người)  +  local / Cloudflare R2  →  FastAPI  →  Next.js (SSE realtime)
```

| Thành phần | File |
|---|---|
| BaseConnector, rate limit, retry | [collector/base.py](apps/api/app/collector/base.py), [factory.py](apps/api/app/collector/factory.py) |
| Meta (public, Graph API EU, Apify) | [adapters/meta.py](apps/api/app/collector/adapters/meta.py) |
| TikTok Creative Center (headless / headers), TikTok Ad Library, Snapchat | [adapters/ad_libraries.py](apps/api/app/collector/adapters/ad_libraries.py) |
| AliExpress · 1688 (AK, fallback Apify) · Apify 1688/Taobao/TikTok | [marketplaces.py](apps/api/app/collector/adapters/marketplaces.py), [ali1688.py](apps/api/app/collector/adapters/ali1688.py), [apify.py](apps/api/app/collector/adapters/apify.py) |
| Export folder, Generic REST, TikTok Commercial API | [adapters/generic.py](apps/api/app/collector/adapters/generic.py) |
| Network / channel registry | [platforms.py](apps/api/app/platforms.py) |
| Ingest: dedup, funnel Mess/Ladi, tên sản phẩm, giá trong ad | [ingest.py](apps/api/app/ingest.py) |
| Gom cụm sản phẩm | [services/entity_resolution.py](apps/api/app/services/entity_resolution.py) |
| Giá landing page + review đối thủ | [services/spy.py](apps/api/app/services/spy.py) |
| Chấm điểm và phân hạng | [services/discovery.py](apps/api/app/services/discovery.py), [scoring.py](apps/api/app/services/scoring.py), [engine.py](apps/api/app/services/engine.py), [hidden.py](apps/api/app/services/hidden.py) |
| Scheduler, live search, liveness | [realtime.py](apps/api/app/realtime.py), [events.py](apps/api/app/events.py) |
| Creative Vault, dọn kho | [media.py](apps/api/app/media.py), [retention.py](apps/api/app/retention.py), [storage.py](apps/api/app/storage.py) |
| API | [main.py](apps/api/app/main.py), [routers/intel.py](apps/api/app/routers/intel.py) |

## Phân hạng (ranking v2, 09/10/2026)

Mỗi sản phẩm có hai trục chính, tính từ quảng cáo **đang chạy** (không tính listing nguồn hàng):

- **Demand (đã được thị trường xác nhận)**: ad mạnh nhất đã chạy bao lâu (35%), đối thủ có đang nhân bản không: số ads / biến thể / ad mạnh (30%), số người bán: 1 = chưa xác nhận, 2–10 = tốt, >15 = đông (20%), số thị trường (15%).
- **Momentum (đang tăng tuần này)**: ads mới và người bán mới trong 7 ngày.

Quyết định đi theo thứ tự, dừng ở điều kiện đầu tiên khớp:

| Điều kiện | Nhãn |
|---|---|
| Không còn ad nào đang chạy (chỉ có listing nguồn hàng hoặc ad đã dừng) | SKIP |
| App / dịch vụ / form đăng ký (≥50% ads), hoặc không phải hàng vật lý | SKIP |
| Rủi ro compliance ≥ 60 (hàng nhái, claim y tế…) | REVIEW |
| >15 người bán, hoặc saturation ≥ 70 | SKIP |
| Chưa xác định được tên sản phẩm (tên là câu CTA) và có tín hiệu | WATCH |
| Demand ≥ 50, momentum ≥ 40, ad chạy ≥ 14 ngày, hợp thị trường | **TEST_NOW** |
| Momentum ≥ 40 (đang tăng nhưng chưa xác nhận) | TEST (ngân sách nhỏ) |
| Demand ≥ 55 và (≥2 người bán hoặc ≥2 biến thể) | TEST |
| Demand ≥ 45 hoặc momentum ≥ 20 | WATCH |
| Còn lại | SKIP (tín hiệu yếu) |

Sắp xếp mọi danh sách theo **opportunity** = 42% demand + momentum, phần còn lại là market fit, vote của team, novelty, creative, biên lợi nhuận, trừ điểm bão hoà và compliance, cộng 15 điểm nếu chạy ở Philippines. Lý do khuyến nghị ghi rõ số ads, số người bán và số ngày chạy. Self-check: `cd apps/api && .venv\Scripts\python test_scoring_rules.py`.

Hidden Winners là danh sách riêng: ≤5 người bán nhưng có ad mạnh (chạy lâu, nhiều biến thể, nhiều placement).

## Màn hình

- **Khám phá sản phẩm:** Tìm sản phẩm · Product Radar · Product Discovery (Opportunity Radar demand × momentum + các tab) · Hidden Winners
- **Nguồn dữ liệu:** Thư viện quảng cáo · Nguồn hàng & Store · Creative Vault
- **Thị trường:** Daily Pulse · Market Radar · Xếp hạng sản phẩm · Competitor Radar
- **Hệ thống:** Alerts · AI Agent · Data & Connectors (health, cấu hình, tracked queries, upload export, dung lượng)

## Thị trường

`TARGET_MARKETS` (mặc định): Philippines · Trung Đông (SA, AE, KW, QA, OM, BH, JO, EG, IQ) · Mỹ · EU + UK · Úc / NZ · Việt Nam. Quảng cáo ở nước khác vẫn được lưu nhưng không lẫn vào số liệu "tất cả"; muốn xem thì chọn nước đó. `PRIORITY_MARKETS=PH` và `PRIORITY_BOOST=15` đẩy sản phẩm chạy ở Philippines lên đầu.

## Chạy tự động (tiến trình `python -m app.worker`, tick 30 giây)

| Việc | Chu kỳ |
|---|---|
| Tracked queries (`python -m app.bootstrap_markets --no-run` tạo bộ từ khoá theo thị trường) | theo từng query (PH 30 phút) |
| Connector có lịch (TikTok Creative Center headless 12 giờ, export folder khi có file mới) | theo cấu hình |
| Lấy ảnh bìa creative | 2 phút |
| Alerts | 15 phút |
| Quét giá landing page + review đối thủ | 10 phút (`SPY_EVERY_MIN`) |
| Xác minh ads còn chạy (2 lần liên tiếp không thấy mới đánh dấu "đã dừng") | 20 phút |
| Chấm lại toàn bộ + snapshot ngày | 6 giờ |
| Dọn kho (video theo lứa ngày, ảnh 30 ngày, raw 90 ngày) | 6 giờ + ngay sau 0h giờ VN |

Chạy **1 API + 1 worker** (`start.ps1` mở cả hai khi `DISABLE_SCHEDULER=1`). Database: Postgres (`DATABASE_URL`); nhiều người bấm Tìm cùng lúc được.

## Creative: ảnh bìa lưu sẵn, video phát từ nguồn

Link video của Meta/TikTok có chữ ký và hết hạn sau vài giờ, nên mỗi creative được lưu ngay **một ảnh bìa JPEG ≤480px**. Bấm Play thì phát thẳng từ nguồn. Muốn giữ file: bấm tải/lưu trên creative (tải theo yêu cầu), hoặc đặt `VIDEO_DOWNLOAD=1` để tải mọi video (theo hạn mức `VIDEO_DAILY_QUOTA`). Không bao giờ tự xoá: creative đã ghim 📌, sản phẩm đã vote LOVE/TEST/WATCH, ads lưu bằng extension.

Lưu trữ: `STORAGE_BACKEND=local` (mặc định, `apps/api/data/`) hoặc `r2`. Với R2, `R2_MAX_GB` (20) là trần cứng, chia ngăn theo `STORAGE_SHARES=meta:45,tiktok:30,ads_other:10,commerce:15`.

Đẩy lên Cloudflare R2:
1. Cloudflare Dashboard → R2 Object Storage → bật R2.
2. Điền `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` (và `R2_PUBLIC_URL` nếu có custom domain) vào `.env`.
3. `cd apps/api` → `.venv\Scripts\python -m app.r2_sync --setup` (kiểm tra token, tạo bucket) → `--upload` (đẩy file đang có).
4. Đổi `STORAGE_BACKEND=r2`, khởi động lại.

## Thêm dữ liệu khi không có API

1. **Meta Ad Library:** bấm "Tìm trực tiếp trên nguồn", "Tải thêm" để lấy tiếp từ chỗ dừng.
2. **Chrome extension** ([apps/extension](apps/extension)): `chrome://extensions` → Developer mode → Load unpacked → chọn `apps/extension`. Điền địa chỉ API, `INGEST_TOKEN` và tên. Duyệt facebook.com/ads/library: quảng cáo hiện ra tự được lưu. Trên trang sản phẩm bất kỳ: "Lưu trang này vào kho".
3. **File export** (Pipiads, Minea): upload trong Data & Connectors, hoặc thả vào `apps/api/data/import/<nguồn>/`.

## Deploy

Hai Dockerfile: `apps/api` (kèm ffmpeg) và `apps/web`. Web proxy `/api/*` sang `API_URL`. Postgres qua `DATABASE_URL`. Connector TikTok Creative Center headless cần Edge/Chrome cài trên máy chạy API (image mặc định không có). Chi tiết: [docs/DATA_SOURCES_AND_DEPLOY.md](docs/DATA_SOURCES_AND_DEPLOY.md#4-triển-khai-ở-nơi-khác).

## Bảo mật

- Mọi request ghi (POST/PATCH/DELETE) cần `X-Admin-Token` = `ADMIN_TOKEN`; web (`apps/web/middleware.js`) tự gắn phía server. Thiếu `ADMIN_TOKEN` → API trả 503 cho mọi request ghi.
- Ingest (`/api/ingest/*`, extension) dùng `INGEST_TOKEN` (header `X-Ingest-Token`), bắt buộc.
- CORS chỉ cho `WEB_ORIGIN`. Web có Basic Auth tuỳ chọn (`BASIC_AUTH_USER` / `BASIC_AUTH_PASS`).
- Token Apify bị che khỏi mọi thông báo lỗi; nguồn trả phí chỉ chạy khi bật tay.
- Audit và nhật ký sửa: [docs/AUDIT_2026-10-07.md](docs/AUDIT_2026-10-07.md).

## Tiện ích vận hành

| Lệnh (`cd apps/api`) | Việc |
|---|---|
| `.venv\Scripts\python -m app.bootstrap_markets --no-run` | Tạo tracked queries theo thị trường |
| `.venv\Scripts\python -m app.purge_removed` (`--apply`) | Xoá dữ liệu của nguồn đã bỏ (đã chạy 09/10/2026; chạy lại với `DATABASE_URL` của server nếu server có DB riêng) |
| `.venv\Scripts\python -m app.r2_sync --setup / --upload` | Cloudflare R2 |
| `POST /api/pipeline/run` | Chạy lại toàn bộ pipeline chấm điểm |
