# Price Intelligence (SerpApi + Bright Data) — trạng thái 2026-10-11

Giá tự động chạy ngầm cho mọi card quảng cáo / sản phẩm. Không có trang riêng, không có nút "Tìm giá".
Code: `apps/api/app/pricing/` · web: `apps/web/lib/prices.ts`, `components/pricing/price-tag.tsx`.

## Luồng

1. Card render → `usePrice("ad:12")` → gom 150 ms → **một** `POST /api/prices/enrich` cho cả trang (≤ 200 subject).
2. API: subject đã có giá còn hạn → trả ngay; còn lại ghi `price_jobs` (1 dòng / subject, dedupe, ưu tiên 10 khi mở chi tiết).
3. Worker (`python -m app.worker`, mỗi 30 s, `PRICE_JOBS_PER_TICK=6`, `PRICE_WORKERS=3`) leo thang `router.resolve()`:
   - **ad**: giá sẵn trên dòng ads (copy / landing đã đọc) → landing page đọc trực tiếp (JSON-LD → meta → DOM → Shopify `.json`) → bị 403 thì Bright Data `scrape_as_markdown` → tham khảo đã có trong cluster (`product:<id>`) → Bright Data `search_engine` (giá trong snippet + đọc trực tiếp link shop đầu tiên) → SerpApi Google Shopping **chỉ khi** Bright Data lỗi / hết quota → `NOT_FOUND` (cache âm 3 ngày).
   - **product**: ad nào của sản phẩm có giá hợp lệ → landing của 1 ad → tìm theo `canonical_name`.
   - **listing** (EU): đọc `url` của listing.
4. Web poll `GET /api/prices?subjects=…` mỗi 6 s cho subject còn `queued/running`, tối đa 4 phút; không reload, không request / render.

## Phân loại (bảng `price_observations`, không ghi đè — mỗi lần nhìn thấy = 1 dòng → lịch sử giá)

| price_type | nghĩa | nguồn |
|---|---|---|
| `VERIFIED_COMPETITOR` | giá đọc từ trang của chính advertiser (seller = host) | ads row, direct |
| `VERIFIED_MARKETPLACE` | giá đọc từ trang shop / marketplace khác | direct |
| `MARKET_REFERENCE` | khoảng giá quan sát trong kết quả tìm kiếm — **chưa** chắc cùng sản phẩm | brightdata snippet, serpapi_shopping, markdown |
| `NOT_FOUND` | không nguồn nào nêu giá | — |

Gate chống giá giả (`router.plausible`, `coherent_range`, `overlap`): currency phải khớp thị trường (VND trên ad US → bỏ), 0.5–5 000 USD, khoảng giá lệch > 6× sau cắt outlier → bỏ, title kết quả phải trùng ≥ 2 từ với query, không có currency → không lưu. Đọc Markdown (Bright Data) luôn là tham khảo (confidence 0.5). Query không bao giờ lấy từ ad copy; tên > 8 từ hoặc "(chưa đặt tên)" → không tìm.

TTL: verified 7 ngày, reference 14 ngày, not-found 3 ngày. Hết hạn → card mở lại sẽ xếp hàng lại (adaptive refresh = theo nhu cầu xem; chưa có lịch refresh nền).

## Quota (ledger chung `external_api_calls`, khoá advisory PG, đặt trước khi gọi mạng)

- **SerpApi**: một pool 250/tháng (`IMAGE_SEARCH_MONTHLY_BUDGET`) cho Lens + Shopping; Lens 8/ngày, Shopping `SERPAPI_SHOPPING_DAILY_LIMIT=4`. Meter thật: `lens.account()` (hiển thị ở `/api/prices/quota`).
- **Bright Data**: `BRIGHTDATA_MONTHLY_LIMIT=5000`, `BRIGHTDATA_DAILY_SOFT_LIMIT=150`, `BRIGHTDATA_RESERVE=200` giữ lại cho việc tương tác. `BRIGHTDATA_PAID_ENABLED` / `STRUCTURED_ENABLED` = false → không bao giờ gọi Web Scraper API (`run_scraper`). Bright Data không có API đo usage → chỉ có ledger local.

## Đo thực tế 2026-10-11 (live, trong quota)

- Bright Data MCP: tools có `search_engine`, `scrape_as_markdown`, batch, `search_scrapers/run_scraper`. **Không có reverse-image** → Lens vẫn là SerpApi.
- `search_engine` geo `PH` → 9 kết quả / 10 s. Geo `US`, `GB`, `SA` → `{"organic": []}` sau 47–62 s; không geo → 9 kết quả / 6 s ⇒ code nhớ geo hỏng 6 h và tìm không geo.
- Snippet Google có giá kiểu "From ₱1,900.00 PHP" → regex `snippet_prices`.
- SerpApi Shopping: 2 lần timeout 60 s (vẫn tính quota) → timeout 30 s, chỉ gọi khi Bright Data thất bại.
- Smoke 4 subject thật: `listing:1` gymshark → VERIFIED_MARKETPLACE $40 (JSON-LD); `ad:8366` akbay.ph → VERIFIED_COMPETITOR ₱2,750; `ad:2431` coach.com → không giá (trang JS); `ad:8354` (mess) → NOT_FOUND (2 snippet lệch nhau → anomaly).
- Dữ liệu sẵn có: `products.canonical_name` phần lớn là hook ("Makapal Tingnan, Mas Healthy", "Don T Miss Out") → tìm theo tên gần như vô ích cho ads Meta/TikTok; giá thật đến từ landing page + Lens. Cần enrichment tên sản phẩm trước khi kỳ vọng coverage ≥ 80 % bằng keyword.
- Request đã dùng cho kiểm thử: Bright Data 7, SerpApi 2 (Shopping).

## Chưa làm (so với spec 4.0)

- Lịch refresh nền (§XII) — hiện refresh theo lượt xem khi hết TTL.
- Ưu tiên theo viewport (IntersectionObserver) — hiện ưu tiên theo trang (36 card) + mở chi tiết = 10.
- TikTok Shop extraction, Bright Data structured datasets (trả phí), Google Product API.
- Marketplace API theo quốc gia (Shopee/Lazada/Noon…) — không có API công khai; chỉ qua SERP + trang công khai.
- Benchmark coverage theo quốc gia / category (§XVII) — cần tập test có tên sản phẩm thật.

Tự kiểm: `.venv/Scripts/python test_price_intelligence.py` (offline, SQLite tạm).
