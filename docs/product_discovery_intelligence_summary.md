# Product Discovery Intelligence System
## Tóm tắt kiến trúc kết nối nguồn Ads, Creative Intelligence và đánh giá sản phẩm theo Market + MKT Taste

---

# 1. Định nghĩa lại mục tiêu của App

App không nên chỉ là:

```text
Spy Ads Tool
```

hoặc:

```text
Win Product Finder
```

Mà nên trở thành:

> **Product Discovery Intelligence System**

Mục tiêu chính:

```text
DISCOVER
→ COLLECT
→ UNDERSTAND
→ SCORE
→ MATCH MARKET
→ MATCH MKT TASTE
→ TEST
→ LEARN
→ SCALE
```

Ứng dụng phải tìm được không chỉ sản phẩm đang bán tốt, mà cả:

- Sản phẩm độc lạ
- Sản phẩm ít người chạy
- Sản phẩm có khả năng tạo sóng
- Sản phẩm dễ tạo creative mạnh
- Sản phẩm team Marketing muốn chọn
- Sản phẩm phù hợp với từng thị trường
- Sản phẩm chưa được chứng minh mạnh nhưng đáng test
- Sản phẩm có thể scale sau khi đã kiểm chứng bằng dữ liệu nội bộ

---

# 2. Kết nối với các nguồn Ads / Spy Tool

Không nên phụ thuộc một nguồn duy nhất.

Kiến trúc:

```text
                 DATA SOURCE LAYER

 Meta Library        TikTok         Pipiads
 Foreplay            Minea          BigSpy
 Similarweb          Stores         Own Ads
      │                 │              │
      └─────────────────┼──────────────┘
                        ↓
                CONNECTOR ROUTER
                        │
          ┌─────────────┼─────────────┐
          ↓             ↓             ↓
         API       Browser Helper    Import
          │             │             │
          └─────────────┼─────────────┘
                        ↓
                RAW INTELLIGENCE
```

Các hình thức kết nối:

| Kiểu kết nối | Mục đích |
|---|---|
| Official API | Lấy dữ liệu trực tiếp từ nền tảng |
| Commercial API | Kết nối provider spy ads |
| First-party API | Lấy Ads Data của chính công ty |
| Browser Extension | MKT lưu ads/product khi đang research |
| CSV / Export | Import dữ liệu từ tool khác |
| Webhook | Nhận dữ liệu realtime |
| Media URL Ingestion | Nhận video/image/landing page từ nguồn hợp lệ |

---

# 3. Chrome Extension cho MKT

Nhân viên Marketing có thể đang mở:

```text
Meta Ad Library
TikTok
Pipiads
Minea
BigSpy
Shopify Store
Landing Page
```

Extension có nút:

```text
+ SAVE TO INTELLIGENCE
```

Khi bấm:

```text
Source
Original Platform
Advertiser
Ad URL
Landing Page
Country
Creative Type
Ad Copy
Video
Images
First Seen
Saved By
```

được gửi về hệ thống.

Sau đó backend tự xử lý:

```text
Video / Image
      ↓
Store Asset
      ↓
Hash / Duplicate Detection
      ↓
OCR
      ↓
Speech-to-Text
      ↓
Scene Analysis
      ↓
Product Recognition
      ↓
Hook Detection
      ↓
Angle Detection
      ↓
CTA / Offer Detection
      ↓
Market Classification
      ↓
Product Cluster
```

---

# 4. Creative Vault

App cần có kho Creative riêng.

Ví dụ một asset:

```text
asset_id
type

source_platform
source_provider

source_ad_id
source_url

advertiser
product_id

market

first_seen_at
collected_at

duration
resolution
language

transcript
ocr_text

hook
angle
cta
offer

landing_page
```

Mục tiêu:

```text
Product
 ↓
Ads
 ↓
Creative
 ↓
Original Source
```

Hoặc:

```text
Video X
 ↓
Find Similar Creative
```

---

# 5. Source Provenance

Mọi dữ liệu phải biết nó đến từ đâu.

Ví dụ:

```text
Product X
 ↓
Creative Y
 ↓
Source:
TikTok

Provider:
Pipiads

Advertiser:
Store A

Collected At:
2026-10-05
```

Điều này giúp:

- Audit dữ liệu
- Kiểm tra nguồn gốc
- Tránh duplicate
- Xem original ad
- Biết tool/provider nào phát hiện sản phẩm
- Đánh giá độ tin cậy của dữ liệu

---

# 6. Creative Family Detection

Một creative có thể bị:

```text
Crop
Mirror
Change Subtitle
Change Music
Change Hook
Change First 3 Seconds
Resize
Dub Language
```

Không nên coi tất cả là creative hoàn toàn khác.

Nên kết hợp:

```text
Perceptual Image Hash
+
Video Frame Embedding
+
Audio Fingerprint
+
Transcript Similarity
+
Visual Embedding
```

để tạo:

```text
CREATIVE FAMILY
```

Ví dụ:

```text
CREATIVE FAMILY #819

Original Concept
     │
     ├── Saudi Arabic Version
     ├── UAE Arabic Version
     ├── English Version
     ├── Female UGC
     ├── Male UGC
     ├── Different Hook
     └── Different CTA
```

Nhờ vậy hệ thống biết:

> Một concept đang được scale mạnh.

Thay vì chỉ đếm số video.

---

# 7. Không dùng một Win Score duy nhất

Không nên chỉ có:

```text
Win Score = 82
```

Nên dùng:

# Product Potential Vector

Ví dụ:

```text
Product X

Market Demand        64
Novelty              94
Wave Potential       91
Creative Potential   95
Market Fit           81
MKT Appeal           92
Competition          28
Economics            74
Operational Fit      77
Confidence           68
```

Kết luận có thể là:

```text
HIGH POTENTIAL TEST
```

dù sản phẩm chưa phải bestseller.

---

# 8. Novelty Score

Đánh giá độ mới và độ hiếm.

Các yếu tố:

```text
Low number of similar products
+
Low advertiser density
+
Visual uniqueness
+
Unusual mechanism
+
Unexpected use case
+
Surprise factor
+
Novel product-market combination
```

Ví dụ:

```text
Ads:                  7
Advertisers:          3
Stores:               4

Visual Uniqueness:   96
Mechanism Novelty:   92

Novelty Score:       94
```

Sản phẩm không bị loại chỉ vì:

```text
Demand Data thấp
```

---

# 9. Wave Potential Score

Đây là chỉ số đo khả năng tạo:

```text
Attention
Curiosity
Discussion
Sharing
Impulse
FOMO
Trend
```

Các thành phần:

```text
Scroll Stop Power
+
Visual Surprise
+
Demo Strength
+
Transformation Delta
+
Curiosity Gap
+
Commentability
+
Shareability
+
UGC Reproducibility
+
Emotional Trigger
+
Social Identity
```

Một sản phẩm có Wave Potential cao khi khách dễ phản ứng:

```text
"Cái gì đây?"

"Thật à?"

"Có tác dụng thật không?"

"Mua ở đâu?"

"Tôi muốn thử."
```

---

# 10. Creative Potential

Đánh giá khả năng làm Marketing Creative.

Các câu hỏi:

```text
Có nhìn thấy vấn đề ngay không?
Có demo được không?
Có Before / After không?
Kết quả có nhìn thấy bằng mắt không?

Có thể tạo Hook trong <3s không?
Có giải thích trong <15s không?

Có thể làm UGC không?
Có thể tạo 20+ concept không?
Có nhiều angle không?
Có thể tạo emotional hook không?
Có thể tạo curiosity hook không?
```

Ví dụ:

```text
Product A

Utility:              90
Creative Potential:   32
```

Có thể không phù hợp cho performance marketing.

Trong khi:

```text
Product B

Utility:              65
Creative Potential:   96
```

có thể đáng test hơn.

---

# 11. MKT Appeal Score

AI không nên là người duy nhất quyết định.

Team Marketing được vote:

```text
🔥 LOVE
👍 TEST
👀 WATCH
😐 NORMAL
👎 SKIP
```

Mỗi quyết định lưu:

```text
user
market
category
product
decision
reason
```

Ví dụ:

```text
MKT A:
LOVE

Reason:
Visual mạnh
Hook dễ
Saudi hợp
Có thể làm UGC
```

MKT B:

```text
SKIP

Reason:
Quá phổ biến
Không có angle mới
```

Sau nhiều quyết định, hệ thống tạo:

```text
MARKETING TEAM TASTE MODEL
```

Sau đó một sản phẩm mới có thể được dự đoán:

```text
MKT Appeal = 91
```

Ý nghĩa:

> Team Marketing của công ty có xác suất cao muốn test sản phẩm này.

---

# 12. Market Product DNA

Không hardcode kiểu:

```text
Saudi = mỹ phẩm
US = phong thủy
```

Mà phải để hệ thống học.

Ví dụ Market DNA:

```text
market
×
category
×
price
×
creative style
×
persona
×
season
×
offer
×
funnel
×
historical outcome
```

---

# 13. Ví dụ Market DNA — Saudi Arabia

Ví dụ hệ thống có thể học:

```text
Saudi Arabia

Visual luxury
Giftability
Beauty
Jewelry-like products
Premium appearance
Strong transformation
Arabic creative
Family / gifting
COD friendly
Female beauty segment
```

Đây chỉ là profile động.

Nó phải được cập nhật theo dữ liệu thực tế.

---

# 14. Ví dụ Market DNA — USA

Ví dụ:

```text
USA

Novelty
Gift
Home
Spiritual / symbolic
Personalization
Hobby
Problem solver
Self improvement
Pet
Convenience
```

Không coi đây là luật cố định.

Hệ thống phải tiếp tục học theo thời gian.

---

# 15. Cùng một Product, điểm khác nhau theo Market

Ví dụ:

```text
PRODUCT X
```

Saudi:

```text
Market Fit:        91
Wave Potential:    87
Creative:          94
MKT Appeal:        89

Decision:
TEST NOW
```

USA:

```text
Market Fit:        43
Wave Potential:    61
Creative:          94
MKT Appeal:        54

Decision:
WATCH / SKIP
```

UAE:

```text
Market Fit:        84

Decision:
TEST
```

Không nên có:

```text
Product X = Win
```

Mà phải là:

```text
Product X
×
Market
×
Creative
×
MKT Team
×
Timing
```

---

# 16. Compliance Risk

Nếu sản phẩm là:

```text
Imitation Jewelry
Fashion Jewelry
Gold-plated Jewelry
```

và được quảng cáo đúng bản chất thì có thể là category bình thường.

Nếu sản phẩm:

```text
Counterfeit Brand
Fake Luxury Brand
Misrepresented Product
```

thì nên đưa vào:

```text
Compliance Risk
```

Không nên coi đây là opportunity để scale.

Product Potential Vector có thể thêm:

```text
Compliance Risk
```

Ví dụ:

```text
Novelty             90
Wave                94
Creative            91
Market Fit          87

Compliance Risk     93

Decision:
REJECT / REVIEW
```

---

# 17. Opportunity Radar

Không nên chỉ:

```text
Sort by Sales
```

Nên tạo radar:

```text
                        HIGH WAVE
                            ▲
                            │
              EXPERIMENT    │    BREAKOUT
                            │
            Novel Products │   Rare Winners
                            │
 LOW MARKET ────────────────┼──────────── HIGH MARKET
 VALIDATION                 │              VALIDATION
                            │
               SKIP         │    STABLE WINNER
                            │
                            ▼
                         LOW WAVE
```

---

# 18. Breakout Opportunity

Góc lý tưởng:

```text
HIGH WAVE
+
HIGH MARKET VALIDATION
```

=>:

```text
BREAKOUT PRODUCT
```

Có thể ưu tiên:

```text
TEST NOW
SCALE FAST
WATCH COMPETITION
```

---

# 19. Experimental Opportunity

Một nhóm cực kỳ quan trọng:

```text
HIGH WAVE
+
LOW CURRENT VALIDATION
```

Đây là:

```text
EXPERIMENTAL OPPORTUNITY
```

Sản phẩm:

- Chưa nhiều người bán
- Chưa nhiều advertiser
- Chưa có nhiều sales signal
- Nhưng rất mới
- Creative mạnh
- Dễ tạo tò mò
- Có thể tạo trend

Đây chính là nhóm cần MKT xem thủ công và test nhỏ.

---

# 20. Product Decision không chỉ có Win / Lose

Các action:

```text
DISCOVER
WATCH
TEST
TEST NOW
ITERATE
SCALE
HOLD
SKIP
REVIEW
STOP
```

Ví dụ:

```text
Novelty > 85
Wave > 85
Creative > 80
Market Fit > 70

→ TEST
```

Hoặc:

```text
Wave > 90
Market validation thấp
Confidence < 60

→ EXPERIMENTAL TEST
```

Hoặc:

```text
Market Fit > 85
Internal Win > 80
Low Refusal
Good Margin

→ SCALE
```

---

# 21. End-to-End Pipeline

```text
                 AD / PRODUCT SOURCES
                         │
             ┌───────────┼───────────┐
             ↓           ↓           ↓
           APIs      Extension     Import
             │           │           │
             └───────────┼───────────┘
                         ↓
                CREATIVE VAULT
                         ↓
              PRODUCT RESOLUTION
                         ↓
                 MARKET MAPPING
                         ↓
              CREATIVE ANALYSIS
                         ↓
            ┌────────────┼────────────┐
            ↓            ↓            ↓
         NOVELTY       WAVE        MARKET FIT
            │            │            │
            └────────────┼────────────┘
                         ↓
                  MKT TASTE MODEL
                         ↓
                PRODUCT OPPORTUNITY
                         ↓
          ┌──────────────┼───────────────┐
          ↓              ↓               ↓
        WATCH           TEST            SKIP
                         ↓
                   COMPANY TEST
                         ↓
               ADS + SALES + COD
                         ↓
                ACTUAL OUTCOME
                         ↓
                  MODEL LEARNS
                         │
                         └──────→ FUTURE SELECTION
```

---

# 22. Mối liên kết với dữ liệu nội bộ

Sau khi MKT chọn product:

```text
Product Discovery
      ↓
Test Ads
      ↓
Leads
      ↓
Orders
      ↓
Confirmed
      ↓
Shipped
      ↓
Delivered
      ↓
Refused / Returned
      ↓
Profit
```

Kết quả được gửi lại:

```text
Product Intelligence Engine
```

để model học:

```text
Product nào MKT thích
+
Product nào khách thích
+
Product nào giao thành công
+
Product nào có lợi nhuận
```

---

# 23. Product Intelligence nên học 3 loại Fit

## Market Fit

```text
Sản phẩm có hợp thị trường không?
```

## MKT Fit

```text
Sản phẩm có hợp cách đánh của Marketing Team không?
```

## Company Fit

```text
Sản phẩm có hợp với Sales, COD, Logistics và Economics của công ty không?
```

Tổng thể:

```text
PRODUCT OPPORTUNITY
=
Market Fit
+
MKT Fit
+
Company Fit
+
Novelty
+
Wave Potential
+
Creative Potential
-
Competition
-
Compliance Risk
```

---

# 24. Dashboard Product Opportunity

Ví dụ:

```text
PRODUCT X
Saudi Arabia
────────────────────────────

Market Demand          67
Novelty                94
Wave Potential         92
Creative Potential     96

MKT Appeal             89
Market Fit             91

Competition            24
Saturation             18

Economics              73
Operational Fit        76

Compliance Risk         8
Confidence             69

────────────────────────────

Opportunity Score      91

Classification:
💎 EXPERIMENTAL BREAKOUT

Recommendation:
🔥 TEST NOW
```

---

# 25. Dashboard MKT Research

Marketing nên có các tab:

```text
New Discoveries

Hidden Products

High Novelty

High Wave

Creative Goldmine

Market Specific

MKT Favorites

Experimental Opportunities

Breakout Products

Recently Scaling

High Risk

Saved By Team
```

---

# 26. AI Agent

AI Agent có thể trả lời:

```text
"Tìm sản phẩm Saudi có Novelty > 80,
Wave > 85 và advertiser < 10."
```

```text
"Tìm creative có visual mạnh nhưng chưa nhiều shop chạy."
```

```text
"Cho tôi những sản phẩm team MKT đã LOVE
nhưng chưa test."
```

```text
"Product nào đang tăng nhanh ở UAE nhưng chưa xuất hiện nhiều ở Saudi?"
```

```text
"Tìm sản phẩm có Creative Potential cao
nhưng Market Validation còn thấp."
```

```text
"Cho tôi 20 Experimental Opportunity
phù hợp Saudi."
```

---

# 27. Nguyên tắc cốt lõi

App không cố tìm:

> Sản phẩm bán chạy nhất.

App phải tìm:

> **Sản phẩm nào đang tạo tín hiệu đáng chú ý, có độ mới, có chất liệu creative, có khả năng tạo sóng, phù hợp với đặc điểm từng market, phù hợp với gu và năng lực của team Marketing, và sau khi test có khả năng phù hợp với economics/vận hành của chính công ty.**

---

# 28. Kiến trúc cuối cùng

```text
                    EXTERNAL SOURCES
                          │
                          ↓
                    CONNECTOR LAYER
                          ↓
                    CREATIVE VAULT
                          ↓
                   PRODUCT ENGINE
                          ↓
           ┌──────────────┼──────────────┐
           ↓              ↓              ↓
       NOVELTY          WAVE          CREATIVE
           │              │              │
           └──────────────┼──────────────┘
                          ↓
                       MARKET
                          ↓
                    MARKET DNA
                          ↓
                     MKT TASTE
                          ↓
                 OPPORTUNITY ENGINE
                          ↓
            WATCH / TEST / SKIP / SCALE
                          ↓
                     COMPANY TEST
                          ↓
            ADS + SALES + COD + PROFIT
                          ↓
                    ACTUAL RESULT
                          ↓
                     LEARNING LOOP
                          ↓
               BETTER PRODUCT DISCOVERY
```

---

# 29. Kết luận

Phiên bản app nên được định nghĩa là:

> **Creative Intelligence + Product Discovery + Market Intelligence + Marketing Taste + Company Learning Platform**

Thay vì:

> Spy Ads Tool.

Lợi thế lớn nhất không nằm ở việc có nhiều ads nhất.

Lợi thế nằm ở khả năng:

```text
Find unusual signal
        ↓
Understand creative potential
        ↓
Understand market
        ↓
Understand Marketing Team taste
        ↓
Test
        ↓
Measure real company outcome
        ↓
Learn
        ↓
Find better products next time
```

Đó là vòng lặp tạo ra lợi thế dữ liệu riêng cho doanh nghiệp.
