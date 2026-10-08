# TikTok Commercial Content API — Integration Guide cho Spy Ads

## 1. Trạng thái hiện tại

Application của bạn đã được TikTok phê duyệt:

> Your Application to Commercial Content API is Approved!

Điều này có nghĩa là bạn đã có quyền sử dụng TikTok Commercial Content API theo project/research client đã được TikTok cấp.

Mục tiêu triển khai trong app:

```text
Marketing Search
      ↓
Spy Ads Frontend
      ↓
Backend API
      ↓
TikTok Commercial Content API
      ↓
Query Ads
      ↓
Ad Detail
      ↓
Normalizer
      ↓
Cloudflare D1
      ↓
Win Score / Product Matching
      ↓
Spy Ads UI
```

---

# 2. Hai key quan trọng nhất cần lấy

Bạn cần lấy:

```env
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
```

Hai giá trị này nằm trong **Research Client** của project Commercial Content API đã được duyệt.

Tài liệu chính thức:

- Commercial Content API:
  https://developers.tiktok.com/products/commercial-content-api
- Getting Started:
  https://developers.tiktok.com/docs/en/commercial-content-api-getting-started

Luồng:

```text
TikTok for Developers
        ↓
Approved Commercial Content API Project
        ↓
Research Client
        ↓
Client Key
Client Secret
```

TikTok thường có nút Display / biểu tượng con mắt để xem credentials.

## Lưu ý bảo mật

Không:

```text
- gửi Client Secret qua chat/public
- commit Client Secret lên GitHub
- đặt Client Secret trong frontend
- hard-code secret vào source code
```

Nên lưu trong backend secrets.

Ví dụ Cloudflare Worker:

```bash
npx wrangler secret put TIKTOK_CLIENT_KEY
npx wrangler secret put TIKTOK_CLIENT_SECRET
```

Local development:

```env
TIKTOK_CLIENT_KEY=xxxxxxxx
TIKTOK_CLIENT_SECRET=xxxxxxxx
```

`.gitignore`:

```gitignore
.dev.vars
.env
.env.local
```

---

# 3. Access Token

Client Key và Client Secret không dùng trực tiếp để query Ads.

App phải đổi chúng thành Access Token.

Endpoint:

```http
POST https://open.tiktokapis.com/v2/oauth/token/
```

Body:

```text
client_key=YOUR_CLIENT_KEY
client_secret=YOUR_CLIENT_SECRET
grant_type=client_credentials
```

Response điển hình:

```json
{
  "access_token": "clt.xxxxxxxxx",
  "expires_in": 7200,
  "token_type": "Bearer"
}
```

Access token có thời hạn, vì vậy app nên tự sinh và cache token.

Không nên lưu access token cố định trong `.env`.

Luồng:

```text
CLIENT KEY
    +
CLIENT SECRET
    ↓
OAuth endpoint
    ↓
Access Token
    ↓
Commercial Content API
```

---

# 4. Test Token bằng PowerShell

```powershell
$clientKey = "YOUR_CLIENT_KEY"
$clientSecret = "YOUR_CLIENT_SECRET"

$tokenResponse = Invoke-RestMethod `
  -Method POST `
  -Uri "https://open.tiktokapis.com/v2/oauth/token/" `
  -ContentType "application/x-www-form-urlencoded" `
  -Body @{
      client_key = $clientKey
      client_secret = $clientSecret
      grant_type = "client_credentials"
  }

$tokenResponse
```

Nếu thành công:

```text
Application Approved ✅
Client Key ✅
Client Secret ✅
Authentication ✅
```

---

# 5. Scope chính

Scope quan trọng cho Commercial Content API Ads:

```text
research.adlib.basic
```

Scope này được dùng cho các API như:

```text
Query Ads
Get Ad Detail
Query Advertisers
```

---

# 6. Query Ads API

Endpoint:

```http
POST https://open.tiktokapis.com/v2/research/adlib/ad/query/
```

API dùng để tìm quảng cáo theo:

```text
keyword
advertiser
country
date range
ad status
ad type
reach
age
gender
```

Các search type quan trọng:

```text
exact_phrase
fuzzy_phrase
```

## Khi nào dùng fuzzy_phrase

Ví dụ:

```text
lucky bracelet
```

`fuzzy_phrase` phù hợp với Spy Ads vì có thể giúp tìm các ads có liên quan đến một hoặc nhiều từ trong cụm.

## Khi nào dùng exact_phrase

Khi bạn muốn tìm chính xác:

```text
feng shui bracelet
```

---

# 7. Ví dụ Query Ads bằng PowerShell

```powershell
$accessToken = $tokenResponse.access_token

$fields = "ad.id,ad.first_shown_date,ad.last_shown_date,ad.status,ad.videos,ad.image_urls,ad.reach,advertiser.business_id,advertiser.business_name,advertiser.paid_for_by"

$body = @{
    search_term = "lucky bracelet"
    search_type = "fuzzy_phrase"
    max_count = 10

    filters = @{
        ad_published_date_range = @{
            min = "20260901"
            max = "20261008"
        }

        country_code_list = @(
            "FR",
            "DE",
            "IT",
            "ES"
        )

        ad_type = "VIDEO"
        ad_status = "ACTIVE"
    }
} | ConvertTo-Json -Depth 10

$result = Invoke-RestMethod `
    -Method POST `
    -Uri "https://open.tiktokapis.com/v2/research/adlib/ad/query/?fields=$fields" `
    -Headers @{
        Authorization = "Bearer $accessToken"
    } `
    -ContentType "application/json" `
    -Body $body

$result.data.ads
```

---

# 8. Các field quan trọng nên lấy từ Query Ads

```text
ad.id
ad.first_shown_date
ad.last_shown_date
ad.status
ad.status_statement
ad.videos
ad.image_urls
ad.reach

advertiser.business_id
advertiser.business_name
advertiser.paid_for_by
```

Trong đó:

```text
ad.id
```

là ID cực kỳ quan trọng vì dùng để:

```text
deduplicate
get detail
update ad
cross-source matching
```

Database key logic nên là:

```text
source = tiktok
source_ad_id = ad.id
```

---

# 9. Pagination

Query Ads thường không trả toàn bộ kết quả trong một request.

Response có thể dạng:

```json
{
  "data": {
    "ads": [],
    "has_more": true,
    "search_id": "xxxxxxxx"
  }
}
```

Nếu:

```text
has_more = true
```

thì request tiếp theo dùng:

```text
search_id
```

Lưu ý:

Nếu đổi:

```text
search_term
filters
country
date
```

thì không được reuse `search_id` của query cũ.

---

# 10. Multi-keyword Search

Đây là cách phù hợp với tính năng search hiện tại của app.

Marketing nhập:

```text
feng shui
lucky bracelet
wealth bracelet
pixiu bracelet
fortune bracelet
```

Backend chạy:

```text
Query 1 → feng shui
Query 2 → lucky bracelet
Query 3 → wealth bracelet
Query 4 → pixiu bracelet
Query 5 → fortune bracelet
```

Sau đó:

```text
TikTok API
    ↓
Raw results
    ↓
Deduplicate ad.id
    ↓
Filter
    ↓
Get Detail
    ↓
Normalize
    ↓
D1
```

---

# 11. Get Ad Detail

Endpoint:

```http
POST https://open.tiktokapis.com/v2/research/adlib/ad/detail/
```

Dùng `ad.id` từ Query Ads để lấy thông tin sâu hơn.

Các field quan trọng:

```text
ad.id
ad.title
ad.videos
ad.image_urls
ad.reach

ad.external_url
ad.download_url
ad.call_to_action
ad.advertising_objective

advertiser.country_code
advertiser.business_id
advertiser.business_name
advertiser.paid_for_by

advertiser.follower_count
advertiser.avatar_url
advertiser.profile_url

ad_group.targeting_info
```

Đây là API rất quan trọng cho Win Score.

---

# 12. TikTok Connector nên nằm trong backend

Cấu trúc đề xuất:

```text
src/
├── services/
│   └── sources/
│       └── tiktok/
│           ├── tiktok.auth.ts
│           ├── tiktok.query.ts
│           ├── tiktok.detail.ts
│           ├── tiktok.mapper.ts
│           └── tiktok.types.ts
│
├── routes/
│   └── tiktok.ts
│
└── db/
    └── tiktok.sql
```

Không gọi TikTok API trực tiếp từ browser.

Sai:

```text
Frontend
   ↓
TikTok API
```

Đúng:

```text
Frontend
   ↓
Your Backend
   ↓
TikTok API
```

---

# 13. Auth Module

```typescript
export interface TikTokEnv {
  TIKTOK_CLIENT_KEY: string;
  TIKTOK_CLIENT_SECRET: string;
}

interface TokenResponse {
  access_token: string;
  expires_in: number;
  token_type: string;
}

export async function getTikTokAccessToken(
  env: TikTokEnv
): Promise<TokenResponse> {
  const body = new URLSearchParams();

  body.set("client_key", env.TIKTOK_CLIENT_KEY);
  body.set("client_secret", env.TIKTOK_CLIENT_SECRET);
  body.set("grant_type", "client_credentials");

  const response = await fetch(
    "https://open.tiktokapis.com/v2/oauth/token/",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body,
    }
  );

  if (!response.ok) {
    throw new Error(
      `TikTok OAuth failed: ${response.status} ${await response.text()}`
    );
  }

  return response.json<TokenResponse>();
}
```

---

# 14. Query Module

```typescript
const QUERY_FIELDS = [
  "ad.id",
  "ad.first_shown_date",
  "ad.last_shown_date",
  "ad.status",
  "ad.status_statement",
  "ad.videos",
  "ad.image_urls",
  "ad.reach",
  "advertiser.business_id",
  "advertiser.business_name",
  "advertiser.paid_for_by",
].join(",");

export interface TikTokSearchParams {
  keyword?: string;
  countries?: string[];
  minDate: string;
  maxDate: string;
  adType?: "VIDEO" | "IMAGES" | "TEXT";
  adStatus?: "ACTIVE" | "INACTIVE";
  searchId?: string;
}
```

---

# 15. Không nên gọi Detail cho tất cả ads

Ví dụ Query Ads trả:

```text
320 ads
```

Không nên ngay lập tức:

```text
320 ads
↓
320 Detail API calls
```

Nên lọc trước.

Ví dụ:

```text
Query Ads
     ↓
Active only
     ↓
Reach >= threshold
     ↓
Longevity >= threshold
     ↓
Has video
     ↓
Top 30–50 ads
     ↓
Get Detail
```

Ví dụ:

```text
120 ads
↓
82 ads sau Reach filter
↓
47 ads sau Longevity filter
↓
47 Detail requests
```

---

# 16. Normalizer

Không nên lưu raw response trực tiếp làm schema chính.

Luồng:

```text
TikTok API
    ↓
raw_source_data
    ↓
TikTok Mapper
    ↓
Normalized Ad
    ↓
ads table
```

Ví dụ normalized object:

```json
{
  "source": "tiktok",
  "source_ad_id": "123456789",

  "advertiser_id": "123",
  "advertiser_name": "Lucky Shop",
  "advertiser_country": "FR",

  "title": "Lucky Bracelet",

  "video_url": "...",
  "image_url": "...",

  "landing_url": "...",
  "download_url": "...",

  "call_to_action": "Shop Now",
  "objective": "...",

  "first_seen": "20260901",
  "last_seen": "20261008",

  "status": "ACTIVE",

  "reach": {},

  "targeting": {},

  "keyword_found": "lucky bracelet"
}
```

---

# 17. Cloudflare D1 Schema

```sql
CREATE TABLE IF NOT EXISTS ads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source TEXT NOT NULL,
    source_ad_id TEXT NOT NULL,

    advertiser_id TEXT,
    advertiser_name TEXT,
    advertiser_country TEXT,

    title TEXT,

    video_url TEXT,
    image_url TEXT,

    landing_url TEXT,
    download_url TEXT,

    call_to_action TEXT,
    objective TEXT,

    first_seen TEXT,
    last_seen TEXT,

    status TEXT,

    reach_json TEXT,
    targeting_json TEXT,

    keyword_found TEXT,

    raw_data TEXT,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(source, source_ad_id)
);

CREATE INDEX IF NOT EXISTS idx_ads_source
ON ads(source);

CREATE INDEX IF NOT EXISTS idx_ads_advertiser
ON ads(advertiser_name);

CREATE INDEX IF NOT EXISTS idx_ads_status
ON ads(status);
```

---

# 18. Win Score

Commercial Content API có thể cung cấp các tín hiệu tốt cho Win Score:

```text
Longevity
Reach
Advertiser
CTA
Landing Page
Objective
Status
Creative
Targeting
```

Một mô hình tham khảo:

```text
TikTok Win Score

25% Longevity
20% Reach
15% Creative strength
15% Advertiser strength
10% CTA / Commercial intent
10% Cross-platform presence
5% Novelty
```

Ví dụ:

```text
ACTIVE
first_seen = 01/09
last_seen = 08/10
reach = 100K+
CTA = Shop Now
landing page = product page
```

Có thể hiển thị:

```text
🔥 TikTok Win Score: 87/100

Why winning

✓ Running continuously
✓ Strong reach
✓ High commercial intent
✓ Dedicated landing page
✓ Active video creative
✓ Advertiser still spending
```

---

# 19. Commercial Content API có spy 100% TikTok Ads không?

Không.

Không nên xem Commercial Content API là:

```text
100% TikTok Ads toàn cầu
```

Nên xem nó là:

```text
Official TikTok Commercial Ads Source
```

Ưu điểm:

```text
official data
reliable advertiser metadata
reach
targeting
creative
landing page
CTA
status
first shown
last shown
```

Nhược điểm:

```text
không bao phủ toàn bộ thế giới
không phải historical database vô hạn
không đảm bảo 100% mọi TikTok Ads
```

---

# 20. Hạn chế thị trường

Đây là điểm đặc biệt quan trọng đối với app của bạn.

Commercial Content API hiện không phải nguồn tốt nhất để bao phủ:

```text
Philippines
Saudi Arabia
UAE
USA
```

Trong khi mục tiêu của app có tập trung vào:

```text
OFW Philippines
Saudi
UAE
```

Do đó không nên dùng Commercial Content API làm nguồn TikTok duy nhất.

---

# 21. Kiến trúc TikTok nên dùng dạng Hybrid

```text
                    TIKTOK ADS
                         │
        ┌────────────────┼─────────────────┐
        │                │                 │
        ▼                ▼                 ▼
Commercial Content    Apify           Creative Center
API official          Scrapers        / Public Sources
        │                │                 │
        └────────────────┼─────────────────┘
                         ↓
                    NORMALIZER
                         ↓
                    DEDUPLICATE
                         ↓
                         D1
                         ↓
                    WIN ENGINE
```

---

# 22. Source Confidence

Nên lưu độ tin cậy theo nguồn.

Ví dụ Official API:

```text
source = tiktok_official
confidence = HIGH
```

Apify:

```text
source = tiktok_apify
confidence = MEDIUM
```

Nếu một ad cùng xuất hiện ở nhiều nguồn:

```text
TikTok Official
+
Apify
+
PipiAds / other source
```

thì:

```text
Cross-source match = 3
Source confidence = VERY HIGH
```

---

# 23. Apify nên đóng vai trò gì?

Commercial Content API:

```text
Official source
↓
EU / supported markets
```

Apify:

```text
Supplementary source
↓
Potential wider market coverage
```

Nhưng phải phân biệt:

```text
TikTok Scraper
≠
TikTok Ads Scraper
```

Nhiều Actor Apify chỉ crawl:

```text
videos
profiles
hashtags
comments
organic search
```

chứ không phải paid ads.

Cần kiểm tra Input/Output của Actor trước khi tích hợp.

---

# 24. Kiến trúc Apify Connector

```text
src/
└── services/
    └── sources/
        └── apify/
            ├── apify.client.ts
            ├── apify.run.ts
            ├── apify.webhook.ts
            │
            ├── tiktok/
            │   ├── tiktok.input.ts
            │   └── tiktok.normalizer.ts
            │
            └── 1688/
                ├── 1688.input.ts
                └── 1688.normalizer.ts
```

TikTok Official và TikTok Apify cùng đi qua một Normalized Ad Schema.

---

# 25. 1688 không nên lưu vào Ads table

1688 là nguồn sourcing/product.

Nên lưu:

```text
products
```

Ví dụ:

```json
{
  "source": "1688",

  "source_product_id": "123456",

  "title": "Pixiu Lucky Bracelet",

  "image_url": "...",

  "price_min": 4.5,
  "price_max": 7.8,

  "currency": "CNY",

  "moq": 10,

  "supplier_name": "...",
  "supplier_url": "...",

  "product_url": "..."
}
```

Sau đó match với TikTok Ads:

```text
TikTok Ad
"Lucky Pixiu Bracelet"
        ↓
AI Similarity
        ↓
1688 Product
"Feng Shui Pixiu Bracelet"
        ↓
Product Cost
        ↓
Potential Selling Price
        ↓
Margin
        ↓
Opportunity Score
```

---

# 26. Mục tiêu đúng của hệ thống

Không nên đặt KPI:

```text
100% TikTok Ads
```

Nên đặt:

```text
Maximum Practical TikTok Ads Coverage
```

Tối ưu theo:

```text
Coverage
Freshness
Reliability
Cross-source verification
Search relevance
Product match quality
```

---

# 27. Kiến trúc cuối cùng cho Spy Ads

```text
                    AD SOURCES

Meta Ad Library
TikTok Commercial Content API
TikTok Apify
TikTok Creative Center
PipiAds / Minea / Other permitted sources

                         ↓

                     ADS DB

                         ↓
                  Product Cluster
                         ↓
                     Win Engine

                         ↕

                 SOURCING SOURCES

1688
Taobao
Alibaba
AliExpress
Amazon
Shopify

                         ↓

                    PRODUCTS DB

                         ↓

               PRODUCT INTELLIGENCE

Ad strength
+
Ad longevity
+
Reach
+
Advertiser strength
+
Creative quality
+
Supplier availability
+
Product cost
+
Selling price
+
Margin
+
Competition
+
Market suitability
+
OFW suitability

                         ↓

                    WIN SCORE
```

---

# 28. Thứ tự triển khai đề xuất

## Phase 1 — Authentication

```text
Client Key
Client Secret
↓
Access Token
```

Test thành công trước.

## Phase 2 — Query Ads

Test:

```text
keyword = lucky bracelet
country = FR
```

Xác nhận trả:

```text
ad.id
advertiser
video
reach
status
```

## Phase 3 — Ad Detail

Dùng một:

```text
ad.id
```

để test:

```text
landing_url
CTA
objective
targeting
advertiser detail
```

## Phase 4 — Backend Connector

Tạo:

```text
tiktok.auth.ts
tiktok.query.ts
tiktok.detail.ts
tiktok.mapper.ts
```

## Phase 5 — Database

Tạo:

```text
ads
raw_source_data
source_runs
```

và UPSERT theo:

```text
source + source_ad_id
```

## Phase 6 — Multi-keyword

Cho phép Marketing nhập:

```text
feng shui
lucky bracelet
wealth bracelet
pixiu
fortune bracelet
```

## Phase 7 — Scoring

Lọc:

```text
Active
Reach
Longevity
Creative
Advertiser
CTA
Landing Page
```

rồi mới gọi Detail.

## Phase 8 — Hybrid TikTok Sources

Thêm:

```text
Commercial Content API
+
Apify
+
Creative Center
```

## Phase 9 — Product Sourcing

Match:

```text
TikTok winning ad
↓
1688 / Taobao / Alibaba
↓
Cost / supplier / margin
↓
Opportunity Score
```

---

# 29. Các biến môi trường nên có

```env
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=

APIFY_TOKEN=

DATABASE_ID=
```

Không cần lưu cố định:

```env
TIKTOK_ACCESS_TOKEN=
```

Access Token nên do backend tự quản lý.

---

# 30. Checklist triển khai

```text
[ ] Commercial Content API approved

[ ] Lấy Client Key
[ ] Lấy Client Secret

[ ] Lưu secrets backend

[ ] Test OAuth token

[ ] Test Query Ads

[ ] Test pagination

[ ] Test Ad Detail

[ ] Tạo TikTok Auth module

[ ] Tạo TikTok Query module

[ ] Tạo TikTok Detail module

[ ] Tạo TikTok Normalizer

[ ] Tạo D1 schema

[ ] Dedupe theo source + ad.id

[ ] Multi-keyword search

[ ] Reach filter

[ ] Longevity filter

[ ] Active status filter

[ ] Detail only for shortlisted ads

[ ] Win Score

[ ] Source confidence

[ ] Apify fallback / supplementary source

[ ] Creative Center source

[ ] 1688 Product source

[ ] Product Matching

[ ] Opportunity Score
```

---

# 31. Tài liệu TikTok cần bookmark

Commercial Content API:

https://developers.tiktok.com/products/commercial-content-api

Getting Started:

https://developers.tiktok.com/docs/en/commercial-content-api-getting-started

Client Access Token:

https://developers.tiktok.com/docs/en/client-access-token-management

Query Ads:

https://developers.tiktok.com/docs/en/commercial-content-api-query-ads

Get Ad Detail:

https://developers.tiktok.com/docs/en/commercial-content-api-get-ad-details

Query Advertisers:

https://developers.tiktok.com/docs/en/commercial-content-api-query-advertisers

Supported Countries:

https://developers.tiktok.com/docs/en/commercial-content-api-supported-countries

---

# 32. Kết luận

Commercial Content API nên là:

```text
TikTok Official Connector
```

trong Spy Ads.

Không nên phụ thuộc hoàn toàn vào nó để đạt coverage toàn cầu.

Kiến trúc tốt nhất:

```text
TikTok Commercial Content API
+
Apify
+
TikTok Creative Center
+
Other permitted sources
        ↓
Normalizer
        ↓
D1
        ↓
Win Score
        ↓
Product Matching
        ↓
1688 / Taobao / Alibaba
        ↓
Opportunity Score
```

Mục tiêu cuối cùng không phải chỉ là:

```text
"tìm được nhiều ads"
```

mà là:

```text
Tìm Ads
↓
Xác minh độ mạnh
↓
Xác định sản phẩm
↓
Tìm nguồn nhập
↓
Ước tính margin
↓
Đánh giá market fit
↓
Đưa ra Win Score
```

Đây là hướng nên dùng để biến Commercial Content API từ một nguồn dữ liệu đơn thuần thành một phần của hệ thống Product Intelligence / Spy Ads hoàn chỉnh.
