# Audit trước triển khai — 15/10/2026

- PostgreSQL đang chạy, 15.792 ads, 4.445 advertisers, 11.584 products (snapshot trước migration).
- Chưa có users/sessions hay bảng theo user. Watch là boolean toàn cục; advertiser ghép theo tên + platform, không đủ để xác minh danh tính.
- 7.845 Meta ads có page_id; TikTok Library có 264/762; TikTok Commercial có 29/29; Creative Center thiếu business ID.
- Có SQLAlchemy additive auto_migrate, ingest/upsert AdSource, creatives, engagement history và worker asyncio. Tái sử dụng; không thêm Docker/Redis/Celery.
- Next middleware tự cấp X-Admin-Token cho mọi request, next.config đưa ADMIN_TOKEN vào build: phải gỡ để phân quyền backend có hiệu lực.
- Liveness cũ suy diễn inactive sau hai lần vắng mặt: thay bằng unknown, chỉ nguồn xác nhận mới là inactive.
- Meta public hỗ trợ page_ids nhưng fallback SSR có access-challenge: tracker dùng chế độ strict, không fallback hoặc vượt challenge. TikTok Commercial API hiện không công bố bộ lọc business ID trong Query Ads; không giả lập hỗ trợ.
- Dữ liệu cũ không gán cho một MKT bất kỳ. Migration cộng thêm bảng/cột, backfill danh tính từ cặp source/page_id, giữ toàn bộ Ads và advertiser cũ.
- Working tree có nhiều sửa đổi từ trước. Không reset hoặc dọn các thay đổi đó.

Nguồn kiểm tra khả năng API: [TikTok Query Ads](https://developers.tiktok.com/docs/en/commercial-content-api-query-ads), [Meta reference client](https://github.com/facebookresearch/Ad-Library-API-Script-Repository/blob/main/python/fb_ads_library_api.py).
