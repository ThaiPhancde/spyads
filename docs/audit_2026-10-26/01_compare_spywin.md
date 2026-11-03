# 01 — So sánh ToolSpy với SpyWinAds (2026-10-26)

Nguồn đối thủ: `docs/research/spywinads_posts_2026-10-26.md` (4 bài của Ánh Nguyễn, quét bằng Playwright).
Phạm vi: chỉ đọc code ToolSpy (không sửa code, không bật/tắt server). **Không truy vấn được Postgres**: mật khẩu trong
`.env` lẫn trong memory note đều bị từ chối (`password authentication failed`), nên mọi kết luận dưới đây dựa trên
code, không có số liệu đếm từ DB.

Các quyết định đã chốt (không tính là lỗi): không eBay/Amazon/Shopify làm *nguồn*; không lưu video hàng loạt; không tự
ký TikTok user-sign; không dữ liệu giả; chỉ tài khoản MKT; EU Market giữ code nhưng ẩn khỏi UI.

---

## 0. Kết luận nhanh

SpyWin trả lời đúng 4 câu: *video mới hay cũ · trang bán mới hay cũ · bao nhiêu shop đang đổ tiền thật · lấy hàng ở đâu*.
ToolSpy lấy **sản phẩm** làm trung tâm, không phải **video**, nên:

| Câu hỏi của SpyWin | ToolSpy hôm nay |
|---|---|
| Video mới hay cũ? | **Không trả lời được.** Chỉ có tuổi từng ad (Meta `start_date`) và tuổi sản phẩm = ad sớm nhất *mà ToolSpy đã thu*. Không có thực thể "video", và các lượt quét chỉ lấy ad **đang chạy** nên ad cũ đã tắt (mốc sớm nhất) bị bỏ sót một cách có hệ thống. |
| Trang bán mới hay cũ? | **Không có.** `Store.first_seen_at` = ngày của ad đầu tiên trỏ vào domain, không phải ngày tạo trang. |
| Bao nhiêu shop đang đổ tiền? | **Có một phần**, ở cấp sản phẩm (`advertiser_count`, `new_advertisers_7d`), nhưng bị thổi phồng vì ad đã tắt vẫn bị coi là "đang chạy" (xem F1). |
| Lấy hàng ở đâu? | **Có một phần**: listing 1688/AliExpress tìm theo từ khoá + tìm bằng ảnh, nhưng không có quy trình báo giá (MOQ, thời gian sản xuất, ship). |

Lỗ hổng lớn nhất nằm ở chất lượng dữ liệu, không phải ở tính năng: **ad một khi đã vào DB thì không bao giờ được đánh
dấu là đã tắt**, trừ khi page đó được follow (F1). Lỗi này làm sai luôn "ad chạy lâu nhất", điểm force, demand, nhãn
TEST_NOW, Stable winner và Hidden Winner. Cần sửa F1 trước khi thêm bất kỳ tính năng nào kiểu SpyWin.

---

## 1. So sánh từng năng lực

### 1.1 Tuổi thật của video = mốc sớm nhất trong (a) ngày upload file lên FB, (b) ad sớm nhất trên mọi shop dùng video đó, (c) tính cả ad đã tắt

| Thành phần | ToolSpy | Bằng chứng |
|---|---|---|
| (a) ngày upload file video | Không có. Không lưu video id hay ngày upload. Payload Ad Library chỉ được map thành URL + preview. | `collector/adapters/meta.py:146-150` |
| (b) ad sớm nhất dùng video, trên mọi advertiser | Không có ở cấp video. Ở cấp sản phẩm: `Product.first_seen_at = min(ad.first_seen_at)`, nhưng chỉ cập nhật lúc **tạo** ad. | `ingest.py:382-383` |
| (c) tính cả ad đã tắt | **Bị chặn từ gốc**: `FetchParams.active_only=True` mặc định, mà cả live search lẫn lịch quét đều không đổi giá trị này. Chỉ page được follow mới quét `active_only=False`. | `collector/base.py:67`; `realtime.py:195` (live search); `realtime.py:48-51,134` (lịch quét); `workspace/sources.py:60,80` (follow) |
| Nghĩa của `first_seen_at` | Không thống nhất: với Meta là `start_date` (ngày bắt đầu chạy thật); với TikTok Top Ads là **ngày ToolSpy thu về** (adapter không truyền `first_seen`, nên `_dt(None, now)` trả về `now`). | `ad_libraries.py:465-480`; `ingest.py:339,359`; `services/connectors.py:139-141` |
| Refresh không bao giờ lùi `first_seen_at` | Nếu một ad được biết lần đầu qua nguồn không có ngày (TikTok, `now`) rồi nguồn khác báo ngày bắt đầu sớm hơn, nhánh refresh vẫn không cập nhật. | `ingest.py:268-324` (không ghi `first_seen_at`) |

**Đề xuất (theo thứ tự rẻ trước):**
1. Trên nhánh refresh của `_upsert_ad`: `if rec.first_seen and _dt(rec.first_seen) < ad.first_seen_at: ad.first_seen_at = ...`, rồi kéo theo `product.first_seen_at`. Một dòng, sửa luôn (b).
2. Thêm tham số `active_only=False` cho các lượt quét **theo lịch** (tracked query), giữ `True` cho live search để nhanh. Ad Library trả về `is_active` + `end_date` cho từng ad, nên vừa có mốc sớm nhất (c) vừa biết ad "vừa tắt".
3. Lưu mốc tuổi của video lên thực thể video ở §1.5 (`video_first_seen = min(first_seen_at)` của mọi ad trong cùng nhóm video), **không** lưu lên sản phẩm.
4. (a) thì bỏ qua: Ad Library công khai không cung cấp ngày upload; muốn có phải gọi Graph API `/{video-id}` cần token. Ghi rõ là "không làm".

### 1.2 Tuổi trang bán (cả nền tảng giấu: Shopify `created_at`, Shoplazza/nền tảng TQ qua ID tăng dần), tô đỏ khi trang cũ hơn video > 14 ngày

| Thành phần | ToolSpy | Bằng chứng |
|---|---|---|
| Thực thể Store | Khoá theo `(domain, product_id)`; `first_seen_at` = ngày của ad đầu tiên trỏ vào domain. Không biết trang được tạo khi nào. | `services/connectors.py:188-200`; `ingest.py:340` |
| Shopify | `fetch_landing` **đã** gọi `/products/<handle>.json`, nhưng chỉ khi thiếu giá, và chỉ đọc `variants`. Bỏ qua `created_at` / `published_at` có ngay trong cùng phản hồi. | `services/spy.py:128-134` |
| Nhận diện nền tảng landing (Shopify/Shoplazza/Ladipage/Woo) | Chỉ so hostname. Mục P1 "landing fingerprint" trong audit 07/10 **vẫn chưa làm**. | `ingest.py:46` (`LADI_HOSTS`); `docs/AUDIT_2026-10-07.md:229` |
| Red flag "trang cũ hơn video >14 ngày" | Không có. | — |

**Đề xuất:** `fetch_landing` vốn đã chạy mỗi 10 phút (`realtime.py:344-345`, `SPY_URLS_PER_RUN=40`) và đã có sẵn nhánh
Shopify. Chỉ cần (1) luôn gọi `.json` khi path chứa `/products/`, (2) lưu `published_at` vào `Store` (thêm cột
`page_published_at`, `page_platform`), (3) nhận diện nền tảng qua HTML (`cdn.shopify.com`, `shoplazza`, `ladicdn`,
`wp-content`). Đây là đọc trang landing mà ToolSpy đã fetch, **không** phải thêm nguồn Shopify (quyết định "không
Shopify" vẫn giữ). Với Shoplazza, "ID tăng dần → ngày" cần một bảng hiệu chuẩn (ID↔ngày) học từ các trang có ngày thật.
Phần này để sau, có ghi chú `ponytail:`. Cờ đỏ được tính lúc hiển thị: `page_published_at < video_first_seen - 14d`.

### 1.3 Fulfillment / nguồn hàng (gửi ảnh/link → xưởng TQ báo giá 24–48h)

| Thành phần | ToolSpy | Bằng chứng |
|---|---|---|
| Listing nguồn hàng TQ | 1688 (AK API chính thức, Apify làm dự phòng), AliExpress, Taobao. Tìm theo **từ khoá**, lưu thành `ads` với `channel=commerce`. | `collector/adapters/ali1688.py:1-37`; `platforms.py:22-24` |
| Ảnh → nguồn hàng | Có: trang `/visual` chế độ `find_suppliers`, mở được từ sản phẩm và từ ad. | `web/app/products/[id]/page.tsx:196`; `web/components/ads/ad-detail-sheet.tsx:84`; `web/components/visual/results.tsx:29` |
| Giá nhập / margin | `supplier_price_usd` = giá **thấp nhất trong mọi listing commerce** (1688/Taobao/AliExpress); margin ≈ ×1.6. Nhãn UI ghi "Giá AliExpress thấp nhất", sai nguồn: giá sỉ 1688 thường thấp nhất nên margin bị đánh giá cao hơn thực tế. | `services/features.py:207,217`; `services/scoring.py:115-116`; `web/components/media.tsx:216`; `web/app/products/[id]/page.tsx:40` |
| Báo giá (MOQ, thời gian SX, ship), handoff cho agent | Không có. | — |

**Đề xuất:** sửa nhãn thành "Giá nguồn thấp nhất (1688/Taobao/AliExpress)", hoặc tách riêng AliExpress (giá lẻ ≈ giá
landed) với 1688 (giá sỉ, cần cộng ship/agent). Nếu muốn có workflow báo giá, chỉ cần thêm nút "Gửi yêu cầu báo giá"
xuất brief (ảnh + link + listing 1688 khớp nhất) cho người đặt hàng. Không nên tự build sàn báo giá.

### 1.4 Dữ liệu tươi: quét Meta Ad Library theo từ khoá ngay khi cần + quét lại page đang theo dõi lúc 1h sáng mỗi đêm

| Thành phần | ToolSpy | Bằng chứng |
|---|---|---|
| Live scan theo từ khoá | **Có**, tương đương SpyWin: trang Search tạo `SearchJob` và chạy song song trên các nguồn, có cursor "load more", nhiều từ khoá bằng `\|`. | `realtime.py:144-195`; `models.py:315-337` |
| Rescan page được follow | **Có**: `ScanJob` chạy theo `Follow.interval_hours` (mặc định 24h, lịch trượt chứ không cố định 1h sáng), sinh event `new_ad` / `ad_inactive` / `ad_changed` / `ad_unobserved`. | `workspace/worker.py:30-34`; `workspace/tracking.py:85-113` |
| Liveness cho page **không** follow | Chạy 20 phút/lần, 12 page mỗi lượt, `active_only=True`. Ad không còn thấy chỉ tăng `verify_misses`, **không bao giờ** chuyển `is_active=False`, và `verify_misses` không được đọc ở đâu cả (xem F1). | `realtime.py:436-439,632-726`; `models.py:193`; `ingest.py:290` |
| TikTok | Không có liveness. Top Ads đặt `active=True` cố định. | `ad_libraries.py:477`; `realtime.py:646` (liveness chỉ chạy `meta_library`) |

**Đề xuất:** xem F1. Không cần thêm lịch 1h sáng: lịch trượt 24h là đủ, chỉ cần cho phép chọn giờ nếu MKT yêu cầu.

### 1.5 Nhận diện cùng một video giữa nhiều advertiser (visual fingerprint), đếm bao nhiêu page chạy, ai vào sớm / ai mới vào tuần này

| Thành phần | ToolSpy | Bằng chứng |
|---|---|---|
| `Creative.family_id` (dHash) | Chỉ được gán trong nhánh **tải đủ file** (`_process`). Mặc định không tải video (`VIDEO_DOWNLOAD=0`), ảnh chỉ tải khi có người bấm. Nhánh thumbnail (`grab_thumb`) không tính phash và không gán family. Vì vậy với video, family gần như không tồn tại. Docs tự thừa nhận "Family không bao giờ hiện". | `media.py:317-318,367-377` (assign_family); `media.py:441-456` (enqueue chỉ lấy thumbnail); `media.py:380-409` (grab_thumb); `docs/DAC_TA_HE_THONG.md:396,548,711` |
| Hash ảnh bìa (ProductImage phash + CLIP) | Có, chạy trên thumbnail mỗi phút, nhưng chỉ dùng **bên trong một cụm sản phẩm** (`creative_replication`), dùng để gom product (`clustering`), và dùng cho `similar_ads`. Chỉ có một khung hình bìa, không hash nội dung video nên bản cắt đầu đuôi hay đổi khung bìa sẽ bị lệch. | `services/product_evidence.py:104-144`; `services/clustering.py:117-146`; `similar.py:296-361`; `realtime.py:401-404` |
| "Concept families" trong điểm Creative | Khi không có creative đã lưu, rơi về `creative_fingerprint`, tức hash **copy chữ** (advertiser + text + landing), không phải hình. Tên gọi gây hiểu nhầm. | `services/discovery.py:72`; `ingest.py:148-154` |
| Số page / shop chạy cùng video | Không có. API chỉ trả `family_size` = số creative, không có số advertiser và không có ngày sớm nhất. | `routers/intel.py:757-759,839-845` |
| Ai vào sớm / mới vào tuần này | Có ở **cấp sản phẩm**: `adv_first` theo page và `new_advertisers_7d`. UI radar chỉ hiện `sellers`, không có danh sách theo thứ tự vào. | `services/features.py:89-95`; `routers/intel.py:1473` |

**Đề xuất (tối thiểu, tái dùng thứ đã có):** khi `visual.index_pending` hash thumbnail (`ProductImage.phash`), gán
`video_key` = phash đã chuẩn hoá theo bucket Hamming ≤ 6, loại `TRIVIAL_PHASH` / stock frame (đã có trong
`clustering.MAX_STOCK_ADS`). Sau đó một endpoint `GET /api/videos/{key}` trả về: số advertiser (page_id), `min(first_seen_at)`
trên mọi ad kể cả đã tắt, và danh sách advertiser sắp theo ngày vào (badge "vào tuần này"). Không cần tải video. Giới hạn
đã biết: chỉ dựa vào khung bìa, nên bản re-upload đổi khung bìa sẽ bị lọt; ghi `ponytail:` và để hash nhiều khung từ
SD rendition (đã stream sẵn, `Creative.sd_source_url`) làm hướng nâng cấp sau.

### 1.6 Kho chung crowdsourced (scan của mọi user đổ chung một kho)

| ToolSpy | Bằng chứng |
|---|---|
| **Có sẵn trong nội bộ**: mọi live search và mọi lượt ingest qua extension ghi vào cùng bảng `ads`; `SearchJob.user_id` chỉ dùng để lọc danh sách job. Tài khoản chỉ có role MKT. | `ingest.py:158-241`; `routers/intel.py:730-731`; `routers/intel.py:260` (`/api/ingest/ads`) |
| Extension dùng **một** `INGEST_TOKEN` chung, nên không biết ad nào do MKT nào đóng góp (`saved_by` do client tự khai). | `routers/intel.py:33-40` |

Không cần làm thêm. Nếu muốn đo "ai đóng góp ngách nào" thì gắn `saved_by` từ session thay vì để client gửi.

### 1.7 Tab theo kênh: Video Win · Video Scale (FB) · Video Tiềm Năng (TikTok, mới nhú) · Sản phẩm bò sữa (evergreen, cho Google/SEO/sàn)

| Tab SpyWin | Thứ gần nhất trong ToolSpy | Bằng chứng | Khoảng trống |
|---|---|---|---|
| Video Win / Video Scale | Quadrant `BREAKOUT`, sort `force` / `variants` trên Product Radar | `services/discovery.py:300-301`; `routers/intel.py:1475-1477` | Lấy sản phẩm làm đơn vị, không phải video; force bị thổi phồng (F1) |
| Video Tiềm Năng (TikTok) | Quadrant `EXPERIMENTAL`, network filter | `services/discovery.py:300` | Nguồn TikTok Top Ads chỉ chạy được khi đã login một lần (memory `toolspy-tiktok-links`); tuổi ad TikTok = ngày ToolSpy thu về |
| Sản phẩm bò sữa | Quadrant `STABLE_WINNER` (demand ≥50, momentum <40) | `services/discovery.py:301`; `web/app/radar/page.tsx:19` | 35% demand đến từ `longevity_days`, mà chỉ số này đang bị thổi phồng (F1), nên "bò sữa" có thể chỉ là ad đã chết từ lâu |

**Đề xuất:** không build trang mới. Thêm 4 preset (tab) trên `/radar` hoặc `/product-radar`, map vào quadrant + network
(BREAKOUT+meta, variants≥3+meta, EXPERIMENTAL+tiktok, STABLE_WINNER). Chỉ làm **sau** khi F1 đã sửa.

### 1.8 Định nghĩa "win" rõ ràng, giải thích được (không có điểm AI mờ)

SpyWin: *win = video win + còn mới + trỏ về trang bán mới + nhiều shop đổ tiền thật*. Thiếu một vế là loại.

ToolSpy:
- Ưu điểm: điểm là công thức tất định, có breakdown; nhãn v2 `TEST_NOW/TEST/WATCH/REVIEW/SKIP` đi kèm câu lý do và
  số liệu thô (`facts`). `services/scoring.py:1-6`; `services/discovery.py:328-351`.
- Nhưng có tới **3 hệ thống cùng trả lời "có nên test không"**: v1 (`win_score`, `rare_winner_score`) vẫn được tính và lưu
  (`services/engine.py:18-42,75-77`); v2 (`opportunity` + `recommendation`, `engine.py:104-106`); Hidden Winner v3
  (`services/hidden.py:16-55`). Thêm `wave_confidence` và `confidence_score` là hai loại "confidence" khác nhau.
- Hidden Winner thưởng việc **chỉ có 1 advertiser** ("💎 Chỉ 1 nhà quảng cáo", `MAX_ADVERTISERS=3`), trong khi demand v2
  cho 1 seller **0 điểm** ("1 seller proves nothing") và SpyWin coi "1 người đang test" là chưa win. Xem C1.
- Hai vế "còn mới" (tuổi video) và "trang bán mới" (tuổi trang) **hoàn toàn không có** trong công thức. Vế "novelty" chỉ
  dùng tuổi sản phẩm (`services/discovery.py:243,247-248`).

**Đề xuất:** hiển thị trên mỗi sản phẩm một checklist 4 vế theo đúng kiểu SpyWin (✔/✘ + số): (1) có ad force ≥ strong
**đã xác minh** trong 48h · (2) `video_first_seen` ≤ 30 ngày · (3) trang không cũ hơn video quá 14 ngày (hoặc "chưa biết") ·
(4) ≥ 2 page chạy, có ≥ 1 page mới vào trong 7 ngày. Checklist này làm **từ dữ liệu sẵn có** sau F1 + §1.1 + §1.2 + §1.5,
không cần thêm điểm mới. Ẩn hẳn v1 khỏi API product (hiện vẫn trả `win_score`).

### 1.9 Đối thủ SpyWin đã benchmark (Shophunter, TrendTrack, Minea, Winning Hunter, BigSpy, PowerAdSpy, Pipiads, PPSPY, Tabcut)

Những tính năng "table-stakes" ToolSpy còn thiếu (không tính những thứ bị loại bởi quyết định đã chốt):

| Tính năng | Ai có | ToolSpy |
|---|---|---|
| Lọc / sort theo **ngày bắt đầu chạy** và **ad đã tắt** (lịch sử) | Pipiads, Minea, BigSpy | Thiếu, vì chỉ thu ad đang chạy (§1.1) |
| Trang "video" với số advertiser dùng chung | Pipiads, Minea (creative) | Thiếu (§1.5) |
| Nền tảng / tuổi của store | Shophunter, TrendTrack, PPSPY | Thiếu (§1.2). *Doanh thu Shopify* bị loại theo quyết định |
| Dữ liệu bán hàng TikTok Shop | Tabcut (Kalodata-style) | Không có (`grep tiktok_shop` chỉ ra deeplink); đã ghi P2 trong `docs/AUDIT_2026-10-07.md` §6 |
| Alert "SP vượt mốc 14/30 ngày", "có người mới vào SP đang theo dõi" | Minea, Winning Hunter | Chỉ có alert theo competitor page (`workspace/tracking.py`); bảng `alerts` cũ không còn được ghi ở đâu (`models.py:438-450`, không còn chỗ nào gọi `Alert(`) |
| Extension lưu ad khi lướt | Pipiads, Minea | **Có** (`apps/extension`) |
| Tìm bằng ảnh | Minea, Pipiads | **Có** (`/visual`) |

---

## 2. Lỗi / mâu thuẫn nội bộ ToolSpy

### F1 (HIGH) — Ad đã tắt vẫn bị tính là "đang chạy" vĩnh viễn (page không follow + toàn bộ TikTok)
- Liveness chỉ hỏi ad **đang chạy** (`realtime.py:674`). Ad vắng mặt chỉ được `verify_misses += 1` (`realtime.py:717-725`),
  giá trị này không được đọc ở bất kỳ đâu (chỉ được ghi ở `realtime.py:722` và reset ở `ingest.py:290`).
- Keyword search và lịch quét đều `active_only=True` (`collector/base.py:67`), nên không bao giờ nhận được `is_active=False`.
- TikTok Top Ads đặt `active=True` cứng và không có liveness (`ad_libraries.py:477`, `realtime.py:646`).
- Hệ quả: `days_running` đếm tới `now` (`services/adsignals.py:31-34`), dẫn tới `longevity_days` (`services/features.py:158`)
  và `force_score` tăng mãi. Demand lấy 35% từ longevity (`services/discovery.py:209`), `TEST_NOW` cần longevity ≥14
  (`discovery.py:344`), Hidden Winner cần ad chạy ≥7 ngày (`hidden.py:33`), radar chỉ đếm `live` (`routers/intel.py:1460`).
  Một ad TikTok thu về hôm nay sẽ thành "chạy 30 ngày" sau 30 ngày dù đã tắt ngay hôm sau.
- Sửa tối thiểu: tính longevity và force đến `min(now, last_verified_at + grace)` thay vì `now` cho ad chưa xác minh lại.
  Liveness `complete=True` + miss ≥2 thì đánh dấu `status=unverified`, loại khỏi `active`. Không cần suy luận "đã tắt",
  chỉ không tính ngày khi chưa có bằng chứng.

### F2 (MEDIUM) — `first_seen_at` mang hai nghĩa, và không bao giờ được lùi lại
Xem §1.1: Meta = ngày bắt đầu chạy, TikTok Top Ads = ngày thu về. `new_ads_7d` (`services/features.py:82`) trộn hai nghĩa
này, nên với TikTok "ad mới tuần này" thực ra là "ToolSpy mới thu tuần này". Nhánh refresh của `_upsert_ad` không bao giờ cập nhật mốc sớm hơn (`ingest.py:268-324`).

### F3 (MEDIUM) — Creative family không vận hành với chính sách "không lưu video"
Xem §1.5. `family_id` chỉ được gán khi tải đủ file (`media.py:317-318`), mà theo quyết định đã chốt thì video không bao giờ được tải. Thứ
được gọi là "concept families" trong điểm Creative thực ra là fingerprint copy chữ (`services/discovery.py:72`).

### C1 (MEDIUM) — Ba định nghĩa "winner" mâu thuẫn nhau
- Hidden Winner v3: 1–3 advertiser, tag "💎 Chỉ 1 nhà quảng cáo" là tín hiệu tốt (`services/hidden.py:16-19,31-32,46-47`).
- v2 demand: `_sellers(1)=0`, 2–10 seller mới là "validated" (`services/discovery.py:202-204`).
- SpyWin (và tài liệu nghiên cứu trong `docs/AUDIT_2026-10-07.md:130`): nhiều shop cùng đổ tiền mới là win.
- v1 vẫn được tính và lưu (`services/engine.py:18-42,75-77`) dù memory `toolspy-unified-scoring` nói v1 đã bị ẩn.
- Cần chọn **một** định nghĩa: giữ v2 + checklist 4 vế (§1.8), đổi tên Hidden Winner thành "Ít cạnh tranh / đang test"
  (là một bộ lọc, không phải nhãn win).

### C2 (LOW) — Thứ hạng theo Opportunity và nhãn TEST/WATCH chồng nhau
`OPPORTUNITY_CAP = {"SKIP": 30, "REVIEW": 30, "WATCH": 55}` và **không có ngưỡng dưới cho TEST** (`services/discovery.py:325`).
Memory `toolspy-unified-scoring` ghi nhận 48/132 TEST có Opp < 40 trong khi WATCH lên tới 55. Hiện vẫn chưa sửa. Sort
theo Opp sẽ trộn nhãn với nhau.

### C3 (LOW) — Nhãn giá nguồn trong UI sai nguồn dữ liệu
UI ghi "Giá AliExpress thấp nhất" nhưng `supplier_price_usd` là min của mọi listing commerce, kể cả 1688 sỉ
(`services/features.py:207,217`; `web/components/media.tsx:216`; `web/app/products/[id]/page.tsx:40`).

### C4 (LOW) — Docs tự nói Vault/Family "không bao giờ hiện", nhưng UI và API vẫn có
`docs/DAC_TA_HE_THONG.md:396,711` so với `routers/intel.py:746-759` (vẫn có filter `family_id` và `family_size`). Nên ẩn
nút Family hoặc chuyển nó sang `video_key` (§1.5).

### C5 (LOW) — Code chết: bảng `alerts`
Model `Alert` (`models.py:438-450`) không còn được ghi ở đâu (không còn chỗ nào gọi `Alert(`), và không có route hay UI dùng tới.

---

## 3. EU Market: những gì còn lộ ra khi đã "ẩn"

Sidebar đã ẩn bằng `EU_VISIBLE` (`web/components/layout/nav.ts:8-9,46`; thay đổi chưa commit). Còn lại:
1. `.env:115 EU_MARKET_ENABLED=1`: worker vẫn sync EU mỗi 6h (`realtime.py:342,429-435`), và nhập **URL trang sản phẩm**
   ở `/visual` vẫn ghi listing vào bảng EU (`visual/routes.py:106-114`). Trang `/visual` còn báo "Đã lưu listing … (#eu_listing_id)"
   (`web/app/visual/page.tsx:233`), tức là lưu vào một module người dùng không còn thấy. Chỉ cần đổi `EU_MARKET_ENABLED=0`
   là tắt được cả hai.
2. Các route `/eu-market/*` vẫn truy cập được trực tiếp bằng URL (`web/app/eu-market/*`), `middleware.js` không chặn.
   Nên thêm redirect khi `!EU_VISIBLE`.
3. Backend `/api/eu-market/*` đã là internal-only (memory `toolspy-account-policy`), không cần đổi.

---

## 4. Những mục trong audit cũ và memory: còn mở hay đã đóng

| Mục | Trạng thái hôm nay | Bằng chứng |
|---|---|---|
| Audit 07/10 H13 (Market DNA đếm category None) | **Đã đóng** | `services/discovery.py:155-156,181-182` |
| Audit 07/10 regex compliance `cure` / `gun` | **Đã đóng** | `services/discovery.py:32-41` |
| Audit 07/10 auth / không có login (memory 14/10 "biggest risk") | **Đã đóng**: có `SessionGuard` | `main.py:36` |
| Audit 07/10 P1 landing fingerprint (Ladipage/Shopify/Woo qua HTML) | **Còn mở** | `ingest.py:46`; không có chỗ nào grep ra `cdn.shopify`/`ladicdn` |
| Audit 07/10 P1 alert có nghĩa (SP vượt mốc 14/30 ngày, có người mới vào) | **Còn mở** (bảng alerts là code chết) | `models.py:438-450` |
| Audit 07/10 P1 lịch sử ad đã gỡ ("still active today") | **Còn mở** cho page không follow (F1) | `realtime.py:717-725` |
| Audit 07/10 meta collation chỉ dedupe trong một trang | **Còn mở** (ghi chú `ponytail:`) | `collector/adapters/meta.py:267-270` |
| Memory unified-scoring: TEST có Opp < WATCH | **Còn mở** | `services/discovery.py:325` |
| Memory tiktok-links: connector TikTok Top Ads tắt cho tới khi login | Không kiểm chứng được (không vào được DB) | — |
| Memory wave-multilingual: "live tick not yet run" | Không kiểm chứng được; tick đang được lên lịch | `realtime.py:350,417-420` |
| Memory product-naming: 5.9k UNKNOWN, Gemini 20/ngày | Không kiểm chứng được; chain provider đã đổi (memory `toolspy-naming-ai-providers`) | — |
| Memory eu-market-sources: bảng mồ côi `idealo_*` trong Postgres | Không kiểm chứng được (không vào được DB) | — |

---

## 5. Đề xuất đưa vào bản thiết kế (theo thứ tự)

1. **F1 trước tiên**: tính ngày chạy tới `last_verified_at` thay vì `now`; thêm trạng thái `unverified`; cho tracked query chạy `active_only=False`.
2. **F2**: lùi `first_seen_at` trên nhánh refresh (một điều kiện).
3. **Thực thể video** (`video_key` từ phash thumbnail đã có): số page, mốc sớm nhất kể cả ad đã tắt, danh sách advertiser theo ngày vào.
4. **Tuổi trang bán**: đọc `published_at` từ Shopify `.json` mà `spy.fetch_landing` đã gọi sẵn, nhận diện nền tảng qua HTML. Cờ đỏ >14 ngày.
5. **Checklist win 4 vế** trên card sản phẩm; gộp Hidden Winner thành một bộ lọc; ẩn v1.
6. **Bốn tab preset** (Win / Scale / Tiềm năng TikTok / Bò sữa) trên radar có sẵn.
7. **Ẩn EU triệt để**: `EU_MARKET_ENABLED=0` + redirect `/eu-market`.
8. Sửa nhãn giá nguồn; dọn bảng `alerts` chết hoặc nối nó vào alert "vượt mốc / người mới vào".
