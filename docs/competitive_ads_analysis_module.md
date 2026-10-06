# MODULE PHÂN TÍCH CẠNH TRANH ADS – CHI TIẾT TỪNG QUẢNG CÁO

> Mục tiêu: Khi người dùng bấm **“Chi tiết”** ở một quảng cáo, hệ thống không chỉ hiển thị thông tin ads mà phải trả lời được:
>
> - **Vì sao ads này đáng chú ý?**
> - **Có nên chọn để nghiên cứu / clone concept / test hay không?**
> - **Đối thủ đang làm tốt điều gì?**
> - **Đội mình đang thiếu điều gì so với đối thủ?**
> - **Muốn chạy ads tương tự thì cần chuẩn bị gì?**
> - **Điểm nào có thể học, điểm nào không nên copy?**
> - **Ads này phù hợp thị trường / sản phẩm / team của mình đến đâu?**
> - **Nếu triển khai thì nên triển khai theo hướng nào?**

---

# 1. Vai trò của module “Cạnh tranh”

Module này nằm sau bước thu thập ads từ nhiều nguồn như:

- Meta Ads Library
- TikTok Creative Center / TikTok Ads
- PipiAds
- Minea
- BigSpy
- AdSpy
- WinningHunter
- Adsparo
- PPSpy
- Dropship.io
- Các nguồn crawl / connector hợp lệ khác

Luồng tổng quát:

```text
Nguồn Ads
   ↓
Crawler / Connector
   ↓
Chuẩn hóa dữ liệu Ads
   ↓
Feature Extraction
   ↓
Competitive Analysis Engine
   ↓
Competitive Score
   ↓
AI Analyst
   ↓
Kết luận:
- Nên chọn
- Theo dõi
- Chỉ tham khảo
- Không nên chọn
```

---

# 2. Màn hình danh sách ads

Ở màn hình danh sách, mỗi quảng cáo nên có một số chỉ số tóm tắt để marketer có thể scan nhanh.

Ví dụ:

```text
┌─────────────────────────────────────────────┐
│ [VIDEO / IMAGE]                             │
│                                             │
│ Product: Gold Bracelet                      │
│ Market: Saudi Arabia                        │
│ Platform: Meta                              │
│                                             │
│ Running: 38 days                            │
│ Competitor Score: 87/100                    │
│ Opportunity Score: 82/100                   │
│ Saturation: Medium                          │
│ Creative Strength: High                     │
│                                             │
│ Recommendation: ⭐ NÊN TEST                 │
│                                             │
│ [Xem Ads]       [Chi tiết]                  │
└─────────────────────────────────────────────┘
```

Không cần hiển thị toàn bộ phân tích ở đây.

Phần quan trọng nhất nằm trong nút:

> **CHI TIẾT**

---

# 3. Khi bấm “Chi tiết”

Màn hình chi tiết nên chia thành các block sau.

```text
1. Executive Summary
2. Vì sao ads này đáng chú ý
3. Đối thủ đang làm gì tốt
4. Điểm yếu của đối thủ
5. Team mình đang thiếu gì
6. Cần chuẩn bị gì nếu muốn triển khai
7. Phân tích Creative
8. Phân tích Offer
9. Phân tích Funnel
10. Phân tích Product
11. Phân tích Market Fit
12. Phân tích độ bão hòa
13. Khả năng tạo “sóng”
14. Risk Analysis
15. Competitive Score
16. Opportunity Score
17. Quyết định cuối cùng
18. Action Plan
```

---

# 4. Executive Summary

Đây là phần AI tổng hợp nhanh nhất.

Ví dụ:

```text
KẾT LUẬN: NÊN TEST

Ads này đáng test vì đối thủ đã duy trì quảng cáo 38 ngày,
đồng thời sử dụng nhiều biến thể creative khác nhau.

Hook của video đánh trực tiếp vào yếu tố:
“trông như vàng thật nhưng giá thấp hơn rất nhiều”.

Đây là một angle phù hợp với thị trường Saudi,
đặc biệt với nhóm khách muốn sản phẩm có tính khoe / quà tặng /
luxury-look nhưng không muốn chi quá nhiều tiền.

Tuy nhiên thị trường đang bắt đầu có dấu hiệu cạnh tranh cao.

Team hiện tại đang thiếu:
- video UGC kiểu local
- trust element
- offer mạnh
- landing page theo ngôn ngữ Arabic

Khuyến nghị:
Không copy nguyên quảng cáo.
Nên clone “angle + structure”, sau đó tạo creative mới.
```

---

# 5. Phần “Vì sao nên chọn ads này?”

AI phải đưa ra **lý do cụ thể**, không được trả lời chung chung.

Các tín hiệu có thể sử dụng:

## 5.1 Tuổi quảng cáo

```text
running_days
```

Ví dụ:

```text
Ads chạy > 30 ngày
→ có khả năng advertiser vẫn thấy hiệu quả.
```

Không được kết luận chắc chắn rằng ads profitable.

Chỉ nên kết luận:

```text
“Có tín hiệu cho thấy advertiser vẫn tiếp tục phân phối quảng cáo.”
```

---

## 5.2 Số lượng creative cùng sản phẩm

```text
creative_variants = 18
```

Nếu advertiser liên tục tạo creative mới:

```text
→ sản phẩm có khả năng đang được scale hoặc test mạnh.
```

---

## 5.3 Số advertiser khác nhau

```text
advertiser_count = 26
```

Nếu nhiều advertiser cùng bán:

```text
→ sản phẩm đã được validate ở một mức độ nhất định.
```

Nhưng đồng thời:

```text
→ cạnh tranh có thể cao.
```

---

# 6. “Đối thủ đã làm được gì?”

Đây là phần quan trọng nhất.

Hệ thống phải phân tích competitor ở nhiều tầng.

---

# 6.1 Creative Strategy

Phân tích:

```text
Hook
Opening 3 seconds
Video format
UGC
Voiceover
Music
Text overlay
Visual proof
Demo
Before / After
Problem / Solution
Storytelling
CTA
Video length
Editing speed
```

Ví dụ output:

```text
ĐỐI THỦ LÀM TỐT:

1. Hook rất nhanh
Trong 2 giây đầu đã show trực tiếp sản phẩm trên tay.

2. Không giải thích dài
Creative tập trung vào visual luxury.

3. Có social proof
Video mô phỏng reaction của người dùng.

4. CTA rõ
“Order Today – Cash on Delivery”.

5. Creative phù hợp mobile
Text lớn, video dọc, nhịp cắt nhanh.
```

---

# 6.2 Offer Strategy

Phân tích offer:

```text
discount
bundle
COD
free_shipping
limited_time
buy_1_get_1
gift
price_anchor
scarcity
urgency
```

Ví dụ:

```text
Offer đối thủ:

Giá niêm yết:
399 SAR

Giá bán:
199 SAR

Discount:
50%

COD:
Có

Free shipping:
Có

Scarcity:
“Only today”
```

AI nhận xét:

```text
Offer của đối thủ mạnh ở perceived discount.

Tuy nhiên discount 50% đã khá phổ biến.

Nếu team chạy cùng mức giá,
nên bổ sung bundle hoặc quà tặng để khác biệt.
```

---

# 6.3 Product Positioning

AI phải trả lời:

```text
Đối thủ đang bán sản phẩm này dưới góc độ gì?
```

Ví dụ:

```text
Luxury
Gift
Beauty
Status
Convenience
Problem-solving
Novelty
Emotional
Religious / spiritual
Health
Trend
```

Ví dụ output:

```text
Competitor Positioning:

“Luxury look at affordable price”

Đối thủ không bán chiếc vòng.

Đối thủ bán cảm giác:

“Nhìn giống sản phẩm cao cấp nhưng giá dễ mua.”
```

---

# 6.4 Target Audience

AI suy luận audience từ creative + copy + product.

Ví dụ:

```text
Primary Audience:

Female
Age: 20–40
Market: Saudi Arabia

Interest Signals:
- jewelry
- fashion
- gifting
- luxury style
```

Phần này cần gắn:

```text
Confidence: Medium
```

Vì target audience chỉ là suy luận.

---

# 7. “Mình đang thiếu gì?”

Phần này phải dựa trên dữ liệu **ads của công ty**.

Hệ thống cần so sánh:

```text
Competitor Ads
vs
Our Ads
```

Ví dụ:

```text
COMPETITOR

UGC video
Arabic voice
Strong Hook
COD
50% Discount
Review
Product demo

OUR ADS

Static image
English text
Weak Hook
COD
20% discount
No review
No demo
```

AI tạo Gap Analysis:

```text
Creative Gap

Competitor: 9/10
Our Team: 5/10

Missing:

- UGC
- Arabic localization
- visual demo
- stronger hook
- social proof
```

---

# 8. Competitor Gap Matrix

UI nên có bảng:

| Thành phần | Đối thủ | Team mình | Gap |
|---|---:|---:|---:|
| Hook | 9 | 5 | -4 |
| UGC | 9 | 2 | -7 |
| Offer | 8 | 6 | -2 |
| Social Proof | 8 | 3 | -5 |
| Landing Page | 9 | 7 | -2 |
| Local Language | 10 | 5 | -5 |
| CTA | 8 | 7 | -1 |
| Product Demo | 9 | 4 | -5 |

Từ đó AI kết luận:

```text
Khoảng cách lớn nhất hiện tại:

1. UGC
2. Local Language
3. Social Proof
4. Product Demonstration
```

---

# 9. “Mình cần gì để chạy ads này?”

AI phải chuyển competitor analysis thành **requirements**.

Ví dụ:

```text
Nếu muốn test sản phẩm này cần:

Creative:
- 3 UGC videos
- 2 product demo videos
- 5 hooks
- 3 CTA variations

Content:
- Arabic voiceover
- Arabic subtitle
- localized copy

Offer:
- COD
- Free shipping
- bundle option

Landing Page:
- Arabic
- social proof
- product demo
- FAQ
- COD explanation

Operations:
- kiểm tra nguồn hàng
- kiểm tra delivery success rate
- kiểm tra return rate

Test Budget:
- X creative
- X adsets
```

Nếu chưa có dữ liệu cost thì hệ thống không tự bịa ngân sách.

---

# 10. Phân tích “nên copy cái gì?”

Không nên cho marketer chỉ bấm “copy ads”.

Thay vào đó AI phải tách thành:

```text
COPY / LEARN / AVOID
```

Ví dụ:

```text
NÊN HỌC

✓ Hook structure
✓ Video pacing
✓ Product demo
✓ Luxury positioning
✓ COD trust message

KHÔNG NÊN COPY NGUYÊN

✗ Video
✗ Script
✗ Branding
✗ Music copyrighted
✗ Review của competitor
```

---

# 11. Phân tích Creative Formula

Hệ thống có thể biến ads thành công thức.

Ví dụ:

```text
HOOK
↓
PRODUCT DEMO
↓
BENEFIT
↓
SOCIAL PROOF
↓
OFFER
↓
CTA
```

AI output:

```text
Creative Formula:

0–3s:
Show product close-up

3–7s:
Demonstrate reflection / luxury appearance

7–12s:
User reaction

12–16s:
Price comparison

16–20s:
Offer + COD

20–23s:
CTA
```

Đây là phần cực hữu ích để creative team dựng ads mới.

---

# 12. Product Opportunity Analysis

Không chỉ nhìn ads.

Cần đánh giá sản phẩm.

Các tiêu chí:

```text
Visual Appeal
Problem Solving
Impulse Buy
Wow Factor
Giftability
Margin Potential
Shipping Complexity
Return Risk
Fake / Counterfeit Risk
Policy Risk
Localization Potential
Repeat Purchase
```

---

# 13. “Sản phẩm có tạo sóng được không?”

Đây là tiêu chí phù hợp với cách team marketing lựa sản phẩm.

Không chỉ hỏi:

```text
“Sản phẩm này có bán được không?”
```

Mà cần hỏi:

```text
“Sản phẩm này có khả năng tạo biên độ quan tâm lớn không?”
```

---

# 14. Wave Potential Score

Có thể tạo score:

```text
Wave Potential Score
```

Các thành phần:

```text
Novelty
Visual Wow
Emotional Trigger
Shareability
Controversy
Trendability
Social Proof Potential
Local Relevance
Price Shock
Gift Potential
```

Ví dụ:

```text
Wave Potential: 84/100

Lý do:

+ Visual mạnh
+ Nhìn giống luxury product
+ Dễ tạo reaction
+ Giá dễ tạo price shock

- Không quá mới
- Đã có khá nhiều competitor
```

---

# 15. Market Fit

Một ads thắng ở Mỹ chưa chắc thắng Saudi.

Do đó cần:

```text
market_fit_score
```

Ví dụ:

```text
Saudi Arabia

Luxury Appeal: HIGH
COD Fit: HIGH
Gift Culture Fit: HIGH
Visual Product Fit: HIGH

Market Fit Score:
88/100
```

---

# 16. Saturation Analysis

AI đánh giá độ bão hòa.

Inputs:

```text
advertiser_count
active_ads_count
creative_count
first_seen
last_seen
growth_rate
new_ads_7d
new_ads_30d
```

Output:

```text
Saturation: MEDIUM

Competitor:
26 advertisers

New advertisers 7d:
4

New creatives 7d:
31
```

AI kết luận:

```text
Sản phẩm đang tăng cạnh tranh nhưng chưa ở mức quá bão hòa.

Nếu test, nên tập trung differentiation creative.
```

---

# 17. Competitor Momentum

Một metric quan trọng:

```text
Competitor Momentum Score
```

Tính từ:

```text
new_ads
new_creatives
new_advertisers
creative_refresh_rate
ad_running_days
```

Ví dụ:

```text
Momentum: 91/100

→ nhiều advertiser đang tăng creative.
```

---

# 18. Creative Saturation

Có thể product chưa saturated nhưng creative angle đã saturated.

Ví dụ:

```text
Product Saturation:
Medium

Creative Saturation:
High
```

Nghĩa là:

```text
Sản phẩm vẫn có tiềm năng
nhưng hook hiện tại đã bị dùng quá nhiều.
```

Kết luận:

```text
Không copy hook.
Tìm angle mới.
```

---

# 19. Opportunity Score

Đây là điểm tổng hợp để quyết định có nên test ads / product hay không.

Ví dụ:

```text
Opportunity Score =

25% Market Fit
20% Creative Strength
15% Wave Potential
15% Competitor Validation
10% Offer Strength
10% Margin Potential
5% Operational Fit
```

---

# 20. Risk Score

Risk cần tách riêng.

```text
Risk Score
```

Bao gồm:

```text
Policy Risk
Counterfeit Risk
Copyright Risk
Brand Risk
Shipping Risk
Return Risk
Quality Risk
COD Risk
Legal Risk
Market Saturation
```

Ví dụ:

```text
Risk: 67/100

Main Risk:

- dễ bị xem là fake luxury
- high return risk
- creative có nguy cơ misleading
```

---

# 21. Final Decision Engine

Hệ thống không chỉ đưa score.

Phải đưa **quyết định**.

Ví dụ:

```text
90–100

🔥 STRONG TEST

75–89

⭐ TEST

60–74

👀 WATCH

40–59

⚠ RESEARCH MORE

0–39

❌ SKIP
```

---

# 22. Nhưng score không đủ

Cần Rule Engine.

Ví dụ:

```text
IF policy_risk > 80
THEN SKIP

IF counterfeit_risk > 80
THEN SKIP

IF opportunity_score > 80
AND saturation < 70
THEN TEST

IF opportunity_score > 80
AND saturation > 85
THEN FIND_NEW_ANGLE
```

---

# 23. Quyết định cuối cùng

Output:

```text
DECISION

⭐ TEST

Confidence:
82%

Reason:

1. Product đã được nhiều advertiser validate.
2. Creative có visual mạnh.
3. Market Saudi phù hợp.
4. COD giúp conversion.
5. Competition đang tăng nhưng chưa quá saturated.

Điểm cần lưu ý:

Creative angle hiện tại đang bắt đầu bị dùng nhiều.

Khuyến nghị:

Clone concept,
không clone creative.
```

---

# 24. AI Competitive Analyst

Prompt system cho AI có thể thiết kế:

```text
You are a competitive advertising analyst.

Your task is to analyze an advertisement
and determine whether our marketing team
should test, monitor, learn from, or ignore it.

You must analyze:

1. Why the ad is interesting
2. Why it may be working
3. Competitor strengths
4. Competitor weaknesses
5. Creative strategy
6. Offer strategy
7. Product positioning
8. Target audience
9. Market fit
10. Saturation
11. Opportunity
12. Risk
13. What our team is missing
14. What our team needs
15. What we should copy
16. What we should not copy
17. Recommended testing strategy

Never claim profitability unless actual sales/revenue data exists.

Clearly separate:
- observed facts
- inferred signals
- AI hypotheses
```

---

# 25. AI Output JSON

Nên ép AI output JSON.

```json
{
  "decision": "TEST",
  "confidence": 0.84,

  "executive_summary": "...",

  "why_interesting": [
    "...",
    "..."
  ],

  "competitor_strengths": [
    "...",
    "..."
  ],

  "competitor_weaknesses": [
    "...",
    "..."
  ],

  "creative_analysis": {
    "hook": "...",
    "structure": "...",
    "visual": "...",
    "ugc": "...",
    "cta": "..."
  },

  "offer_analysis": {
    "price": null,
    "discount": null,
    "cod": true,
    "free_shipping": true,
    "urgency": "medium"
  },

  "market_analysis": {
    "market": "SA",
    "market_fit_score": 88,
    "saturation_score": 63,
    "wave_potential_score": 84
  },

  "gap_analysis": {
    "missing": [
      "Arabic UGC",
      "Social proof",
      "Product demo"
    ]
  },

  "requirements": [
    "3 Arabic UGC videos",
    "5 hooks",
    "2 offers"
  ],

  "copy": [
    "Hook structure",
    "Product demo style"
  ],

  "avoid": [
    "Exact video",
    "Competitor testimonials"
  ],

  "scores": {
    "creative": 89,
    "offer": 82,
    "product": 86,
    "market_fit": 88,
    "saturation": 63,
    "risk": 42,
    "opportunity": 84
  }
}
```

---

# 26. Quan trọng: Evidence Layer

AI analysis phải có evidence.

Không nên chỉ hiển thị:

```text
“Ads rất tốt”
```

Nên hiển thị:

```text
Creative Strength: 9/10

Evidence:

- Video mở đầu bằng product close-up.
- CTA xuất hiện ở giây 17.
- Creative có 3 trust elements.
- Advertiser đang chạy 8 variations.
```

---

# 27. Fact / Signal / Hypothesis

Nên phân biệt 3 loại dữ liệu.

## FACT

Dữ liệu crawl được.

Ví dụ:

```text
Ad running:
38 days
```

## SIGNAL

Suy luận từ dữ liệu.

```text
Ads chạy lâu
→ advertiser có thể đang tiếp tục phân phối.
```

## HYPOTHESIS

AI suy luận.

```text
Luxury positioning có thể phù hợp Saudi.
```

UI nên có tag:

```text
FACT
SIGNAL
AI HYPOTHESIS
```

Điều này giảm hallucination.

---

# 28. Database

Có thể tạo bảng:

```text
ads
advertisers
products
creatives
offers
markets
competitor_analysis
creative_features
ad_metrics
competitor_scores
```

---

# 29. competitor_analysis

Ví dụ schema:

```sql
CREATE TABLE competitor_analysis (

    id TEXT PRIMARY KEY,

    ad_id TEXT,

    decision TEXT,

    confidence REAL,

    executive_summary TEXT,

    competitor_strengths TEXT,

    competitor_weaknesses TEXT,

    our_gaps TEXT,

    requirements TEXT,

    opportunity_score INTEGER,

    saturation_score INTEGER,

    creative_score INTEGER,

    market_fit_score INTEGER,

    wave_score INTEGER,

    risk_score INTEGER,

    created_at DATETIME

);
```

Nếu dùng Cloudflare D1:

```text
JSON field
```

có thể lưu dạng TEXT JSON.

---

# 30. competitor_score

Có thể tách bảng:

```sql
CREATE TABLE competitor_scores (

    ad_id TEXT PRIMARY KEY,

    creative_score INTEGER,

    product_score INTEGER,

    offer_score INTEGER,

    market_score INTEGER,

    saturation_score INTEGER,

    momentum_score INTEGER,

    wave_score INTEGER,

    risk_score INTEGER,

    opportunity_score INTEGER

);
```

---

# 31. API

Ví dụ:

```text
GET /api/ads/:id
```

Trả về basic ads.

```text
GET /api/ads/:id/analysis
```

Trả về competitive analysis.

---

# 32. Generate analysis

API:

```text
POST /api/ads/:id/analyze
```

Flow:

```text
Load Ad
↓
Load Competitor Data
↓
Load Similar Ads
↓
Load Our Ads
↓
Calculate Metrics
↓
AI Analysis
↓
Store Result
↓
Return JSON
```

---

# 33. Compare với ads của công ty

Một tính năng cực quan trọng:

```text
Compare With Our Ads
```

UI:

```text
Competitor
vs
Our Best Ad
```

AI trả lời:

```text
Competitor mạnh hơn ở:

- Hook
- UGC
- Offer

Team mạnh hơn ở:

- Landing Page
- Branding

Gap lớn nhất:

UGC
```

---

# 34. Similar Ads Cluster

Hệ thống nên group ads tương tự.

Ví dụ:

```text
Product Cluster:
Gold Bracelet

Ads:
328

Advertisers:
47

Markets:
SA
AE
KW
QA
```

Sau đó phân tích:

```text
Top Hooks
Top Offers
Top Creatives
Top Advertisers
```

---

# 35. Winning Pattern Extraction

Ví dụ hệ thống phát hiện:

```text
72% ads top-running dùng video

61% dùng COD

48% dùng discount > 40%

54% show product trong 3 seconds
```

Output:

```text
Winning Pattern:

Video
+
Product-first hook
+
COD
+
Price anchor
+
Urgency
```

---

# 36. Creative Angle Library

AI tự động tạo angle.

Ví dụ:

```text
Angle 1
Luxury

Angle 2
Gift

Angle 3
Price Shock

Angle 4
Before / After

Angle 5
Reaction

Angle 6
Problem / Solution
```

---

# 37. “Untapped Angle”

Đây là tính năng rất quan trọng.

AI không chỉ hỏi:

```text
“Đối thủ đang dùng angle gì?”
```

Mà hỏi:

```text
“Angle nào đối thủ chưa dùng?”
```

Ví dụ:

```text
Competitor angles:

Luxury
Price
Gift

Untapped:

Couple
Wedding
Mother gift
Ramadan gift
```

---

# 38. Competitive Moat

AI đánh giá:

```text
Đối thủ có lợi thế khó copy không?
```

Ví dụ:

```text
Brand
Exclusive supplier
Celebrity
Unique creative
Price
Fulfillment
Community
Patent
```

Nếu không có moat:

```text
Opportunity cao hơn.
```

---

# 39. “Can We Beat This?”

Một block rất hữu ích.

```text
CAN WE BEAT THIS AD?

YES – 76% confidence

Reason:

Competitor creative mạnh
nhưng offer chưa khác biệt.

Team có thể cạnh tranh bằng:

- Arabic UGC
- better bundle
- stronger social proof
- faster landing page
```

---

# 40. Action Plan

Cuối trang phải có:

```text
NEXT ACTION
```

Ví dụ:

```text
1. Download / save reference creative
2. Generate 5 hooks
3. Produce 3 UGC variants
4. Prepare Arabic landing page
5. Create 2 offers
6. Test product
7. Monitor competitor
```

---

# 41. Button

Các nút cuối màn hình:

```text
[Save Product]

[Add To Watchlist]

[Create Test Plan]

[Generate Creative Brief]

[Find Similar Ads]

[Compare Competitors]

[Download Creative]

[Send To Creative Team]
```

---

# 42. Creative Brief tự động

Khi marketer bấm:

```text
Generate Creative Brief
```

AI sinh:

```text
PRODUCT

Gold Bracelet

MARKET

Saudi Arabia

ANGLE

Affordable Luxury

HOOK

“Everyone thinks this is real gold.”

VIDEO

UGC

DURATION

20 seconds

STRUCTURE

Hook
Demo
Reaction
Offer
CTA

CTA

Order Today – COD Available
```

---

# 43. Competitive Dashboard

Ngoài ads detail có dashboard tổng.

Ví dụ:

```text
Top Products

Top Competitors

Fastest Growing Ads

Most Copied Products

High Momentum Products

Low Saturation Products

High Wave Potential

Competitor Creative Trends
```

---

# 44. Competitor Timeline

Ví dụ:

```text
Day 1

1 creative

Day 7

5 creatives

Day 14

14 creatives

Day 30

32 creatives
```

AI:

```text
Competitor đang tăng tốc creative production.
```

---

# 45. Alert

Có thể tạo:

```text
Competitor Alert
```

Ví dụ:

```text
Advertiser X vừa launch 12 ads mới.
```

Hoặc:

```text
Product X tăng 40% ads trong 7 ngày.
```

---

# 46. Competitive Intelligence Engine

Architecture:

```text
              ADS SOURCES
                  │
                  ▼
         Data Collector Layer
                  │
                  ▼
        Normalization Pipeline
                  │
                  ▼
          Feature Extraction
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
Creative Analyzer     Product Analyzer
        │                   │
        └─────────┬─────────┘
                  ▼
        Competitor Intelligence
                  │
                  ▼
             Scoring Engine
                  │
                  ▼
             AI Analyst
                  │
                  ▼
            Recommendation
```

---

# 47. Feature Extraction

Có thể dùng:

```text
Computer Vision
Speech-to-Text
OCR
LLM
```

Extract:

```text
product
price
discount
hook
CTA
language
voice
offer
visual style
video structure
```

---

# 48. Video Analysis

Pipeline:

```text
Video
↓
Frames extraction
↓
Speech to text
↓
OCR
↓
Scene detection
↓
LLM analysis
```

---

# 49. Similarity Engine

Dùng:

```text
image embedding
video embedding
text embedding
```

Để tìm:

```text
same product
same creative
same hook
same video
same landing page
```

---

# 50. Duplicate Ads

Hệ thống cần deduplicate.

Ví dụ:

```text
Meta Ad
PipiAds
Minea
```

Có thể cùng là một ads.

Dùng:

```text
video hash
image hash
text similarity
landing domain
```

---

# 51. Competitor Graph

Có thể tạo graph:

```text
Advertiser
↓
Products
↓
Ads
↓
Markets
```

Ví dụ:

```text
Store A

Product X
Product Y

Markets:

SA
AE
KW
```

---

# 52. Store Strategy

AI có thể phân tích cả store.

Ví dụ:

```text
Store này test 20 products / tháng

Nhưng chỉ scale 3 products.
```

Hệ thống tìm:

```text
winner ratio
```

---

# 53. Product Lifecycle

Có thể detect:

```text
Emerging
Growing
Scaling
Saturated
Declining
```

Ví dụ:

```text
Product Stage:

GROWING
```

---

# 54. Early Winner Detection

Một trong các feature đáng giá nhất.

Tìm:

```text
advertiser_count thấp
+
creative_growth cao
+
running_days tăng
```

Ví dụ:

```text
Advertisers:
7

Creatives:
41

7d growth:
+190%
```

AI:

```text
Possible early winner.
```

---

# 55. “Why Now?”

Block:

```text
WHY NOW?
```

Ví dụ:

```text
Product mới bắt đầu tăng ads trong 14 ngày gần đây.

Competition vẫn thấp.

Creative refresh cao.

→ có thể là thời điểm phù hợp để test.
```

---

# 56. Không nên phụ thuộc hoàn toàn vào AI

Score nên chia:

```text
60% Data
40% AI
```

Ví dụ:

```text
Data Score

running_days
advertisers
creative_growth
market distribution

AI Score

creative
offer
product
market fit
```

---

# 57. Human Override

Marketer có thể:

```text
Approve
Reject
Watch
Tested
Winner
Loser
```

Hệ thống học từ quyết định này.

---

# 58. Internal Feedback Loop

Sau khi test:

```text
Ad Candidate
↓
Test
↓
Revenue
Orders
Delivery Rate
Return Rate
CPA
ROAS
↓
Update Model
```

Đây là điểm giúp app tốt hơn các spy tool thông thường.

---

# 59. Business Score

Cuối cùng không chỉ có:

```text
Ad Score
```

Mà có:

```text
Business Score
```

Ví dụ:

```text
Creative Potential
Market Demand
Margin
Delivery Success
Return Risk
Customer Satisfaction
Scalability
```

---

# 60. Final Product Score

Ví dụ:

```text
PRODUCT SCORE

Creative           89
Market Fit          88
Wave Potential      84
Offer               82
Competitor Signal   86
Margin              76
Operations          71

Risk               -42

FINAL SCORE

84 / 100
```

---

# 61. UI đề xuất cho “Chi tiết Ads”

```text
┌─────────────────────────────────────────────────────────────┐
│ AD DETAIL                                                   │
│                                                             │
│ [VIDEO]                                                     │
│                                                             │
│ Gold Bracelet                                               │
│ Saudi Arabia                                                │
│                                                             │
│ Opportunity: 84                                             │
│ Wave: 84                                                    │
│ Saturation: 63                                              │
│ Risk: 42                                                    │
│                                                             │
│ ⭐ TEST                                                      │
├─────────────────────────────────────────────────────────────┤
│ WHY THIS AD?                                                │
│                                                             │
│ - Running 38 days                                           │
│ - 18 creatives                                              │
│ - Strong visual                                             │
│ - COD                                                       │
├─────────────────────────────────────────────────────────────┤
│ COMPETITOR DID WELL                                         │
│                                                             │
│ Hook                                                        │
│ UGC                                                         │
│ Offer                                                       │
│ Social proof                                                │
├─────────────────────────────────────────────────────────────┤
│ WE ARE MISSING                                              │
│                                                             │
│ Arabic UGC                                                  │
│ Review                                                      │
│ Product demo                                                │
├─────────────────────────────────────────────────────────────┤
│ WHAT WE NEED                                                │
│                                                             │
│ 3 UGC videos                                                │
│ 5 hooks                                                     │
│ Arabic landing page                                         │
├─────────────────────────────────────────────────────────────┤
│ CAN WE BEAT IT?                                             │
│                                                             │
│ YES – 76%                                                   │
├─────────────────────────────────────────────────────────────┤
│ ACTION                                                      │
│                                                             │
│ [Generate Brief]                                            │
│ [Add Watchlist]                                             │
│ [Test Product]                                              │
└─────────────────────────────────────────────────────────────┘
```

---

# 62. Phần quan trọng nhất của module

Nếu rút gọn toàn bộ module cạnh tranh thì mỗi quảng cáo phải trả lời được **8 câu hỏi chính**:

```text
1. Ads này có gì đáng chú ý?

2. Vì sao đối thủ có thể đang chạy nó?

3. Đối thủ đang làm tốt điều gì?

4. Đối thủ đang yếu ở đâu?

5. Team mình đang thiếu gì?

6. Muốn cạnh tranh thì cần làm gì?

7. Có thể làm tốt hơn đối thủ ở điểm nào?

8. Có nên test hay không?
```

---

# 63. Output cuối cùng nên giống một “Investment Memo cho Ads”

Không nên chỉ là:

```text
Score: 85
```

Mà nên là:

```text
ADS INVESTMENT MEMO

Decision:

TEST

Why:

Strong competitor signal
Good market fit
High visual potential

Risk:

Medium saturation

Competitive Advantage:

Team có thể thắng bằng localized UGC.

Action:

Create 3 new creatives
Test 2 offers
Launch Saudi campaign
```

---

# 64. Kết luận kiến trúc cho app

Phần **Competitive Intelligence** nên trở thành một module lõi của app.

```text
Ads Discovery
      ↓
Product Discovery
      ↓
Competitive Intelligence
      ↓
Opportunity Scoring
      ↓
Creative Strategy
      ↓
Testing
      ↓
Internal Sales Data
      ↓
Winner / Loser Feedback
```

Điểm khác biệt của app so với các spy tool thông thường:

```text
Spy Tool

→ cho marketer xem ads.


App của mình

→ xem ads
→ hiểu competitor
→ phân tích opportunity
→ tìm gap
→ đề xuất creative
→ đề xuất test
→ theo dõi kết quả
→ học từ dữ liệu nội bộ
```

Mục tiêu cuối:

> Không phải xây một **Ads Library**.

Mà là xây một:

# AI COMPETITIVE INTELLIGENCE SYSTEM

giúp marketer trả lời:

```text
“ADS NÀY CÓ ĐÁNG ĐỂ MÌNH ĐÁNH KHÔNG?”
```

và nếu có:

```text
“MÌNH PHẢI ĐÁNH NHƯ THẾ NÀO ĐỂ THẮNG ĐỐI THỦ?”
```

---

# 65. Bổ sung đề xuất triển khai thực tế

Để module này chạy ổn trong app, nên chia làm 4 tầng:

```text
Tầng 1 – Data
Thu thập ads + creative + landing page + advertiser + market.

Tầng 2 – Deterministic Metrics
Tính running days, số creative, số advertiser, tốc độ tăng, độ trùng lặp,
phân bố thị trường và các chỉ số có thể tính trực tiếp.

Tầng 3 – AI Analysis
Phân tích hook, angle, offer, positioning, creative structure,
gap và action plan.

Tầng 4 – Internal Validation
Đối chiếu với ads, đơn hàng, delivery rate, return rate, CPA/ROAS
của chính công ty nếu có.
```

Không nên cho LLM tự tính toàn bộ score từ cảm giác.

Các score định lượng nên được backend tính trước rồi gửi vào AI để giải thích.

---

# 66. Cấu trúc object đầy đủ nên lưu cho mỗi Ads Detail

```json
{
  "ad": {
    "id": "ad_001",
    "platform": "meta",
    "advertiser_id": "adv_01",
    "product_id": "prd_88",
    "market": "SA",
    "first_seen": "2026-08-01",
    "last_seen": "2026-09-08",
    "running_days": 38
  },

  "competitive_metrics": {
    "advertiser_count": 26,
    "active_ads_count": 74,
    "creative_variants": 18,
    "new_ads_7d": 31,
    "new_advertisers_7d": 4,
    "momentum_score": 91,
    "saturation_score": 63
  },

  "creative": {
    "format": "ugc_video",
    "hook_type": "luxury_price_shock",
    "language": "ar",
    "duration_seconds": 23,
    "has_demo": true,
    "has_social_proof": true,
    "has_price_anchor": true,
    "has_cod": true
  },

  "analysis": {
    "decision": "TEST",
    "confidence": 0.84,
    "opportunity_score": 84,
    "wave_score": 84,
    "risk_score": 42,
    "market_fit_score": 88
  },

  "gap_analysis": {
    "our_hook_score": 5,
    "competitor_hook_score": 9,
    "our_ugc_score": 2,
    "competitor_ugc_score": 9,
    "missing_capabilities": [
      "Arabic UGC",
      "Social proof",
      "Product demo"
    ]
  },

  "recommendation": {
    "copy": [
      "Hook structure",
      "Product demonstration"
    ],
    "avoid": [
      "Exact script",
      "Exact video"
    ],
    "requirements": [
      "3 UGC videos",
      "5 hooks",
      "2 offer variations"
    ]
  }
}
```

---

# 67. Logic backend gợi ý

```text
function analyzeAd(ad):

    competitorData =
        getCompetitorData(ad.product)

    ourData =
        getOurAds(ad.product, ad.market)

    deterministicScores =
        calculateScores(ad, competitorData)

    creativeFeatures =
        extractCreativeFeatures(ad)

    gap =
        compare(
            competitorData,
            ourData
        )

    aiAnalysis =
        analyzeWithLLM(
            ad,
            deterministicScores,
            creativeFeatures,
            gap
        )

    return merge(
        deterministicScores,
        gap,
        aiAnalysis
    )
```

---

# 68. Điều kiện bắt buộc để tránh AI phân tích sai

Mỗi insight nên chứa:

```text
claim
evidence
confidence
type
```

Ví dụ:

```json
{
  "claim": "Advertiser có tín hiệu đang tiếp tục scale creative.",
  "evidence": [
    "18 creative variants",
    "31 new creatives in 7 days"
  ],
  "confidence": 0.82,
  "type": "signal"
}
```

Nếu không có evidence:

```text
Không đưa thành kết luận mạnh.
```

---

# 69. Bộ trạng thái đề xuất

Không nên chỉ có Yes / No.

Nên có:

```text
STRONG_TEST
TEST
WATCH
RESEARCH_MORE
FIND_NEW_ANGLE
SKIP
HIGH_RISK
```

Trong đó:

```text
FIND_NEW_ANGLE
```

rất quan trọng.

Vì nhiều trường hợp:

```text
Product tốt
nhưng creative angle đã saturated.
```

---

# 70. Một mẫu kết quả hoàn chỉnh khi marketer bấm “Chi tiết”

```text
PRODUCT
Gold Bracelet

MARKET
Saudi Arabia

DECISION
⭐ TEST

OPPORTUNITY
84 / 100

CONFIDENCE
84%

WHY THIS AD

Ads đã hoạt động 38 ngày và advertiser có 18 creative variations.
Điều này cho thấy advertiser đang đầu tư đáng kể vào product/angle này.

COMPETITOR STRENGTHS

1. Hook rõ trong 2 giây đầu.
2. Product visual mạnh.
3. Arabic localization tốt.
4. COD giúp giảm friction.
5. Có price anchor và social proof.

COMPETITOR WEAKNESSES

1. Offer khá giống các store khác.
2. Hook luxury đã được sử dụng nhiều.
3. Chưa khai thác angle wedding / gifting sâu.

OUR GAP

1. Chưa có Arabic UGC.
2. Chưa có social proof đủ mạnh.
3. Demo sản phẩm chưa thuyết phục.
4. Discount chưa đủ khác biệt.

WHAT WE NEED

1. 3 Arabic UGC videos.
2. 5 hook variations.
3. 2 offer variations.
4. Arabic landing page.
5. COD trust elements.

WHAT TO COPY

- Creative structure
- Product-first hook
- Product demo
- COD messaging

WHAT NOT TO COPY

- Exact video
- Exact script
- Competitor review
- Branding

UNTAPPED ANGLE

Wedding gift

WHY

Saudi market có strong gifting behavior
và competitor hiện chủ yếu dùng luxury / discount angle.

CAN WE BEAT IT?

YES

Confidence:
76%

Recommended strategy:

Local UGC
+
Wedding Gift angle
+
Bundle offer
+
COD
```

---

# 71. Kết luận cho dev / product team

Nút **“Chi tiết”** không nên là trang xem thêm dữ liệu.

Nó phải là một **decision engine**.

Khi marketer mở chi tiết một ads, trong vòng vài giây phải hiểu được:

```text
ADS NÀY ĐANG THẮNG Ở ĐÂU?
↓
ĐỐI THỦ ĐANG LÀM GÌ?
↓
MÌNH ĐANG THIẾU GÌ?
↓
CÓ CỬA THẮNG KHÔNG?
↓
NÊN TEST KHÔNG?
↓
NẾU TEST THÌ TEST THEO HƯỚNG NÀO?
```

Đó mới là giá trị thực sự của phần **Competitive Intelligence** trong app.
