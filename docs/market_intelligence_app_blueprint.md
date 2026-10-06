# Market Intelligence OS
## Blueprint xây dựng ứng dụng tổng hợp Spy Ads, Product Intelligence và dữ liệu nội bộ

> Mục tiêu: xây dựng một hệ thống **Market Intelligence OS** có khả năng tổng hợp dữ liệu từ các công cụ spy ads, nguồn public/official API, dữ liệu quảng cáo nội bộ, CRM, sales và vận đơn để đánh giá thị trường, nhận diện sản phẩm có khả năng thắng, phát hiện sản phẩm hiếm nhưng tiềm năng, phân tích test thất bại, tỷ lệ từ chối và sentiment từ comment/review.

---

# 1. Mục tiêu sản phẩm

Ứng dụng không chỉ đóng vai trò như một công cụ "spy ads", mà là một nền tảng hỗ trợ ra quyết định cho:

- Marketing
- Performance Ads
- Product Research
- Sales
- CRM
- Vận đơn
- Customer Service
- Management

Các câu hỏi hệ thống cần trả lời được:

- Thị trường nào đang tăng trưởng?
- Sản phẩm nào đang có dấu hiệu thắng?
- Sản phẩm nào ít người chạy nhưng tín hiệu tốt?
- Sản phẩm nào đang bị bão hòa?
- Sản phẩm nào công ty đã test nhưng thất bại?
- Thất bại do creative, landing page, price, product hay logistics?
- Sản phẩm nào có tỷ lệ khách từ chối cao?
- Sản phẩm nào có nhiều comment tiêu cực?
- Khách hàng đang phàn nàn điều gì?
- Sản phẩm nào nên `TEST`, `WATCH`, `SCALE`, `HOLD` hoặc `STOP`?

---

# 2. Kiến trúc tổng thể

```text
                    MARKET INTELLIGENCE OS
                              │
        ┌─────────────────────┼───────────────────────┐
        │                     │                       │
   EXTERNAL DATA         INTERNAL ADS            BUSINESS DATA
        │                     │                       │
Meta / TikTok            Meta Ads               Orders
Foreplay                 TikTok Ads             CRM
Similarweb               Google Ads             Pancake
Semrush                                          COD / Shipping
Google                                           Call Center
Stores                                           Refund / Return
Comments                                         Inventory
        │                     │                       │
        └─────────────────────┴───────────────────────┘
                              ↓
                     DATA CONNECTOR LAYER
                              ↓
                        RAW DATA LAKE
                              ↓
                NORMALIZATION + DEDUPLICATION
                              ↓
                       ENTITY RESOLUTION
                 "Ad nào thuộc Product nào?"
                              ↓
                        AI ENRICHMENT
             ┌─────────────────────────────────┐
             │ OCR                             │
             │ Video transcription             │
             │ Product classification          │
             │ Market classification           │
             │ Hook / Angle / Offer            │
             │ Comment sentiment               │
             │ Complaint classification        │
             │ Similar-product clustering      │
             └─────────────────────────────────┘
                              ↓
                        FEATURE STORE
                              ↓
                       SCORING ENGINE
             ┌───────────────────────────────────┐
             │ Product Win Score                 │
             │ Market Opportunity Score          │
             │ Rare Winner Score                 │
             │ Saturation Score                  │
             │ Failed Test Score                 │
             │ Customer Rejection Score          │
             │ Comment Sentiment Score           │
             │ Confidence Score                  │
             └───────────────────────────────────┘
                              ↓
                    ANALYTICS + AI AGENT
                              ↓
                 DASHBOARD / ALERT / REPORT
```

---

# 3. Nguyên tắc tích hợp dữ liệu

Không nên xây hệ thống theo hướng scrape trực tiếp tất cả tool trên thị trường.

Nên xây một **Connector Layer**.

```text
Connector
│
├── official_api
├── paid_provider_api
├── first_party_account
├── webhook
├── csv_import
└── manual_url/import
```

## 3.1. Nhóm nguồn dữ liệu

| Nhóm | Ví dụ | Vai trò |
|---|---|---|
| Ad Intelligence | Minea, Foreplay, BigSpy, PiPiAds | Creative, advertiser, product, ad history |
| Official Transparency | Meta Ad Library, TikTok Creative Center | Xác minh quảng cáo |
| Web Intelligence | Similarweb, Semrush | Traffic, keyword, competitor, demand |
| Internal Ads | Meta Ads, TikTok Ads, Google Ads | Spend, CTR, CPA, ROAS |
| Internal Business | CRM, Orders, Pancake, COD, Shipping | Conversion, refusal, delivery, revenue |
| Customer Feedback | Comment, inbox, review, call notes | Sentiment, complaint, intent |

---

# 4. Product làm trung tâm của hệ thống

Không nên lấy `Advertisement` làm entity trung tâm.

Hãy lấy **Product** làm trung tâm.

```text
MARKET
  │
  ├── PRODUCT
  │      │
  │      ├── ADVERTISEMENT
  │      │       ├── CREATIVE
  │      │       ├── ADVERTISER
  │      │       └── LANDING PAGE
  │      │
  │      ├── STORE
  │      ├── COMMENT
  │      ├── EXPERIMENT
  │      ├── ORDER
  │      ├── DELIVERY
  │      ├── RETURN
  │      └── CUSTOMER FEEDBACK
  │
  └── DAILY SNAPSHOT
```

---

# 5. Entity Resolution

Một sản phẩm có thể xuất hiện dưới nhiều tên khác nhau.

Ví dụ:

```text
Portable Neck Massager
Electric Neck Massage Device
Cervical Massage Machine
```

Hệ thống phải xác định chúng có thể cùng thuộc:

```text
Canonical Product ID:
PRD_0001842

Canonical Product:
Electric Neck Massager
```

Nếu không giải quyết tốt bài toán này thì:

- Advertiser count sẽ sai
- Ad count sẽ sai
- Market count sẽ sai
- Score sẽ sai
- Ranking sẽ sai

## 5.1. Các tín hiệu dùng để match product

- Product title similarity
- OCR trên creative
- Landing page title
- Product image embedding
- Video embedding
- Price range
- SKU
- Store category
- Description similarity
- Brand
- Variant
- Product URL pattern

---

# 6. Product Data Model

Một record sản phẩm nên có tối thiểu:

```text
product_id

canonical_name
category
subcategory

market
country
language

price
cost
gross_margin

first_seen_at
last_seen_at

advertiser_count
active_ad_count
total_ad_count

creative_count
creative_growth_7d
creative_growth_30d

market_count
store_count

estimated_traffic
traffic_growth

positive_comment_rate
negative_comment_rate
complaint_rate

internal_spend
orders
revenue
profit

cpa
roas
mer
cvr

delivery_rate
refusal_rate
return_rate

win_score
rarity_score
opportunity_score
saturation_score
confidence_score
```

---

# 7. Phân loại thị trường

Không chỉ dùng Country.

Market hierarchy nên là:

```text
Country
   ↓
Region
   ↓
Language
   ↓
Platform
   ↓
Category
   ↓
Subcategory
   ↓
Price Segment
   ↓
Customer Persona
   ↓
Offer Type
   ↓
Funnel Type
```

Ví dụ:

```text
Saudi Arabia
→ Arabic
→ Meta
→ Beauty
→ Skincare
→ Acne
→ 100-200 SAR
→ Female 18-34
→ COD
→ Discount + Free shipping
```

---

# 8. External Win Score

Dùng để đánh giá sản phẩm trước khi công ty test.

```text
External Win Score
=
15% Ad longevity
+ 15% Creative velocity
+ 10% Advertiser growth
+ 10% Advertiser diversity
+ 10% Geographic expansion
+ 10% Traffic momentum
+ 10% Engagement quality
+ 10% Store momentum
+ 5% Comment sentiment
+ 5% Search trend
- Saturation penalty
```

Ví dụ:

```text
Product A

Advertisers:       19
Active ads:        84
New ads 7d:        +31
Ads running >30d:  22
Countries:         4
Traffic trend:     +28%
Sentiment:         76% positive

External Win Score = 82
```

Kết luận nên hiển thị dưới dạng:

```text
Likely Winner
Confidence: Medium
```

Không nên khẳng định:

```text
Guaranteed Winner
```

---

# 9. Internal Win Score

Dùng khi sản phẩm đã được công ty test.

```text
Internal Win Score
=
20% Contribution Margin
+ 15% ROAS / MER
+ 10% CPA
+ 10% CVR
+ 10% AOV
+ 15% Successful delivery rate
+ 10% Return/refund
+ 5% Customer sentiment
+ 5% Repeat order
```

## 9.1. Final Product Score

```text
Chưa test:
External Score = 100%

Đã có ít dữ liệu:
External = 60%
Internal = 40%

Đủ dữ liệu:
External = 30%
Internal = 70%
```

Mục tiêu là càng có nhiều dữ liệu nội bộ, hệ thống càng ít phụ thuộc vào spy tool bên ngoài.

---

# 10. Rare Winner Detection

Mục tiêu: phát hiện sản phẩm ít người chạy nhưng tín hiệu tăng trưởng tốt.

Ví dụ:

```text
Product A
2,400 advertisers
Win Score = 90

Product B
8 advertisers
Win Score = 78
Growth = +190%
```

Spy tool thông thường có thể ưu tiên Product A.

Hệ thống của công ty phải tìm được Product B.

## 10.1. Rarity Score

```text
Rarity Score
=
Low advertiser density
+ Low ad density
+ Low store density
+ Low keyword competition
```

## 10.2. Rare Winner Score

```text
Rare Winner Score
=
Win Score
× Rarity
× Growth Velocity
× Market Fit
× Margin Potential
```

Dashboard nên có tab:

```text
Hidden Winners

🔥 Fast growing
💎 Rare
📉 Low competition
🌍 New market
💰 Good margin
💬 Positive feedback
```

---

# 11. Saturation Score

Sản phẩm win chưa chắc còn cơ hội.

Có thể tính Saturation dựa trên:

```text
Saturation Score
=
Advertiser density
+ Creative duplication
+ Ad volume
+ Store count
+ Keyword competition
+ Price compression
+ Growth deceleration
```

Các trạng thái:

```text
Low Saturation
Growing
Competitive
Highly Saturated
Declining
```

---

# 12. Product Experiment Tracking

Mỗi sản phẩm công ty test phải tạo một experiment.

```text
Experiment #184

Product
Market
Platform
Creative
Angle
Offer

Spend
Impressions
Clicks
CTR
CPC

Landing views
ATC
Checkout
Purchase

CPA
Revenue
ROAS

Confirmed Orders
Delivered
Refused
Returned
```

---

# 13. Phân tích lý do test thất bại

Không chỉ trả về:

```text
FAILED
```

Phải phân tích funnel.

```text
CTR thấp
→ Creative problem

CTR tốt
↓
ATC thấp
→ Product / landing / price problem

ATC tốt
↓
Checkout thấp
→ Offer / trust problem

Checkout tốt
↓
Purchase thấp
→ Payment / checkout issue

Purchase tốt
↓
Confirmation thấp
→ Lead quality / telesales issue

Confirmation tốt
↓
Delivery thấp
→ COD / logistics problem
```

Các loại failure nên lưu:

- Creative Failure
- Product-Market Mismatch
- Price Problem
- Landing Page Problem
- Offer Problem
- Checkout Problem
- Low Lead Quality
- Telesales Failure
- Logistics Failure
- High Refusal
- High Return
- Compliance Failure

---

# 14. Product Lifecycle

Dùng state machine:

```text
DISCOVERED
    ↓
WATCHLIST
    ↓
CANDIDATE
    ↓
TESTING
  ↙   ↓   ↘
FAIL HOLD WIN
          ↓
        SCALE
          ↓
      SATURATED
          ↓
        RETIRE
```

Điều này giúp theo dõi toàn bộ vòng đời sản phẩm.

---

# 15. Ad Rejection Intelligence

Tách riêng hai loại rejection:

## 15.1. Ad rejection

Theo dõi:

```text
Ad rejected
Policy reason
Platform
Creative
Product
Country
```

Ví dụ:

```text
Product X
43% creative rejected

Reason:
Medical claims
Before/after
Misleading promise
Restricted content
```

Tạo metric:

```text
ad_rejection_rate
```

---

# 16. COD / Customer Refusal Intelligence

Khác với ad rejection.

```text
COD Refusal Rate
=
Refused deliveries
─────────────────
Delivery attempts
```

Ví dụ:

```text
Orders            4,281
Confirmed         3,827
Shipped           3,542
Delivered         2,814
Refused             511
Other failed        217

Refusal rate       14.4%
```

## 16.1. Lý do khách từ chối

AI nên phân loại:

```text
Changed mind
Price too high
Fake order
Cannot contact
Product expectation mismatch
Delivery too slow
Duplicate order
Bought elsewhere
Quality concern
Trust issue
```

Từ đó tính:

```text
Customer Rejection Score
```

---

# 17. Comment Intelligence

Không chỉ phân loại `Good / Bad`.

Dùng **Aspect-Based Sentiment Analysis**.

Ví dụ comment:

> "Sản phẩm dùng ổn nhưng giao quá chậm và giá hơi cao."

AI trả:

```json
{
  "overall": "neutral",
  "quality": "positive",
  "delivery": "negative",
  "price": "negative",
  "purchase_intent": "medium"
}
```

## 17.1. Taxonomy comment

| Aspect | Mục tiêu phân tích |
|---|---|
| Product quality | tốt / kém |
| Effectiveness | có hiệu quả không |
| Price | đắt / rẻ |
| Shipping | nhanh / chậm |
| Packaging | vỡ / hỏng |
| Authenticity | fake / chính hãng |
| Trust | scam / uy tín |
| Sizing | rộng / chật |
| Usability | khó / dễ dùng |
| Side effects | kích ứng / tác dụng phụ |
| Support | CSKH |
| Refund | hoàn tiền |
| Purchase intent | muốn mua |
| Question | hỏi thông tin |

## 17.2. Dashboard comment

```text
12,419 comments analysed

Positive              66%
Neutral               18%
Negative               16%

TOP POSITIVE
Effectiveness         41%
Quality               29%
Easy to use           18%

TOP COMPLAINTS
Delivery              31%
Price                 24%
Quality               19%
Expectation mismatch  14%
```

---

# 18. Daily Snapshot Engine

Mỗi ngày tạo snapshot:

```text
product_daily_snapshot
market_daily_snapshot
advertiser_daily_snapshot
creative_daily_snapshot
```

## 18.1. Ví dụ product_daily_snapshot

```text
product_id
date

active_ads
new_ads
removed_ads

advertisers
new_advertisers

markets
new_markets

comments
positive_comments
negative_comments

traffic

external_win_score
internal_win_score

rarity_score
saturation_score
opportunity_score
```

## 18.2. Các metric theo thời gian

```text
7d Growth
14d Growth
30d Growth

Acceleration
Deceleration
```

---

# 19. Confidence Score

Bắt buộc có để tránh false positive.

Ví dụ:

```text
Product A
Win Score = 94

Data:
2 ads
80 comments
3 days

Confidence = 22%
```

So với:

```text
Product B
Win Score = 81

Data:
136 ads
23 advertisers
90 days
12,300 comments
4 markets

Confidence = 94%
```

Có thể tính:

```text
Displayed Opportunity
=
Raw Opportunity × Confidence Adjustment
```

---

# 20. AI nên làm gì?

AI nên chuyển dữ liệu phi cấu trúc thành dữ liệu có cấu trúc.

```text
video
→ transcription

image
→ objects / OCR

landing page
→ price / offer

comment
→ sentiment / complaints

creative
→ hook / angle
```

AI không nên tự quyết định Win Score.

Sai:

```text
GPT:
"Tôi nghĩ sản phẩm này win 89/100."
```

Đúng:

```text
Database metrics
      ↓
Scoring formula
      ↓
Score = 89
      ↓
AI:
"Giải thích vì sao score = 89"
```

---

# 21. Dashboard Structure

Ứng dụng nên có ít nhất 8 màn hình.

| Screen | Chức năng |
|---|---|
| Daily Pulse | Hôm nay thị trường thay đổi gì |
| Market Radar | Quốc gia/category nào đang tăng |
| Product Explorer | Search/filter product |
| Hidden Winners | Sản phẩm hiếm nhưng có tín hiệu tốt |
| Product 360 | Toàn bộ intelligence một sản phẩm |
| Test Lab | Sản phẩm công ty đã test |
| Comment Intelligence | Comment/review analysis |
| Competitor Radar | Theo dõi advertiser/store |

---

# 22. Home Dashboard

```text
TODAY — SAUDI ARABIA

Products monitored             18,241
New products                      312 ↑

New active ads                  8,419
New advertisers                  614

Potential winners                 43
Hidden winners                    11
Saturated products                28

────────────────────────────────

INTERNAL TESTS

Testing                           17

Winning                            5
Promising                          4
Failed                             6
Waiting data                       2

COD refusal                   12.8% ↑

────────────────────────────────

⚠ ALERT

Product #1823
Negative sentiment increased
12% → 31%

Main complaint:
"Product doesn't match advertisement"

────────────────────────────────

💎 HIDDEN WINNER

Product XYZ

Market: UAE
Advertisers: 7
Growth 7d: +144%
Win Score: 81
Rarity Score: 93
Opportunity: 89
```

---

# 23. Product 360

Một trang Product 360 nên gồm:

```text
Product Overview

Market Performance
Ad Performance
Creative Library
Advertisers
Stores
Traffic
Search Demand
Comment Intelligence
Pricing
Competitor Analysis
Internal Test History
Orders
Delivery
Refusal
Return
Profitability

Win Score
Rare Winner Score
Saturation Score
Opportunity Score
Confidence Score
```

---

# 24. Tech Stack đề xuất

## Frontend

```text
Next.js
React
Tailwind
```

## Backend

```text
FastAPI
hoặc
NestJS
```

## Database

```text
PostgreSQL
```

## Analytics

```text
ClickHouse
```

## Cache / Queue

```text
Redis
```

## Object Storage

```text
S3
hoặc
Cloudflare R2
```

## Search

```text
OpenSearch
```

## Vector Search

```text
pgvector
```

## Workflow

```text
Temporal
Airflow

MVP:
n8n
```

## AI Workers

```text
Python
OCR
Vision Model
Speech-to-Text
LLM
Embedding Model
```

---

# 25. Data Flow Backend

```text
API
 ↓
Queue
 ↓
Workers
 ↓
Raw Storage
 ↓
ETL
 ↓
Postgres
 ↓
ClickHouse
 ↓
Feature computation
 ↓
Scores
 ↓
Dashboard
```

---

# 26. Cấu trúc source code

```text
/apps
    web
    api

/services

    connector-service
    ad-service
    product-service
    market-service

    creative-service
    comment-service

    order-service
    logistics-service

    experiment-service

    ai-enrichment-service
    entity-resolution-service

    scoring-service
    alert-service

/workers

    ingestion-worker
    video-worker
    image-worker
    comment-worker

    daily-snapshot-worker
    scoring-worker

/data

    raw
    normalized
    features
```

---

# 27. Product Pipeline

```text
01
Detect new ad/product

        ↓

02
Extract
image/video/text/link

        ↓

03
AI Product Identification

        ↓

04
Product matching

Existing?
YES → attach
NO → create product

        ↓

05
Landing page extraction

Price
Offer
Currency
Product description

        ↓

06
Market classifier

Country
Language
Category
Price segment
Funnel

        ↓

07
Search additional sources

Meta
TikTok
Stores
Traffic
Search

        ↓

08
Competitor aggregation

Ads
Stores
Advertisers

        ↓

09
Comment analysis

        ↓

10
Feature generation

Growth
Longevity
Sentiment
Competition

        ↓

11
Score

Win
Rarity
Growth
Saturation
Confidence

        ↓

12
Opportunity Score

        ↓

13
Trigger

Hidden Winner
Potential Winner
Saturated
Risk

        ↓

14
Marketing receives alert

        ↓

15
Company launches test

        ↓

16
Ads data flows back

        ↓

17
Orders

        ↓

18
COD / refunds / comments

        ↓

19
Internal Win Score

        ↓

20
Final Decision

SCALE
ITERATE
HOLD
STOP
```

---

# 28. Decision Engine

Hệ thống nên trả về hành động chứ không chỉ điểm số.

Ví dụ:

```text
Opportunity > 80
Confidence > 70
Saturation < 50
→ TEST

Internal Win > 80
Margin tốt
Refusal thấp
→ SCALE

ROAS tốt
Refusal cao
→ HOLD / FIX OPERATIONS

CTR thấp
→ ITERATE CREATIVE

CPA cao
CVR thấp
→ FIX PRODUCT / LANDING / OFFER

External Score thấp
Internal Score thấp
→ STOP
```

---

# 29. Alert Engine

Các alert quan trọng:

```text
New Hidden Winner detected
Competitor scaling rapidly
New market detected
Creative velocity spike
Price change
Store traffic spike
Negative comments increasing
Product saturation increasing
Ad rejection increasing
COD refusal increasing
ROAS dropping
Delivery rate dropping
```

---

# 30. Learning Loop

Lợi thế cạnh tranh chính của hệ thống:

```text
Market Data
     +
Company Data
     ↓
Learning Loop
```

Chi tiết:

```text
Product discovered
       ↓
Product tested
       ↓
Ads performance
       ↓
Sales performance
       ↓
Delivery performance
       ↓
Comment / Complaint
       ↓
Profit
       ↓
Model learns
       ↓
Better next product selection
```

Sau nhiều vòng test, hệ thống có thể học được:

```text
Market: Saudi Arabia
Category: Beauty
Price: 100–170 SAR
Creative: UGC
Presenter: Female
Structure: Problem → Demo → Proof
Funnel: COD

Historical company performance:
CVR: 4.8%
Delivery: 84%
Margin: 37%
```

Từ đó xác định profile sản phẩm phù hợp với công ty.

---

# 31. Roadmap triển khai

## Phase 01 — Data Core

Xây:

```text
Product
Market
Advertiser
Ad
Creative
Store
```

---

## Phase 02 — Connector Layer

Tích hợp:

```text
Ad intelligence providers
TikTok
Meta internal account
Google Ads
Similarweb / Semrush
CRM
Orders
Pancake
Shipping
```

---

## Phase 03 — Product Resolution

Xây:

```text
Duplicate detection
Product clustering
Canonical Product
Image matching
Title matching
Landing page matching
```

---

## Phase 04 — AI Intelligence

Xây:

```text
Category classification
Hook classification
Angle classification
Offer extraction
OCR
Video transcription
Comment sentiment
Complaint classification
```

---

## Phase 05 — Daily Engine

Xây:

```text
Product Daily Snapshot
Market Daily Snapshot
Advertiser Daily Snapshot
Creative Daily Snapshot
```

---

## Phase 06 — Scoring Engine

Xây:

```text
External Win Score
Rarity Score
Saturation Score
Opportunity Score
Confidence Score
```

---

## Phase 07 — Test Lab

Xây:

```text
Experiment
Ads metrics
Landing metrics
Sales metrics
Decision
```

---

## Phase 08 — Sales + Logistics

Xây:

```text
Confirmed rate
Delivery rate
COD refusal
Return
Refund
Call center reason
```

---

## Phase 09 — Recommendation Engine

Trả về:

```text
TEST
WATCH
HOLD
ITERATE
SCALE
STOP
```

---

## Phase 10 — AI Agent

Cho phép hỏi bằng ngôn ngữ tự nhiên.

Ví dụ:

```text
"Tìm 20 sản phẩm beauty tại Saudi đang tăng nhanh nhưng dưới 10 advertiser."

"Sản phẩm nào trong 30 ngày qua có Win Score cao nhưng COD refusal >20%?"

"Những sản phẩm chúng ta đã test thất bại do creative?"

"Cho tôi top complaint của sản phẩm X."

"Market nào tuần này có nhiều hidden winner nhất?"

"Đối thủ nào đang scale nhanh?"
```

---

# 32. MVP nên làm gì trước?

Không bắt đầu bằng chatbot AI.

Thứ tự đúng:

```text
1. Data Model
2. Connector
3. Product Resolution
4. Daily Snapshot
5. External Win Score
6. Rare Winner
7. Dashboard
8. Internal Test Data
9. Logistics
10. AI Agent
```

---

# 33. Moat của hệ thống

Các spy tool hiện tại đã làm tốt:

```text
Find Ads
Find Competitors
Find Products
```

Điểm mạnh riêng của hệ thống công ty sẽ là:

```text
External Market Intelligence
+
First-party Ads Data
+
Sales
+
CRM
+
COD
+
Delivery
+
Return
+
Customer Feedback
+
Historical Experiments
```

Từ đó hệ thống không chỉ biết:

> "Sản phẩm này đang hot."

Mà có thể biết:

> "Sản phẩm này phù hợp với công ty chúng ta, ở market này, price range này, với loại creative này, và xác suất scale có lợi nhuận cao."

---

# 34. Kiến trúc cuối cùng

```text
                  YOUR COMPANY INTELLIGENCE

                          ┌───────────┐
                          │ MARKET    │
                          └─────┬─────┘
                                │
                         Which market?
                                ↓
                          ┌───────────┐
                          │ PRODUCT   │
                          └─────┬─────┘
                                │
                       Which product?
                                ↓
                     ┌─────────────────┐
                     │ OPPORTUNITY AI  │
                     └────────┬────────┘
                              │
              ┌───────────────┼───────────────┐
              ↓               ↓               ↓
           TEST             WATCH            SKIP
              ↓
         ADS + SALES
              ↓
           ORDERS
              ↓
          DELIVERY
              ↓
        CUSTOMER DATA
              ↓
       ACTUAL PROFITABILITY
              ↓
        ┌──────────────┐
        │ SCALE / STOP │
        └──────┬───────┘
               │
               ↓
         LEARN & IMPROVE
               │
               └──────────────→ Product selection
```

---

# 35. Kết luận

Ứng dụng nên được định nghĩa là:

> **Market Intelligence + Product Intelligence + Internal Business Intelligence Platform**

Thay vì chỉ là:

> **Spy Ads Tool**

Mục tiêu cuối cùng là xây một hệ thống có khả năng:

```text
DISCOVER
→ ANALYZE
→ SCORE
→ TEST
→ MEASURE
→ LEARN
→ SCALE
```

Vòng lặp này càng chạy lâu thì dữ liệu nội bộ càng lớn và hệ thống càng có khả năng đưa ra quyết định phù hợp riêng với doanh nghiệp.

Đây là phần tạo lợi thế cạnh tranh dài hạn mà các spy tool bên ngoài không thể có được.
