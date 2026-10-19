# Hidden Winners v3 và cải thiện giá — 14/10/2026

Hidden Winner là nhãn bổ trợ; danh sách chỉ xếp hạng bằng Opportunity v2, không còn Hidden Winner Score.

## Điều kiện

- Trong Product Cluster hiện có: 1–3 danh tính nhà quảng cáo duy nhất, không còn advertiser chưa xác định.
- Bộ lọc thị trường chọn sản phẩm có ad đang chạy ở thị trường đó; số advertiser và bằng chứng vẫn tính trên toàn bộ cluster.
- Có ít nhất một quảng cáo đang chạy ≥7 ngày.
- Khuyến nghị Opportunity v2 đã được chấm và khác SKIP; loại non_product không đạt.
- Có **momentum đã xác nhận hoặc creative replication đã xác nhận**.

Momentum: ít nhất 3 snapshot quan sát ở các ngày khác nhau, phủ ≥7 ngày, snapshot cuối không cũ hơn 2 ngày.
So sánh trên cùng tập ad đã biết và được quan sát ở đầu/cuối kỳ. Hoạt động active hoặc số biến thể phải tăng ít nhất
1 đơn vị và ≥20%, duy trì ở cả 2 snapshot cuối. Ad mới xuất hiện trong crawl không làm tăng cohort.
Snapshot cũ không có danh tính ad và thời điểm capture không được nâng thành bằng chứng. Backfill ngày quá khứ để trống
activity_evidence. Đây là quy tắc xác nhận nhãn, không tạo thêm điểm cạnh tranh với Opp.

Replication: ít nhất 2 nhóm creative khác biệt trên các ad độc lập của cùng sản phẩm. Không dùng collation_count,
số URL hoặc số bản ghi làm bằng chứng. Loại bản trùng bằng hash cảm nhận (≤6 bit), SHA, URL tài nguyên không có query,
family và CLIP đã lưu (cosine ≥0,97). Không tải model trong bước chấm nhãn. Carousel trên một ad không đủ.

UI có các nhóm đã xác nhận, chưa xác nhận/cần theo dõi và không đạt, kèm từng điều kiện và lý do.
Product detail cũng hiển thị bằng chứng; các thẻ sản phẩm có nhãn bổ trợ.

## Backfill

Chạy từ apps/api: `.venv/Scripts/python scripts/backfill_hidden_v3.py --apply`.
Script không gọi provider hoặc model, chỉ cập nhật nhãn, khởi đầu snapshot quan sát hôm nay và bỏ cache không-tìm-được-giá
cũ để nguồn miễn phí mới có thể được kiểm tra. Không sửa Opportunity và không dựng lại momentum lịch sử.

Lần backfill cuối lúc dừng worker: 1.160 Hidden Winners theo baseline luật cũ → 286 đã xác nhận theo luật mới; 1.181 cần theo dõi.
286 trường hợp đạt bằng replication, chưa có momentum đủ lịch sử quan sát. Số lượng có thể thay đổi khi dữ liệu/khuyến nghị cập nhật; không đặt mục tiêu số lượng cố định.
Lưu so sánh tại `hidden_winners_v3_comparison.json`; baseline tại `hidden_winners_before.json`.

## Dữ liệu chưa có nguồn

Ẩn Traffic, Traffic growth, Search trend, Keyword competition trên màn hình chi tiết và bảng cửa hàng.
Ẩn thành phần không có nguồn/không có trọng số trong breakdown. Customer rejection/Internal Win đã được loại khỏi
giao diện và payload trước thay đổi này, tiếp tục giữ ẩn. Chưa bổ sung provider cho những chỉ số này.

## Giá và nhận diện

- Giữ số model/SKU trong query; chặn biến thể có mã khác nhau.
- Lưu metadata sản phẩm từ nguồn và trang bán: tên, brand, SKU/model, GTIN. GTIN hoặc brand+SKU hỗ trợ clustering;
  SKU riêng của shop không đủ để gộp hai sản phẩm. Không ghi đè tên MKT đã xác nhận.
- Cache theo subject và Product Cluster, giữ nguyên thời điểm quan sát và hạn dùng khi tái sử dụng.
- Ưu tiên giá ad, nguồn hàng đã có trong cluster và các nguồn được hỗ trợ bởi metadata/hash/CLIP đã lưu.
- Đọc trang công khai miễn phí trước. Lens cho TEST/TEST_NOW dùng cache và job chung; chỉ nhận ảnh khớp đã kiểm tra.
- Bright Data tìm kiếm/xác minh trang; SerpApi Shopping dự phòng cả khi Bright Data thành công nhưng không có giá phù hợp.
  Giá snippet/Lens chưa xác minh trang vẫn là tham khảo. Không trộn tiền tệ hoặc dùng giá nguồn hàng như giá bán đối thủ.
- Các thẻ tìm bằng ảnh, từ khóa, Meta và TikTok dùng chung hàng đợi giá.
- Trang chi tiết tự ưu tiên tìm giá và cập nhật sau khi có kết quả. Giá bán đối thủ và giá nguồn hàng trong cache
  đã xác minh được dùng để tính biên gộp; không lấy snippet/giá tham khảo làm giá vốn. Giữ rõ đơn vị USD khi hiển thị giá trung bình đã quy đổi.

Tự động trả phí chỉ cho TEST/TEST_NOW, giới hạn mặc định 10 cụm/ngày và 100 cụm/tháng, thêm hạn mức provider hiện có.
`PRICE_AUTO_TEST_ENABLED=0` tắt phần trả phí tự động. Điều chỉnh bằng `PRICE_AUTO_DAILY_LIMIT` và
`PRICE_AUTO_MONTHLY_LIMIT`. Luồng miễn phí vẫn chạy. Mỗi cluster có lock để tránh nhiều ad mua cùng một lượt tìm kiếm.

Không khẳng định tỷ lệ thiếu giá đã giảm từ 72% khi chưa có mẫu đo sau chạy thực tế. Credits, khả năng truy cập nguồn
và bằng chứng nhận diện vẫn quyết định số sản phẩm có giá xác minh được.
