# 30 nguồn Commerce + Ads có thể dùng để xây hệ thống Spy Ads / Product Intelligence miễn phí

> ⚠️ **Tài liệu nghiên cứu (lưu trữ), không phải phạm vi sản phẩm.** Từ 07/10/2026 app chỉ dùng nguồn Ads social (Meta, TikTok, Snapchat, spy tool) và nguồn hàng Trung Quốc (AliExpress; 1688/Taobao/PDD/Alibaba qua Apify). Các mục Google, Bing/Microsoft, Amazon, Walmart, eBay, Etsy, Temu, Noon, Shopee, Lazada, Mercado Libre… **không triển khai** — xem `spy_ads_product_sourcing_architecture.md` §1 (Phạm vi) và §10.1.

> Cập nhật: 06/10/2026  
> Mục tiêu: tìm các repo GitHub có thể thu thập **dữ liệu quảng cáo công khai**, **sản phẩm**, **seller**, **giá**, **sold count**, **rating**, **creative**, **trend** và các tín hiệu cạnh tranh để tích hợp vào một hệ thống Spy Ads / Winning Product riêng.

---

## 0. Cách hiểu đúng trước khi triển khai

Không phải nền tảng nào cũng có một **Ad Library** giống Meta/TikTok/Google.

Có 2 nhóm dữ liệu khác nhau:

### Nhóm A — Ad Spy trực tiếp

Có thể lấy quảng cáo/creative/advertiser từ thư viện quảng cáo công khai:

- Meta / Facebook / Instagram
- TikTok Ads
- Google / YouTube Ads
- LinkedIn Ads
- Pinterest Ads
- Snapchat Ads
- Microsoft / Bing Ads
- X / Twitter: rất hạn chế, chủ yếu EU / DSA

### Nhóm B — Commerce Spy

Các nền tảng như:

- Shopify
- Amazon
- Alibaba
- AliExpress
- Shopee
- Lazada
- Temu
- SHEIN
- Etsy
- Walmart
- Tokopedia
- Taobao
- JD
- Pinduoduo

thường không cho ta thư viện creative quảng cáo toàn cầu.

Thay vào đó ta thu thập:

```text
Product
Price
Original Price
Discount
Seller
Shop
Sold Count
Orders
Rating
Review Count
Variants
Stock
Images
Videos
Category
Ranking
Sponsored flag
Bestseller
Trend
First seen
Last seen
```

Sau đó nối sản phẩm với quảng cáo từ Meta/TikTok/Google bằng:

```text
Landing Domain
Landing URL
Product Title
Brand
Seller
Image Hash
Video Hash
OCR
Embedding
```

---

# 1. Quy ước đánh giá

| Mức | Ý nghĩa |
|---|---|
| 🟢 | Có thể self-host / dùng source trực tiếp, không bắt buộc dịch vụ trả phí |
| 🟡 | Source miễn phí nhưng thường cần free tier API / proxy / Apify / Bright Data hoặc có giới hạn |
| 🔴 | Repo cũ, cần login, hạn chế mạnh hoặc không nên dùng làm production connector chính |
| ADS | Lấy quảng cáo / advertiser / creative trực tiếp |
| COMMERCE | Lấy sản phẩm / giá / seller / sales signal |
| TREND | Lấy trend / popularity / market signal |

---

# 2. Danh sách 30 nguồn nên nghiên cứu

## 1. Shopify

**Loại:** COMMERCE  
**Mức:** 🟢

### Repo
https://github.com/zhaoheng588-tech/shopify-scout

### Có thể lấy
- Full product catalog
- Product title
- Price
- Variants
- Compare-at price
- Tags
- Images
- Vendor

### Điểm mạnh

Nhiều Shopify store expose endpoint:

```text
/products.json
```

nên có thể lấy catalog mà không cần Shopify Admin API.

### Repo bổ sung
https://github.com/Boo-n/shopify-scraper-apps-spy

Có thể nghiên cứu thêm:

- app stack
- Klaviyo
- Yotpo
- Loox
- Judge.me
- Facebook Pixel
- TikTok Pixel
- reviews
- product catalog

### Kết luận

Shopify không có Ad Library riêng.

Nên triển khai:

```text
Shopify Store Spy
      +
Meta Ads
      +
TikTok Ads
      +
Google Ads
```

---

## 2. Amazon

**Loại:** COMMERCE + Sponsored Product Signal  
**Mức:** 🟡

### Repo chính
https://github.com/scraper-bank/Amazon.com-Scrapers

### Repo nhẹ hơn
https://github.com/ChocoData-com/amazon-product-scraper

### Có thể lấy
- ASIN
- Product
- Price
- Rating
- Review count
- Category
- Sales rank
- Search ranking
- Product pages

### Ghi chú

Amazon search có sponsored placements nhưng việc lấy toàn bộ dữ liệu quảng cáo không đơn giản như Meta Ad Library.

Amazon cũng có Ads Library / transparency access tại EU, nhưng nên coi Amazon connector chính là:

```text
Product Search
+
Sales Rank
+
Sponsored Position
+
Seller
+
Price
```

---

## 3. Alibaba

**Loại:** COMMERCE / SUPPLIER INTELLIGENCE  
**Mức:** 🟢

### Repo rất đáng dùng
https://github.com/omkarcloud/alibaba-scraper

### Repo tham khảo
https://github.com/scraper-bank/Alibaba.com-Scrapers

### Có thể lấy
- Product search
- Product detail
- Wholesale price
- MOQ
- SKU
- Supplier
- Buyer reviews
- Certifications
- Supplier profile
- Quantity price ladder

### Rất phù hợp để xây

```text
Winning Product
      ↓
Find Supplier
      ↓
MOQ
      ↓
Cost
      ↓
Estimated Margin
```

---

## 4. AliExpress

**Loại:** COMMERCE / DROPSHIPPING  
**Mức:** 🟢

### Repo self-host đáng chú ý
https://github.com/jnslmk/aliexpress-mcp

### Repo API/free tier
https://github.com/omkarcloud/aliexpress-scraper

### Có thể lấy
- Search
- Product detail
- Price
- Orders
- Rating
- Seller
- Variants
- Stock
- Shipping
- Images

### Điểm mạnh của `aliexpress-mcp`

Có thể chạy như MCP server và không cần official AliExpress API key.

Rất phù hợp để nối trực tiếp vào AI Agent.

---

## 5. Shopee

**Loại:** COMMERCE  
**Mức:** 🟡

### Repo
https://github.com/DataKazKN/shopee-product-scraper-examples

### Repo bổ sung
https://github.com/ScrapingBee/shopee-scraper

### Có thể lấy
- Product
- Price
- Discount
- Shop
- Rating
- Stock
- Variants
- Sold signals

### Hạn chế

Shopee anti-bot khá mạnh.

Một số implementation chỉ hỗ trợ một market nhất định.

Không nên xây toàn bộ hệ thống phụ thuộc duy nhất vào một Shopee scraper.

---

## 6. Lazada

**Loại:** COMMERCE  
**Mức:** 🟢 / 🟡

### Repo
https://github.com/data-scrape/lazada-scraper

### Repo khác
https://github.com/omkarcloud/lazada-scraper

### Có thể lấy
- Search
- Product
- Price
- Seller
- Rating
- Review count
- Availability

### Market quan trọng

- Vietnam
- Thailand
- Philippines
- Malaysia
- Singapore
- Indonesia

---

## 7. TikTok Shop

**Loại:** COMMERCE + CREATOR + ADS SIGNAL  
**Mức:** 🟡

### Repo top product dashboard
https://github.com/bluzername/tiktok-top-products

### Repo affiliate research
https://github.com/the-ai-entrepreneur-ai-hub/tiktok-shop-scraper

### Có thể nghiên cứu
- Product
- Sales count
- Rating
- Creator
- Affiliate creator
- CTR
- CVR
- CPA
- Popularity

### Đặc biệt quan trọng

TikTok Shop nên nối với:

```text
TikTok Shop Product
        +
Creator Videos
        +
TikTok Top Ads
        +
Creative Center
```

---

## 8. Temu

**Loại:** COMMERCE / WINNING PRODUCT  
**Mức:** 🟡

### Repo
https://github.com/apivault-labs/temu-product-scraper-python

### Có thể lấy
- Price
- Original price
- Discount
- Sold count
- Rating
- Review count
- Shop
- Images
- Variants
- Category

Repo còn tính sẵn:

```text
Demand Score
Hot Product Score
Profit Margin
Trend
Risk Flags
```

### Hạn chế

Backend chính chạy qua Apify.

Free tier dùng được cho thử nghiệm nhưng không phải unlimited free.

---

## 9. SHEIN

**Loại:** COMMERCE / TREND  
**Mức:** 🟢

### Repo
https://github.com/DanielWTE/shein-scraper

### GitHub topic
https://github.com/topics/shein

### Có thể lấy
- Product URLs
- Product details
- Reviews
- Images
- Price
- Category

### Giá trị

SHEIN cực hữu ích cho:

```text
Fashion Trend
Price Trend
Creative Style
Product Velocity
```

---

## 10. eBay

**Loại:** COMMERCE  
**Mức:** 🟡

### Repo
https://github.com/scraper-bank/Ebay.com-Scrapers

### Repo price history
https://github.com/data-scrape/ebay-price-scraper

### Có thể lấy
- Search
- Product
- Category
- Price
- Sold item history
- Seller
- Active listings
- Historical pricing

### Giá trị

`sold item history` rất hữu ích để đánh giá nhu cầu thực tế.

---

## 11. Walmart Marketplace

**Loại:** COMMERCE  
**Mức:** 🟢

### Repo
https://github.com/ChocoData-com/walmart-product-scraper

### Có thể lấy
- Product
- Price
- Brand
- Rating
- Reviews
- Seller
- Specifications
- Images
- GTIN
- Stock signal

Repo có free local script đọc dữ liệu từ `__NEXT_DATA__`.

---

## 12. Etsy

**Loại:** COMMERCE  
**Mức:** 🟡

### Repo
https://github.com/scraper-bank/Etsy.com-Scrapers

### Có thể lấy
- Product category
- Product data
- Search
- Price
- Listing
- Shop signal

### Giá trị

Rất phù hợp cho:

```text
US Handmade
Gift
Home Decor
Jewelry
Personalization
```

---

## 13. Mercado Libre

**Loại:** COMMERCE  
**Mức:** 🟢 / 🟡

### Multi-platform repo
https://github.com/BrenoFariasdaSilva/E-Commerces-WebScraper

### Repo API examples
https://github.com/bondvit/mercadolibre-scraper-examples

### Có thể lấy
- Price
- Seller
- Sold quantity
- Rating
- Reviews
- Shipping
- Location
- Product listing

### Market
- Brazil
- Mexico
- Argentina
- Chile
- Colombia

---

## 14. Flipkart

**Loại:** COMMERCE  
**Mức:** 🟢

### Repo
https://github.com/kayden-vs/flipkart-scraper

### Có thể lấy
- Product search
- Discounts
- Price
- Historical deal signals
- Alerts

### Repo khác
https://github.com/SirMist/flipkart-advanced-product-scraper

Actor/API based.

---

## 15. Rakuten

**Loại:** COMMERCE  
**Mức:** 🟡

### Repo
https://github.com/luminati-io/rakuten-price-tracker

### Có thể lấy
- Product
- Price
- Keyword discovery
- Category discovery
- Price monitoring

### Hạn chế

Implementation hiện tại phụ thuộc Bright Data service.

---

## 16. Coupang

**Loại:** COMMERCE  
**Mức:** 🟢 / 🔴

### Repo
https://github.com/diakes/coupang_crawler_python

### Công nghệ
- Selenium
- BeautifulSoup

### Dữ liệu
- Product
- Price
- Listing

### Lưu ý

Nên benchmark lại trước khi dùng production vì Coupang thay đổi anti-bot thường xuyên.

---

## 17. Tokopedia

**Loại:** COMMERCE  
**Mức:** 🟢

### Repo nên dùng
https://github.com/hilmiazizi/tokopaedi

### Có thể lấy
- Product search
- Product detail
- Reviews
- Price
- Seller
- Shop type
- Rating

### Repo hosted khác
https://github.com/logiover/tokopedia-product-scraper

---

## 18. Taobao

**Loại:** COMMERCE  
**Mức:** 🔴

### Repo
https://github.com/YWJCJ/taobao

### Có thể lấy
- Search
- Product
- Price
- Sales
- Analysis

### Hạn chế lớn

Repo yêu cầu manual login để lấy một số dữ liệu.

Do đó không phù hợp connector anonymous production.

---

## 19. Tmall

**Loại:** COMMERCE  
**Mức:** 🟡

### Repo
https://github.com/luminati-io/tmall-price-tracker

### Có thể lấy
- Product
- Price
- Keyword
- Category
- Price history

### Hạn chế

Phụ thuộc Bright Data API.

---

## 20. JD.com

**Loại:** COMMERCE  
**Mức:** 🟡

### Repo
https://github.com/JeremyDong22/JD_Price_Crawler

### Repo khác
https://github.com/rfyiamcool/jd_product_spider

### Có thể lấy
- Product
- Price
- Seller
- Product detail
- Search
- Price variations

### Hạn chế

JD có anti-bot/login challenge.

---

## 21. Pinduoduo

**Loại:** COMMERCE  
**Mức:** 🔴

### Repo
https://github.com/Northxw/Pinduoduo

### Multi-market monitoring repo
https://github.com/yooooooiiiiiillllll-jpg/ecommerce-monitor

### Có thể lấy
- Search
- Product
- Price
- Sales signal

### Hạn chế

Nhiều repo public hiện khá cũ hoặc cần cập nhật protocol.

Không nên chọn làm connector đầu tiên.

---

# NHÓM AD LIBRARY / CREATIVE INTELLIGENCE

## 22. Meta — Facebook + Instagram

**Loại:** ADS  
**Mức:** 🟢

### Repo cực nên dùng
https://github.com/promisingcoder/MetaAdsCollector

### Có thể lấy
- Advertiser
- Ad text
- Headline
- CTA
- Images
- Videos
- Platforms
- Active ads
- Start date
- Page
- Media download
- State / incremental collection

### Điểm mạnh

Không cần official Meta API key theo thiết kế của repo.

### Repo khác
https://github.com/kalilfagundes/meta-ads-competitor-tracker

Có sẵn:

```text
competitor monitor
archive creative
long-running winner detection
```

---

## 23. TikTok Ads

**Loại:** ADS + TREND  
**Mức:** 🟢 / 🟡

### TikTok Ad Library
https://github.com/CheckFirstHQ/Tikadrchivist

### Creative Center
https://github.com/techgokdeniz/tiktok-creative-center-api

### Multi-platform CLI rất mạnh
https://github.com/ifccod/social-media-research-cli

### Có thể lấy
- Ads Library
- Advertiser
- Video
- Top Ads
- CTR
- CVR
- 2s play rate
- 6s play rate
- Keyframes
- Trending hashtag
- Trending video
- Keyword ideas

### Đây là một trong các connector quan trọng nhất.

---

## 24. Google Ads + YouTube Ads

**Loại:** ADS  
**Mức:** 🟢

### Repo tốt nhất tìm được
https://github.com/block-town/google-ads-transparency-mcp

### Repo khác
https://github.com/lionkiii/gads-transparency-mcp

### Có thể lấy
- Advertiser search
- Domain search
- Ads
- Creative
- Text ads
- Image ads
- Video ads
- First seen
- Last seen
- Creative download
- Compare advertisers

### Điểm mạnh

`block-town/google-ads-transparency-mcp` mô tả:

```text
No API key
No paid service
No browser required
```

Đây là nguồn rất đáng ưu tiên.

---

## 25. X / Twitter

**Loại:** ADS + SOCIAL  
**Mức:** 🔴 / 🟡

### Repo hỗ trợ Twitter public research
https://github.com/ifccod/social-media-research-cli

### Có thể lấy từ repo
- Tweets
- Search
- Trending
- User tweets
- User graph
- Public content

### Ads thực tế

X hiện không có một Meta-style global Ad Library mạnh.

X Ads Repository chủ yếu phục vụ DSA / quảng cáo tại EU.

Official access có giới hạn và API route có thể yêu cầu developer token.

### Kết luận

Đối với X nên dùng:

```text
X Ads Repository
+
Public Twitter Search
+
Promoted-post collection
```

chứ không kỳ vọng có nguồn quảng cáo toàn cầu như Meta.

---

## 26. LinkedIn Ads

**Loại:** ADS  
**Mức:** 🟢

### Repo
https://github.com/wukimidaire/linkedin_ads_scraper

### Multi-platform repo
https://github.com/ifccod/social-media-research-cli

### Có thể lấy
- Company
- Ads
- Ad detail
- Advertiser
- Creative
- Company posts
- Public company data

LinkedIn có public Ad Library.

Rất hữu ích cho B2B competitor research.

---

## 27. Pinterest Ads

**Loại:** ADS + COMMERCE INTENT  
**Mức:** 🟢

### Repo
https://github.com/ifccod/social-media-research-cli

### Commands hiện có

```text
pinterest_ads visual-search
pinterest_ads search-ads
pinterest_ads get-ad
pinterest_ads search-pins
pinterest_ads pin
pinterest_ads download-media
```

### Rất phù hợp với
- Home decor
- Beauty
- Fashion
- Jewelry
- Gifts
- US ecommerce

---

## 28. Snapchat Ads

**Loại:** ADS  
**Mức:** 🟢

### Repo
https://github.com/ifccod/social-media-research-cli

### Commands

```text
snapchat_ads search-ads
snapchat_ads get-ad
snapchat_ads sponsored-content
snapchat_ads search-sponsored-content
snapchat_ads download-media
```

Snap có Ads Gallery cho dữ liệu transparency.

---

## 29. Microsoft / Bing Ads

**Loại:** ADS  
**Mức:** 🟢

### Repo
https://github.com/ifccod/social-media-research-cli

### Commands

```text
microsoft_ads search-advertisers
microsoft_ads get-advertiser
microsoft_ads search-ads
microsoft_ads get-ad
```

Microsoft cũng có public Ad Library chính thức.

Rất đáng tích hợp vì ít Spy Ads tool phổ thông để ý tới Bing.

---

## 30. Xiaohongshu / RedNote

**Loại:** SOCIAL COMMERCE + CREATIVE + TREND  
**Mức:** 🟢 / 🟡

### Repo
https://github.com/ifccod/social-media-research-cli

### Có thể lấy
- Notes
- Search
- User
- Comments
- Hot list
- Related searches

### Commercial / PGY signals

```text
pgy-good-case-classes
pgy-good-notes
pgy-good-lives
pgy-top-bloggers
pgy-industries
```

Đây là nguồn cực đáng nghiên cứu cho:

- Beauty
- Cosmetics
- Fashion
- Lifestyle
- China trend
- Product seeding

---

# 3. Những repo đáng clone trước nhất

Nếu mục tiêu là build app thực tế, không cần clone cả 30 ngay.

## Tier S — Nên thử trước

### Ads

```text
promisingcoder/MetaAdsCollector
CheckFirstHQ/Tikadrchivist
techgokdeniz/tiktok-creative-center-api
block-town/google-ads-transparency-mcp
ifccod/social-media-research-cli
wukimidaire/linkedin_ads_scraper
```

### Commerce

```text
zhaoheng588-tech/shopify-scout
omkarcloud/alibaba-scraper
jnslmk/aliexpress-mcp
hilmiazizi/tokopaedi
ChocoData-com/walmart-product-scraper
scraper-bank/Amazon.com-Scrapers
data-scrape/lazada-scraper
```

---

# 4. Kiến trúc mình khuyên dùng

```text
                         ┌─────────────────────┐
                         │     SOURCE LAYER    │
                         └──────────┬──────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         │                          │                          │
         ▼                          ▼                          ▼

   AD LIBRARIES               MARKETPLACES              SOCIAL/TREND
   ------------               ------------              ------------
   Meta                       Shopify                   TikTok Trends
   TikTok                     Amazon                    Pinterest
   Google                     Alibaba                   Xiaohongshu
   LinkedIn                   AliExpress                X
   Pinterest                  Shopee                    Snapchat
   Snapchat                   Lazada
   Bing                       Temu
                              SHEIN
                              Etsy
                              Walmart
                              ...

         └──────────────────────────┼──────────────────────────┘
                                    ▼
                         RAW DATA STORAGE
                                    ↓
                         NORMALIZATION ENGINE
                                    ↓
                         ENTITY RESOLUTION
                                    ↓
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼

    PRODUCT MATCH              BRAND MATCH                 MEDIA MATCH
 title / SKU / URL          domain / seller               pHash / video
        │                           │                           │
        └───────────────────────────┼───────────────────────────┘
                                    ▼
                             PRODUCT GRAPH
                                    ↓
                           AI ANALYSIS ENGINE
                                    ↓
             ┌──────────────────────┼───────────────────────┐
             ▼                      ▼                       ▼

        Winning Score          Creative Score          Market Fit
        Margin Score           Hook Score              Country Fit
        Trend Score            CTA Score               MKT Fit
        Competition            Longevity               Audience Fit
```

---

# 5. Schema chuẩn nên normalize

## ads

```text
platform
ad_id
advertiser_id
advertiser_name
brand
country
start_date
last_seen
active
ad_text
headline
cta
landing_url
image_urls[]
video_urls[]
creative_type
impression_range
spend_range
source_url
raw_json
```

## products

```text
platform
product_id
title
brand
seller_id
seller_name
product_url
price
original_price
currency
discount_pct
sold_count
orders_count
rating
review_count
stock
category
images[]
videos[]
first_seen
last_seen
is_sponsored
rank
raw_json
```

## product_ad_links

```text
product_id
ad_id
match_type
match_score
landing_url_score
title_similarity
image_similarity
brand_similarity
manual_verified
```

---

# 6. Cách nối Ads với E-commerce product

## Method 1 — Landing URL

```text
Meta Ad
   ↓
https://store.com/products/portable-blender
   ↓
Shopify
   ↓
Product ID
```

Độ chính xác cao nhất.

---

## Method 2 — Domain

```text
Advertiser:
beautystore.com

↓ Shopify scanner

beautystore.com/products.json
```

---

## Method 3 — Product title similarity

Ví dụ:

```text
Ad:
"Portable Mini Blender"

AliExpress:
"Portable USB Rechargeable Mini Blender"

Similarity = 0.91
```

---

## Method 4 — Image pHash

```text
Ad Image
    ↓
pHash
    ↓
AliExpress / Temu / Amazon Images
```

Nếu hash gần nhau:

```text
same product
```

---

## Method 5 — Video frame matching

```text
TikTok Ad Video
      ↓
Extract Keyframes
      ↓
CLIP/Image Embedding
      ↓
Search Product Images
```

---

# 7. Winning Product Score đề xuất

```text
WinningScore =
    0.18 * AdLongevity
  + 0.15 * AdVelocity
  + 0.12 * CreativeVariants
  + 0.15 * MarketplaceSales
  + 0.10 * RatingScore
  + 0.10 * TrendMomentum
  + 0.10 * MarginPotential
  + 0.05 * SupplierAvailability
  + 0.05 * MarketFit
```

---

# 8. Không nên đánh đồng "miễn phí"

Có 3 loại:

## 100% local/self-host

Ví dụ:

```text
Shopify Scout
Alibaba Scraper
AliExpress MCP
MetaAdsCollector
Google Ads Transparency MCP
Tikadrchivist
Tokopaedi
```

Đây là nhóm đáng ưu tiên nhất.

## Free source + free tier infrastructure

Ví dụ:

```text
Temu Apify
Shopee Apify
Bright Data examples
ScrapeOps based scrapers
```

Code miễn phí nhưng chạy lớn có thể tốn tiền proxy/API.

## Repo chỉ là wrapper / documentation

Một số GitHub repo quảng bá scraper nhưng code crawler thật nằm ở:

```text
Apify Actor
Bright Data
ScrapingBee
private backend
```

Không nên nhầm nhóm này với open-source scraper thực sự.

---

# 9. Ưu tiên triển khai cho app của bạn

## Phase 1 — Ads Core

```text
Meta
TikTok
Google
LinkedIn
Pinterest
Snapchat
Microsoft
```

## Phase 2 — Commerce Core

```text
Shopify
Amazon
Alibaba
AliExpress
Shopee
Lazada
TikTok Shop
Temu
SHEIN
Walmart
Etsy
```

## Phase 3 — Product Matching

```text
Ad
 ↓
Landing URL
 ↓
Store
 ↓
Product
 ↓
Supplier
 ↓
Marketplace comparison
```

## Phase 4 — AI scoring

```text
Why competitor chose it
What competitor is doing well
What is missing
What market it fits
What MKT person it fits
Whether to test
How much margin is possible
Creative angle
Hook
CTA
Trend
Risk
```

---

# 10. Kết luận

Không cần tìm một tool duy nhất có thể spy mọi nơi.

Hướng tốt nhất là xây:

```text
Universal Commerce Intelligence Layer
```

gồm nhiều connector:

```text
META CONNECTOR
TIKTOK CONNECTOR
GOOGLE CONNECTOR
SHOPIFY CONNECTOR
AMAZON CONNECTOR
ALIBABA CONNECTOR
ALIEXPRESS CONNECTOR
SHOPEE CONNECTOR
LAZADA CONNECTOR
TEMU CONNECTOR
SHEIN CONNECTOR
PINTEREST CONNECTOR
LINKEDIN CONNECTOR
SNAPCHAT CONNECTOR
BING CONNECTOR
...
```

Sau đó mọi connector trả về schema chung:

```text
Ads
Products
Sellers
Creatives
Metrics
Trends
```

và đẩy vào một pipeline:

```text
CRAWL
  ↓
NORMALIZE
  ↓
DEDUP
  ↓
ENTITY MATCH
  ↓
PRODUCT GRAPH
  ↓
AI ANALYSIS
  ↓
WINNING SCORE
  ↓
MARKETER PERSONALIZATION
  ↓
SPY ADS APP
```

Đây là hướng có khả năng mở rộng tốt hơn nhiều so với việc cố phụ thuộc vào Minea, PipiAds hoặc một API duy nhất.

---

## Nguồn GitHub chính được nhắc trong tài liệu

- https://github.com/zhaoheng588-tech/shopify-scout
- https://github.com/Boo-n/shopify-scraper-apps-spy
- https://github.com/scraper-bank/Amazon.com-Scrapers
- https://github.com/ChocoData-com/amazon-product-scraper
- https://github.com/omkarcloud/alibaba-scraper
- https://github.com/scraper-bank/Alibaba.com-Scrapers
- https://github.com/jnslmk/aliexpress-mcp
- https://github.com/omkarcloud/aliexpress-scraper
- https://github.com/DataKazKN/shopee-product-scraper-examples
- https://github.com/ScrapingBee/shopee-scraper
- https://github.com/data-scrape/lazada-scraper
- https://github.com/omkarcloud/lazada-scraper
- https://github.com/bluzername/tiktok-top-products
- https://github.com/the-ai-entrepreneur-ai-hub/tiktok-shop-scraper
- https://github.com/apivault-labs/temu-product-scraper-python
- https://github.com/DanielWTE/shein-scraper
- https://github.com/scraper-bank/Ebay.com-Scrapers
- https://github.com/data-scrape/ebay-price-scraper
- https://github.com/ChocoData-com/walmart-product-scraper
- https://github.com/scraper-bank/Etsy.com-Scrapers
- https://github.com/BrenoFariasdaSilva/E-Commerces-WebScraper
- https://github.com/bondvit/mercadolibre-scraper-examples
- https://github.com/kayden-vs/flipkart-scraper
- https://github.com/SirMist/flipkart-advanced-product-scraper
- https://github.com/luminati-io/rakuten-price-tracker
- https://github.com/diakes/coupang_crawler_python
- https://github.com/hilmiazizi/tokopaedi
- https://github.com/YWJCJ/taobao
- https://github.com/luminati-io/tmall-price-tracker
- https://github.com/JeremyDong22/JD_Price_Crawler
- https://github.com/rfyiamcool/jd_product_spider
- https://github.com/Northxw/Pinduoduo
- https://github.com/yooooooiiiiiillllll-jpg/ecommerce-monitor
- https://github.com/promisingcoder/MetaAdsCollector
- https://github.com/kalilfagundes/meta-ads-competitor-tracker
- https://github.com/CheckFirstHQ/Tikadrchivist
- https://github.com/techgokdeniz/tiktok-creative-center-api
- https://github.com/block-town/google-ads-transparency-mcp
- https://github.com/lionkiii/gads-transparency-mcp
- https://github.com/wukimidaire/linkedin_ads_scraper
- https://github.com/ifccod/social-media-research-cli

---

## Lưu ý sử dụng

Chỉ nên thu thập dữ liệu công khai và tuân thủ điều khoản của từng nền tảng, robots rules, rate limits và quy định pháp luật liên quan. Không nên thiết kế hệ thống dựa vào việc đánh cắp session, cookie, tài khoản hoặc vượt paywall/private API.
