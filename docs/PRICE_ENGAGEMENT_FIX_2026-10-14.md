# Sửa Price Engine & Engagement Fallback — 2026-10-14

## 1. Nguyên nhân thật của từng lỗi (đọc từ DB / ledger, tái hiện trước khi sửa)

| Triệu chứng | Nguyên nhân | Bằng chứng |
|---|---|---|
| HTTP 500 khi bấm "Hiện giá"; 45 `price_jobs` ở trạng thái error | `pricing/identity.py local_sources` chạy `SELECT DISTINCT products.*`; cột `product_identity` là `json`, Postgres không có toán tử `=` cho json → `ProgrammingError`. Route `/api/prices/lookup` không bắt lỗi nên thành 500 | tái hiện trong process với product 13790; `price_jobs.error` cả 45 dòng cùng một câu |
| `serpapi_lens (bad_request)` | `router.rung_lens` gọi Lens với `type=products`; SerpApi đã **ngừng** loại này: `HTTP 400: The products search type has been discontinued` (chỉ còn `all` / `exact_matches` / `visual_matches`) | 4 dòng `lens_searches.error_detail`; docs SerpApi |
| Lens "chết" 6 giờ sau lỗi | `rung_lens` bỏ qua ảnh 6 giờ sau **mọi** search lỗi, kể cả lỗi do chính code | `router.py` (failed lookup) |
| Hạn mức SerpApi bị trừ oan | 400 vẫn ghi `counted=True` trong ledger (6 dòng) | `external_api_calls` |
| "không tìm được tên sản phẩm hoặc nguồn tìm giá đang tắt" | `lib/prices.ts notFoundWhy` trả đúng một câu cho mọi trường hợp không có bước SerpApi/Bright Data, kể cả khi thực ra là lỗi nội bộ / hết hạn mức / thiếu tên | code |
| Giá sai thị trường | `_from_page` giữ giá ₹569 cho sản phẩm Ả Rập ở dạng "tham khảo" | quan sát khi test live product 8461 |
| Like/Comment/Share = 0 sai | `ingest.py: int(rec.comments or 0)`. Raw lake của TikTok Creative Center có `comments: null` ở 8.743/9.490 bản ghi, Meta Ad Library không có engagement nào → mọi "không biết" thành 0 (13.717 ad) | `data/raw/tiktok_top_ads`, DB |
| Không có engagement fallback | Chưa từng có code Bright Data Posts; `views` không bao giờ được lưu | grep |

## 2. File đã sửa / thêm

Price (backend): `pricing/identity.py` (DISTINCT), `visual/lens.py` (type `products`→`all`, 400/422 không tính quota), `pricing/quota.py`, `pricing/router.py` (guard từng rung, `provider_order` + `_via_brightdata/_via_shopping` hai chiều, `near_cached_search` dùng lại search Lens của ảnh gần trùng bằng hash index có sẵn, bỏ giá sai thị trường), **`pricing/naming_hints.py` (mới: tên từ identity → bằng chứng → metadata (chỉ từ trang sản phẩm thật) → caption → OCR thumbnail → CLIP neighbour đã có tên → tiêu đề Lens, có nguồn trong step log)**, `pricing/jobs.py` + `routes.py` (không bao giờ 500; click tường minh luôn thử lại job lỗi), `pricing/brightdata.py` (timeout search 45 s).
Price (web): `lib/prices.ts` (lý do thật: thiếu tên / hết hạn mức / lỗi nội bộ / …).

Engagement (mới, `app/engagement/`): `models.py` (`ad_engagement` lịch sử + `engagement_jobs`), `mapping.py` (Ad ID → Post/Video ID chỉ khi quảng cáo tự trỏ tới bài), `brightdata_posts.py`, `service.py`, `jobs.py`, `routes.py` (`/api/engagement`, `/enrich`, `/lookup`, `/history`, `/quota`). Sửa `models.py` (likes/comments/shares nullable), `db.py` (DROP NOT NULL), `ingest.py` (None giữ None + ghi lịch sử khi đổi), `routers/intel.py` (payload `engagement`), `realtime.py` (tick ngầm), `repair_engagement.py` (sửa dữ liệu cũ, có `--rollback`).
Web: `lib/engagement.ts`, `components/ads/engagement-line.tsx`, `ad-card.tsx`, `ad-detail-sheet.tsx`.

## 3. Bright Data Facebook / TikTok Posts — đã kiểm tra thật
- `GET /datasets/list` + `/datasets/<id>/metadata` (miễn phí): **TikTok - Posts `gd_lu702nij2f790tmv9h`** (`post_id, digg_count, comment_count, share_count, play_count, collect_count`), **Facebook - Posts by post URL `gd_lyclm1571iy3mv57zw`** (`post_id, likes, num_comments, num_shares, video_view_count, play_count`). Mapping trong code dùng đúng các tên này; số dạng "1.2K"/"3,401" được đọc, thiếu/null → `None`, 0 thật giữ 0.
- Gọi thật `POST /datasets/v3/scrape` (1 request): **`HTTP 400 "Customer is not active"`** — tài khoản Bright Data này chưa kích hoạt Web Scraper API (sản phẩm trả phí theo bản ghi). Code ghi nhận `account_not_active`, không tính vào hạn mức, không gọi lại trong 6 giờ. Muốn dùng: kích hoạt tài khoản rồi đặt `BRIGHTDATA_STRUCTURED_ENABLED=true` (hạn mức riêng `BRIGHTDATA_POSTS_MONTHLY_LIMIT=30`, `..._DAILY_LIMIT=10`); chạy tự động nền chỉ khi `ENGAGEMENT_AUTO_ENABLED=true`.
- **Không có ad nào đủ điều kiện map sang Post ID hôm nay**: 0 ad có link bài đăng. ID quảng cáo Meta Ad Library và TikTok Creative Center là ID *quảng cáo*, không phải ID bài/video. Thẻ hiện N/A kèm lý do ("ID quảng cáo TikTok không phải ID video…"). Fallback chỉ chạy khi quảng cáo tự trỏ tới bài (`tiktok.com/@u/video/<id>`, `facebook.com/…/posts|videos|reel/<id>`, permalink, watch).

## 4. Trước / sau
| | Trước | Sau |
|---|---|---|
| `price_jobs` lỗi DISTINCT | 45 | 0 (đã xếp lại, 1.429 done) |
| Lens `type=products` | 4/4 lỗi 400 | search thật OK: 58 và 59 kết quả (job 16, 18) |
| Ledger SerpApi Lens tính oan | 6 | 0 (đã trả lại) |
| Ad có likes/comments/shares | 13.717 ad hiện 0 giả | NULL → N/A; TikTok CC giữ số thật (3.425 likes, 210 comments/shares) |
| Lịch sử engagement | không có | 3.480 dòng baseline + mỗi lần đổi |
| Test | 113 | 225 (112 mới trong phiên làm việc này; riêng phần price/engagement: 52) |

## 5. API requests đã tiêu thụ trong phần việc này
- Bright Data MCP (`search_engine`, free tier 5.000): **4** (2 dò link bài, 2 lần "Hiện giá" product 8461). Ledger tổng trong cửa sổ +10, phần còn lại do worker/UI của bạn đang chạy.
- Bright Data REST: 3 lệnh liệt kê (không tính phí) + **1** `scrape` bị từ chối 400 (không tính).
- SerpApi Lens: **2** search tính quota (job 16, 18; upload không tính). SerpApi Shopping: **0**. Gemini: 0.
- Tháng này: Bright Data MCP 251/5.000, SerpApi Lens 13 + Shopping 10 / 250.

## 6. Việc cần bạn
- Khởi động lại API + worker để chạy code mới (worker cũ vẫn gọi Lens `products` và ghi engagement = 0).
- Muốn có số liệu bài gốc: kích hoạt tài khoản Bright Data Web Scraper API **và** cần nguồn ad có link bài đăng (hiện chưa có).
- Tự động bấm giá cho mọi thẻ vẫn dừng ở bước miễn phí (landing/trang shop/cache/CLIP/Lens đã có); rung Bright Data/SerpApi tự động chỉ cho sản phẩm TEST trong hạn mức `PRICE_AUTO_DAILY_LIMIT=10/ngày` (hôm nay đã dùng hết 10/10).

## 7. Cập nhật: Web Scraper API đã kích hoạt + tìm bài đăng của quảng cáo bằng thuật toán của dự án
- `.env`: `BRIGHTDATA_STRUCTURED_ENABLED=true`, `BRIGHTDATA_POSTS_MONTHLY_LIMIT=30`, `BRIGHTDATA_POSTS_DAILY_LIMIT=10` (đếm *request*), `ENGAGEMENT_AUTO_ENABLED=false` (chỉ bấm "Lấy tương tác" mới tốn phí). Gọi thật sau khi kích hoạt: 202 + snapshot (không còn `Customer is not active`).
- ID quảng cáo Meta/TikTok không phải ID bài, nên bài được **tìm trong các bài đăng gần đây của chính page** (Bright Data "Facebook - Pages Posts by Profile URL", `gd_lkaxegm826bjpoo9m5`, 1 request / page, 20 bài, cache PostgreSQL `page_posts` 7 ngày) rồi khớp bằng đúng các họ bằng chứng của `services/clustering.py`: **landing** (`landing_key`) .85 · **nội dung** (chuẩn hoá, ≥ 0,9 → .85; 0,6–0,9 → .50) · **ảnh** (pHash ≤ 6 bit, chỉ tính khi chữ chưa đủ, ≤ 3 lần tải ảnh/ad) .70; điểm = 1 − Π(1 − w), nhận khi ≥ 0,90 và không có bài thứ hai sát điểm (chênh < 0,15 → "mơ hồ"). Không khớp chắc = N/A kèm lý do, không bao giờ lấy bài gần nhất. Một request phục vụ mọi ad của page; số liệu lấy từ bài đã lưu, không tốn thêm.
- Snapshot chậm được lưu (`page_fetches.pending`) và worker thu thập miễn phí (progress/snapshot không tính phí).
- Kiểm tra thật: page DavinesPh → 20 bài thật vào PostgreSQL (likes/comments/shares/views đúng trường), ad "Hair falling…" **không** bị gán bài về tóc xoăn (điểm 0) — đúng; page `61588387103789` trả 0 bản ghi (chỉ có dark post) → N/A "page không có bài", request không tính phí.
- Chưa làm: TikTok (CC chỉ có tên thương hiệu, không có link profile; TikTok Ad Library EU mới có 25 ad có profile), CLIP cho ảnh bài đăng (pHash đã đủ để quyết định; CLIP cần nạp model chỉ để nhúng ảnh bài).
- Request đã dùng cho phần này: Bright Data Posts **3** (1 TikTok snapshot sd_mv7k… vẫn chạy, 2 page: 1 rỗng không tính, 1 đã thu 20 bài).
