# Tổng hợp phương hướng triển khai App Spy Ads đa nguồn

## 1. Mục tiêu hệ thống

Mục tiêu không phải là để App Spy tự đi crawl từng website, mà tách riêng một lớp thu thập dữ liệu dùng chung.

Kiến trúc tổng thể:

```text
Meta Ads
Pipiads
Minea
BigSpy
AdSpy
TikTok
Google
Các nguồn khác
    ↓
SOURCE CONNECTORS
    ↓
UNIFIED SPY COLLECTOR
    ↓
RAW STORAGE
    ↓
NORMALIZER
    ↓
DEDUP / CLUSTER / ANALYTICS
    ↓
DATABASE
    ↓
BACKEND API
    ↓
SPY APP
```

Nguyên tắc quan trọng:

> App chỉ đọc dữ liệu đã được gom và chuẩn hóa.  
> App không trực tiếp phụ thuộc vào giao diện hoặc API của từng spy tool.

Nhờ vậy sau này thêm nguồn mới chỉ cần viết thêm connector, không phải sửa toàn bộ app.

---

# 2. Vai trò của MasterVIPKey

Không nên coi MasterVIPKey là nguồn dữ liệu trung tâm.

Nếu MasterVIPKey là dạng công cụ giúp truy cập nhiều dịch vụ spy/premium thì nên xem nó là:

```text
ACCESS LAYER
```

chứ không phải:

```text
DATA LAYER
```

Tức là:

```text
MasterVIPKey
    ↓
Truy cập được tool
    ↓
Browser / Collector
    ↓
Extract dữ liệu
    ↓
Unified Collector
```

Không nên xây hệ thống theo mô hình:

```text
MasterVIPKey
    ↓
Lấy cookie/session của tất cả tool
    ↓
Reverse private API
    ↓
Crawl toàn bộ dữ liệu
```

Vì mô hình này:

- phụ thuộc session
- dễ bị logout
- dễ bị khóa
- private endpoint có thể đổi bất kỳ lúc nào
- khó scale
- khó bảo trì
- có rủi ro vi phạm điều khoản sử dụng

MasterVIPKey nếu có thì chỉ nên là một nguồn truy cập phụ cho Browser Connector.

---

# 3. Không có một “master key” hợp pháp để lấy toàn bộ dữ liệu thị trường

Không nên kỳ vọng một tool duy nhất có thể:

- truy cập mọi app miễn phí
- truy cập mọi app trả phí
- lấy toàn bộ dữ liệu
- tự động có API cho tất cả nguồn

App miễn phí không đồng nghĩa với việc có public API.

Mỗi nguồn sẽ rơi vào một trong các nhóm:

```text
1. Official API
2. Enterprise API
3. Export CSV / Excel / JSON
4. Browser Automation
5. Webhook
6. Public data endpoint được phép sử dụng
```

Hệ thống phải hỗ trợ tất cả các kiểu này thông qua Connector Layer.

---

# 4. Cách lấy dữ liệu theo từng nguồn

## Meta Ads

Ưu tiên:

```text
Meta API
    ↓
MetaConnector
```

Nếu cần dữ liệu public ngoài phạm vi API thì xây Collector riêng cho phần dữ liệu được phép truy cập.

Dữ liệu nên chuẩn hóa:

```text
ad_id
page_id
page_name
country
platform
ad_text
creative_url
landing_page
start_date
end_date
active_status
snapshot_url
```

---

## Pipiads

Ưu tiên:

```text
Pipiads API
    ↓
PipiadsConnector
```

Nếu tài khoản hiện tại chưa có API:

```text
Pipiads Export
    ↓
File Watcher
    ↓
ExportConnector
```

Browser automation chỉ nên là phương án dự phòng.

---

## Minea

Nếu không có public API phù hợp:

```text
Minea
    ↓
Export
hoặc
Browser Automation
    ↓
MineaConnector
```

Nên ưu tiên:

```text
Export > Browser Automation
```

Nếu có Enterprise API về sau thì thay connector mà không ảnh hưởng phần còn lại.

---

## Các nguồn khác

Ví dụ:

```text
BigSpy
AdSpy
TikTok
Google
ShopHunter
WinningHunter
Dropispy
...
```

Mỗi nguồn chỉ cần implement một adapter riêng.

---

# 5. Unified Spy Collector

Đây là thành phần quan trọng nhất.

Tên service đề xuất:

```text
spy-collector-service
```

Nhiệm vụ:

```text
authenticate
fetch
extract
map
send to ingest
health check
retry
rate limit
```

Không xử lý:

```text
win_score
trend_score
market_score
product_score
```

Các phần này phải nằm ở Analytics Engine.

---

# 6. Base Connector Interface

Tất cả connector nên implement cùng một interface.

Ví dụ:

```python
class BaseConnector:

    def authenticate(self):
        pass

    def fetch_ads(self, params):
        pass

    def fetch_products(self, params):
        pass

    def fetch_creatives(self, params):
        pass

    def normalize(self, raw_data):
        pass

    def health_check(self):
        pass
```

Các implementation:

```text
MetaConnector
PipiadsConnector
MineaConnector
BigSpyConnector
TikTokConnector
GenericWebConnector
```

---

# 7. Các loại Connector nên hỗ trợ

Hệ thống nên có tối thiểu 4 loại.

## 7.1 APIConnector

Dùng khi nguồn có API.

```text
API
↓
JSON
↓
Connector
↓
Ingest
```

Ví dụ:

```text
Meta
Pipiads Enterprise API
TikTok API
```

---

## 7.2 ExportConnector

Dùng cho nguồn hỗ trợ export.

```text
CSV / XLSX / JSON
        ↓
Watch Folder
        ↓
ExportConnector
        ↓
Normalize
```

Folder ví dụ:

```text
/data/import/pipiads/
/data/import/minea/
/data/import/meta/
```

---

## 7.3 BrowserConnector

Dùng khi không có API phù hợp nhưng tài khoản công ty có quyền truy cập.

Có thể sử dụng:

```text
Playwright
Apify
Browse AI
```

Luồng:

```text
Open browser
↓
Login
↓
Set filter
↓
Pagination / Scroll
↓
Extract
↓
Convert JSON
↓
Ingest
```

Không nên phụ thuộc vào private API hoặc session không ổn định nếu có lựa chọn khác.

---

## 7.4 WebhookConnector

Dùng khi dịch vụ ngoài có thể push dữ liệu.

Ví dụ:

```http
POST /connector/webhook/pipiads
```

---

# 8. Common Data Contract

Đây là schema chung cho tất cả nguồn.

Ví dụ:

```json
{
  "source": "pipiads",
  "source_ad_id": "123456",

  "platform": "facebook",
  "country": "SA",

  "advertiser": "ABC Store",
  "page_name": "ABC",

  "ad_text": "...",

  "creative_type": "video",
  "creative_url": "...",

  "landing_page": "...",

  "product_name": "...",
  "product_url": "...",

  "first_seen": "2026-10-01",
  "last_seen": "2026-10-05",

  "active": true,

  "engagement": {
    "likes": 500,
    "comments": 20,
    "shares": 10
  },

  "raw_source": {}
}
```

Nguồn nào không có field thì để:

```text
null
```

Không tự suy đoán hoặc bịa dữ liệu.

---

# 9. Ingest API chung

Tất cả connector nên đẩy dữ liệu về một endpoint chung.

Ví dụ:

```http
POST /ingest/ads
POST /ingest/products
POST /ingest/creatives
```

Ví dụ flow:

```text
PipiadsConnector
     ↓

MineaConnector
     ↓

MetaConnector
     ↓

POST /ingest/ads
     ↓
Raw Storage
```

---

# 10. Connector Factory

Nên có một factory trung tâm.

```python
class ConnectorFactory:

    connectors = {
        "meta": MetaConnector,
        "pipiads": PipiadsConnector,
        "minea": MineaConnector
    }

    @staticmethod
    def get(source):
        connector = ConnectorFactory.connectors[source]
        return connector()
```

Sau này thêm:

```python
"bigspy": BigSpyConnector
```

là đủ.

---

# 11. Raw Data Layer

Phải giữ cả:

```text
RAW DATA
+
NORMALIZED DATA
```

Ví dụ:

```text
/raw/pipiads/2026-10-05/data.json
/raw/minea/2026-10-05/data.json
/raw/meta/2026-10-05/data.json
```

Lợi ích:

- audit nguồn dữ liệu
- reprocess lại khi schema đổi
- không cần crawl lại
- có thể bổ sung field mới sau này
- dùng cho AI training
- xử lý bug normalize

---

# 12. Normalizer

Sau khi nhận raw data:

```text
Raw Data
   ↓
Field Mapping
   ↓
Country Normalize
   ↓
Platform Normalize
   ↓
Date Normalize
   ↓
URL Normalize
   ↓
Creative Normalize
   ↓
Normalized Record
```

---

# 13. Dedup Engine

Cùng một quảng cáo hoặc sản phẩm có thể xuất hiện trên nhiều nguồn.

Ví dụ:

```text
Pipiads thấy Product A
Minea thấy Product A
Meta thấy Product A
```

Không nên coi đó là 3 sản phẩm.

Cần:

```text
creative_hash
landing_page_hash
product_cluster_id
advertiser_match
image/video similarity
text similarity
```

Kết quả:

```text
Product A
  ├─ Meta Ads
  ├─ Pipiads
  └─ Minea
```

---

# 14. Product / Creative Clustering

Sau dedup:

```text
Ads
 ↓
Creative Cluster
 ↓
Product Cluster
 ↓
Advertiser Cluster
```

Mục tiêu:

- biết cùng sản phẩm có bao nhiêu creative
- bao nhiêu advertiser đang chạy
- bao nhiêu thị trường
- sản phẩm bắt đầu xuất hiện từ khi nào
- số nguồn cùng detect sản phẩm

---

# 15. Analytics Engine

Connector không tính score.

Analytics Engine xử lý:

```text
trend_score
win_score
market_score
novelty_score
competition_score
creative_velocity
advertiser_growth
market_velocity
```

Ví dụ tiêu chí:

```text
số ads active
số creative mới
số advertiser
days_running
country expansion
cross-source appearance
ad velocity
creative velocity
```

Sau này có thể kết hợp dữ liệu nội bộ:

```text
sales conversion
delivery success
return rate
failed delivery
profit margin
AOV
COD confirmation rate
```

để Win Score chính xác hơn.

---

# 16. Storage

Không nên chỉ dùng một file Excel làm data chính.

## Raw

```text
S3 / MinIO
```

lưu:

```text
JSON
HTML snapshot
CSV
Parquet
creative metadata
```

---

## Main Database

Khuyến nghị:

```text
PostgreSQL
```

cho:

```text
products
advertisers
sources
connectors
settings
users
markets
product_clusters
```

---

## Analytics Database

Khi dữ liệu ads lớn:

```text
ClickHouse
```

dùng cho:

```text
ads
events
daily metrics
trend calculation
cross-market analytics
```

---

## Parquet

Dùng cho:

```text
backup
offline analytics
AI training
data lake
export
```

Không nên coi Parquet là database chính của app.

---

# 17. Job Queue

Không để frontend trigger crawler trực tiếp.

Nên dùng:

```text
Redis + Celery
```

hoặc:

```text
Redis + BullMQ
```

Luồng:

```text
Scheduler
↓
Create Job
↓
Queue
↓
Worker
↓
Connector
↓
Raw Storage
↓
Normalizer
```

Job ví dụ:

```text
pipiads_sync_sa
pipiads_sync_us
minea_sync_fr
meta_sync_sa
```

---

# 18. Scheduler

Có thể dùng:

```text
n8n
```

nhưng n8n không nên là crawler chính.

Vai trò của n8n:

```text
08:00
↓
trigger Pipiads
↓
trigger Meta
↓
trigger Minea
↓
chờ hoàn tất
↓
run aggregation
↓
run scoring
↓
refresh app
```

Logic crawl vẫn nằm trong Connector Service.

---

# 19. Rate Limit và Retry

Mỗi nguồn phải có config riêng.

Ví dụ:

```yaml
pipiads:
  requests_per_minute: 20
  retry: 3

meta:
  requests_per_minute: 50
  retry: 5

minea:
  concurrency: 2
```

Khi gặp:

```text
429 Too Many Requests
```

thì dùng:

```text
Retry-After
Exponential Backoff
Queue Delay
```

---

# 20. Credential Manager

Không lưu trực tiếp:

```text
API_KEY
username
password
cookie
session
```

trong source code.

Ban đầu:

```text
.env
```

Production:

```text
AWS Secrets Manager
HashiCorp Vault
GCP Secret Manager
Azure Key Vault
```

---

# 21. Connector Health Check

App admin nên có màn hình:

```text
Meta        🟢 Healthy
Pipiads     🟢 Healthy
Minea       🟡 Slow
BigSpy      🔴 Auth expired
```

API:

```http
GET /connectors
GET /connectors/{source}/status
POST /connectors/{source}/test
POST /connectors/{source}/sync
```

Response ví dụ:

```json
{
  "source": "pipiads",
  "status": "healthy",
  "last_sync": "2026-10-05T16:00:00",
  "records": 1520
}
```

---

# 22. Backend API cho Spy App

Spy App chỉ gọi API nội bộ.

Ví dụ:

```http
GET /api/products/trending
GET /api/products/new
GET /api/products/{id}

GET /api/ads

GET /api/creatives

GET /api/markets

GET /api/sources

GET /api/winners

GET /api/advertisers
```

Frontend không biết Pipiads/Minea/Meta được crawl bằng cách nào.

---

# 23. Kiến trúc production đề xuất

```text
                     ┌─ Meta API
                     │
                     ├─ Pipiads API
                     │
                     ├─ Minea Browser
                     │
                     ├─ Export Sources
                     │
                     ├─ Webhook Sources
                     │
                     └─ Future Sources
                              │
                              ▼
                     CONNECTOR ADAPTERS
                              │
                              ▼
                    UNIFIED COLLECTOR API
                              │
                              ▼
                         JOB QUEUE
                              │
                              ▼
                         RAW STORAGE
                              │
                              ▼
                         NORMALIZER
                              │
                              ▼
                         DEDUP ENGINE
                              │
                              ▼
                      CREATIVE CLUSTER
                              │
                              ▼
                       PRODUCT CLUSTER
                              │
                              ▼
                       ANALYTICS ENGINE
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
            PostgreSQL                 ClickHouse
                 │                         │
                 └────────────┬────────────┘
                              ▼
                         BACKEND API
                              │
                              ▼
                           SPY APP
```

---

# 24. Stack đề xuất

```text
Connector:       Python
API:             FastAPI
Browser:         Playwright
Complex Crawler: Apify
No-code Crawler: Browse AI
Workflow:        n8n
Queue:           Redis + Celery
Raw Storage:     S3 / MinIO
Main DB:         PostgreSQL
Analytics DB:    ClickHouse
Offline Data:    Parquet
```

---

# 25. Folder structure đề xuất

```text
spy-platform/
│
├── collector/
│   ├── base.py
│   ├── factory.py
│   ├── scheduler.py
│   └── queue.py
│
├── connectors/
│   ├── meta.py
│   ├── pipiads.py
│   ├── minea.py
│   ├── bigspy.py
│   ├── export.py
│   └── generic_browser.py
│
├── ingestion/
│   ├── ads.py
│   ├── products.py
│   └── creatives.py
│
├── normalizer/
│   ├── ads.py
│   ├── products.py
│   └── urls.py
│
├── dedup/
│   ├── creative.py
│   ├── products.py
│   └── advertisers.py
│
├── analytics/
│   ├── trends.py
│   ├── win_score.py
│   ├── market_score.py
│   └── competition.py
│
├── storage/
│   ├── raw.py
│   ├── postgres.py
│   ├── clickhouse.py
│   └── parquet.py
│
└── api/
    ├── products.py
    ├── ads.py
    ├── creatives.py
    ├── sources.py
    └── connectors.py
```

---

# 26. MVP nên triển khai trước

Không cần làm tất cả nguồn ngay.

MVP:

```text
Meta
+
Pipiads
+
Minea
```

Luồng:

```text
Meta API
Pipiads API / Export
Minea Export / Browser
        ↓
Unified Collector
        ↓
Raw Storage
        ↓
Normalizer
        ↓
Dedup
        ↓
PostgreSQL
        ↓
Backend API
        ↓
Spy App
```

Sau khi ổn mới thêm:

```text
BigSpy
AdSpy
TikTok
Google
ShopHunter
...
```

---

# 27. Nguyên tắc quan trọng khi triển khai

## Connector chỉ thu thập

```text
Authenticate
Fetch
Extract
Normalize
Push
```

## Analytics mới đánh giá

```text
Trend
Winner
Novelty
Competition
Market fit
Scale potential
```

## Frontend chỉ hiển thị

```text
Search
Filter
Dashboard
Compare
Ranking
Download
Market Analysis
```

---

# 28. Kết luận

Không nên tìm một tool như MasterVIPKey để đóng vai trò:

```text
"mở toàn bộ spy tools + lấy toàn bộ database"
```

Thay vào đó nên tự xây:

```text
UNIFIED SPY COLLECTOR
```

Nó là lớp trung gian duy nhất mà App Spy cần biết.

Mô hình cuối cùng:

```text
Nhiều nguồn Spy
      ↓
Source Connector
      ↓
Unified Collector
      ↓
Raw Data
      ↓
Normalize
      ↓
Dedup / Cluster
      ↓
Analytics
      ↓
Database
      ↓
App
```

Ưu điểm:

- dễ thêm nguồn
- không phụ thuộc một tool
- không phải sửa frontend khi nguồn đổi
- dễ scale
- dễ kiểm soát lỗi
- dễ audit
- có thể kết hợp dữ liệu nội bộ Sale/Vận đơn
- có thể xây AI ranking riêng
- phù hợp để phát triển thành platform spy/product intelligence nội bộ của công ty
