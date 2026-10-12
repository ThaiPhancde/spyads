# SPYADS — EU MARKET INTELLIGENCE

> Tài liệu đặc tả triển khai (09/10/2026). Mục tiêu: tạo **một chỉ mục cấp cao `EU Market`** độc lập với `Search`/`Market`, chuyên theo dõi mua bán, xu hướng, giá, sản phẩm hiếm và nguồn hàng tại châu Âu. Không biến EU Market thành một shop bán hàng hoặc nguồn quảng cáo giả định.

## 1. Mục tiêu và nguyên tắc

- Một mục menu lớn **EU Market** trong sidebar, không phải bộ lọc con của Search.
- Phục vụ marketer: tìm sản phẩm mới, sản phẩm bán chạy, biến động giá, nhà cung cấp, sản phẩm hiếm/độc lạ; đối chiếu quảng cáo Meta/TikTok nếu có bằng chứng.
- Thu thập **metadata, thumbnail URL, product URL**; không tải/mã hóa video hàng loạt.
- Tách biệt: `marketplace_listing` (sản phẩm rao bán), `supplier_offer` (nguồn nhập), `ad_creative` (quảng cáo), `trend_signal` (tín hiệu xu hướng). Không gọi listing là ads.
- Mọi số liệu phải gắn `source`, `observed_at`, `country`, `confidence`, `collection_method`. Không suy diễn số lượng bán từ thứ hạng tìm kiếm.
- API chính thức trước; chỉ dùng dữ liệu được cấp quyền. Không né CAPTCHA, hạn chế truy cập hoặc điều khoản dịch vụ.

## 2. Menu/UI mới

```text
SPYADS
├── Dashboard
├── Search Ads
├── Markets
├── EU Market                 <-- MENU CẤP CAO RIÊNG
│   ├── Overview             (tổng quan EU)
│   ├── Product Explorer     (tìm kiếm đa từ khóa)
│   ├── Trending             (xu hướng theo quốc gia/ngành)
│   ├── Price Intelligence   (giá bán, lịch sử, chênh lệch)
│   ├── Rare Finds           (mặt hàng hiếm/độc lạ)
│   ├── Wholesale & Suppliers (nguồn nhập hàng B2B)
│   ├── Competitor Shops     (shop và listing công khai)
│   ├── Ad Matches           (liên kết với kho SpyAds)
│   ├── Watchlists & Alerts  (theo dõi giá, xu hướng)
│   └── Data Sources         (kết nối API, trạng thái, quyền)
└── Settings
```

**Route Next.js đề xuất:** `/eu-market`, `/eu-market/products`, `/eu-market/trending`, `/eu-market/prices`, `/eu-market/rare`, `/eu-market/suppliers`, `/eu-market/shops`, `/eu-market/ad-matches`, `/eu-market/watchlists`, `/eu-market/sources`.

**Bộ lọc chung:** quốc gia (DE, FR, IT, ES, NL, BE, PL, UK, SE, RO, HU, BG...), nguồn, ngành, giá/currency, seller, thương hiệu, từ khóa đa ngôn ngữ, ngày cập nhật, độ tin cậy, có/không có quảng cáo liên quan. Mỗi thẻ sản phẩm: ảnh nhỏ, tên, giá, giá quy đổi tham khảo, nguồn, quốc gia, timestamp, nút mở trang gốc, lịch sử giá, lý do gợi ý.

## 3. Nguồn thị trường và mức độ API

| Nguồn | Loại | API / truy cập | Dùng cho EU Market | Lưu ý |
|---|---|---|---|---|
| Kaufland | Marketplace | Seller API chính thức | Catalog, lookup, giá khi quyền cho phép | Key/HMAC; không mặc nhiên có toàn bộ dữ liệu đối thủ |
| bol | Marketplace NL/BE | Retailer API chính thức | Product discovery/competitive catalog theo quyền | OAuth/retailer authorization; kiểm tra phiên bản hiện hành |
| Allegro | Marketplace PL | REST API chính thức | Offer/catalog theo quyền ứng dụng | Một số endpoint listing bị hạn chế; xác minh trước |
| BigBuy | B2B/dropship | API thương mại | Nguồn nhập, catalog, giá sỉ | Tài khoản/gói/quyền; không phải chỉ báo doanh số |
| eMAG | Marketplace RO/HU/BG | Seller API | Tích hợp seller/catalog được phép | Không đồng nghĩa có competitor search công khai |
| OTTO | Marketplace DE | Partner API | Tích hợp partner | Không ưu tiên competitor intelligence |
| Cdiscount | Marketplace FR | Seller API | Catalog seller được cấp quyền | Cần seller credentials |
| ManoMano | Marketplace | Partner integration | Home/DIY | Xác minh phạm vi truy cập |
| Zalando | Fashion marketplace | Partner API | Fashion partner | Hạn chế với phân tích đối thủ |
| Faire | B2B wholesale | Partner integrations | Sourcing | Không giả định API catalog mở |
| Ankorstore | B2B wholesale | Tích hợp đối tác | Thương hiệu, sourcing | Có thể cần xác minh retailer |
| Orderchamp | B2B wholesale | Tích hợp đối tác | Sourcing lifestyle | Cần xác minh API/quyền |
| Merkandi | B2B clearance | Chưa xác minh API catalog công khai | Hàng thanh lý/hiếm | Không tự động scrape khi chưa có quyền |
| Europages / wlw | B2B directories | Chưa xác minh API catalog công khai | Tìm nhà cung cấp | Có thể là lead directory, không phải giá sỉ |
| SoloStocks | B2B wholesale | Cần xác minh | Nguồn hàng ES | Kiểm tra hợp đồng/API |
| OnBuy | Marketplace UK | Cần xác minh phạm vi API | UK product research | Không giả định public competitor API |

**Không đánh đồng** “có Seller API” với “API lấy toàn bộ sản phẩm đối thủ miễn phí”. Trước khi bật connector phải kiểm tra credentials, endpoint, scope, hạn mức, ToS và quyền lưu trữ dữ liệu.

## 4. Mã nguồn GitHub tham khảo

> Đây là repository SDK/mẫu tích hợp, **không phải ứng dụng SpyAds hoàn chỉnh**. Tình trạng endpoint, license, maintenance và khả năng chạy thực tế phải được kiểm tra khi triển khai.

| Marketplace | Repository | Ngôn ngữ | Phần cần học/tái sử dụng | Lưu ý |
|---|---|---|---|---|
| Kaufland | https://github.com/saleweaver/python-kaufland-api | Python | HMAC signing, base client sync/async, headers, error handling | Theo AGENTS.md, scope hiện tập trung base client; chưa mặc định đủ endpoint nghiệp vụ |
| bol | https://github.com/pennersr/python-bol-api | Python | Client, response parsing | Có thể dùng API cũ, phải kiểm tra |
| bol | https://github.com/Soneritics/BolRetailerAPI | C# | Luồng Retailer API, model/endpoint | Chỉ tham khảo logic nếu backend Python |
| BigBuy | https://github.com/shopiqo/bigbuy-oas | OpenAPI | Sinh client, schema, endpoint discovery | Kiểm tra schema và hợp đồng API mới |
| Allegro | https://github.com/conviu/allegro-api | Không ưu tiên Python | Authentication, offer API mẫu | Không giả định endpoint cũ còn hoạt động |
| Allegro | https://github.com/kmi3c/allegro-api | Ruby | REST client/offer request | Đối chiếu docs hiện hành |
| eMAG | https://github.com/chawel/python-emag | Python | Seller API client, authentication | Kiểm tra phiên bản và phạm vi dữ liệu |

Tài liệu chính thức: https://sellerapi.kaufland.com/?page=rest-api ; https://api.bol.com/retailer/public/redoc/v10/retailer.html  ; https://developer.allegro.pl/ ; https://www.bigbuy.eu/ .

**Thứ tự nghiên cứu:** Kaufland → bol → BigBuy → Allegro → eMAG; thứ tự tích hợp thực tế tùy **quyền API đã có** và bài test dữ liệu.

## 5. Kiến trúc thực thi

```text
[EU Market UI (Next.js/TSX)]
       | search / filters / watchlists
[FastAPI EU Market Router]
       |
[Search & Intelligence Service]
       |--------------------------|
[Connector Registry]       [Existing SpyAds Ad Index]
  | Kaufland                     | Meta / TikTok
  | bol                          | chỉ dùng ads đã thu hợp lệ
  | BigBuy                       |
  |                              v
  | Allegro                 [Ad Matcher]
  | eMAG                        |
  v                             |
[Rate-limit + Retry + Auth]      |
       v                         |
[Normalize + Deduplicate + Entity Resolution]
       |
[DB listings / products / suppliers / price_history / trend_signals]
       |
[Trend Engine / Price Engine / Rare Finds Engine]
       |
[API response + cached thumbnails + evidence + confidence]
```

**Không cần Docker hoặc n8n để chạy MVP.** Có thể chạy worker Python riêng bằng Task Scheduler trên Windows, hoặc APScheduler/queue phù hợp. Không cho tác vụ đồng bộ lớn chạy trong request web. Dùng httpx async, giới hạn concurrency theo nguồn, backoff với 429/5xx, checkpoint cursor, idempotent upsert. Không dùng FFmpeg để tải và mã hóa video.

### Cấu trúc file

```text
apps/
  api/
    app/
      modules/eu_market/
        api/routes.py
        schemas/product.py
        schemas/search.py
        connectors/base.py
        connectors/kaufland.py
        connectors/bol.py
        connectors/bigbuy.py
        connectors/allegro.py
        connectors/emag.py
        services/normalize.py
        services/deduplicate.py
        services/search.py
        services/trends.py
        services/pricing.py
        services/rarity.py
        services/ad_match.py
        services/watchlists.py
        jobs/sync_catalog.py
        jobs/snapshot_prices.py
        models.py
        tests/
  web/
    app/eu-market/
      page.tsx
      products/page.tsx
      trending/page.tsx
      prices/page.tsx
      rare/page.tsx
      suppliers/page.tsx
      shops/page.tsx
      ad-matches/page.tsx
      watchlists/page.tsx
      sources/page.tsx
    components/eu-market/
```

Đường dẫn chỉ là gợi ý; IDE phải **kiểm tra cấu trúc repo hiện có trước**, không tạo trùng router/model/service.

## 6. Mô hình dữ liệu

**Bảng:** `eu_sources`, `eu_products`, `eu_listings`, `eu_suppliers`, `eu_price_snapshots`, `eu_trend_signals`, `eu_product_matches`, `eu_ad_matches`, `eu_watchlists`, `eu_sync_runs`, `eu_fx_rates`.

- `eu_products`: `id`, `canonical_title`, `brand`, `category`, `gtin/ean`, `image_url`, `attributes_json`, `created_at`.
- `eu_listings`: `id`, `product_id`, `source`, `source_product_id`, `seller_id`, `market_country`, `currency`, `price`, `shipping_price`, `availability`, `url`, `thumbnail_url`, `observed_at`, `last_seen_at`, `raw_metadata_ref`.
- `eu_price_snapshots`: `listing_id`, `price`, `shipping_price`, `currency`, `captured_at`.
- `eu_trend_signals`: `product_id`, `country`, `signal_type`, `value`, `sample_size`, `source`, `captured_at`, `confidence`.
- `eu_ad_matches`: `product_id`, `ad_id`, `match_method`, `score`, `evidence`, `review_status`.
- `eu_sync_runs`: `connector`, `started_at`, `ended_at`, `status`, `items_read`, `items_written`, `error_code`, `next_cursor`.

**Ví dụ JSON chuẩn:**

```json
{
  "source": "kaufland",
  "source_product_id": "example-id",
  "market_country": "DE",
  "title": "Lucky Bracelet",
  "price": 19.99,
  "currency": "EUR",
  "thumbnail_url": "https://example.com/image.jpg",
  "product_url": "https://example.com/product",
  "seller_id": null,
  "ean": null,
  "observed_at": "2026-10-09T00:00:00Z",
  "availability": "unknown",
  "data_access": "authorized_api",
  "matched_ads": []
}
```

Không lưu `sales_count`, `conversion_rate`, `revenue` nếu không có dữ liệu thật. Nếu dùng ước lượng, đặt tên rõ `estimated_*`, kèm phương pháp và confidence.

## 7. Tính năng chi tiết

### 7.1 Product Explorer / tìm kiếm đa từ khóa

- Cho nhập nhiều từ khóa: `feng shui bracelet`, `lucky bracelet`, `bracelet porte-bonheur`, `Glücksarmband`.
- OR giữa các từ đồng nghĩa; AND cho bộ lọc quốc gia/giá/danh mục; loại bỏ trùng EAN hoặc theo title+image similarity.
- Kết quả theo listing, có nhóm sản phẩm chung và các offer khác nhau.
- Không dịch máy và gộp các sản phẩm khác nhau chỉ vì cùng từ khóa.

### 7.2 Trending

**Trend score chỉ là tín hiệu nội bộ**, ví dụ chuẩn hóa 0–100 từ: tăng số listing mới, tăng mức hiện diện qua các nguồn, biến động thứ hạng nếu có, thay đổi giá, tăng số quảng cáo match có xác nhận. Ghi rõ tín hiệu nào có/thiếu; không gọi là số bán thật. So sánh 7/30/90 ngày chỉ khi đã có đủ lịch sử.

### 7.3 Price Intelligence

- Lịch sử giá theo listing, cùng currency; hiển thị giá gốc và giá quy đổi theo tỷ giá có timestamp.
- `landed_cost_estimate` chỉ khi có shipping/tax/duty đủ dữ liệu; không so sánh giá sỉ chưa VAT với giá lẻ gồm VAT mà không ghi chú.
- Alert: giảm giá > X%, chênh lệch nguồn > Y%, tăng giá liên tục.
- Biểu đồ dùng snapshot thật; nếu chỉ có 1 snapshot thì không hiển thị “xu hướng giá”.

### 7.4 Rare Finds / hàng hiếm, độc lạ

- Điểm gợi ý dựa trên ít listing tương đương trong **phạm vi nguồn đã thu thập**, sự khác biệt ảnh/thuộc tính, sự mới xuất hiện, khoảng giá và độ phủ quốc gia.
- Phân biệt `rare_in_dataset` với `rare_in_market`; tuyệt đối không khẳng định hàng hiếm toàn EU từ mẫu dữ liệu nhỏ.
- Có bộ lọc: unusual design, novelty, niche, limited supply, emerging; đánh dấu thủ công của marketer.
- Loại trừ sản phẩm bị cấm, hàng giả xâm phạm thương hiệu, hàng nguy hiểm/không tuân thủ quy định; không khuyến nghị kinh doanh hàng vi phạm.

### 7.5 Wholesale & Suppliers

- Phân loại `manufacturer`, `distributor`, `wholesale_marketplace`, `dropshipper`, `liquidation`.
- Trường cần có: MOQ, lead time, quốc gia xuất kho, VAT, shipping, nguồn giá, điều kiện truy cập (nếu có).
- Liên kết một sản phẩm retail với nguồn nhập chỉ khi EAN/thuộc tính/ảnh phù hợp, có confidence.

### 7.6 Competitor Shops & Ad Matches

- Theo dõi shop/offer có thể truy cập hợp lệ: giá, thay đổi listing, danh mục, thời điểm quan sát.
- Ghép quảng cáo hiện có bằng EAN/URL (mạnh), ảnh perceptual hash (trung bình), tiêu đề/keyword (yếu).
- Chỉ hiển thị “đã chạy ads” khi có ad record thực; match yếu cần review. Không suy diễn doanh thu, ROAS, số đơn từ creative.

## 8. API nội bộ FastAPI (đề xuất)

```text
GET  /api/eu-market/overview
GET  /api/eu-market/products?q=&keywords=&countries=&sources=&page=
GET  /api/eu-market/products/{id}
GET  /api/eu-market/trending?country=&window=30d
GET  /api/eu-market/prices?product_id=&window=90d
GET  /api/eu-market/rare?country=&min_confidence=
GET  /api/eu-market/suppliers?country=&category=
GET  /api/eu-market/shops?source=&country=
GET  /api/eu-market/ad-matches?product_id=
GET  /api/eu-market/sources
POST /api/eu-market/watchlists
POST /api/eu-market/admin/sync   (admin-only)
GET  /api/eu-market/admin/sync-runs
```

**BaseConnector interface** (pseudo-code, không gán endpoint ngoài khi chưa xác minh):

```python
from typing import Protocol

class EUMarketConnector(Protocol):
    source: str
    async def healthcheck(self) -> dict: ...
    async def search_products(self, query: str, country: str, cursor: str | None = None) -> dict: ...
    async def get_product(self, source_id: str, country: str) -> dict: ...
```

Connector không hỗ trợ search phải trả `unsupported_capability`, không âm thầm dùng dữ liệu giả. Cần phân biệt lỗi `not_configured`, `unauthorized`, `rate_limited`, `temporarily_unavailable`, `unsupported_capability`.

## 9. Quy trình thực thi theo giai đoạn

### P0 — Audit hiện trạng (bắt buộc)

1. Đọc router, DB schema, worker, auth, frontend navigation hiện có; ghi danh sách thành phần có thể tái sử dụng.
2. Xác minh API scope, credentials, endpoint, quota, ToS và license repo mẫu. Không copy nguyên repo vào production.
3. Tạo `EU Market` sidebar và feature flag `EU_MARKET_ENABLED`.

### P1 — MVP thật (1–2 nguồn đã có quyền)

1. Tạo schema/migration và base connector, source registry.
2. Kết nối nguồn có credentials hợp lệ, ưu tiên Kaufland hoặc bol nếu thực sự cho phép tìm kiếm catalog; BigBuy cho nguồn nhập.
3. Test 10 sản phẩm/từ khóa, 2 quốc gia nếu nguồn hỗ trợ: `id, title, price, currency, thumbnail, URL, timestamp`.
4. Tạo Product Explorer + Sources health dashboard, cache, pagination, rate limiting.
5. Thêm test mapping, dedup, auth, retry, lỗi 429/5xx.

### P2 — Price + Trend

1. Job snapshot theo hạn mức/ToS, tránh đồng bộ quá dày.
2. Price history, so sánh giá hợp lệ, cảnh báo biến động.
3. Trend score có provenance và cờ `insufficient_data`.

### P3 — Rare + Supplier + Ad Matching

1. Rare Finds scoring với explainability.
2. Supplier matching có review thủ công.
3. Match vào kho quảng cáo Meta/TikTok hiện có, không yêu cầu tải video.
4. Watchlist cá nhân theo marketer, lưu preferences có thể chỉnh sửa.

### P4 — Scale

1. Mở rộng Allegro, eMAG theo quyền API được duyệt.
2. Monitoring latency, quota, freshness, duplicate rate, API costs.
3. Index/search phù hợp quy mô dữ liệu; không thêm hạ tầng phức tạp khi chưa cần.

## 10. Acceptance criteria

- `EU Market` xuất hiện như **một mục sidebar cấp cao độc lập**.
- Search đa từ khóa + country + source; không trả dữ liệu giả.
- Có tối thiểu 1 connector gọi được API thật bằng credentials hợp lệ và trả ≥10 sản phẩm trong bài test nếu nguồn có dữ liệu.
- Mọi listing có nguồn, thời điểm quan sát, URL; dữ liệu thiếu hiện `unknown`.
- Price chart chỉ có dữ liệu snapshot thật; trend/rarity giải thích được cách tính.
- UI vẫn chạy nếu connector lỗi; lỗi hiển thị rõ và không làm hỏng Search Ads hiện có.
- Không download/transcode video; thumbnail chỉ tham chiếu URL theo quyền sử dụng.
- Có unit tests cho normalize, dedup, price calculation, scoring; integration tests có mock, smoke test API thật tùy credentials.
- Không ghi secret ra frontend, log, git; phân quyền admin cho sync và credential management.

## 11. Prompt giao IDE/agent

> Hãy triển khai module **EU Market Intelligence** trong repo SpyAds hiện có (FastAPI backend, Next.js 14/React 18/Tailwind frontend). Đọc và audit repo trước khi sửa. Tạo một sidebar top-level **EU Market** độc lập với Search Ads/Markets, gồm Overview, Product Explorer, Trending, Price Intelligence, Rare Finds, Wholesale & Suppliers, Competitor Shops, Ad Matches, Watchlists và Data Sources. Tuân thủ tài liệu này; tạo migration, base connector, normalized models, source registry, API endpoints, UI, tests. Ưu tiên tích hợp API chính thức của Kaufland/bol/BigBuy tùy credentials có sẵn, không hardcode API keys, không bịa endpoint hay số liệu. Nếu thiếu quyền API, dựng connector ở trạng thái `not_configured` và ghi chính xác biến môi trường cần cung cấp, không dùng mock data giả như dữ liệu production. Dùng metadata, thumbnail URL, product URL; không tải/transcode video. Tái sử dụng các thành phần hiện có và tránh tạo service trùng. Triển khai theo từng phase nhỏ, chạy tests và báo file đã sửa, kết quả, blockers.

## 12. Những thông tin cần bổ sung khi chạy thật

- Kaufland client/secret, bol retailer OAuth, BigBuy credentials, Allegro OAuth, eMAG seller access **chỉ khi đã được cấp**.
- Danh sách quốc gia EU ưu tiên; ngân sách/quota API; DB hiện dùng; cơ chế job/worker hiện có.
- Không gửi key trực tiếp vào chat hoặc commit vào Git. Dùng `.env` nội bộ/secret manager.

## 13. Nguồn tham khảo

- Kaufland API: https://sellerapi.kaufland.com/?page=rest-api
- Kaufland wrapper: https://github.com/saleweaver/python-kaufland-api
- bol API: https://api.bol.com/retailer/public/redoc/v10/retailer.html
- Allegro developer: https://developer.allegro.pl/
- BigBuy: https://www.bigbuy.eu/

---
**Ghi chú xác minh:** các repository và tài liệu là điểm bắt đầu để audit; việc tồn tại SDK không bảo đảm endpoint còn dùng được, có thể lấy dữ liệu đối thủ hoặc có quyền miễn phí. Không triển khai bất kỳ connector nào trước khi xác minh capability thực tế.
