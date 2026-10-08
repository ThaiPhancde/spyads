# Thiết kế hệ thống Spy Ads + Product Sourcing Platform

## 1. Mục tiêu cốt lõi

Hệ thống không phải là một app tổng hợp sản phẩm từ 1688, Taobao, Alibaba, Pinduoduo hay AliExpress.

Mục tiêu chính là xây dựng một **kho quảng cáo tập trung (Central Ads Store / Ads Intelligence Platform)** để đội Marketing có thể:

- Xem các quảng cáo đang chạy trên nhiều nền tảng.
- Tìm sản phẩm đang được nhiều đối thủ quảng cáo.
- Xem creative, video, hình ảnh, hook, CTA, landing page.
- Theo dõi advertiser, số ngày chạy, thị trường, mức độ cạnh tranh.
- Gom nhiều quảng cáo cùng một sản phẩm vào chung một Product Cluster.
- Phân tích sản phẩm nào có khả năng đang được scale.
- Sau khi phát hiện sản phẩm tiềm năng mới truy ngược về nguồn hàng Trung Quốc.
- So sánh giá nhập, supplier, MOQ, số đơn, rating và biên lợi nhuận.
- Hỗ trợ Marketing quyết định có nên test sản phẩm hay không.

Tư duy đúng của hệ thống là:

```text
ADS
↓
COMPETITOR
↓
CREATIVE
↓
PRODUCT
↓
MARKET
↓
SOURCE
↓
DECISION
```

Trong đó:

- **ADS** là dữ liệu trung tâm.
- **1688 / Taobao / Alibaba / Pinduoduo / AliExpress** chỉ là lớp `SOURCE`.
- Không biến hệ thống thành catalog hàng Trung Quốc.

**Phạm vi (cập nhật 07/10/2026):**

- Chỉ săn **sản phẩm vật lý nhập được từ Trung Quốc** (xem §10.1), bán qua quảng cáo social tới ME / US / EU / AU, chốt đơn qua Messenger / WhatsApp / landing page COD.
- Nguồn Ads là **social ad library + spy tool**: Meta, TikTok, Snapchat, Pipiads, Minea, BigSpy, Chrome extension.
- **Không dùng** Google / YouTube Ads, Bing / Microsoft Ads: quảng cáo theo search intent, phần lớn là text ads, ít creative sản phẩm, không phải kênh của MKT Mess / Ladi.
- **Không dùng** Amazon, Walmart, eBay, Etsy, Temu, Noon: đó là sàn bán lẻ, không phải nguồn hàng và không phải quảng cáo. Thêm chúng sẽ biến app thành "marketplace tracker".

---

# 2. Luồng tổng thể

```text
META ADS
TIKTOK ADS
SNAPCHAT ADS
PIPIADS
MINEA
BIGSPY
CHROME EXTENSION
OTHER SPY EXPORTS
      │
      ▼
┌───────────────────────────────┐
│       CENTRAL ADS STORE       │
│                               │
│ Video / Image / Copy          │
│ Advertiser                    │
│ Landing Page                  │
│ Country                       │
│ First Seen / Last Seen        │
│ Running Days                  │
│ Engagement                    │
│ CTA                           │
│ Product Detected              │
│ Ad Trend                      │
└──────────────┬────────────────┘
               │
               ▼
        AI PRODUCT DETECT
               │
       ┌───────┴────────┐
       ▼                ▼
  Image Search      Text Search
       │                │
       └───────┬────────┘
               ▼
┌──────────────────────────────────────┐
│         CHINA SOURCE MATCHER         │
│                                      │
│ 1688                                 │
│ Taobao                               │
│ Alibaba                              │
│ Pinduoduo                            │
│ AliExpress                           │
└──────────────────┬───────────────────┘
                   │
                   ▼
         MATCH PRODUCT TO AD
                   │
                   ▼
        PRODUCT OPPORTUNITY
                   │
                   ▼
             TEST / SKIP
```

---

# 3. "Shop" trong hệ thống là Shop Ads

Giao diện có thể được thiết kế giống marketplace/shop nhưng thứ được hiển thị không phải sản phẩm nguồn hàng.

Thứ được hiển thị là:

- Winning Ads
- Trending Ads
- New Ads
- Scaling Ads
- Longest Running Ads
- Most Advertisers
- Most Copied Creatives
- Video Ads
- Image Ads
- Saved Ads
- Collections

Ví dụ card ngoài trang chính:

```text
┌─────────────────────────────┐
│        [VIDEO PREVIEW]      │
│                             │
│ Gold-plated Watch           │
│                             │
│ 🔥 Win Score: 91            │
│ 📈 Rising Fast              │
│                             │
│ Active Ads:        84       │
│ Advertisers:       21       │
│ Markets:            7       │
│ Longest Running:   63d      │
│                             │
│ Meta:              54       │
│ TikTok:            24       │
│ Snapchat:           6       │
│                             │
│ [ VIEW DETAILS ]            │
└─────────────────────────────┘
```

---

# 4. Không được thiết kế theo kiểu 1 Ad = 1 Product

Một sản phẩm có thể có hàng chục hoặc hàng trăm Ads.

Quan hệ đúng:

```text
PRODUCT CLUSTER
│
├── Meta Ad #1
├── Meta Ad #2
├── Meta Ad #3
├── TikTok Ad #1
├── TikTok Ad #2
├── Snapchat Ad #1
├── Minea Ad
├── PipiAds Ad
├── Store A
├── Store B
└── Store C
```

Ví dụ:

```text
PRODUCT:
Magic Hair Removal Device

Meta Ads:        81
TikTok Ads:      34
Snapchat Ads:    11
Advertisers:     22
Landing Pages:   14
Creatives:      126
Markets:          8
```

Mục tiêu là giúp Marketing trả lời:

> Bao nhiêu bên đang chạy sản phẩm này?

> Ai đang scale?

> Họ dùng creative gì?

> Họ đang dùng angle gì?

> Ads nào sống lâu nhất?

> Sản phẩm có đang bão hòa không?

---

# 5. Kiến trúc hệ thống đề xuất

```text
                        YOUR SPY ADS APP
                              │
                 ┌────────────┴────────────┐
                 │     CONNECTOR LAYER     │
                 └────────────┬────────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        ▼                     ▼                      ▼
      META                  TIKTOK                SNAPCHAT
        │                     │                      │
        └─────────────────────┼──────────────────────┘
                              ▼
                     NORMALIZED AD
                              │
                              ▼
                    CENTRAL ADS DATABASE
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
                PostgreSQL           R2 / S3
                    │            Image / Video
                    │
                    ▼
              PRODUCT CLUSTERING
                    │
                    ▼
               ADS INTELLIGENCE
                    │
              ┌─────┴─────┐
              ▼           ▼
         AI ANALYSIS   SOURCE MATCH
                          │
             ┌────────────┼─────────────┐
             ▼            ▼             ▼
           1688        Taobao       Alibaba
             │            │             │
             ├──────── PDD ─────────────┤
             │                          │
             └────── AliExpress ────────┘
                          │
                          ▼
                 PRODUCT OPPORTUNITY
```

---

# 6. Connector Layer

Mỗi nguồn Ads cần được đóng thành connector riêng nhưng trả về chung một cấu trúc.

Ví dụ interface:

```text
search_ads()
get_ad_detail()
get_advertiser()
get_creative()
get_landing_page()
get_metrics()
get_raw_payload()
```

Nguồn Ads có thể gồm:

```text
Meta Ad Library (public / API / Apify)
TikTok Ad Library / Commercial Content API / Creative Center
TikTok Trending (organic, tín hiệu viral)
Snapchat Ads Library
Minea
PipiAds
BigSpy
Chrome extension (MKT lưu ads khi duyệt)
Other Spy Tools (export CSV / API)
```

Không làm connector cho Google Ads Transparency, YouTube, Bing / Microsoft Ad Library, LinkedIn, X (xem Phạm vi ở §1).

Mỗi connector phải trả về cùng một schema chuẩn.

Ví dụ:

```json
{
  "ad_id": "",
  "source": "meta",
  "advertiser_id": "",
  "advertiser_name": "",
  "creative_type": "video",
  "creative_url": "",
  "thumbnail_url": "",
  "ad_text": "",
  "headline": "",
  "cta": "",
  "landing_page": "",
  "country": "",
  "language": "",
  "first_seen": "",
  "last_seen": "",
  "running_days": 0,
  "views": null,
  "likes": null,
  "comments": null,
  "shares": null,
  "raw_payload": {}
}
```

---

# 7. Database nên lấy AD làm trung tâm

## 7.1 ADS

```text
ADS
────────────────────────

id
source
source_ad_id

advertiser_id
product_cluster_id

creative_type

video_url
image_url
thumbnail_url

ad_text
headline
cta

landing_page

country
language

first_seen
last_seen
running_days

status

created_at
updated_at
```

---

## 7.2 AD_METRICS

Không ghi đè metrics cũ.

Nên lưu dạng time-series.

```text
AD_METRICS
────────────────────────

id
ad_id

timestamp

views
likes
comments
shares

estimated_reach
estimated_spend

trend_score
velocity_score
```

Từ bảng này mới tính được:

```text
7-day growth
14-day growth
engagement velocity
creative momentum
scaling signal
```

---

## 7.3 ADVERTISERS

```text
ADVERTISERS
────────────────────────

id
source
source_advertiser_id

name
page_url
website

country

first_seen
last_seen

total_ads
active_ads
```

---

## 7.4 CREATIVES

Nên tách creative khỏi Ad.

Bởi cùng một video có thể được nhiều advertiser copy lại.

```text
CREATIVES
────────────────────────

id

type
video_url
image_url

hash
perceptual_hash

transcript
ocr_text

hook
angle
cta

first_seen
last_seen

usage_count
```

Điều này cho phép phát hiện:

```text
Creative X

Used by 17 advertisers
Used in 51 ads
Markets: Saudi / UAE / Kuwait / US

First Seen: ...
Last Seen: ...
```

---

# 8. Product Cluster

Đây là một trong những module quan trọng nhất.

Không nên để mỗi Ad thành một sản phẩm riêng.

Hệ thống cần tự phát hiện:

```text
Ad #1821
Ad #9912
Ad #11928
TikTok #291
Meta #827
```

đều đang bán:

```text
Portable Neck Massager X3
```

Sau đó gom thành:

```text
PRODUCT_CLUSTER
────────────────────────

id

name
canonical_name

category
subcategory

main_image

ai_description

trend_score
win_score
competition_score
saturation_score

market_score_saudi
market_score_uae
market_score_kuwait
market_score_us

first_seen
last_seen
```

---

# 9. Cách Cluster sản phẩm

Có thể kết hợp nhiều tín hiệu:

```text
Image similarity
Video frame similarity
Product title similarity
Landing-page text
OCR
Object detection
Logo detection
SKU clues
Product dimensions
Keyword similarity
Reverse-image search
```

Ví dụ:

```text
Meta Ad A
TikTok Ad B
Meta Ad C
Snapchat Ad D
```

đều có:

```text
image similarity > 0.91
title similarity > 0.82
same physical object
same shape/color/usage
```

→ gom về cùng một cluster.

---

# 10. Source Product Layer

Sau khi xác định được Product Cluster mới tìm nguồn hàng.

Nguồn chính:

```text
1688
Taobao
Alibaba
Pinduoduo
AliExpress
```

Các nguồn này KHÔNG phải Ads Store.

Chúng chỉ trả lời:

```text
Sản phẩm này lấy ở đâu?
Giá bao nhiêu?
MOQ?
Bao nhiêu supplier?
Supplier nào tốt?
Có OEM không?
Có đủ margin không?
```

Hiện trạng: **AliExpress** đã chạy (tìm theo từ khoá cùng lúc với Ads: giá nhập, giá gốc, số đã bán). **1688 / Taobao / Pinduoduo / Alibaba** trả login wall / captcha cho request thường → đi qua Apify khi cần. Giá nhập thấp nhất → `supplier_price_usd` → biên gộp ước tính `1 − giá nhập × 1,6 / giá bán` (1,6 ≈ ship + phí về kho, chưa gồm ads, COD, hoàn).

## 10.1 Tiêu chí "nhập được từ Trung Quốc"

Sản phẩm chỉ vào Radar / Hidden Winners / Products / Dashboard khi là **hàng vật lý generic mua được từ nhà máy / sỉ TQ**. Bị loại (gắn `non_product`):

```text
Không phải hàng vật lý      dịch vụ, app, khoá học, bảo hiểm, vay, bất động sản, khách sạn, vé
Tiêu thụ / bị quản lý chặt   thực phẩm, đồ uống, thực phẩm chức năng, vitamin dạng viên, thuốc, CBD
Hàng brand                  chỉ brand bán (iPhone, Nike, Rolex…) → rủi ro hàng giả
                            (phụ kiện generic như ốp iPhone thì VẪN nhập được)
```

Cách xác định: bước AI (nếu có `ANTHROPIC_API_KEY`) hỏi "có phải hàng vật lý nhập generic từ TQ không", không có AI thì dùng bộ từ khoá `NOT_IMPORTABLE` (EN / VI / AR) trên tên sản phẩm.

Rủi ro nhập hàng còn lại **không loại** mà nên trừ điểm (Logistics Risk ở §13 — chưa có trong công thức hiện tại): pin / chất lỏng / hàng cồng kềnh (ship khó, COD hoàn cao), mỹ phẩm (cần đăng ký SFDA / MoCRA / CPNP theo thị trường), hàng nhái kiểu dáng.

---

# 11. PRODUCT_SOURCE

```text
PRODUCT_SOURCE
────────────────────────

id
product_cluster_id

platform

source_product_id
source_url

title

supplier_id
supplier_name

price_min
price_max

currency

moq

orders
rating
review_count

image_url

is_factory
supports_oem

last_checked
```

---

# 12. Ví dụ Product Detail hoàn chỉnh

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        PRODUCT INTELLIGENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Product:
Portable Neck Massager X3

🔥 Win Score             94
📈 Trend                 Rising Fast
📢 Active Ads            83
🏪 Advertisers           17
🌎 Markets                6
⏱ Longest Ad            62 days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ADS BY PLATFORM

Meta                   49
TikTok                 24
Snapchat                6
Other                   4

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATIVE LIBRARY

Video                  41
Image                  18

Top Hook:
"Say goodbye to neck pain..."

Top Angle:
Pain Point

Top CTA:
Order Now

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMPETITORS

Store A                32 Ads
Store B                17 Ads
Store C                 9 Ads

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SOURCE MATCH

1688
¥18 - ¥29
132 suppliers

Taobao
¥43
4,200+ sold

Pinduoduo
¥25
12,000+ sold

Alibaba
$4.90
MOQ 100

AliExpress
$8.20
8,400+ orders

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AI ANALYSIS

Strong ad longevity
Multiple advertisers scaling
Supplier depth good
High margin potential

Competition increasing
Creative saturation medium

Recommendation:

TEST NOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

# 13. Win Score

Không nên chỉ dựa vào engagement.

Có thể xây:

```text
Win Score =
  Ad Longevity
+ Number of Advertisers
+ Ad Growth
+ Creative Replication
+ Market Expansion
+ Supplier Depth
+ Margin
+ Order Signals
- Competition
- Saturation
- Logistics Risk
```

Ví dụ:

```text
Demand                94
Ad Momentum           92
Ad Longevity          88
Advertiser Growth     89
Supplier Depth        96
Margin Potential      91
Market Fit Saudi      93
Competition           81
Saturation            58

WIN SCORE             92/100
```

---

# 14. Một Product Opportunity không đồng nghĩa với "nhiều đơn"

MKT không chỉ cần sản phẩm đã bán rất nhiều.

Hệ thống cần tìm được nhiều loại cơ hội.

Ví dụ:

```text
EARLY WINNER
LOW COMPETITION
HIGH MARGIN
NEW CREATIVE WAVE
SAUDI FIT
UAE FIT
US FIT
COPYCAT EXPLOSION
LONG-RUNNING EVERGREEN
SEASONAL
HIGH TICKET
COD FRIENDLY
```

Một sản phẩm mới có thể chưa có nhiều đơn nhưng:

```text
Ads mới tăng nhanh
nhiều advertiser bắt đầu copy
supplier còn ít
creative chưa bão hòa
margin cao
```

→ vẫn có thể là sản phẩm đáng test.

---

# 15. Market Score

Cần chấm theo từng thị trường.

Không nên có một Win Score duy nhất cho toàn thế giới.

Ví dụ:

```text
Saudi Arabia       94
UAE                88
Kuwait             81
US                 63
Europe             57
```

Các yếu tố có thể gồm:

```text
Culture fit
COD compatibility
Average selling price
Shipping size
Return risk
Product category acceptance
Existing competitor density
Ad response
Creative language
Religious/cultural sensitivity
Local buying behavior
```

---

# 16. Competitive Analysis

Khi bấm "Chi tiết", hệ thống nên trả lời:

```text
Vì sao đối thủ đang thắng?

Họ đang dùng hook gì?

Offer gì?

Creative format nào?

Video dài bao nhiêu?

CTA gì?

Landing Page dạng nào?

Có COD không?

Có Upsell không?

Có Bundle không?

Có Review không?

Có Scarcity không?

Có Social Proof không?

Mình đang thiếu gì?

Có nên copy angle không?

Có nên làm angle mới không?
```

---

# 17. AI Analysis cho từng Ads

Ví dụ:

```text
WHY THIS AD MAY BE WINNING

1. Hook xuất hiện trong 2 giây đầu.
2. Demonstration trực tiếp.
3. Pain point rõ.
4. Before/After mạnh.
5. Price anchor tốt.
6. CTA rõ.
7. Social proof xuất hiện sớm.

WHAT COMPETITOR DOES WELL

- Fast pacing
- Clear product demo
- Strong benefit statement
- Localized Arabic subtitle
- COD emphasized

WHAT WE ARE MISSING

- Better opening hook
- More local trust signals
- Stronger offer
- More UGC
- Better product demonstration

RECOMMENDATION

Test product.
Do not copy creative 1:1.
Reuse angle.
Create 3 local Saudi variants.
```

---

# 18. Cá nhân hóa theo từng Marketing

Nếu Marketing A hay tìm:

```text
vàng mạ
trang sức
đồng hồ
Islamic gifts
```

Marketing B thường tìm:

```text
mỹ phẩm
skincare
beauty
haircare
```

thì feed không nên giống nhau.

Có thể tạo:

```text
USER_PROFILE

preferred_categories
preferred_markets
preferred_price_range
preferred_margin
preferred_ad_platform
selected_products_history
rejected_products_history
saved_ads
favorite_angles
```

Sau đó recommendation engine:

```text
Global Product Score
        +
User Preference Score
        +
Team Performance History
        =
Personalized Ranking
```

---

# 19. Repo mã nguồn đáng tham khảo

## 19.1 soxoj/AdsLibrary

Vai trò đề xuất:

```text
Connector architecture
Normalized Ads model
Multi-source abstraction
```

Điểm nên học:

- Ads từ nhiều nền tảng.
- Một interface chung.
- Normalized data model.
- Giữ raw payload.
- Dễ thêm connector mới.

Đánh giá sử dụng:

```text
30%
```

---

# 19.2 athm793/meta-ads-scraper

Đây là repo gần nhất với concept "Shop Ads".

Nên tham khảo:

```text
Saved Ads
Collections
Tags
Search
Filter
Ad detail
Hook Lab
Advertiser analysis
Running-days segmentation
CSV/JSON export
Webhook
```

Dùng làm ý tưởng chính cho:

```text
Frontend
Ads Store UX
Saved Research
Collections
Analyst Workflow
```

Đánh giá sử dụng:

```text
40%
```

---

# 19.3 meta-ads-competitor-tracker

Nên học phần:

```text
PostgreSQL
Media archive
S3-compatible storage
R2
Competitor tracking
Long-term ad preservation
```

Điểm quan trọng:

Ads có thể biến mất khỏi nguồn.

Do đó nên tải:

```text
video
image
thumbnail
creative metadata
```

về storage riêng.

Đánh giá sử dụng:

```text
20%
```

---

# 19.4 MetaAdsCollector

Nên dùng để nghiên cứu:

```text
Meta collection
Meta Ad Library parser
GraphQL data extraction
```

Không nên biến nó thành toàn bộ app.

Vai trò:

```text
Meta Collector
```

Đánh giá sử dụng:

```text
10%
```

---

# 20. Phương án ghép repo

Không nên chọn duy nhất một repo.

Nên lấy:

```text
athm793/meta-ads-scraper
        │
        └── UI + UX + Saved Ads + Collections

soxoj/AdsLibrary
        │
        └── Connector Layer + Normalized Models

meta-ads-competitor-tracker
        │
        └── PostgreSQL + Media Storage + Archive

MetaAdsCollector
        │
        └── Meta collector/parser
```

→ ghép thành:

```text
CUSTOM SPY ADS PLATFORM
```

---

# 21. Media Storage

Không nên lưu video Ads chỉ bằng URL gốc.

Vì:

```text
Ad deleted
URL expired
CDN changed
Source blocked
Advertiser disabled
```

→ creative sẽ mất.

Nên:

```text
Crawler
   │
   ├── metadata → PostgreSQL
   │
   └── media
        │
        ├── image
        ├── video
        └── thumbnail
             │
             ▼
          Cloudflare R2
```

R2 phù hợp nếu hệ thống hiện đã dùng Cloudflare.

---

# 22. Deduplication

Hệ thống bắt buộc có dedup.

## Ad dedup

```text
source + source_ad_id
```

## Creative dedup

```text
file hash
perceptual image hash
video fingerprint
```

## Product dedup

```text
image similarity
title similarity
AI embedding
landing page content
```

## Advertiser dedup

```text
domain
page ID
brand name
website
```

---

# 23. Search

Search của app nên hỗ trợ:

```text
keyword
product name
brand
advertiser
domain
landing page
hook
category
country
platform
date
number of ads
running days
creative type
Win Score
Trend Score
```

Ví dụ:

```text
Saudi
Beauty
Video
Running > 20 days
Advertisers > 5
Win Score > 80
```

→ trả về những product cluster tốt nhất.

---

# 24. Tabs đề xuất cho frontend

```text
Dashboard

Discover
├── Trending
├── Winning
├── New
├── Scaling
└── Long Running

Ads

Products

Advertisers

Creatives

Markets

Competitors

Sources

Saved

Collections

Analytics

Settings
```

---

# 25. Dashboard

Có thể hiển thị:

```text
Ads Collected Today

New Products

Fastest Growing Products

Top Markets

Top Categories

Fastest Growing Advertisers

Most Copied Creatives

Longest Running Ads

New Saudi Opportunities

New US Opportunities
```

---

# 26. Phân biệt Ads Store và Product Source

Cực kỳ quan trọng:

```text
ADS STORE
```

là:

```text
Meta
TikTok
Snapchat
Minea
PipiAds
BigSpy
...
```

Trong khi:

```text
PRODUCT SOURCES
```

là:

```text
1688
Taobao
Alibaba
Pinduoduo
AliExpress
```

Quan hệ:

```text
AD
↓
PRODUCT
↓
SOURCE
```

KHÔNG PHẢI:

```text
SOURCE
↓
PRODUCT
↓
APP
```

---

# 27. Ví dụ hoàn chỉnh

Hệ thống phát hiện:

```text
PRODUCT:
Car Vacuum Mini Pro
```

Ads:

```text
Meta Ads             62
TikTok Ads           21
Snapchat Ads          5
Advertisers          16

Longest Running      49d
Ads Added 7d         +18
```

Source:

```text
1688
¥18
91 suppliers

Taobao
¥33

Alibaba
$3.90
MOQ 100

Pinduoduo
¥21

AliExpress
$7.90
```

Market:

```text
Saudi           94
UAE             89
Kuwait          84
US              71
```

AI:

```text
High ad momentum
High supplier availability
High margin
Medium saturation

Recommendation:
TEST NOW
```

---

# 28. Nguồn dữ liệu Ads nên ưu tiên

Core nên dựa vào nguồn mà công ty tự kiểm soát được.

Ưu tiên:

```text
Official / public Ad Libraries
Public transparency centers
Own collectors
Own crawlers
```

Minea, PipiAds hoặc các dịch vụ trả phí nên được coi là:

```text
Optional Data Connector
```

Không nên trở thành xương sống duy nhất của hệ thống.

Lý do:

```text
Subscription dependency
API changes
Anti-bot changes
Account lock
Pricing changes
Rate limits
Terms changes
```

---

# 29. Kiến trúc cuối cùng đề xuất

```text
                  ┌──────────────────────────┐
                  │       DATA SOURCES       │
                  └────────────┬─────────────┘
                               │
          ┌────────────────────┼─────────────────────┐
          ▼                    ▼                     ▼
        Meta                 TikTok              Snapchat
          │                    │                     │
          └────────────────────┼─────────────────────┘
                               ▼
                       CONNECTOR LAYER
                               │
                               ▼
                       NORMALIZATION
                               │
                               ▼
                      CENTRAL ADS STORE
                               │
             ┌─────────────────┼──────────────────┐
             ▼                 ▼                  ▼
         PostgreSQL         R2 Storage        Search Index
             │
             ▼
        DEDUPLICATION
             │
             ▼
      PRODUCT CLUSTERING
             │
             ▼
        AI ENRICHMENT
             │
       ┌─────┴─────────┐
       ▼               ▼
 COMPETITOR        MARKET ANALYSIS
 ANALYSIS
       │               │
       └──────┬────────┘
              ▼
        SOURCE MATCHER
              │
     ┌────────┼────────┐
     ▼        ▼        ▼
   1688    Taobao   Alibaba
     │        │        │
     └── PDD / AliExpress
              │
              ▼
       OPPORTUNITY ENGINE
              │
              ▼
        PERSONALIZED FEED
              │
              ▼
        MARKETING USER
```

---

# 30. Kết luận

Tên phù hợp nhất cho hệ thống:

```text
Ads Intelligence + Product Sourcing Platform
```

hoặc ngắn hơn:

```text
Spy Ads Intelligence Platform
```

Nguyên tắc cốt lõi:

> Ads là trung tâm.

> Product là đối tượng được suy luận từ Ads.

> Competitor là bằng chứng thị trường.

> Creative là tài sản cần phân tích.

> Market là điều kiện để quyết định bán.

> 1688 / Taobao / Alibaba / Pinduoduo / AliExpress là nguồn hàng để kiểm chứng khả năng nhập và biên lợi nhuận.

> Kết quả cuối cùng không phải "tìm thấy sản phẩm", mà là "có nên test sản phẩm này hay không".

Mục tiêu cuối cùng của hệ thống:

```text
FIND ADS
↓
UNDERSTAND WHY THEY WIN
↓
GROUP SAME PRODUCT
↓
MEASURE COMPETITION
↓
FIND SOURCE
↓
CALCULATE MARGIN
↓
EVALUATE MARKET FIT
↓
RECOMMEND TEST / SKIP
```
