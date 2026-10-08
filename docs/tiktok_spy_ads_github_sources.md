
# Tổng hợp nguồn GitHub Spy TikTok Ads không cần đăng nhập

> ⚠️ Đã được thay bằng `TikTok_Ads_Strategy_V3_ToolSpy.md` (bản đang dùng). Giữ lại làm tài liệu tham khảo.

## Mục tiêu
Tìm các mã nguồn GitHub có thể lấy hoặc phân tích TikTok Ads từ nguồn công khai mà không cần đăng nhập tài khoản TikTok, ưu tiên:
- Có source code thật
- Có thể self-host
- Có thể tìm ads theo keyword/advertiser
- Có thể tải creative/video
- Có thể tích hợp vào hệ thống Spy Ads riêng
- Hạn chế phụ thuộc cookie/session TikTok

---

## 1. CheckFirstHQ/Tikadrchivist — Nguồn nên ưu tiên nhất

**GitHub:** https://github.com/CheckFirstHQ/Tikadrchivist

### Điểm mạnh
- Không cần đăng nhập TikTok
- Không cần cookie tài khoản
- Gọi trực tiếp TikTok Commercial Content / Ad Library
- Có source code rõ ràng
- Có thể self-host
- Hỗ trợ:
  - Keyword
  - Advertiser Business ID
  - Region
  - Start/End Time
  - Sort theo impression
  - Sort theo last shown date
  - Pagination
  - Xuất JSON
  - Download video creative

### Endpoint đáng chú ý
```text
POST https://library.tiktok.com/api/v1/search
```

### Vai trò trong hệ thống
```text
TikTok Public Ad Library
        ↓
Tikadrchivist
        ↓
Normalizer
        ↓
Database
        ↓
Ads Analyzer
```

### Hạn chế
TikTok Commercial Content Library chủ yếu mạnh ở dữ liệu quảng cáo được phân phối tại EEA/Europe, nên không thể xem nó như nguồn toàn bộ ads TikTok toàn cầu.

### Đánh giá
**5/5 — Nên dùng làm connector TikTok Public Ads chính.**

---

## 2. techgokdeniz/tiktok-creative-center-api — Nguồn Top Ads đáng nghiên cứu

**GitHub:** https://github.com/techgokdeniz/tiktok-creative-center-api

### Có thể lấy
- Top Ads
- Ads theo keyword
- Trending Videos
- Trending Hashtags
- Trending Songs
- Trending Creators

### Các metric hữu ích
- Impression
- CTR
- CVR
- 2-second play rate
- 6-second play rate
- Like
- Các chỉ số creative performance khác

### Endpoint liên quan
```text
top_ads/v2/list
```

### Có thể dùng để xây
```text
CTR Score
Impression Score
Hook Score
2s Retention
6s Retention
CVR Score
Creative Score
```

### Điểm mạnh
Rất phù hợp với việc tìm **Top Ads / ads hiệu suất cao**, thay vì chỉ biết ads đang tồn tại.

### Hạn chế
TikTok Creative Center hiện hạn chế dữ liệu anonymous. Người không đăng nhập có thể chỉ thấy một phần nhỏ Top Ads. Các cơ chế như web-id, signature, endpoint nội bộ cũng có thể thay đổi.

### Đánh giá
**4/5 — Rất nên reverse-engineer nhưng không nên coi là connector production duy nhất.**

---

## 3. FlowExtractAPI/tiktok-ad-library-scraper

**GitHub:** https://github.com/FlowExtractAPI/tiktok-ad-library-scraper

### Hỗ trợ
- Keyword
- Advertiser
- Region
- Date Range
- Newest / Oldest
- Popular / Unpopular
- Advertiser information
- Creative
- First shown / Last shown
- Estimated audience
- Video / images
- Targeting
- Age
- Gender
- Country
- Impression

### Điểm đáng chú ý
Repo quảng bá cơ chế:
```text
No login
No cookies
No browser automation
```

### Hạn chế
Phần implementation chính phụ thuộc Apify Actor; GitHub không phải toàn bộ crawler hoàn chỉnh.

### Cách nên sử dụng
Dùng để:
- Tham khảo schema
- Tham khảo filter
- Tham khảo output
- Học cách tổ chức dữ liệu

Không nên chọn làm codebase trung tâm.

### Đánh giá
**3/5 — Tốt để tham khảo kiến trúc/schema.**

---

## 4. at-dan/tiktok-ads-mcp

**GitHub:** https://github.com/at-dan/tiktok-ads-mcp

### Chức năng
Có các tool kiểu:
```text
search ads
get ad
search companies
```

### Điểm mạnh
- Không cần TikTok account cho competitor research
- Có kiến trúc MCP
- Dễ nối Claude/Cursor/AI Agent
- Rất phù hợp để xây lớp AI phân tích ads

### Hạn chế
Cần API bên thứ ba:
```text
SCRAPECREATORS_API_KEY
```

Tức là:
```text
Không cần TikTok login    → Có
Không cần dịch vụ ngoài  → Không
```

### Cách nên dùng
Không lấy nó làm crawler chính.

Nên lấy:
- MCP architecture
- Tool schema
- AI agent workflow
- Competitor analysis flow

Sau đó thay nguồn dữ liệu bên dưới bằng crawler của mình.

### Đánh giá
**4/5 cho AI/MCP layer.**

---

## 5. proxy-intell/tiktok-ads-library-mcp

**GitHub:** https://github.com/proxy-intell/tiktok-ads-library-mcp

### Workflow
```text
Search Ads
    ↓
Get Creatives
    ↓
Video Analysis
    ↓
Scene-by-scene Analysis
    ↓
Compare Competitors
    ↓
Creative Insight
```

### Phù hợp với
- Claude
- Cursor
- MCP Clients
- AI Ads Analyzer

### Hạn chế
Cần:
```text
SCRAPECREATORS_API_KEY
GEMINI_API_KEY
```

Do đó đây không phải crawler TikTok độc lập.

### Giá trị chính
Rất đáng tham khảo phần:
- Video analysis
- Creative analysis
- Competitor comparison
- AI summarization

### Đánh giá
**4/5 cho layer phân tích AI.**

---

## 6. Zivsteve/trendgetter — Bổ sung dữ liệu trend

**GitHub:** https://github.com/Zivsteve/trendgetter

### Không phải Ads Spy chính
Repo này thiên về:
- Trending videos
- Trending hashtags
- Creative Center trends
- Nội dung TikTok đang tăng trưởng

### Tại sao vẫn nên tích hợp
Có thể dùng để tính độ mạnh của “sóng” sản phẩm/creative:

```text
TikTok Ads
+
TikTok Trending Videos
+
Trending Hashtags
+
Trending Products
        ↓
Correlation Engine
        ↓
Potential Winning Score
```

### Đánh giá
**3/5 — Nguồn phụ trợ rất hữu ích cho Trend Score.**

---

# Bảng lựa chọn nhanh

| Repo | Không login TikTok | Spy Ads | Self-host | Vai trò |
|---|---:|---:|---:|---|
| CheckFirstHQ/Tikadrchivist | Có | Có | Có | Public Ad Library |
| techgokdeniz/tiktok-creative-center-api | Có theo code | Có | Có | Top Ads / performance |
| FlowExtractAPI/tiktok-ad-library-scraper | Có | Có | Hạn chế | Tham khảo schema |
| at-dan/tiktok-ads-mcp | Có | Có | Có | MCP / AI competitor analysis |
| proxy-intell/tiktok-ads-library-mcp | Có | Có | Có | Video + AI analysis |
| Zivsteve/trendgetter | Có | Một phần | Có | Trends / signal |

---

# Kiến trúc đề xuất cho app Spy Ads

```text
                 ┌──────────────────────────┐
                 │     TIKTOK CONNECTOR     │
                 └────────────┬─────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           │                  │                  │
           ▼                  ▼                  ▼

 TikTok Ad Library      Creative Center      TikTok Trends
 Tikadrchivist          Creative Center API  Trendgetter
       │                     │                    │
       ▼                     ▼                    ▼

 Public Ads          Top-performing Ads     Trend Signals
       │                     │                    │
       └─────────────────────┼────────────────────┘
                             ▼
                    NORMALIZATION ENGINE
                             ↓
                       PostgreSQL / D1
                             ↓
                    DUPLICATE DETECTION
                             ↓
                      MEDIA DOWNLOADER
                             ↓
                 ┌─────────────────────────┐
                 │     AI ADS ANALYZER     │
                 ├─────────────────────────┤
                 │ Hook                    │
                 │ Product                 │
                 │ Pain Point              │
                 │ Selling Point           │
                 │ CTA                     │
                 │ Creative Style          │
                 │ Competitor              │
                 │ Trend Correlation       │
                 │ Market Suitability      │
                 │ Winning Score           │
                 └───────────┬─────────────┘
                             ↓
                       SPY ADS APP
```

---

# Nên triển khai theo thứ tự

## Phase 1 — Crawl Public Ads
Dùng:
```text
CheckFirstHQ/Tikadrchivist
```

Mục tiêu:
- Search ads
- Search advertiser
- Crawl pagination
- Download media
- Lưu raw JSON
- Normalize database

## Phase 2 — Top Ads / Performance
Dùng:
```text
techgokdeniz/tiktok-creative-center-api
```

Bổ sung:
- CTR
- CVR
- Impression
- 2s play rate
- 6s play rate
- Top creatives
- Keyword discovery

## Phase 3 — Trend Layer
Dùng:
```text
Zivsteve/trendgetter
```

Tính:
```text
Trend Score
Product Momentum
Creative Momentum
Market Momentum
```

## Phase 4 — AI Analysis
Tham khảo:
```text
at-dan/tiktok-ads-mcp
proxy-intell/tiktok-ads-library-mcp
```

Xây:
```text
AI Competitor Analyzer
Creative Analyzer
Video Analyzer
Winning Product Score
Market Fit Score
```

---

# Kết luận

Nếu chỉ chọn 2 repo để bắt đầu:

```text
1. CheckFirstHQ/Tikadrchivist
2. techgokdeniz/tiktok-creative-center-api
```

Sau đó bổ sung:

```text
Trendgetter
```

để phân tích xu hướng, rồi lấy kiến trúc từ:

```text
tiktok-ads-mcp
tiktok-ads-library-mcp
```

để xây AI layer.

## Công thức hệ thống cuối

```text
Tikadrchivist
      +
Creative Center API
      +
Trendgetter
      +
AI/MCP Analyzer
      ↓
TikTok Spy Ads Engine
```

> Lưu ý: “Không cần đăng nhập” không đồng nghĩa với “có thể lấy toàn bộ TikTok Ads toàn thế giới”. TikTok vẫn giới hạn dữ liệu theo nguồn, khu vực, endpoint và mức truy cập anonymous. Vì vậy nên thiết kế hệ thống theo kiểu nhiều connector thay vì phụ thuộc một nguồn duy nhất.
