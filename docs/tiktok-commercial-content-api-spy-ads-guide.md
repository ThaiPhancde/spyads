# TikTok Commercial Content API (CCA) trong ToolSpy

> Cập nhật 08/10/2026. Đã đối chiếu với tài liệu chính thức của TikTok (link ở §10) và với code hiện có trong repo.
> Bản trước của file này viết cho Cloudflare Worker + D1 bằng TypeScript. ToolSpy chạy **Python/FastAPI + SQLite/Postgres + R2**,
> và đã có sẵn connector, bảng `ads`, khử trùng lặp, tải video và chấm điểm. Vì vậy phần kiến trúc TS/D1 đã được bỏ.

---

## 1. Kết luận nhanh

| Câu hỏi | Trả lời |
|---|---|
| CCA lấy được gì? | Quảng cáo TikTok **được phân phối ở EU/EEA/UK/CH**, gồm video, ngày chạy đầu/cuối, trạng thái, reach, advertiser. Gọi thêm detail sẽ có landing page, CTA, objective và targeting. |
| CCA có ads cho **PH / SA / AE / US / VN** không? | **Không, hoàn toàn không có.** Không phải "phủ kém" như bản cũ viết: những nước này không nằm trong danh sách hỗ trợ. Một ad chỉ chạy ở PH sẽ không bao giờ xuất hiện. |
| Vậy dùng CCA để làm gì? | (1) Spy thị trường **EU/UK**. (2) Thay thế ổn định cho connector `tiktok_ad_library`: cùng nguồn dữ liệu (library.tiktok.com) nhưng connector đó đang dính 429 (xem AUDIT H6). (3) Phát hiện sản phẩm đã chạy được ở EU để đưa sang PH. |
| Nguồn TikTok cho PH (thị trường #1)? | **Creative Center Top Ads**: connector `apify_tiktok_top_ads` (đã bật) hoặc `tiktok_top_ads` (dán headers DevTools). CCA không thay được hai nguồn này. |
| Code ở đâu? | [generic.py](../apps/api/app/collector/adapters/generic.py), class `TikTokCommercialConnector` (key `tiktok_commercial`). Self-check: [test_tiktok_commercial.py](../apps/api/test_tiktok_commercial.py). |

Quốc gia được hỗ trợ (32): AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE · NO IS LI · GB CH.

---

## 2. Credentials

Lấy Client key và Client secret tại developers.tiktok.com → app đã được duyệt CCA. Bấm biểu tượng con mắt để xem **đủ** secret.

> ⚠️ Chuỗi secret 10 ký tự dán trong chat bị TikTok trả `invalid_client — Client key or secret is incorrect` (đã thử ngày 08/10). Nhiều khả năng đó là bản bị che hoặc bị cắt. Hãy copy lại đủ chuỗi.
> Secret đã đi qua chat thì nên bấm **Reset/Regenerate** rồi dùng secret mới.

Cấu hình: điền vào `.env` ở thư mục gốc (đã nằm trong `.gitignore`):

```env
TIKTOK_CLIENT_KEY=awonwnbthlz64bnq
TIKTOK_CLIENT_SECRET=<đủ chuỗi>
```

Cũng có thể điền thẳng trong màn **Data & Connectors**, connector *TikTok Commercial Content API*. Giá trị trong UI được ưu tiên hơn `.env`.
Không đặt secret trong frontend, trong docs hoặc trong git.

Access token do connector tự lấy (`grant_type=client_credentials`, sống khoảng 2 giờ), lưu cache trong process và làm mới trước khi hết hạn 5 phút. **Không** lưu `TIKTOK_ACCESS_TOKEN` cố định.

### Test tay bằng PowerShell

```powershell
$tok = Invoke-RestMethod -Method POST -Uri "https://open.tiktokapis.com/v2/oauth/token/" `
  -ContentType "application/x-www-form-urlencoded" `
  -Body @{ client_key = $env:TIKTOK_CLIENT_KEY; client_secret = $env:TIKTOK_CLIENT_SECRET; grant_type = "client_credentials" }
$tok   # thành công: access_token = "clt.…", expires_in = 7200. Sai key: error = invalid_client (vẫn HTTP 200)

$fields = "ad.id,ad.first_shown_date,ad.last_shown_date,ad.status,ad.videos,ad.reach,advertiser.business_name"
$body = @{
  search_term = "lucky bracelet"; search_type = "fuzzy_phrase"; max_count = 10
  filters = @{
    ad_published_date_range = @{ min = "20260710"; max = "20261008" }
    country_code_list = @("FR", "DE"); ad_type = "VIDEO"; ad_status = "ACTIVE"
  }
} | ConvertTo-Json -Depth 5
$r = Invoke-RestMethod -Method POST -Uri "https://open.tiktokapis.com/v2/research/adlib/ad/query/?fields=$fields" `
  -Headers @{ Authorization = "Bearer $($tok.access_token)" } -ContentType "application/json" -Body $body
$r.data.ads; $r.data.has_more; $r.data.search_id; $r.error
```

Hoặc test qua connector của app (sau khi điền `.env`):

```powershell
cd apps/api
.venv/Scripts/python -c "from dotenv import load_dotenv; load_dotenv('../../.env'); from app.collector.adapters.generic import TikTokCommercialConnector as C; print(C().health_check())"
```

---

## 3. Spec đã xác minh (sửa các điểm bản cũ thiếu hoặc sai)

### Query Ads: `POST /v2/research/adlib/ad/query/?fields=…`

| Tham số | Giá trị | Ghi chú |
|---|---|---|
| `fields` (query string) | `ad.id, ad.first_shown_date, ad.last_shown_date, ad.status, ad.status_statement, ad.videos, ad.image_urls, ad.reach, advertiser.business_id, advertiser.business_name, advertiser.paid_for_by` | Bắt buộc. Phải ghi từng field; `fields=ad,advertiser` không hợp lệ (connector cũ dùng cách này). |
| `search_term` | ≤ **50 ký tự** | |
| `search_type` | `exact_phrase` / `fuzzy_phrase` | Spy nên dùng `fuzzy_phrase`. Dùng `exact_phrase` khi cần khớp đúng cụm. |
| `max_count` | mặc định 10, **tối đa 10** | Bản cũ ngầm hiểu là lấy được nhiều; connector cũ gửi 50. |
| `search_id` | lấy từ response trước | Dùng để phân trang. Đổi term/filter thì không được dùng lại search_id cũ. |
| `filters.ad_published_date_range` | `{min, max}` dạng `YYYYMMDD` | **Bắt buộc**, khoảng tối đa **1 năm**. |
| `filters.country_code_list` | list mã nước ở §1 | Bỏ trống = mọi nước hỗ trợ. |
| `filters.ad_type` | `VIDEO` / `IMAGES` / `TEXT` | |
| `filters.ad_status` | `ACTIVE` / `INACTIVE` | Filter dùng chữ HOA, response trả chữ thường `active`/`inactive`. |
| `filters.ad_reach` | `0-10K`, `10K-100K`, `100K+`, `all` | Lọc reach ngay từ API: cách rẻ nhất để chỉ lấy ad mạnh. |
| `filters.ages` / `filters.gender` | `18,24` … / `FEMALE`, `MALE`, `ALL` | |

Response:

```json
{
  "data": { "ads": [{ "ad": {...}, "advertiser": {...} }], "has_more": true, "search_id": "2837…" },
  "error": { "code": "ok", "message": "", "log_id": "…", "http_status_code": 200 }
}
```

- `ad.reach` = `{"unique_users_seen": "11K"}`, là **chuỗi viết tắt**, không phải số. Connector lưu cận dưới thành số nguyên (`reach`) và giữ chuỗi gốc ở `impressions_text`.
- `ad.videos` = `[{"url": …, "cover_image_url": …}]`. Link video có chữ ký và **hết hạn**, nên pipeline tải video về ngay như với Meta.
- Phải kiểm tra `error.code != "ok"`, vì không phải lỗi nào cũng trả HTTP 4xx.

### Get Ad Detail: `POST /v2/research/adlib/ad/detail/?fields=…`, body `{"ad_id": <id>}`

Các field có thêm so với Query: `ad.title`, `ad.external_url` (landing page), `ad.download_url`, `ad.call_to_action`, `ad.advertising_objective`, `ad.rejection_info`, `advertiser.country_code`, `advertiser.follower_count`, `advertiser.avatar_url`, `advertiser.profile_url`, `ad_group.targeting_info`.

`targeting_info` gồm `number_of_users_targeted`, `country[]`, `age{}`, `gender{}`, `interest`, `audience_targeting`, `video_interactions`, `creator_interactions`, `languages[]`, `provinces[]`, `cities[]`, `device_models[]`, `operating_systems[]`, `high_spending_power`, `audience_targeting_exclude`.

Mỗi ad tốn 1 request detail. Nguyên tắc "không gọi detail cho tất cả" của bản cũ vẫn đúng.

### Quota

TikTok **không công bố** rate limit cho CCA trong docs (Research API công bố 1.000 request/ngày; chưa xác nhận con số này áp cho CCA). Connector giới hạn 30 request/phút và tự backoff khi gặp 429. Chi phí một lần tìm với `limit=100`: khoảng 10 request query cộng 10 request detail (mặc định).

---

## 4. Connector trong app: đã sửa những gì

Connector cũ (`generic.py`) **không chạy được** với API thật. Đã sửa:

| Lỗi cũ | Sửa |
|---|---|
| `fields=ad,advertiser` | Liệt kê đủ field (query + detail). |
| Body dùng `country_code` (1 nước), mặc định `"ALL"` | `country_code_list`. Nếu job chỉ có nước ngoài danh sách (PH, SA, US…) thì **trả 0 kết quả và không gọi API**, để không lẫn ads EU vào job PH (cùng lỗi AUDIT H6). |
| `max_count` tới 50, không phân trang | 10/trang, lặp theo `search_id` đến khi đủ `limit` hoặc `has_more=false`, khử trùng lặp theo `ad.id`. |
| Không kiểm `error.code`; secret sai thì báo `KeyError` | Báo `AuthExpired: invalid_client …` (hiện thành `auth_expired` trên màn health) và `ConnectorError` kèm `log_id`. |
| Lấy token mới ở mọi lần gọi | Cache token theo `expires_in`. |
| Không có `ad_status` / `ad_type` | `active_only` → `ACTIVE`, `media_type=video` → `VIDEO`. |
| Ngày `YYYYMMDD` lưu thô, reach bỏ qua | Ngày chuyển sang ISO. Reach lưu thành số + chuỗi gốc. |
| Không gọi detail | Gọi detail cho `details` ad đầu tiên (mặc định 10) để có landing page, CTA, profile advertiser, follower và nước bị target. |
| Bắt buộc nhập key trong UI | UI hoặc `.env` (`TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`), giống cách làm với `APIFY_TOKEN`. |

Map vào Common Data Contract (`AdRecord`):

```
ad.id → source_ad_id          (source = "tiktok_commercial", platform = "tiktok")
advertiser.business_name/id → advertiser / page_id
ad.videos / image_urls → media (video + cover)
ad.first/last_shown_date → first_seen / last_seen (ISO)
ad.status == "active" → active
ad.reach.unique_users_seen → reach (int) + impressions_text ("11K")
detail: external_url → landing_page, call_to_action → cta_text, title → title/ad_text,
        profile_url → advertiser_url, follower_count → page_likes, targeting_info.country → countries
```

Lưu DB, khử trùng lặp (`source + source_ad_id`), tải video, gom creative family và chấm điểm đều do pipeline sẵn có xử lý ([ingest.py](../apps/api/app/ingest.py), [scoring.py](../apps/api/app/services/scoring.py)). **Không** tạo bảng hay Win Score riêng cho TikTok.

---

## 5. Bật trong app

1. Điền `TIKTOK_CLIENT_SECRET` (đủ chuỗi) vào `.env`, rồi restart API.
2. **Data & Connectors** → *TikTok Commercial Content API* → **Test**, kỳ vọng `healthy` (probe "shop"/FR).
3. Bật connector. Tìm kiếm multi-keyword (`feng shui | lucky bracelet | pixiu`) đã tách keyword sẵn: mỗi keyword là 1 lần query.
4. Khi CCA đã chạy ổn, **tắt `tiktok_ad_library`**. Hai connector cùng một nguồn dữ liệu; để cả hai thì mỗi ad bị lưu 2 lần với 2 `source` khác nhau. Giữ `tiktok_ad_library` làm dự phòng.
5. Không cần đổi gì cho PH. Job PH tự bỏ qua CCA, và TikTok cho PH vẫn đến từ Creative Center Top Ads.

---

## 6. Vị trí của CCA trong các nguồn TikTok

| Nguồn | Connector | Thị trường | Tìm theo keyword/đối thủ | Tín hiệu | Ghi chú |
|---|---|---|---|---|---|
| **Commercial Content API** | `tiktok_commercial` | EU/EEA/UK/CH | ✅ | reach, ngày chạy, landing page, CTA, targeting | Chính thức và ổn định. Miễn phí. |
| TikTok Ad Library (scrape) | `tiktok_ad_library` | EU/UK | ✅ | như trên | Trùng nguồn với CCA, đang dính 429. Để làm dự phòng. |
| Creative Center Top Ads (Apify) | `apify_tiktok_top_ads` | **PH**, US, EU, SA, AE… | ⚠️ chỉ top ads theo nước/ngành | likes, CTR, ngày | **Nguồn TikTok chính cho PH.** Trả phí theo item. |
| Creative Center Top Ads (headers) | `tiktok_top_ads` | như trên | như trên | như trên | Dán headers DevTools từ tài khoản free. App **không** tự ký `user-sign`. |

Lưu ý khi chọn Apify actor: actor "TikTok scraper" thường chỉ crawl video organic, profile, hashtag, **không phải ads**. Phải xem input/output trước khi tích hợp.

Win Score: dùng engine chung. Từ CCA có thể lấy thêm: thời gian chạy (`first_seen` → `last_seen`), `reach`, có landing page hay không, CTA mang tính bán hàng (Shop now / Order now), `advertising_objective = Sales`, và việc cùng một sản phẩm xuất hiện ở nhiều nguồn (Meta + TikTok EU + Creative Center PH).

---

## 7. Phần của bản cũ đã bỏ và lý do

- §12–§14, §16–§17 (TypeScript module, Cloudflare Worker, `wrangler secret`, schema D1): sai stack. App đã có connector, `ads` table và ingest.
- §18 trọng số Win Score riêng cho TikTok: đã có `scoring.py` dùng chung. Một công thức riêng cho từng nguồn sẽ làm điểm không so sánh được giữa các nguồn.
- §22 "source confidence" và §25–§27 (1688, product matching): đã có trong `spy_ads_product_sourcing_architecture.md` và `entity_resolution.py`, không thuộc phạm vi tài liệu CCA.
- §20 "không phải nguồn tốt nhất cho PH/SA/UAE/US": đã sửa thành **không có dữ liệu** cho các nước này.

---

## 8. Checklist

```
[x] Application được duyệt
[x] Connector sửa theo spec chính thức + self-check (test_tiktok_commercial.py)
[x] Client key trong .env
[ ] Client secret đủ chuỗi trong .env (nên regenerate vì đã dán qua chat)
[ ] Health check healthy → bật tiktok_commercial
[ ] Tắt tiktok_ad_library sau 1–2 ngày CCA chạy ổn
[ ] Theo dõi 429 / quota thực tế. Nếu thiếu quota: giảm `details`, dùng filter `ad_reach = 100K+` hoặc `10K-100K`
```

---

## 9. Điều khoản

Chỉ dùng CCA trong phạm vi mục đích đã khai báo khi xin quyền. TikTok có thể thu hồi quyền truy cập nếu dữ liệu bị dùng sai mục đích. Đọc lại Terms of Service của CCA trước khi dùng dữ liệu ngoài phạm vi nội bộ (ví dụ chia sẻ ra ngoài công ty hoặc bán lại).

---

## 10. Tài liệu chính thức

- Tổng quan: https://developers.tiktok.com/products/commercial-content-api
- Query Ads: https://developers.tiktok.com/doc/commercial-content-api-query-ads
- Get Ad Details: https://developers.tiktok.com/doc/commercial-content-api-get-ad-details
- Supported countries: https://developers.tiktok.com/doc/commercial-content-api-supported-countries
- Client access token: https://developers.tiktok.com/doc/client-access-token-management
