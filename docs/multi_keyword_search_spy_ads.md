# Thiết kế Multi-Keyword Search cho App Spy Ads

## 1. Mục tiêu

Sửa phần tìm kiếm sản phẩm từ mô hình:

```text
1 từ khóa = 1 truy vấn
```

thành:

```text
Multi-keyword Search + Semantic Expansion + Product Clustering
```

Mục tiêu là giúp MKT có thể nhập nhiều cách gọi khác nhau của cùng một nhóm sản phẩm để mở rộng phạm vi tìm kiếm.

Ví dụ MKT muốn tìm sản phẩm vòng tay phong thủy có thể nhập:

```text
fengshui
lucky bracelet
wealth bracelet
fortune bracelet
pixiu bracelet
prosperity bracelet
```

Hệ thống không chỉ tìm chính xác từng từ khóa, mà còn phải tìm các sản phẩm có ngữ nghĩa liên quan.

---

## 2. Giao diện nhập nhiều từ khóa

Thay vì chỉ có một ô tìm kiếm đơn giản, nên cho phép nhập nhiều keyword liên tục.

Ví dụ:

```text
[fengshui ×] [lucky bracelet ×] [pixiu bracelet ×] [+ Thêm từ khóa]
```

MKT chỉ cần:

1. Nhập keyword.
2. Nhấn `Enter`.
3. Keyword được chuyển thành một tag.
4. Tiếp tục nhập keyword khác.
5. Có thể xóa từng keyword bằng nút `×`.

Frontend có thể gửi request dạng:

```json
{
  "keywords": [
    "fengshui",
    "lucky bracelet",
    "pixiu bracelet",
    "wealth bracelet"
  ]
}
```

---

## 3. Logic Search Aggregator

Không nên search từng keyword rồi hiển thị riêng biệt.

Các keyword phải được đưa vào một tầng tìm kiếm chung:

```text
fengshui
       \
lucky bracelet ----> Search Aggregator ---> Deduplicate ---> Ranking ---> Results
       /
pixiu bracelet
       \
wealth bracelet
```

Một sản phẩm có thể xuất hiện nếu match một hoặc nhiều keyword.

---

# 4. Ba chế độ tìm kiếm

## 4.1 Broad Search — OR

Đây nên là chế độ mặc định dành cho MKT săn sản phẩm.

Ví dụ:

```text
fengshui OR lucky bracelet OR pixiu bracelet
```

Các sản phẩm sau đều có thể xuất hiện:

```text
fengshui bracelet
lucky bracelet for men
pixiu wealth bracelet
black obsidian fortune bracelet
```

Không yêu cầu phải chứa toàn bộ keyword.

---

## 4.2 Strict Search — AND

Dùng khi muốn lọc chính xác hơn.

Ví dụ:

```text
fengshui AND bracelet
```

Chỉ lấy sản phẩm thỏa mãn cả hai điều kiện.

---

## 4.3 Smart Search — Semantic / AI

Đây nên là phần quan trọng nhất.

Ví dụ MKT nhập:

```text
fengshui
lucky bracelet
```

Hệ thống tự mở rộng thành:

```text
feng shui bracelet
fortune bracelet
wealth bracelet
prosperity bracelet
good luck bracelet
pixiu bracelet
obsidian bracelet
money attraction bracelet
wealth charm
lucky charm bracelet
```

Mục tiêu là giải quyết vấn đề:

> MKT không thể biết đối thủ đang sử dụng chính xác từ khóa nào để đặt tên sản phẩm hoặc quảng cáo.

---

# 5. Không phụ thuộc hoàn toàn vào Exact Match

Ví dụ có sản phẩm:

```text
Black Obsidian Pixiu Wealth Bracelet — Attract Money & Good Fortune
```

MKT chỉ tìm:

```text
fengshui
```

Mặc dù tiêu đề không chứa chính xác chữ `fengshui`, sản phẩm vẫn nên xuất hiện vì nội dung và concept liên quan.

Do đó Search Score nên bao gồm:

```text
Search Score =
    Exact Keyword Match
  + Partial Match
  + Semantic Similarity
  + Product Category Match
  + Ad Performance Score
  + Market Relevance
```

Ví dụ:

```text
Pixiu Wealth Bracelet

Keyword match:
fengshui            0.76
lucky bracelet      0.91
wealth bracelet     0.98
fortune bracelet    0.88

Semantic score:     0.93

Search relevance:   92/100
```

---

# 6. Keyword Weight

Không phải keyword nào cũng quan trọng như nhau.

Cho phép MKT đặt trọng số:

```text
fengshui          High
lucky bracelet    High
pixiu             Medium
obsidian          Low
```

Backend:

```json
{
  "keywords": [
    {
      "text": "fengshui",
      "weight": 1.0
    },
    {
      "text": "lucky bracelet",
      "weight": 1.0
    },
    {
      "text": "pixiu",
      "weight": 0.7
    },
    {
      "text": "obsidian",
      "weight": 0.4
    }
  ]
}
```

Điều này giúp tránh kết quả bị loãng khi MKT nhập nhiều keyword.

---

# 7. Negative Keyword

Cần thêm khả năng loại trừ keyword.

Ví dụ MKT muốn tìm vòng tay phong thủy nhưng không muốn:

```text
kids
children
DIY
handmade tutorial
free pattern
```

UI:

```text
Include Keywords

fengshui
lucky bracelet
wealth bracelet
pixiu bracelet

Exclude Keywords

kids
children
DIY
handmade tutorial
free pattern
```

Query logic:

```text
(
 fengshui
 OR "lucky bracelet"
 OR "wealth bracelet"
 OR "pixiu bracelet"
)
NOT
(
 kids
 OR children
 OR DIY
)
```

---

# 8. Keyword Groups

Không nên chỉ hỗ trợ một danh sách keyword phẳng.

Cho phép tạo nhóm keyword.

Ví dụ:

## Product Type

```text
bracelet
bangle
wristband
```

## Concept

```text
fengshui
lucky
fortune
wealth
prosperity
```

## Material / Symbol

```text
pixiu
obsidian
jade
tiger eye
dragon
```

Hệ thống có thể tự kết hợp:

```text
bracelet + fengshui
bracelet + lucky
bracelet + wealth
bracelet + pixiu
bracelet + obsidian

bangle + fengshui
bangle + lucky
...
```

Ví dụ:

```text
3 Product Types
×
5 Concepts
×
5 Material/Symbols
=
75 Search Variants
```

Điều này giúp MKT mở rộng phạm vi tìm kiếm mà không phải nhập thủ công hàng chục câu.

---

# 9. Related Keyword Suggestions

Ngay khi MKT nhập:

```text
fengshui
```

App nên gợi ý:

```text
Related Keywords

+ lucky bracelet
+ feng shui bracelet
+ pixiu bracelet
+ wealth bracelet
+ fortune bracelet
+ prosperity bracelet
+ obsidian bracelet
+ lucky charm
+ wealth charm
```

Có hai thao tác:

```text
+ Add
+ Add all
```

Ví dụ UI hiển thị:

```text
Keywords: 8
Search variants: 31
Platforms: 6
Found: 2,481 ads
Unique products: 387
```

---

# 10. Search trên tất cả nguồn dữ liệu

Multi-keyword search không nên chỉ chạy trên một nguồn.

Search Aggregator cần tìm trên toàn bộ hệ thống:

```text
Meta Ads
TikTok Ads
Amazon
Shopify
Taobao
1688
AliExpress
Minea
PipiAds
Các nguồn khác
```

Kiến trúc:

```text
                   ┌─ Meta Ads
                   ├─ TikTok
Search Query ──────┼─ Amazon
                   ├─ Shopify
                   ├─ 1688
                   ├─ Taobao
                   ├─ AliExpress
                   └─ Other Sources
                         ↓
                  NORMALIZATION
                         ↓
                   DEDUPLICATION
                         ↓
                PRODUCT CLUSTERING
                         ↓
                     RANKING
```

---

# 11. Product Clustering

Một sản phẩm giống nhau xuất hiện trên nhiều nguồn không nên bị coi là nhiều sản phẩm khác nhau.

Ví dụ cùng một vòng Pixiu xuất hiện:

```text
Meta Ads        23 ads
TikTok Ads      14 ads
Amazon           9 sellers
AliExpress      31 sellers
1688             8 suppliers
```

Không nên hiển thị thành hàng chục sản phẩm riêng biệt.

Nên gom thành:

```text
BLACK OBSIDIAN PIXIU BRACELET

Ads found:            37
Stores:                9
Suppliers:            39

Platforms:
Meta
TikTok
Amazon
AliExpress
1688

Keywords matched:
fengshui
lucky bracelet
pixiu
wealth bracelet
```

Mục tiêu của app là:

> Tìm sản phẩm đang được nhiều người chạy, nhiều nguồn bán và có tín hiệu thị trường mạnh.

Không chỉ đơn thuần tìm quảng cáo chứa keyword.

---

# 12. Search Relevance và Win Score phải tách riêng

Không được dùng Search Score làm Win Score.

Một sản phẩm có thể rất đúng keyword nhưng chưa chắc đáng bán.

Ví dụ:

```text
Product:
Pixiu Wealth Bracelet

Search Match:
96/100

Win Score:
73/100
```

Search Match chỉ trả lời:

> Sản phẩm này liên quan tới truy vấn bao nhiêu?

Win Score trả lời:

> Sản phẩm này có đáng để MKT lựa chọn và triển khai hay không?

---

# 13. Ranking Formula đề xuất

Có thể sử dụng:

```text
Final Score =
    Keyword relevance       × 20%
  + Semantic relevance      × 15%
  + Number of advertisers   × 15%
  + Ad longevity            × 10%
  + Engagement              × 10%
  + Cross-platform presence × 10%
  + Product growth          × 10%
  + Market fit              × 10%
  + Priority market bonus   (+15 nếu chạy ở Philippines)
```

Ví dụ:

```text
1. Pixiu Obsidian Wealth Bracelet

Search Match: 96
Win Score: 91

Ads: 73
Advertisers: 18
Platforms:
Meta
TikTok
Amazon

Markets:
Philippines ★
US
Saudi Arabia
UAE
```

```text
2. Feng Shui Red String Bracelet

Search Match: 94
Win Score: 84
Ads: 42
```

```text
3. Tiger Eye Wealth Bracelet

Search Match: 87
Win Score: 89
Ads: 61
```

---

# 14. Search Flow hoàn chỉnh

```text
MKT nhập:

fengshui
lucky bracelet
wealth bracelet
pixiu bracelet

          ↓

Keyword Parser

          ↓

Keyword Normalization

          ↓

Synonym Expansion

          ↓

Semantic Expansion

          ↓

Keyword Combination Generator

          ↓

Search Query Generator

          ↓

┌──────────────┬───────────────┬────────────────┐
│ Meta Ads     │ TikTok Ads    │ Ecommerce      │
│              │               │                │
│              │               │ Amazon         │
│              │               │ Shopify        │
│              │               │ 1688           │
│              │               │ Taobao         │
│              │               │ AliExpress     │
└──────────────┴───────────────┴────────────────┘

          ↓

Result Aggregator

          ↓

Normalization

          ↓

Deduplication

          ↓

Product Clustering

          ↓

Search Relevance Score

          ↓

Win Score

          ↓

Market Fit Score

          ↓

MKT Personalization

          ↓

FINAL RESULTS
```

---

# 15. API đề xuất

## Search Request

```json
{
  "keywords": [
    {
      "text": "fengshui",
      "weight": 1
    },
    {
      "text": "lucky bracelet",
      "weight": 1
    },
    {
      "text": "pixiu bracelet",
      "weight": 0.8
    }
  ],
  "negativeKeywords": [
    "kids",
    "DIY"
  ],
  "mode": "smart",
  "semanticExpansion": true,
  "deduplicate": true,
  "clusterProducts": true,
  "sources": [
    "meta",
    "tiktok",
    "amazon",
    "shopify",
    "1688",
    "taobao",
    "aliexpress"
  ]
}
```

---

# 16. Search Response đề xuất

```json
{
  "query": {
    "originalKeywords": [
      "fengshui",
      "lucky bracelet",
      "pixiu bracelet"
    ],
    "expandedKeywords": [
      "wealth bracelet",
      "fortune bracelet",
      "prosperity bracelet",
      "obsidian bracelet",
      "good luck bracelet"
    ]
  },
  "summary": {
    "adsFound": 2481,
    "uniqueProducts": 387,
    "platforms": 7
  },
  "products": [
    {
      "productId": "product_001",
      "title": "Black Obsidian Pixiu Wealth Bracelet",
      "searchScore": 96,
      "winScore": 91,
      "matchedKeywords": [
        "fengshui",
        "lucky bracelet",
        "pixiu bracelet"
      ],
      "platforms": [
        "meta",
        "tiktok",
        "amazon",
        "1688"
      ],
      "adsCount": 73,
      "advertisers": 18
    }
  ]
}
```

---

# 17. Cấu trúc Database gợi ý

Có thể tạo bảng:

```text
search_keywords
search_sessions
search_keyword_groups
search_results
product_clusters
product_keyword_relations
keyword_synonyms
keyword_embeddings
```

Ví dụ:

```sql
search_keywords
---------------
id
keyword
normalized_keyword
language
category
embedding
created_at
```

```sql
product_keyword_relations
-------------------------
product_id
keyword_id
exact_score
semantic_score
final_relevance
```

---

# 18. Cache Search

Vì nhiều MKT có thể tìm keyword tương tự nhau, nên cache kết quả.

Ví dụ:

```text
fengshui
lucky bracelet
pixiu bracelet
```

Hash:

```text
search:fengshui:lucky_bracelet:pixiu
```

Cache:

```text
Redis / KV / D1 cache table
```

TTL có thể tùy nguồn:

```text
Meta Ads          1–3 giờ
TikTok Ads        1–3 giờ
Amazon            6–12 giờ
1688              12–24 giờ
Taobao            12–24 giờ
```

---

# 19. Personalization theo từng MKT

Search Ranking có thể thay đổi tùy thế mạnh của từng MKT.

Ví dụ:

```text
MKT A
Thế mạnh:
Jewelry
Gold plated
Saudi Arabia

MKT B
Thế mạnh:
Cosmetics
Beauty
UAE

MKT C
Thế mạnh:
Feng Shui
Philippines
```

Cùng tìm:

```text
lucky bracelet
```

MKT A có thể được ưu tiên:

```text
Gold-plated Pixiu Bracelet
Arabic luxury bracelet
```

MKT C được ưu tiên:

```text
Obsidian Feng Shui Bracelet
Lucky Charm Bracelet
Wealth Bracelet
```

Có thể bổ sung:

```text
Personalized Score =
    Search Relevance
  + Win Score
  + MKT Historical Success
  + Market Fit
  + Category Strength
  + Priority Market (Philippines)
```

---

# 19b. Thị trường mục tiêu & thứ tự ưu tiên

Đối tượng khách hàng cuối mà MKT nhắm tới, theo thứ tự ưu tiên:

```text
1. Philippines ★   (ưu tiên số 1 — COD, Taglish, chat Messenger)
2. Trung Đông      (SA, AE, KW, QA, OM, BH, JO, EG, IQ)
3. Mỹ
4. Châu Âu + UK
5. Úc / NZ
6. Việt Nam
+  Toàn cầu (Ad Library worldwide)
```

Áp dụng cho Search:

- Chế độ "Tất cả thị trường" search live luôn quét **PH trước**, rồi worldwide.
- Sản phẩm có ads chạy ở PH được cộng điểm ưu tiên (`PRIORITY_BOOST`, mặc định +15) → luôn đứng trên sản phẩm tương đương ở nước khác.
- Semantic Expansion cần sinh thêm biến thể Taglish cho PH, ví dụ:
  `lucky bracelet` → `pampaswerte bracelet`, `swerte bracelet`; offer: `COD nationwide`, `libreng shipping`, `order na`.
- Cấu hình: `PRIORITY_MARKETS=PH` trong `.env` (đổi được, nhiều nước cách nhau bằng dấu phẩy).

---

# 20. Phiên bản nên triển khai trước

Không cần xây toàn bộ ngay từ đầu.

## Phase 1

Triển khai:

```text
Multi-keyword input
OR Search
Negative Keyword
Deduplication
```

---

## Phase 2

Thêm:

```text
Keyword Suggestions
Synonyms
Semantic Search
Keyword Groups
```

---

## Phase 3

Thêm:

```text
Product Clustering
Cross-platform Matching
Search Score
Win Score
```

---

## Phase 4

Thêm:

```text
MKT Personalization
Market Fit
AI Keyword Expansion
Search History Learning
Automatic Keyword Discovery
```

---

# 21. Phiên bản Search đề xuất cho app

Phiên bản chính nên sử dụng:

```text
Multi-keyword Search
+
OR mặc định
+
Semantic Expansion
+
Negative Keywords
+
Keyword Groups
+
Product Clustering
+
Search Relevance Score
+
Win Score
+
MKT Personalization
```

Ví dụ MKT chỉ cần nhập:

```text
fengshui
lucky bracelet
pixiu
```

Hệ thống tự hiểu và mở rộng thành:

```text
feng shui bracelet
lucky bracelet
fortune bracelet
wealth bracelet
prosperity bracelet
pixiu bracelet
obsidian bracelet
wealth charm
money attraction bracelet
good luck bracelet
```

Sau đó:

```text
Search toàn bộ nguồn
        ↓
Gom Ads
        ↓
Gom Product
        ↓
Loại Duplicate
        ↓
Tính Search Score
        ↓
Tính Win Score
        ↓
Tính Market Fit
        ↓
Cá nhân hóa theo MKT
        ↓
Đưa ra sản phẩm đáng nghiên cứu nhất
```

---

# Kết luận

Mục tiêu của phần Search không nên chỉ là:

```text
"Tìm quảng cáo có chứa keyword"
```

mà nên chuyển thành:

```text
"Từ ý tưởng sản phẩm của MKT → tự mở rộng ngữ nghĩa → quét nhiều nguồn →
gom sản phẩm tương tự → đánh giá độ liên quan → đánh giá khả năng thắng →
xếp hạng sản phẩm đáng nghiên cứu."
```

Đây sẽ là nền tảng để app Spy Ads chuyển từ một công cụ tìm Ads đơn thuần thành một **Product Discovery Engine dành cho Marketing**.
