# Realtime & Product Fit Intelligence
## Tóm tắt kiến trúc cập nhật realtime và đánh giá sản phẩm có phù hợp với công ty hay không

---

# 1. Mục tiêu

Hệ thống không chỉ trả lời:

> Sản phẩm này đang hot hay không?

Mà phải trả lời:

> Sản phẩm này có thực sự phù hợp để công ty test, scale và kiếm lợi nhuận hay không?

Để làm được điều đó, hệ thống cần kết hợp:

```text
External Market Data
+
Internal Ads Data
+
Sales Data
+
Order Data
+
Shipping Data
+
COD / Return Data
+
Customer Feedback
+
Profitability
```

---

# 2. Kiến trúc Realtime

Không phải tất cả dữ liệu đều có thể realtime 100%.

Nên dùng kiến trúc **Hybrid Realtime**.

```text
                         DATA SOURCES
                              │
        ┌─────────────────────┼─────────────────────┐
        ↓                     ↓                     ↓
     WEBHOOK                API POLL              BATCH
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ↓
                         EVENT BUS
                              ↓
                    PROCESSING WORKERS
                              ↓
                       FEATURE ENGINE
                              ↓
                        SCORE ENGINE
                              ↓
                       ALERT ENGINE
                              ↓
                  DASHBOARD / AI AGENT
```

---

# 3. Các mức realtime

## Tier 0 — Realtime / Near Instant

Thời gian mục tiêu:

```text
< 5 giây
```

Dữ liệu:

- Order mới
- Sales thay đổi trạng thái đơn
- Customer confirmed
- Customer cancelled
- Shipment status
- COD
- Refused order
- Returned order
- Comment mới
- Internal business event

Nguồn:

```text
Webhook
Event Bus
Internal API
```

---

## Tier 1 — Near Realtime

Thời gian mục tiêu:

```text
1–5 phút
```

Dữ liệu:

- Spend
- CPA
- ROAS
- Lead
- Campaign performance
- Ad performance
- Creative performance

Nguồn:

```text
Meta Ads API
TikTok Ads API
Google Ads API
```

---

## Tier 2 — Fast Market Intelligence

Thời gian:

```text
15–60 phút
```

Dữ liệu:

- Competitor ads
- New creative
- New advertiser
- New product
- Product growth
- Creative velocity
- Market movement

Nguồn:

```text
Spy Ads Provider
Commercial API
Public Ad Sources
```

---

## Tier 3 — Market Enrichment

Thời gian:

```text
6–24 giờ
```

Dữ liệu:

- Traffic estimate
- SEO
- Search volume
- Keyword trends
- Market demand
- Long-term competitor trend

---

# 4. Realtime không có nghĩa là tính lại toàn bộ hệ thống

Không làm:

```text
New Order
    ↓
Recalculate toàn bộ database
```

Mà làm:

```text
New Order
    ↓
Identify:
product_id
market_id
campaign_id
creative_id
    ↓
Update đúng entity liên quan
```

Đây là:

```text
Incremental Computation
```

Ví dụ:

```text
Previous:

Orders = 1000
Refused = 120
Refusal Rate = 12%
```

Có thêm một đơn từ chối:

```text
Orders = 1001
Refused = 121

Refusal Rate = 12.09%
```

Chỉ update product liên quan.

---

# 5. Event Driven Architecture

Mọi thay đổi quan trọng nên trở thành event.

Ví dụ:

```text
ORDER_CREATED
ORDER_CONFIRMED
ORDER_CANCELLED

SHIPMENT_CREATED
SHIPMENT_DELIVERED
SHIPMENT_FAILED
SHIPMENT_REFUSED
SHIPMENT_RETURNED

COMMENT_CREATED
NEGATIVE_SENTIMENT_SPIKE

PRODUCT_DISCOVERED
PRODUCT_GROWTH_SPIKE
RARE_WINNER_DETECTED

AD_CREATED
AD_REJECTED

ROAS_DROP
REFUSAL_SPIKE
DELIVERY_RATE_DROP
```

---

# 6. Alert Engine

Realtime chỉ có giá trị khi hệ thống chủ động cảnh báo.

Ví dụ:

```text
🔥 Product X tăng Ad Velocity 180%
```

```text
💎 Product Y đạt Rare Winner threshold
```

```text
⚠ Product Z có COD Refusal tăng:
11% → 23%
```

```text
⚠ Negative sentiment tăng:
12% → 37%
```

```text
📉 Delivery rate giảm:
84% → 69%
```

```text
🚀 Competitor mở thêm 18 creatives mới
```

---

# 7. Realtime + Historical Snapshot

Hệ thống phải giữ cả:

```text
Current State
```

và:

```text
Historical State
```

Ví dụ:

```text
product_current_state

Win Score = 84
```

Trong khi:

```text
product_daily_snapshot

01/10 = 62
02/10 = 65
03/10 = 71
04/10 = 78
05/10 = 84
```

Nhờ vậy hệ thống biết:

```text
Growth
Acceleration
Deceleration
Trend
Anomaly
```

---

# 8. Product Fit — Sản phẩm có phù hợp với công ty không?

Đây là phần quan trọng hơn việc xác định sản phẩm có "win trên thị trường".

Cần phân biệt:

```text
Market Winner
≠
Company Winner
```

Một sản phẩm có thể đang thắng trên thị trường nhưng không phù hợp với:

- Khả năng chạy ads của công ty
- Telesales
- COD
- Logistics
- Margin
- Customer profile
- Market công ty đang vận hành
- Creative capability
- Fulfillment capability

---

# 9. Product Fit cần 2 tầng score

## External Win Score

Đánh giá sản phẩm trên thị trường.

```text
External Win Score
=
Ad longevity
+ Creative velocity
+ Advertiser growth
+ Market expansion
+ Traffic growth
+ Engagement
+ Comment sentiment
- Saturation
```

Dùng để trả lời:

> Có đáng để đưa sản phẩm này vào test không?

---

## Internal Win Score

Đánh giá sản phẩm dựa trên dữ liệu công ty.

```text
Internal Win Score
=
Profit Margin
+ ROAS
+ CPA
+ CVR
+ Confirm Rate
+ Delivery Rate
+ Low Refusal
+ Low Return
+ Customer Sentiment
```

Dùng để trả lời:

> Sản phẩm này có phù hợp với công ty không?

---

# 10. Final Product Fit Score

Ví dụ:

```text
Product chưa test:

External = 100%
Internal = 0%
```

Sau khi test ít dữ liệu:

```text
External = 60%
Internal = 40%
```

Khi đủ dữ liệu:

```text
External = 30%
Internal = 70%
```

Càng chạy lâu, dữ liệu nội bộ càng quan trọng.

---

# 11. Order Lifecycle

Muốn biết sản phẩm thực sự win hay không thì phải theo dõi toàn bộ vòng đời đơn.

```text
NEW
 ↓
CONTACTING
 ↓
CONFIRMED
 ↓
PACKED
 ↓
SHIPPED
 ↓
IN_TRANSIT
 ↓
OUT_FOR_DELIVERY
 ↓
┌──────────────┬──────────────┬──────────────┐
↓              ↓              ↓              ↓
DELIVERED     FAILED        REFUSED       RETURNED
```

Các trạng thái phụ:

```text
CANCELLED
DUPLICATE
FAKE_ORDER
UNREACHABLE
REFUNDED
```

---

# 12. Chuẩn hóa trạng thái vận chuyển

Các carrier có thể dùng status khác nhau.

Ví dụ:

```text
customer_refused
refused_by_consignee
RTO_CUSTOMER_REJECT
```

Hệ thống normalize thành:

```text
REFUSED
```

Tương tự:

```text
DELIVERED
FAILED
RETURNED
UNREACHABLE
WRONG_ADDRESS
CANCELLED
```

---

# 13. Delivery Intelligence

Ví dụ:

```text
Total shipped:        1,000

Delivered:              720
Refused:                 130
Unreachable:              50
Wrong address:            30
Failed:                   20
Still in transit:         50
```

Không tính đơn đang vận chuyển là failure.

Closed shipments:

```text
950
```

Delivery rate:

```text
720 / 950
= 75.8%
```

Failure:

```text
230 / 950
= 24.2%
```

---

# 14. Failure Reason

Không chỉ lưu:

```text
FAILED
```

Phải lưu reason:

```text
CUSTOMER_REFUSED
CUSTOMER_UNREACHABLE
WRONG_ADDRESS
CUSTOMER_CANCELLED
DUPLICATE_ORDER
FAKE_ORDER
DELIVERY_DELAY
DAMAGED_PACKAGE
PRODUCT_MISMATCH
PRICE_DISPUTE
OUT_OF_STOCK
CARRIER_FAILURE
OTHER
```

Nhờ vậy hệ thống biết:

> Sản phẩm fail vì sản phẩm, sales hay vận đơn?

---

# 15. Delivered chưa chắc là Win

Một đơn:

```text
DELIVERED
```

chưa chắc là:

```text
WIN
```

Win thật nên được tính:

```text
Revenue
-
Ads Cost
-
COGS
-
Shipping
-
COD Fee
-
Sales Commission
-
Return Cost
-
Payment Fee
=
Contribution Profit
```

Nếu:

```text
Contribution Profit > Target Margin
```

thì mới coi là:

```text
PROFITABLE ORDER
```

---

# 16. Ví dụ Product Win

```text
Revenue / order       150 SAR

COGS                   40
Ads                    35
Shipping               18
COD fee                 5
Sales commission        7

Contribution Profit    45 SAR
```

=> Có thể coi là đơn tốt.

Trong khi:

```text
Revenue                150
Total variable cost    167

Contribution Profit    -17
```

=> Delivered nhưng vẫn là:

```text
LOSS
```

---

# 17. Attribution Chain

Phải truy ngược được một đơn từ cuối funnel về đầu funnel.

```text
MARKET
   ↓
PRODUCT
   ↓
CREATIVE
   ↓
AD
   ↓
ADSET
   ↓
CAMPAIGN
   ↓
CLICK
   ↓
LEAD
   ↓
CONVERSATION
   ↓
ORDER
   ↓
SHIPMENT
   ↓
DELIVERY
   ↓
COD
   ↓
PROFIT
```

Nhờ đó mới trả lời được:

- Creative nào tạo nhiều delivered order nhất?
- Campaign nào có profit tốt nhất?
- Product nào có high ROAS nhưng high refusal?
- Market nào có delivery rate tốt nhất?
- Sales team nào confirm tốt?
- Creative nào tạo lead rẻ nhưng lead chất lượng thấp?

---

# 18. Ví dụ Ads tốt nhưng thực tế không tốt

Creative A:

```text
Spend = $1,000
Orders = 100
CPA = $10
```

Creative B:

```text
Spend = $1,000
Orders = 70
CPA = $14.3
```

Ads Manager có thể cho rằng:

```text
A tốt hơn B
```

Nhưng nếu:

```text
                 A          B

Orders          100         70
Confirmed        90         65
Delivered        45         59
Refused          35          3
Returned         10          3
```

Thì:

```text
Cost / Delivered

A = $22.22
B = $16.95
```

=> B mới là creative hiệu quả hơn.

---

# 19. Product Fit Score đề xuất

Có thể chia trọng số:

```text
Marketing       20%
Sales           15%
Confirmation    10%
Delivery        20%
Refusal         10%
Return           5%
Margin          15%
Customer         5%
```

Sau đó tạo:

```text
Company Product Fit Score
```

---

# 20. Các trạng thái Product Decision

Hệ thống nên trả về action:

```text
TEST
WATCH
HOLD
ITERATE
SCALE
STOP
```

Ví dụ:

```text
External Score > 80
Confidence > 70
Saturation < 50
→ TEST
```

```text
Internal Score > 80
Margin tốt
Refusal thấp
Delivery tốt
→ SCALE
```

```text
ROAS tốt
Refusal cao
→ HOLD
```

```text
CTR thấp
→ ITERATE CREATIVE
```

```text
CVR thấp
→ FIX PRODUCT / LANDING / OFFER
```

```text
External thấp
Internal thấp
→ STOP
```

---

# 21. Realtime Product Decision

Ví dụ ban đầu:

```text
Product X

ROAS:           3.2
Refusal:        11%
Sentiment:      78%
Internal Score: 82

Status:
SCALE
```

Sau đó realtime có thêm nhiều đơn refusal:

```text
+32 refused orders
```

Hệ thống cập nhật:

```text
Refusal:
11% → 19%

Internal Score:
82 → 74

Decision:
SCALE → WATCH
```

Alert:

```text
⚠ COD refusal increased abnormally.
```

---

# 22. Product Fit Dashboard

Một product nên có dashboard dạng:

```text
PRODUCT 001
Saudi Arabia
────────────────────────────

MARKETING

Spend                 $18,420
Leads                    3,812
Orders                   2,104
CPA                      $8.75

SALES

Confirmed                1,829
Confirmation Rate        86.9%

LOGISTICS

Shipped                  1,761
Delivered                1,397
Refused                    221
Returned                    74

Delivery Rate            79.3%
Refusal Rate             12.5%

FINANCE

Revenue               $74,821
COGS                  -$19,500
Ads                   -$18,420
Shipping               -$8,200
COD/Fees               -$2,900
Returns                -$1,400

Contribution Profit    $24,401

────────────────────────────

External Win Score          81
Internal Win Score          88
Delivery Score              79
Customer Score              84

FINAL PRODUCT FIT SCORE     85

Decision:
🟢 SCALE
```

---

# 23. Trường hợp cần HOLD

Ví dụ:

```text
ROAS                  4.1
```

Nhưng:

```text
Refusal              31%
Return               14%
Negative comment     27%
```

Kết quả:

```text
Final Score           54

Decision:
🟠 HOLD
```

Điều này tránh việc scale sản phẩm chỉ vì ROAS đẹp.

---

# 24. Connector nội bộ bắt buộc

Tối thiểu cần:

| Connector | Dữ liệu |
|---|---|
| Pancake / CRM | Lead, conversation, order, telesales |
| Ads Platforms | Campaign, ad, creative, spend |
| Shipping / ERP | Tracking, delivered, refused, returned |
| Finance / Order System | Revenue, COGS, fees, profit |

Nếu nhiều hãng vận chuyển:

```text
Shipping Connector
        │
 ┌──────┼─────────┬─────────┐
 ↓      ↓         ↓         ↓
Carrier A Carrier B Carrier C Carrier D
 │      │         │         │
 └──────┴────┬────┴─────────┘
             ↓
      NORMALIZED STATUS
```

---

# 25. Logic cuối cùng

Ứng dụng phải phân biệt:

```text
Market Winner
```

với:

```text
Company Winner
```

## Market Winner

Dựa vào:

```text
Ads
Competitors
Traffic
Growth
Comments
Demand
Saturation
```

## Company Winner

Dựa vào:

```text
Marketing
Sales
Confirm Rate
Delivery
Refusal
Return
Margin
Customer Satisfaction
Profit
```

---

# 26. Kiến trúc tổng kết

```text
                    EXTERNAL MARKET
                          │
                          ↓
                    PRODUCT DISCOVERY
                          │
                          ↓
                    EXTERNAL SCORE
                          │
                          ↓
                        TEST
                          │
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
      ADS               SALES           LOGISTICS
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ↓
                     ORDER RESULT
                          ↓
                       FINANCE
                          ↓
                 CONTRIBUTION PROFIT
                          ↓
                  INTERNAL WIN SCORE
                          ↓
                COMPANY PRODUCT FIT
                          ↓
          ┌───────────────┼───────────────┐
          ↓               ↓               ↓
        SCALE           HOLD             STOP
```

---

# 27. Kết luận

Realtime không phải mục tiêu cuối cùng.

Mục tiêu cuối cùng là:

```text
Detect change quickly
        ↓
Understand why
        ↓
Measure business impact
        ↓
Make decision
```

Và tiêu chí cuối cùng để xác định một sản phẩm phù hợp với công ty không phải chỉ là:

```text
High ROAS
```

mà là:

```text
Market Demand
+
Ads Performance
+
Sales Conversion
+
Delivery Success
+
Low Refusal
+
Low Return
+
Customer Satisfaction
+
Healthy Margin
+
Actual Profit
```

Từ đó hệ thống mới có khả năng xác định:

> **Sản phẩm nào thực sự phù hợp để công ty test, scale và duy trì lâu dài.**
