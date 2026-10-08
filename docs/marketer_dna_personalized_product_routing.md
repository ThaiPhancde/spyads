# MARKETER DNA + PERSONALIZED PRODUCT ROUTING ENGINE

> Mục tiêu: Mỗi marketer có một thế mạnh khác nhau, vì vậy hệ thống không nên dùng cùng một bộ lọc và cùng một thứ tự ưu tiên sản phẩm/ads cho toàn bộ team.
>
> Thay vào đó, app cần xây dựng một **Marketer Profile / Marketer DNA** cho từng người, sau đó tự động:
>
> - lọc sản phẩm phù hợp hơn với từng marketer;
> - xếp hạng ads khác nhau cho từng người;
> - đề xuất sản phẩm nào nên giao cho marketer nào;
> - học từ lịch sử winner/loser;
> - học từ market, category, creative angle và hành vi của từng marketer;
> - vẫn giữ một phần sản phẩm mới để tránh “đóng khung” marketer mãi trong một ngành.

---

# 1. Bài toán

Ví dụ trong cùng một team:

```text
MKT A
→ mạnh ở:
- vàng mạ
- jewelry
- luxury-look
- gift products
- Saudi / Kuwait

MKT B
→ mạnh ở:
- mỹ phẩm
- skincare
- beauty
- haircare

MKT C
→ mạnh ở:
- phong thủy
- spiritual
- novelty products

MKT D
→ mạnh ở:
- problem-solving products
- household
- gadgets
```

Nếu app chỉ có một danh sách `Top Product / Top Ads / Winner Products` cho tất cả mọi người thì sẽ không tối ưu.

Một sản phẩm tốt với MKT A chưa chắc tốt với MKT B.

---

# 2. Hướng triển khai

Nên xây 3 module:

## MARKETER DNA

Lưu và học năng lực thực tế của từng marketer.

## PERSONALIZED RECOMMENDATION ENGINE

Xếp hạng sản phẩm/ads riêng theo từng người.

## PRODUCT ROUTING ENGINE

Đề xuất sản phẩm nào phù hợp nhất với marketer nào.

Luồng:

```text
ADS SOURCES
      ↓
Ads Intelligence
      ↓
Product Detection
      ↓
Competitive Intelligence
      ↓
Global Product Score
      ↓
Marketer DNA
      ↓
Personal Fit Score
      ↓
Personalized Ranking
      ↓
Personalized Feed
      ↓
Product Assignment
```

---

# 3. Tách Global Score và Personal Score

Không nên dùng một score duy nhất.

```text
GLOBAL SCORE
→ Sản phẩm này có tốt về mặt khách quan không?

PERSONAL SCORE
→ Sản phẩm này có phù hợp với năng lực của MKT này không?
```

Ví dụ:

```text
Gold Bracelet
Market: Saudi Arabia

Global Opportunity:
82 / 100
```

Nhưng:

```text
MKT A
Personal Fit:
94 / 100
→ STRONG TEST
```

Trong khi:

```text
MKT B
Personal Fit:
51 / 100
→ LOW PRIORITY
```

---

# 4. Marketer Profile

Ví dụ:

```json
{
  "marketer_id": "mkt_A",
  "strengths": [
    "jewelry",
    "gold_plated",
    "luxury_look",
    "gift_products"
  ],
  "strong_markets": [
    "SA",
    "KW",
    "AE"
  ],
  "strong_angles": [
    "luxury",
    "status",
    "gift",
    "price_shock"
  ],
  "preferred_price_range": {
    "min": 100,
    "max": 400
  },
  "preferred_formats": [
    "ugc",
    "product_demo"
  ],
  "weak_categories": [
    "electronics",
    "complex_health_products"
  ]
}
```

---

# 5. Marketer DNA phải học từ 3 tầng dữ liệu

## 5.1 Explicit Preference

Marketer tự khai:

```text
Category sở trường
Market sở trường
Creative sở trường
Angle sở trường
Khoảng giá thường chạy
Loại sản phẩm thích
```

## 5.2 Historical Performance

Đây là nguồn quan trọng nhất.

Hệ thống học từ:

```text
Products Tested
Winners
Losers
CPA
ROAS
Orders
Delivery Rate
Return Rate
Revenue
Profit
Scale Level
```

Ví dụ:

```text
MKT A

Jewelry:
20 products tested
8 winners

Beauty:
15 products tested
2 winners

Electronics:
8 products tested
0 winners
```

Từ đó:

```text
Jewelry Fit: 92
Beauty Fit: 48
Electronics Fit: 21
```

## 5.3 Behavioral Signals

Hệ thống học từ:

```text
Save Product
Open Detail
Watch Creative
Add Watchlist
Create Brief
Assign Product
Test Product
Mark Winner
Mark Loser
```

Nhưng hành vi chỉ nên là tín hiệu phụ.

Ưu tiên dữ liệu:

```text
1. Actual Business Results
2. Historical Test Performance
3. Marketer Explicit Preference
4. App Behavior
```

---

# 6. Marketer DNA

Ví dụ:

```text
MKT A DNA

CATEGORY
Jewelry           96
Accessories       82
Beauty            43
Electronics       24

MARKET
Saudi             91
Kuwait            85
UAE               73
USA               31

ANGLE
Luxury            95
Gift              88
Price Shock       83
Problem/Solution  44

CREATIVE
Product Demo      90
UGC               78
Static Image      35
```

DNA phải thay đổi theo thời gian, không được hard-code.

Ví dụ:

```text
Tháng 7
Beauty Fit: 38

Tháng 8
A có 2 beauty winner
Beauty Fit: 55

Tháng 9
A tiếp tục có 3 beauty winner
Beauty Fit: 73
```

---

# 7. Personal Recommendation Score

Có thể dùng công thức:

```text
Personal Recommendation Score =

30% Global Opportunity
+
20% Category Affinity
+
15% Historical Win Rate
+
10% Market Strength
+
10% Creative Angle Fit
+
5% Price Range Fit
+
5% Operational Fit
+
5% Current Trend
```

Ví dụ:

```text
Gold Bracelet
Saudi Arabia

Global Opportunity          82
Category Fit                98
Historical Win Rate         91
Market Fit                  94
Creative Angle Fit          89
Price Fit                   87

PERSONAL SCORE:
92 / 100
```

---

# 8. Personalized Feed

MKT A:

```text
FOR YOU

🔥 Gold-plated Bracelet       94
🔥 Luxury Watch               91
⭐ Women's Jewelry Set        89
⭐ Ramadan Gift Jewelry       87
```

MKT B:

```text
FOR YOU

🔥 Hair Serum                 95
🔥 Whitening Cream            92
⭐ Lip Product                90
⭐ Skin Treatment Device      87
```

---

# 9. Ads Detail phải cá nhân hóa

Khi bấm `Chi tiết`, ngoài Competitive Analysis phải có:

```text
WHY THIS FITS YOU
```

Ví dụ MKT A:

```text
Bạn có performance tốt với:
- Jewelry
- Saudi
- Luxury angle
- COD products

Historical Similarity:
82%

Bạn từng có:
4 winner thuộc nhóm tương tự.

Recommendation:
🔥 STRONG TEST
```

MKT B:

```text
Global Opportunity:
High

Nhưng:
- low performance với Jewelry
- mạnh hơn với Beauty
- chưa có winner ở Luxury Product

Recommendation:
WATCH
hoặc
PASS TO MKT A
```

---

# 10. Product Routing Engine

Hệ thống phải trả lời:

```text
Product này phù hợp nhất với ai?
```

Ví dụ:

```text
Gold Bracelet

1. MKT A       94%
2. MKT D       81%
3. MKT C       70%
4. MKT B       55%
```

UI:

```text
[Assign to MKT A]
```

---

# 11. Auto Assignment + Workload

Không nên lúc nào cũng giao cho người có fit cao nhất nếu người đó đang quá tải.

Cần xét:

```text
Current Workload
Products Testing
Products Pending
Active Campaigns
Recent Assignments
```

Routing Score có thể là:

```text
70% Personal Fit
+
15% Availability
+
10% Current Workload
+
5% Strategic Priority
```

---

# 12. Tránh Filter Bubble

Nếu MKT A mạnh vàng mạ mà app chỉ đưa vàng mạ mãi thì A sẽ không thấy ngành mới.

Nên dùng:

```text
70% Core Fit
20% Adjacent Opportunity
10% Exploration
```

## Core Fit – 70%

```text
Jewelry
Gold-plated
Luxury
Gift
Saudi
Kuwait
```

## Adjacent Opportunity – 20%

```text
Watch
Accessories
Fashion
Luxury gifts
Women's accessories
```

## Exploration – 10%

Sản phẩm ngoài DNA nhưng global score cực cao.

---

# 13. Discovery Score

Thêm:

```text
Discovery Score
```

Nó trả lời:

> Sản phẩm ngoài sở trường nhưng có đáng để marketer thử nhằm mở rộng năng lực không?

Ví dụ:

```text
MKT A
Beauty Product

Personal Fit:
52

Global Opportunity:
96

Similarity to Previous Winners:
72

Wave Potential:
95

Discovery Score:
86

→ RECOMMEND EXPLORATION
```

---

# 14. 4 Score chính

Mỗi Product / Ads nên có:

```text
GLOBAL OPPORTUNITY
→ Sản phẩm này có đáng đánh không?

PERSONAL FIT
→ Có phù hợp với marketer này không?

WAVE POTENTIAL
→ Có khả năng tạo sóng không?

DISCOVERY SCORE
→ Dù ngoài sở trường, có đáng thử không?
```

---

# 15. Winner DNA

Ngoài Marketer DNA, nên có:

```text
WINNER DNA
```

Ví dụ winner của MKT A thường có:

```text
Market:
Saudi

Category:
Jewelry

Price:
149–299 SAR

Offer:
COD + Discount

Creative:
UGC + Product Demo

Angle:
Luxury + Gift

Video:
15–25 seconds
```

Khi có product mới:

```text
Product Match Winner DNA:
88%
```

Personal Fit nên dùng cả:

```text
Marketer DNA Match
+
Winner DNA Match
+
Global Opportunity
```

---

# 16. Feedback Loop

```text
Candidate
      ↓
Assigned
      ↓
Tested
      ↓
Orders
      ↓
Delivered
      ↓
Returned
      ↓
CPA / ROAS
      ↓
Winner / Loser
      ↓
Update Marketer DNA
```

Không nên học chỉ từ Revenue.

Cần xét:

```text
Orders
Delivered Orders
Delivery Rate
Return Rate
CPA
ROAS
Profit
Net Margin
Scale Duration
```

---

# 17. Business Performance Score

Ví dụ:

```text
30% Profit
20% Delivery Rate
15% ROAS
15% CPA
10% Scale Duration
10% Return Rate
```

Score này dùng để update DNA.

---

# 18. Marketer Score theo Category

| Category | Tests | Winners | Win Rate | Business Score |
|---|---:|---:|---:|---:|
| Jewelry | 20 | 8 | 40% | 92 |
| Beauty | 15 | 2 | 13% | 48 |
| Electronics | 8 | 0 | 0% | 21 |

---

# 19. Market Strength

| Market | Tests | Winners | Score |
|---|---:|---:|---:|
| Saudi | 24 | 9 | 91 |
| Kuwait | 14 | 5 | 85 |
| UAE | 10 | 3 | 73 |
| USA | 8 | 1 | 31 |

---

# 20. Team Capability Map

```text
              Jewelry   Beauty   Gadget   Spiritual

MKT A            96       43       31        48
MKT B            38       94       45        27
MKT C            71       35       40        91
MKT D            51       64       88        42
```

Manager sẽ thấy:

```text
team mạnh ở đâu
team yếu ở đâu
category nào thiếu người giỏi
```

---

# 21. Company DNA

Ngoài Marketer DNA có thể có:

```text
Company DNA
```

Ví dụ:

```text
Company mạnh:
Saudi
COD
Beauty
Jewelry
Price Shock
UGC
```

Recommendation nên đi qua 3 tầng:

```text
PRODUCT QUALITY
        ↓
COMPANY FIT
        ↓
MARKETER FIT
```

---

# 22. Final Recommendation Engine

Ví dụ logic:

```text
IF global_score < 60
    SKIP

ELSE IF company_fit < 50
    RESEARCH_MORE

ELSE IF personal_fit > 85
    STRONG_TEST

ELSE IF personal_fit > 70
    TEST

ELSE IF discovery_score > 80
    EXPLORATION

ELSE
    WATCH
```

---

# 23. Confidence Score

DNA phải có confidence.

Ví dụ:

```text
Jewelry Score:
95

Confidence:
92%
```

Nếu mới test 2 sản phẩm:

```text
Jewelry Score:
95

Confidence:
23%
```

Không nên để `1 winner / 1 test` thành 100 điểm chắc chắn.

Nên dùng:

```text
Bayesian smoothing
minimum sample size
time decay
```

---

# 24. Time Decay

Performance cũ giảm trọng số:

```text
Last 30 days:
High weight

30–90 days:
Medium weight

90+ days:
Lower weight
```

Vì:

```text
market thay đổi
creative trend thay đổi
marketer skill thay đổi
```

---

# 25. Multi-Dimensional DNA

Các dimension nên có:

```text
Category
Subcategory
Market
Price
Creative Format
Angle
Offer
Audience
Platform
Product Lifecycle
Seasonality
```

Ví dụ:

```text
MKT A mạnh:

Category:
Gold-plated Jewelry

Market:
Saudi Arabia

Price:
149–299 SAR

Offer:
COD + Discount

Creative:
UGC Product Demo

Angle:
Luxury / Gift

Audience:
Female
```

---

# 26. Personalized Competitive Analysis

Kết hợp với module Competitive Intelligence.

Trang Ads Detail:

```text
GLOBAL ANALYSIS
+
COMPETITOR ANALYSIS
+
PERSONALIZED ANALYSIS
```

Ví dụ:

```text
PRODUCT:
Gold Bracelet

GLOBAL
Opportunity: 84
Wave: 88
Saturation: 63
Risk: 42

COMPETITOR
Strong:
- UGC
- Arabic
- Luxury angle
- COD

PERSONAL
Marketer:
MKT A

Personal Fit:
95

Why:
- Jewelry strength: 96
- Saudi strength: 91
- Luxury angle: 95
- Winner similarity: 82%

DECISION:
🔥 STRONG TEST
```

---

# 27. Suggested Architecture

```text
                    ADS SOURCES
                         │
                         ▼
                 DATA COLLECTION
                         │
                         ▼
                 PRODUCT DETECTION
                         │
                         ▼
             COMPETITIVE INTELLIGENCE
                         │
                         ▼
               GLOBAL SCORING ENGINE
                         │
                         ▼
                PERSONALIZATION ENGINE
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
       MARKETER DNA            WINNER DNA
             │                       │
             └───────────┬───────────┘
                         ▼
                PERSONAL FIT SCORE
                         │
                         ▼
                PRODUCT ROUTING
                         │
                         ▼
                 PERSONAL FEED
                         │
                         ▼
                    TESTING
                         │
                         ▼
               BUSINESS RESULTS
                         │
                         ▼
                 FEEDBACK LOOP
```

---

# 28. Database đề xuất

Các bảng:

```text
marketers
marketer_profiles
marketer_category_scores
marketer_market_scores
marketer_angle_scores
marketer_creative_scores
marketer_price_scores

products
ads
product_scores

product_assignments

product_tests
test_results

marketer_winner_patterns
```

Ví dụ:

```sql
CREATE TABLE marketer_profiles (
    marketer_id TEXT PRIMARY KEY,
    preferred_categories TEXT,
    preferred_markets TEXT,
    preferred_angles TEXT,
    preferred_formats TEXT,
    preferred_price_min REAL,
    preferred_price_max REAL,
    dna_version INTEGER,
    updated_at DATETIME
);
```

```sql
CREATE TABLE marketer_category_scores (
    marketer_id TEXT,
    category TEXT,
    test_count INTEGER,
    winner_count INTEGER,
    win_rate REAL,
    performance_score REAL,
    affinity_score REAL,
    updated_at DATETIME,
    PRIMARY KEY (
        marketer_id,
        category
    )
);
```

```sql
CREATE TABLE product_assignments (
    id TEXT PRIMARY KEY,
    product_id TEXT,
    marketer_id TEXT,
    personal_fit REAL,
    routing_score REAL,
    assigned_by TEXT,
    status TEXT,
    created_at DATETIME
);
```

---

# 29. Product Recommendation Object

```json
{
  "product_id": "prd_88",
  "global": {
    "opportunity": 84,
    "wave": 88,
    "risk": 42
  },
  "marketer": {
    "id": "mkt_A",
    "category_fit": 98,
    "market_fit": 94,
    "angle_fit": 89,
    "winner_similarity": 82,
    "personal_fit": 95,
    "discovery_score": 64
  },
  "recommendation": {
    "decision": "STRONG_TEST",
    "priority": "HIGH"
  }
}
```

---

# 30. API đề xuất

```text
GET /api/marketers/:id/profile
GET /api/marketers/:id/dna
GET /api/marketers/:id/recommendations

GET /api/products/:id/marketer-fit
GET /api/products/:id/best-marketer

POST /api/products/:id/assign
POST /api/products/:id/result
```

---

# 31. Backend Flow

```text
function recommendProduct(marketer, product):

    globalScore =
        getGlobalProductScore(product)

    dna =
        getMarketerDNA(marketer)

    winnerDNA =
        getWinnerDNA(marketer)

    categoryFit =
        calculateCategoryFit(product, dna)

    marketFit =
        calculateMarketFit(product, dna)

    angleFit =
        calculateAngleFit(product, dna)

    winnerSimilarity =
        compareWithPreviousWinners(
            product,
            winnerDNA
        )

    personalFit =
        calculatePersonalFit(
            globalScore,
            categoryFit,
            marketFit,
            angleFit,
            winnerSimilarity
        )

    discovery =
        calculateDiscoveryScore(
            product,
            marketer
        )

    return {
        globalScore,
        personalFit,
        discovery
    }
```

---

# 32. Routing Flow

```text
function routeProduct(product):

    marketers =
        getAvailableMarketers()

    for marketer in marketers:

        fit =
            recommendProduct(
                marketer,
                product
            )

        workload =
            getWorkload(marketer)

        routingScore =
            calculateRoutingScore(
                fit,
                workload
            )

    return best_marketer
```

---

# 33. Cold Start

Nếu marketer mới chưa có lịch sử:

```text
Explicit Preference
Team Average
Category Preference
Market Preference
Manual Skill Assessment
```

Khi đã có đủ dữ liệu test thì giảm trọng số preference và tăng trọng số actual performance.

---

# 34. Root Cause Analysis

Khi product loser, không được giảm DNA ngay.

Phải tìm nguyên nhân:

```text
Product Issue
Creative Issue
Offer Issue
Market Issue
Operations Issue
Delivery Issue
Supplier Issue
```

Ví dụ:

```text
MKT A test Jewelry thất bại
```

nhưng nguyên nhân:

```text
supplier lỗi
```

thì không nên giảm mạnh Jewelry Fit.

---

# 35. Pipeline quy mô lớn

Ví dụ:

```text
Crawl:
50,000 ads
```

↓

```text
Detect:
3,000 products
```

↓

```text
High opportunity:
500 products
```

↓

```text
Company Fit:
250 products
```

↓

```text
Match Marketer DNA
```

↓

```text
Mỗi MKT chỉ nhận:
20–50 sản phẩm phù hợp nhất
```

---

# 36. So sánh với Spy Tool thông thường

Spy Tool:

```text
50,000 ads
↓
Marketer tự tìm
↓
Marketer tự lọc
↓
Marketer tự đoán
```

App của mình:

```text
50,000 ads
↓
AI Filter
↓
Competitive Analysis
↓
Opportunity Score
↓
Marketer DNA
↓
Personal Ranking
↓
20 products
```

---

# 37. Tên module

Có thể đặt:

```text
MARKETER DNA
+
PERSONALIZED PRODUCT RECOMMENDATION ENGINE
+
PRODUCT ROUTING ENGINE
```

hoặc gộp thành:

# PERSONALIZED MARKETING INTELLIGENCE

---

# 38. Vị trí trong toàn hệ thống

Module này nên nằm:

```text
sau Competitive Intelligence
```

và:

```text
trước Product Assignment / Testing
```

Luồng tổng:

```text
Ads Collection
      ↓
Product Detection
      ↓
Competitive Analysis
      ↓
Global Opportunity Scoring
      ↓
Company Fit
      ↓
Marketer DNA Matching
      ↓
Personalized Feed
      ↓
Product Routing
      ↓
Product Assignment
      ↓
Testing
      ↓
Sales
      ↓
Delivery
      ↓
Returns
      ↓
Profit
      ↓
Feedback Learning
```

---

# 39. Mục tiêu cuối cùng

App không chỉ trả lời:

```text
“Sản phẩm nào đang hot?”
```

mà phải trả lời:

```text
“Sản phẩm nào phù hợp nhất với từng marketer?”
```

và:

```text
“Ai trong team có xác suất đánh sản phẩm này tốt nhất?”
```

và:

```text
“Dựa trên lịch sử thật,
MKT này đang mạnh ở category,
market và creative nào?”
```

---

# 40. Kết luận

Điểm cốt lõi:

```text
Mỗi marketer có một năng lực khác nhau.
```

Do đó không nên:

```text
one feed for everyone
```

mà nên:

```text
one personalized feed per marketer
```

Personalization phải dựa trên:

```text
Actual Performance
Winner History
Category Strength
Market Strength
Creative Strength
Angle Strength
Price Strength
Business Result
```

Luồng cuối:

```text
ADS DATA
→ INTELLIGENCE
→ MARKETER DNA
→ PERSONAL MATCH
→ ROUTING
→ TEST
→ REAL RESULT
→ LEARNING
```

Nếu triển khai đúng, app càng sử dụng lâu càng hiểu:

```text
MKT nào giỏi sản phẩm gì.
MKT nào giỏi thị trường nào.
MKT nào giỏi angle nào.
MKT nào phù hợp với sản phẩm mới nào.
```

Đây là bước biến hệ thống từ một **Ads Spy Platform** thành một:

# AI MARKETING DECISION & ROUTING SYSTEM
