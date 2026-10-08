# TikTok Ads Data Strategy V3 for ToolSpy

> Mục tiêu: xây dựng hệ thống TikTok Ads cho ToolSpy có thể lấy dữ liệu quảng cáo đối thủ, dữ liệu quảng cáo nội bộ công ty, dữ liệu trend/creative, và có cơ chế fallback để hạn chế phụ thuộc vào các dịch vụ trả phí như Pipiads, Minea hoặc Apify.

---

# 1. Kết luận tổng thể

Sau khi đối chiếu nhiều nguồn, bao gồm TikTok official docs và các repo GitHub thực tế, kiến trúc TikTok trong ToolSpy nên được tách thành **3 nhóm dữ liệu khác nhau**:

```text
TikTok
│
├─ Competitor Ads
│  ├─ TikTok Commercial Content API
│  ├─ TikTok Ad Library public UI
│  └─ Browser Scraper / Apify fallback
│
├─ Internal Company Ads
│  └─ TikTok Marketing API
│     └─ tham khảo AdsMCP
│
└─ Trend / Creative Intelligence
   └─ TikTok Creative Center
```

Điểm quan trọng:

```text
TikTok Ads Library
≠
TikTok Creative Center
≠
TikTok Marketing API
```

Ba nguồn này phục vụ ba mục đích khác nhau.

---

# 2. Vấn đề hiện tại của ToolSpy

Hiện connector TikTok đang không lấy được ads.

Cách gọi hiện tại có dạng:

```json
{
  "query": "posture corrector"
}
```

và hệ thống nhận:

```text
0 results
```

Điều này chưa đủ để kết luận:

```text
TikTok blocked
```

Có nhiều nguyên nhân khác có thể xảy ra:

```text
- sai endpoint
- sai parameter
- sai date range
- sai region
- dùng Creative Center thay vì Ad Library
- keyword quá niche
- API schema đã đổi
- parser đã hỏng
- HTTP 200 nhưng body là HTML/challenge
- lỗi auth/signature bị hiểu nhầm thành geo-block
```

---

# 3. Không nên mặc định rằng TikTok Ad Library cần EU Proxy

TikTok Commercial Content Library:

```text
https://library.tiktok.com/
```

cho phép tìm kiếm public content.

Dữ liệu ads chủ yếu thuộc các thị trường được TikTok hỗ trợ trong Commercial Content Library.

Do đó:

```text
EU Proxy
```

không nên là điều kiện mặc định.

Nên dùng logic:

```text
Public request trước
↓
nếu GEO_BLOCKED
↓
mới dùng EU Proxy
```

Proxy không giải quyết được các lỗi như:

```text
401
invalid signature
wrong request body
wrong endpoint
wrong date
wrong token
```

---

# 4. Repo GitHub 1: CheckFirstHQ/Tikadrchivist

Repo:

```text
https://github.com/CheckFirstHQ/Tikadrchivist
```

Repo này cho thấy TikTok Ad Library từng được query bằng:

```text
POST https://library.tiktok.com/api/v1/search
```

Body dạng:

```json
{
  "query": "posture corrector",
  "query_type": "",
  "adv_biz_ids": "",
  "order": "impression,desc",
  "offset": 0,
  "search_id": "",
  "limit": 100
}
```

Ngoài body còn có query parameters như:

```text
region
type
start_time
end_time
```

Ví dụ:

```text
/api/v1/search
?region=FR
&type=1
&start_time=...
&end_time=...
```

Điểm này chứng minh:

```json
{
  "query": "posture corrector"
}
```

một mình là quá ít.

---

# 5. Repo GitHub 2: tarxn/tiktok-ads-scraper

Repo:

```text
https://github.com/tarxn/tiktok-ads-scraper
```

Đây là một nguồn rất quan trọng vì nó không chỉ có README mà còn có:

```text
src/main.py
output_data/
```

và có dataset mẫu thực tế.

Repo dùng:

```text
Selenium
BeautifulSoup
Apify SDK
```

để lấy ads từ TikTok Library.

---

## 5.1 Cách repo tarxn tìm quảng cáo

Repo không search bằng:

```json
{
  "query": "fashion"
}
```

mà mở URL:

```text
https://library.tiktok.com/ads
?region=FR
&start_time=...
&end_time=...
&adv_name=fashion
&adv_biz_ids=
&query_type=1
&sort_type=last_shown_date,desc
```

Điểm cực kỳ quan trọng:

```text
keyword nằm trong adv_name
```

Không phải:

```text
query=...
```

Điều này cho thấy connector ToolSpy hiện tại có thể đang sai parameter.

---

## 5.2 Flow của scraper tarxn

```text
TikTok Library search page
        │
        ▼
Selenium mở trang
        │
        ▼
click "View More"
        │
        ▼
load thêm ad cards
        │
        ▼
BeautifulSoup parse HTML
        │
        ▼
lấy ad_id
        │
        ▼
mở từng:
https://library.tiktok.com/ads/detail/?ad_id=...
        │
        ▼
parse detail
```

---

## 5.3 Các field repo tarxn lấy được

```text
ad_id
advertiser
first_shown
last_shown
unique_user_views
target_audience
country
gender
age
additional targeting parameters
video URL
```

Đây là schema rất phù hợp cho ToolSpy.

---

## 5.4 Dataset mẫu

Repo có file:

```text
output_data/tiktok_ads_data_UK_last7days_fashion.csv
```

Dung lượng khoảng:

```text
1.3 MB
```

Điều này quan trọng vì đây là bằng chứng scraper từng chạy thực tế, không chỉ là project demo không có output.

---

# 6. Không nên copy nguyên repo tarxn vào production

Repo được push từ 2024.

Code phụ thuộc vào các class HTML như:

```text
.loading_more
.ad_card
.link
.item_value
.ad_advertiser_value
.ad_target_audience_size_value
.byted-Table-Body
```

Nếu TikTok đổi frontend thì scraper sẽ chết.

Vì vậy repo này nên được xem như:

```text
reverse-engineering reference
```

không phải:

```text
production-ready connector
```

---

# 7. Cách tận dụng repo tarxn đúng nhất

Không clone chạy nguyên xi.

Nên dùng nó để xác định:

```text
1. TikTok Library search URL
2. query parameters
3. cách tìm ad_id
4. detail URL
5. những field có thể scrape
```

Sau đó viết connector mới theo kiến trúc riêng.

---

# 8. Repo GitHub 3: AdsMCP/tiktok-ads-mcp-server

Repo:

```text
https://github.com/AdsMCP/tiktok-ads-mcp-server
```

Đây KHÔNG phải scraper competitor ads.

Nó là:

```text
TikTok Marketing API MCP Server
```

và dùng:

```text
https://business-api.tiktok.com/open_api/v1.3
```

---

# 9. AdsMCP dùng để làm gì?

Repo yêu cầu:

```text
TikTok For Business account
TikTok developer app
App ID
App Secret
OAuth
Access Token
advertiser_id
```

Nó truy cập dữ liệu ads thuộc chính advertiser account mà user authorize.

Các endpoint gồm:

```text
campaign/get/
adgroup/get/
ad/get/
report/integrated/get/
advertiser/info/
pixel/list/
pixel/event/stats/
dmp/custom_audience/list/
tool/targeting/info/
```

---

# 10. AdsMCP không dùng để spy đối thủ

Không thể dùng nó theo kiểu:

```text
nhập Nike
↓
lấy toàn bộ ads Nike
```

Marketing API chỉ truy cập account đã authorize.

Vì vậy:

```text
AdsMCP
```

không phải giải pháp cho:

```text
Competitor Ads
```

Nhưng lại rất hữu ích cho:

```text
Internal Ads Intelligence
```

---

# 11. Giá trị của AdsMCP đối với ToolSpy

ToolSpy không chỉ nên biết:

```text
đối thủ đang chạy ads gì
```

mà còn nên biết:

```text
ads của chính công ty đang chạy như thế nào
```

AdsMCP giúp lấy:

```text
campaign
adgroup
ads
spend
impressions
clicks
CTR
audience
pixel
conversion-related metrics
targeting
advertiser info
```

Điều này rất quan trọng cho engine phân tích.

---

# 12. Kiến trúc TikTok hoàn chỉnh cho ToolSpy

```text
                          TIKTOK
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
           ▼                 ▼                 ▼

    COMPETITOR ADS       OWN ADS          TREND DATA

           │                 │                 │
           ▼                 ▼                 ▼

  TikTok Ad Library    Marketing API    Creative Center
           │                 │                 │
           │                 │                 │
           ▼                 ▼                 ▼

 competitor_ads       internal_ads       trend_ads

           │                 │                 │
           └─────────────────┼─────────────────┘
                             │
                             ▼

                         NORMALIZER
                             │
                             ▼
                            D1
                             │
                             ▼
                      ANALYSIS ENGINE
```

---

# 13. TikTok Competitor Connector

Đây là connector quan trọng nhất cho spy ads.

Priority:

```text
1. TikTok Commercial Content API
2. TikTok Public Ad Library
3. Browser scraper
4. Apify / external service
```

---

# 14. Official TikTok Commercial Content API

Endpoint:

```text
POST https://open.tiktokapis.com/v2/research/adlib/ad/query/
```

Body dạng:

```json
{
  "filters": {
    "ad_published_date_range": {
      "min": "20260901",
      "max": "20261007"
    },
    "country_code_list": [
      "FR"
    ]
  },
  "search_term": "posture corrector",
  "search_type": "fuzzy_phrase",
  "max_count": 10
}
```

Đây là schema chính thức.

Không phải:

```json
{
  "query": "posture corrector"
}
```

---

# 15. Official Ad Detail API

Dữ liệu có thể bao gồm:

```text
ad.id
first_shown_date
last_shown_date
videos
image_urls
reach
title
external_url
download_url
CTA
advertising_objective

advertiser.business_id
advertiser.business_name
advertiser.profile_url
advertiser.follower_count

ad_group.targeting_info
```

Đây là dữ liệu rất phù hợp cho ToolSpy.

---

# 16. Public TikTok Ad Library scraper

Nếu chưa có official API access:

```text
TikTok Ad Library UI
```

là fallback free quan trọng.

Search URL có thể dựa theo pattern:

```text
https://library.tiktok.com/ads
?region=FR
&start_time=...
&end_time=...
&adv_name=shop
&adv_biz_ids=
&query_type=1
&sort_type=last_shown_date,desc
```

Không hardcode tuyệt đối vì TikTok có thể đổi URL/schema.

---

# 17. Healthcheck không dùng `posture corrector` duy nhất

Keyword niche có thể trả 0 thật.

Healthcheck nên dùng:

```text
shop + FR
```

hoặc:

```text
fashion + FR
beauty + FR
phone + FR
```

Logic:

```text
shop + FR → có data
posture corrector + FR → 0
```

=> connector vẫn:

```text
HEALTHY
```

Nếu:

```text
shop → 0
fashion → 0
beauty → 0
```

mới nghi connector hỏng.

---

# 18. Test URL trực tiếp trước khi sửa API

Trước khi debug connector:

```text
1. mở library.tiktok.com
2. search thủ công
3. mở DevTools
4. Network
5. xem request đang dùng
6. Copy as cURL
7. compare với connector
```

Đây là cách tốt nhất khi TikTok đổi endpoint.

---

# 19. Browser Scraper Provider

Nên xây browser scraper riêng.

Ví dụ:

```text
src/connectors/tiktok/providers/browser-library.ts
```

Flow:

```text
build search URL
↓
open page
↓
wait ads
↓
paginate / click load more
↓
collect ad_id
↓
visit detail
↓
parse fields
↓
normalize
```

---

# 20. Không tạo Chrome mới cho mỗi ad

Repo tarxn đang tạo browser mới nhiều lần.

Production nên:

```text
1 browser
↓
1 context
↓
multiple pages/tabs
```

hoặc:

```text
browser pool
```

Ví dụ:

```text
max concurrency = 3
```

tránh tốn RAM.

---

# 21. Dùng Playwright thay Selenium nếu xây mới

Khuyến nghị:

```text
Playwright
```

thay vì:

```text
Selenium
```

vì dễ:

```text
- intercept network
- wait API response
- reuse context
- manage cookies
- browser automation
- debugging
```

---

# 22. Tốt nhất là intercept API thay vì parse HTML

Browser scraper chỉ nên dùng để:

```text
bootstrap request
```

Sau đó nếu phát hiện network API:

```text
Browser
↓
intercept request
↓
API endpoint
↓
call API trực tiếp
```

sẽ nhanh hơn:

```text
parse DOM
```

---

# 23. TikTok Creative Center

Creative Center không nên là nguồn competitor ads chính.

Nó phù hợp cho:

```text
Top Ads
Trending Ads
Creative inspiration
Trending videos
Trending hashtags
Trending products
performance percentile
creative analytics
```

Có thể cần:

```text
cookie
csrf token
anonymous-user-id
web-id
timestamp
signature
```

Do đó:

```text
EU Proxy
```

không giải quyết lỗi signature/auth.

---

# 24. TikTok Internal Ads Connector

Module riêng:

```text
TikTok Marketing API
```

tham khảo kiến trúc AdsMCP.

---

# 25. Internal Ads data

Nên lấy:

```text
campaigns
adgroups
ads
creative IDs
spend
impressions
clicks
CTR
CPC
CPM
conversions
CPA
ROAS
audience breakdown
country
age
gender
pixel events
```

---

# 26. Internal vs competitor comparison

Ví dụ sản phẩm:

```text
Posture Corrector
```

ToolSpy có thể tạo:

```text
              POSTURE CORRECTOR
                     │
          ┌──────────┴──────────┐
          │                     │
          ▼                     ▼

Competitor Ads              Our Ads

TikTok Library           Marketing API
     │                        │
     ├─ 80 ads                ├─ spend
     ├─ 20 advertisers        ├─ CTR
     ├─ creatives             ├─ CPC
     ├─ hooks                 ├─ conversion
     ├─ targeting             ├─ CPA
     └─ first/last seen       └─ ROAS

          └──────────┬──────────┘
                     ▼

               AI Analysis
```

---

# 27. AI Analysis Engine

Sau khi có cả hai nguồn, ToolSpy có thể trả lời:

```text
- đối thủ đang dùng hook gì
- creative format nào phổ biến
- ads nào chạy lâu nhất
- advertiser nào scale mạnh
- landing page pattern
- angle marketing
- target audience
- creative gap của công ty
- hook nào công ty chưa test
- ads nội bộ nào đang underperform
- ads nào nên scale
- ads nào nên kill
```

---

# 28. Source priority đề xuất

```ts
const TIKTOK_SOURCE_PRIORITY = {
  official_adlib_api: 100,
  public_ad_library_api: 95,
  public_ad_library_browser: 90,
  creative_center: 80,
  apify: 70,
  third_party_paid: 60
};
```

---

# 29. Provider architecture

```ts
interface TikTokProvider {
  healthCheck(): Promise<HealthResult>;

  searchAds(
    input: TikTokSearchInput
  ): Promise<NormalizedAd[]>;
}
```

Implement:

```text
TikTokOfficialAdlibProvider

TikTokPublicLibraryApiProvider

TikTokPublicLibraryBrowserProvider

TikTokCreativeCenterProvider

TikTokApifyProvider
```

Internal ads tách riêng:

```text
TikTokMarketingApiProvider
```

---

# 30. Folder structure

```text
src/
├─ connectors/
│  └─ tiktok/
│
│     ├─ competitor/
│     │  ├─ official-adlib.ts
│     │  ├─ public-api.ts
│     │  ├─ browser-library.ts
│     │  └─ apify.ts
│     │
│     ├─ internal/
│     │  ├─ marketing-api.ts
│     │  ├─ campaigns.ts
│     │  ├─ reports.ts
│     │  └─ oauth.ts
│     │
│     ├─ creative-center/
│     │  ├─ trends.ts
│     │  ├─ top-ads.ts
│     │  └─ metrics.ts
│     │
│     ├─ normalize.ts
│     ├─ dedup.ts
│     ├─ health.ts
│     ├─ errors.ts
│     └─ types.ts
│
├─ services/
│  ├─ product-match.ts
│  ├─ competitor-analysis.ts
│  └─ creative-analysis.ts
│
└─ db/
   ├─ ads.ts
   ├─ advertiser.ts
   ├─ internal-ads.ts
   └─ connector-runs.ts
```

---

# 31. Normalized Ad Schema

```ts
interface NormalizedAd {
  platform: "tiktok";

  source:
    | "official_adlib"
    | "public_library"
    | "creative_center"
    | "marketing_api"
    | "apify";

  externalAdId: string;

  advertiserId?: string;
  advertiserName?: string;

  title?: string;
  text?: string;

  creativeType?:
    | "video"
    | "image"
    | "carousel";

  mediaUrl?: string;
  thumbnailUrl?: string;

  landingPageUrl?: string;

  firstSeenAt?: string;
  lastSeenAt?: string;

  countries?: string[];

  ageTargeting?: unknown;
  genderTargeting?: unknown;

  reach?: number;

  impressions?: number;
  clicks?: number;
  ctr?: number;

  spend?: number;
  conversions?: number;
  cpa?: number;
  roas?: number;

  rawData: unknown;
}
```

---

# 32. Deduplication

Competitor data có thể xuất hiện từ nhiều provider.

Primary key:

```text
platform
+
externalAdId
```

Fallback:

```text
advertiser
+
video hash
+
landing domain
```

---

# 33. Keyword expansion

Không chỉ search:

```text
posture corrector
```

Nên mở rộng:

```text
posture brace
back brace
back support
posture support
shoulder posture
back straightener
```

Sau đó:

```text
merge
↓
dedup
```

---

# 34. Multi-region

Không chỉ search một region.

Ví dụ:

```ts
const REGIONS = [
  "FR",
  "DE",
  "IT",
  "ES",
  "NL",
  "BE",
  "SE",
  "GB"
];
```

Flow:

```text
keyword
│
├─ FR
├─ DE
├─ IT
├─ ES
└─ GB
```

---

# 35. Date range

Không hardcode timestamp cũ.

Dùng dynamic range.

Ví dụ:

```ts
const endTime = Date.now();

const startTime =
  endTime - 90 * 24 * 60 * 60 * 1000;
```

Lưu ý:

```text
UI có thể dùng milliseconds
API có thể dùng seconds hoặc YYYYMMDD
```

Cần xác định chính xác theo provider.

---

# 36. Connector statuses

Không dùng:

```text
healthy / unhealthy
```

quá đơn giản.

Nên có:

```ts
type ConnectorStatus =
  | "OK"
  | "SUCCESS_EMPTY"
  | "REQUEST_INVALID"
  | "GEO_BLOCKED"
  | "AUTH_REQUIRED"
  | "RATE_LIMITED"
  | "BOT_BLOCKED"
  | "PARSER_BROKEN"
  | "UPSTREAM_CHANGED"
  | "NETWORK_ERROR";
```

---

# 37. Healthcheck logic

Không:

```text
HTTP 200
↓
healthy
```

HTTP 200 có thể là:

```text
HTML challenge
captcha
error page
schema khác
empty abnormal
```

Phải check:

```text
status
content-type
body
schema
record count
```

---

# 38. Logging

Mỗi connector run cần lưu:

```text
connector
provider
query
region
date range
request URL
request method
HTTP status
content-type
result count
duration
error
response preview
```

---

# 39. connector_runs table

```sql
CREATE TABLE connector_runs (
    id TEXT PRIMARY KEY,

    platform TEXT NOT NULL,
    connector TEXT NOT NULL,
    provider TEXT NOT NULL,

    query TEXT,
    region TEXT,

    started_at TEXT,
    finished_at TEXT,

    status TEXT,

    http_status INTEGER,

    records_found INTEGER DEFAULT 0,

    error_message TEXT,

    response_preview TEXT
);
```

---

# 40. competitor_ads table

```sql
CREATE TABLE competitor_ads (
    id TEXT PRIMARY KEY,

    platform TEXT NOT NULL,
    source TEXT NOT NULL,

    external_ad_id TEXT,

    advertiser_id TEXT,
    advertiser_name TEXT,

    title TEXT,
    ad_text TEXT,

    media_url TEXT,
    thumbnail_url TEXT,

    landing_page_url TEXT,

    first_seen_at TEXT,
    last_seen_at TEXT,

    countries_json TEXT,
    targeting_json TEXT,

    raw_json TEXT,

    created_at TEXT,
    updated_at TEXT
);
```

---

# 41. internal_ads table

```sql
CREATE TABLE internal_ads (
    id TEXT PRIMARY KEY,

    platform TEXT NOT NULL,

    advertiser_id TEXT,
    campaign_id TEXT,
    adgroup_id TEXT,
    ad_id TEXT,

    spend REAL,
    impressions INTEGER,
    clicks INTEGER,
    ctr REAL,

    conversions REAL,
    cpa REAL,
    roas REAL,

    stat_date TEXT,

    raw_json TEXT,

    created_at TEXT,
    updated_at TEXT
);
```

---

# 42. Fallback architecture

```text
Search Competitor Ads
       │
       ▼
Official AdLib API
       │
       ├─ success → normalize
       │
       ▼ fail

Public Library API
       │
       ├─ success → normalize
       │
       ▼ fail

Browser Library Scraper
       │
       ├─ success → normalize
       │
       ▼ fail

Apify
       │
       ├─ success → normalize
       │
       ▼ fail

Paid provider
```

---

# 43. Internal Ads architecture

```text
TikTok Business
       │
       ▼
OAuth
       │
       ▼
Marketing API
       │
       ├─ campaigns
       ├─ adgroups
       ├─ ads
       ├─ reports
       ├─ audience
       └─ pixel
       │
       ▼
internal_ads
```

---

# 44. Rate limiting

Không spam TikTok.

Dùng:

```text
delay
retry
backoff
jitter
```

Ví dụ:

```ts
const delay =
  1000 +
  Math.random() * 2000;
```

---

# 45. Browser concurrency

Giới hạn:

```text
2–5 concurrent pages
```

Không tạo hàng trăm Chrome instance.

---

# 46. Cache

Cache theo:

```text
keyword
region
date
provider
```

Ví dụ:

```text
tiktok:public:FR:posture-corrector:2026-10-07
```

TTL:

```text
6–24 giờ
```

---

# 47. Video storage

Không nhất thiết download mọi video ngay.

Nên lưu trước:

```text
source video URL
thumbnail
ad_id
```

Chỉ download khi:

```text
- user mở detail
- ad được đánh giá tiềm năng
- cần AI video analysis
```

giảm storage cost.

---

# 48. AI Creative Analysis

Sau khi có video:

```text
video
↓
frame extraction
↓
speech transcription
↓
OCR text
↓
creative analysis
```

Output:

```text
hook
problem
solution
CTA
offer
emotion
visual pattern
product demo
UGC style
duration
angle
```

---

# 49. Competitor score

Có thể tính:

```text
Ad Score =
longevity
+ advertiser frequency
+ creative recurrence
+ reach
+ targeting breadth
+ landing page quality
+ product novelty
```

Không chỉ dựa vào:

```text
views
```

---

# 50. Internal performance score

```text
Internal Ad Score =
CTR
+ conversion rate
+ ROAS
- CPA
- CPC
```

---

# 51. Gap Analysis

Ví dụ:

```text
Competitor:
UGC
before/after
pain hook
female 25–34

Our ads:
product demo
no UGC
generic hook

Gap:
UGC missing
pain-first hook missing
audience mismatch
```

---

# 52. Product decision engine

ToolSpy có thể đưa ra:

```text
BUY / TEST / WATCH / SKIP
```

dựa trên:

```text
competitor density
ad longevity
creative diversity
market fit
our performance
margin
product novelty
trend velocity
```

---

# 53. n8n workflow

```text
Cron
↓
Keyword Queue
↓
TikTok Competitor Connector
↓
TikTok Internal Connector
↓
Normalize
↓
Dedup
↓
AI Analyze
↓
D1
↓
Dashboard
```

---

# 54. Priority triển khai

## Phase 1

Sửa TikTok competitor connector.

Test:

```text
shop + FR
fashion + FR
beauty + FR
```

---

## Phase 2

Test URL UI:

```text
https://library.tiktok.com/ads
```

với:

```text
adv_name
region
start_time
end_time
query_type
sort_type
```

---

## Phase 3

Dùng DevTools Network để tìm API request thật hiện tại.

---

## Phase 4

Nếu API call trực tiếp được:

```text
API provider
```

Nếu không:

```text
Playwright browser provider
```

---

## Phase 5

Đăng ký TikTok Commercial Content API.

---

## Phase 6

Tích hợp Marketing API theo hướng AdsMCP.

---

## Phase 7

Creative Center enrichment.

---

## Phase 8

Apify fallback.

---

# 55. Cái gì cần lấy từ từng repo?

## Tikadrchivist

Lấy:

```text
request structure
pagination concept
TikTok public search API pattern
```

Không copy nguyên.

---

## tarxn/tiktok-ads-scraper

Lấy:

```text
search URL pattern
adv_name
detail page
field mapping
DOM exploration logic
dataset schema
```

Không copy nguyên Selenium implementation.

---

## AdsMCP/tiktok-ads-mcp-server

Lấy:

```text
OAuth flow
Marketing API client architecture
campaign endpoints
report endpoints
audience endpoints
pixel endpoints
error handling
token storage pattern
```

Không dùng nó cho competitor scraping.

---

# 56. Mức độ hữu ích

```text
tarxn/tiktok-ads-scraper

Competitor Ads:
★★★★★

Internal Ads:
★☆☆☆☆
```

```text
AdsMCP/tiktok-ads-mcp-server

Competitor Ads:
★★☆☆☆

Internal Ads:
★★★★★
```

```text
Tikadrchivist

Competitor Ads:
★★★★★

Internal Ads:
☆☆☆☆☆
```

---

# 57. Kết luận về lỗi TikTok hiện tại

Trước khi tiếp tục đổi proxy:

```text
STOP
```

Hãy kiểm tra:

```text
1. URL search có dùng adv_name không?
2. region có đúng không?
3. date range có đúng không?
4. timestamp là seconds hay milliseconds?
5. query_type có đúng không?
6. sort_type có đúng không?
7. UI TikTok Library có trả ads không?
8. Network request hiện tại là gì?
9. parser có còn đúng schema không?
10. response có phải JSON không?
```

---

# 58. Test case đề xuất

Test 1:

```text
keyword = shop
region = FR
```

Test 2:

```text
keyword = fashion
region = FR
```

Test 3:

```text
keyword = beauty
region = FR
```

Chỉ khi cả 3 đều fail mới kết luận connector có vấn đề lớn.

---

# 59. Kiến trúc cuối cùng đề xuất

```text
                         TOOLSPY
                            │
                            ▼
                   Product Search Engine
                            │
                            ▼
                      Keyword Expansion
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼

        Competitor Data              Internal Data
              │                           │
      ┌───────┼────────┐                  │
      │       │        │                  │
      ▼       ▼        ▼                  ▼

 TikTok    Meta     Shopify       TikTok Marketing API
 Library   Ads      Stores                  │
      │       │        │                  │
      └───────┼────────┘                  │
              │                           │
              └─────────────┬─────────────┘
                            ▼
                        NORMALIZER
                            │
                            ▼
                          DEDUP
                            │
                            ▼
                     ANALYSIS ENGINE
                            │
             ┌──────────────┼──────────────┐
             │              │              │
             ▼              ▼              ▼

       Product Score   Creative Gap   Market Fit
             │              │              │
             └──────────────┼──────────────┘
                            ▼
                         ToolSpy UI
```

---

# 60. Final strategy

TikTok trong ToolSpy không nên chỉ là:

```text
TikTok scraper
```

mà nên là:

```text
TikTok Intelligence Layer
```

bao gồm:

```text
Competitor Ads
+
Internal Ads
+
Creative Trends
+
Audience
+
Performance
+
AI Analysis
```

---

# 61. Tài liệu tham khảo

TikTok Commercial Content Library:

```text
https://library.tiktok.com/
```

TikTok Commercial Content API:

```text
https://developers.tiktok.com/products/commercial-content-api
```

TikTok Query Ads API:

```text
https://developers.tiktok.com/docs/en/commercial-content-api-query-ads
```

TikTok Ad Details:

```text
https://developers.tiktok.com/docs/en/commercial-content-api-get-ad-details
```

Tikadrchivist:

```text
https://github.com/CheckFirstHQ/Tikadrchivist
```

tarxn TikTok Ads Scraper:

```text
https://github.com/tarxn/tiktok-ads-scraper
```

AdsMCP:

```text
https://github.com/AdsMCP/tiktok-ads-mcp-server
```

FlowExtract TikTok Ad Library Scraper:

```text
https://github.com/FlowExtractAPI/tiktok-ad-library-scraper
```

TikTok Creative Center:

```text
https://ads.tiktok.com/business/creativecenter/
```

---

# 62. Checklist triển khai ngay

- [ ] Tách competitor ads khỏi internal ads.
- [ ] Tách Ad Library khỏi Creative Center.
- [ ] Test `shop + FR`.
- [ ] Test `fashion + FR`.
- [ ] Test `beauty + FR`.
- [ ] Test `adv_name` thay vì `query`.
- [ ] Kiểm tra timestamp seconds vs milliseconds.
- [ ] Kiểm tra Network request thật bằng DevTools.
- [ ] Viết TikTokPublicLibraryProvider.
- [ ] Viết TikTokBrowserLibraryProvider.
- [ ] Dùng Playwright thay Selenium nếu viết mới.
- [ ] Không hardcode DOM selector quá sâu.
- [ ] Log raw response.
- [ ] Thêm status classification.
- [ ] Thêm pagination.
- [ ] Thêm multi-region.
- [ ] Thêm keyword expansion.
- [ ] Thêm cache.
- [ ] Thêm rate limit.
- [ ] Thêm dedup.
- [ ] Đăng ký Commercial Content API.
- [ ] Tích hợp Marketing API.
- [ ] Tham khảo AdsMCP cho OAuth/reporting.
- [ ] Creative Center chỉ dùng enrichment.
- [ ] Apify chỉ dùng fallback.
- [ ] So competitor ads với internal ads.
- [ ] Xây AI creative gap analysis.

---

# 63. Kết luận ngắn

Không tiếp tục xử lý TikTok theo hướng:

```text
0 results
↓
đổi proxy
↓
0 results
↓
đổi proxy
```

Hướng đúng:

```text
xác định nguồn
↓
xác định request thật
↓
test keyword rộng
↓
intercept network
↓
API first
↓
browser fallback
↓
paid fallback cuối cùng
```

Và quan trọng hơn:

```text
Competitor Ads
+
Internal Ads
+
Creative Trends
```

phải được kết hợp trong cùng ToolSpy để hệ thống không chỉ biết:

```text
đối thủ đang làm gì
```

mà còn biết:

```text
công ty đang thiếu gì
và nên làm gì tiếp theo
```
