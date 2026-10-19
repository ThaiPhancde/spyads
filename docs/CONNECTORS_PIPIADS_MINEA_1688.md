# Pipiads, Minea và 1688 AK — 14/10/2026

## Nhận diện nguồn

| Nguồn | Connector | Provider | Màu |
|---|---|---|---|
| Pipiads | Pipiads · Nhập file CSV/XLSX/JSON | pipiads | #11A683 |
| Pipiads API | Pipiads · API doanh nghiệp | pipiads | #11A683 |
| Minea | Minea · Nhập file CSV/XLSX/JSON | minea | #E85F2E |

Provider, adapter và nền tảng quảng cáo là ba thông tin khác nhau. Hai nguồn có thể chứa cả Meta và TikTok;
không đổi network của quảng cáo thành tên nhà cung cấp. Màu badge nguồn lấy từ connector_identity.py.

Default được nhận diện bằng adapter + source, giữ ID/config/lịch và trạng thái bật khi đổi tên. API không cho tạo
hai watcher dùng chung thư mục nguồn hoặc chuyển watcher Minea sang thư mục Pipiads đã có watcher.
Đọc file được khoá theo thư mục; token bucket của adapter generic được tách theo nguồn.

## Khả năng hiện có

- Nhập quảng cáo từ CSV/XLSX/JSON tương thích hoặc dùng mapping cột. Qua pipeline: kiểm tra nội dung,
  dedup, provenance, Product Clustering, creative và chấm điểm.
- Thư mục: `apps/api/data/import/pipiads/` và `apps/api/data/import/minea/` (hoặc DATA_DIR cấu hình).
- Chưa có file → “Chờ file”, không báo Healthy như một nguồn API đang trả dữ liệu.
- File không nhận diện được giữ lại để sửa mapping, không chuyển sang done một cách im lặng.
- Cột Platform ưu tiên theo từng hàng. Nếu thiếu, cấu hình platform mặc định hoặc nhận diện được từ CDN.
  Không mặc định Pipiads=TikTok/Minea=Meta cho dữ liệu mới. File danh sách store thuần túy không được tạo thành ad.

Ví dụ CSV:

```csv
Ad ID,Title,Platform,Advertiser,Country,Video URL,Landing Page,First Seen
123,Neck massager,TikTok,Shop A,PH,https://cdn.example/video.mp4,https://shop.example/products/massager,2026-10-01
```

Đây là ví dụ định dạng, không được nhập vào DB thật. Các trường không có trong file không được suy đoán từ nguồn.

Tại thời điểm kiểm tra: cả Pipiads và Minea có 0 ads/0 provenance trong DB; không có file chờ nhập.
Pipiads API thiếu endpoint và mapping, đang tắt. Minea chưa có cấu hình API trong project. Không thể xác nhận API tự
động từ cấu hình này. Pipiads công bố dịch vụ API cần làm việc với nhà cung cấp:
https://www.pipiads.com/pricing?tab=membership . Không tự tạo endpoint hoặc credential.

API generic đủ cấu hình chỉ báo “Đủ cấu hình”; không gọi đó là Healthy khi chưa chạy truy vấn thật.

## 1688 AK

Parser hỗ trợ object theo offerId, danh sách có offerId và JSON mã hoá thành chuỗi. Kết quả rỗng hợp lệ không bị
coi là lỗi schema; model.success=false được báo thành lỗi nghiệp vụ. Thiếu data, sai phần tử hoặc offerId sai vẫn
bị từ chối, không tạo listing giả. Phản hồi thiếu cấu trúc, không phải JSON, HTTP 429/5xx hoặc lỗi nghiệp vụ có mã
429/5xx được thử lại tối đa 3 lần, mỗi lần ký nonce mới. Lỗi xác thực và lỗi nghiệp vụ khác được báo ngay;
ưu tiên đọc lỗi nghiệp vụ trước khi kiểm tra data, kể cả model.success=false không kèm data.

Giữ kết quả các biến thể trước khi biến thể sau rỗng. Fallback Apify chỉ chạy khi được chọn; chỉ yêu cầu số lượng còn
thiếu, loại ID trùng và dừng ở giới hạn của lần tìm. Không bật fallback trả phí để che lỗi parser.

Lượt kiểm tra cuối qua `realtime.run_connector` với `gold bracelet`, limit=60: **53 offer duy nhất, 53 giá CNY**,
AK chính thức, không Apify; DB báo status=ok, health=healthy, last_sync_count=53, last_error=null.
Số lượng có thể thay đổi giữa các lượt; ba biến thể trả 20 offer/lượt, sau đó loại offerId trùng.
Một lần chạy thất bại đặt last_sync_count=0 để UI không ghép timestamp lỗi mới với số 42 từ lần thành công cũ.

Kiểm thử: 92 regression tests (connector, 1688, Hidden Winners, pricing, naming) qua; TypeScript noEmit qua.
