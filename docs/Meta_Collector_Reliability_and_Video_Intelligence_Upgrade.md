# ToolSpy — Meta Collector Reliability & Video Intelligence Upgrade

Repository: https://github.com/ThaiPhancde/spyads

## Mục tiêu

Nâng cấp hệ thống thu thập quảng cáo dựa trên kết quả audit mã nguồn hiện tại và các nguyên lý kỹ thuật đã phân tích từ SpyWinAds 0.6.6.

Không sao chép mã độc quyền của SpyWinAds. Triển khai độc lập bằng công nghệ và dependency hợp lệ.

## Các phase

**Phase 1 — Meta Collector Reliability**
- Audit `collector/adapters/meta.py`, `realtime.py` và Chrome extension.
- Triển khai batch ingest có transaction và checkpoint sau khi lưu thành công.
- Đảm bảo cursor không được advance khi batch lưu thất bại.
- Dedup quảng cáo và collation xuyên trang, xuyên job.
- Chrome extension lưu batch chưa gửi vào IndexedDB và retry với exponential backoff.
- Bảo đảm mỗi batch có idempotency key.
- Phân biệt empty page, end-of-pagination và lỗi từ nguồn.

**Phase 2 — Video Fingerprint**
- Mở rộng dHash hiện tại thành fingerprint 3 khung 20%, 50%, 80%.
- Không bật tải video hàng loạt hoặc re-encode.
- Ưu tiên metadata/poster và chỉ xử lý video khi có dữ liệu được phép truy cập.
- Lưu fingerprint và confidence vào DB.
- Dùng fingerprint làm tín hiệu dedup bổ trợ, không gộp sản phẩm chỉ vì video giống nhau.

**Phase 3 — Persistent Jobs**
- Thiết kế PostgreSQL job queue với lease, heartbeat, retry_count, next_retry_at.
- Phục hồi job sau worker restart.
- Chống nhiều worker nhận trùng một job.
- Lưu lịch sử crawl và các checkpoint.

**Phase 4 — Optional Browser Collector**
- Thiết kế browser worker riêng dùng Playwright/CDP cho nguồn công khai được phép.
- Giới hạn concurrency theo CPU/RAM.
- Có timeout, resource cleanup và trạng thái blocked/rate_limited.
- Không vượt CAPTCHA hoặc cơ chế kiểm soát truy cập.

**Phase 5 — Monitoring**
- Bổ sung KPI trên Data & Connectors.
- Thể hiện pages fetched, ads accepted, duplicate, invalid, cursor, retry, health và media availability.

## Ràng buộc

1. Không phá vỡ tính năng hiện tại.
2. Giữ nguyên FastAPI, Next.js, PostgreSQL.
3. Không thay đổi công thức TEST/TEST_NOW nếu chưa có bằng chứng cần thiết.
4. Không thay đổi các quy tắc cá nhân hóa MKT.
5. Không bật ffmpeg re-encode hoặc tải video hàng loạt.
6. Mọi thay đổi database phải có migration an toàn.
7. Bổ sung unit test, integration test và failure-recovery test.
8. Tạo nhánh Git riêng, chia commit theo phase; không push trực tiếp lên main.
9. Tạo tài liệu `docs/META_COLLECTOR_UPGRADE.md` mô tả kiến trúc, file thay đổi, vấn đề đã sửa và kết quả kiểm thử.

Bắt đầu bằng audit và triển khai Phase 1. Sau khi hoàn thành, chạy test và cung cấp báo cáo kết quả thực tế.