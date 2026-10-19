# Authentication, MKT Workspace & Competitor Tracking — 15/10/2026

Đã triển khai trực tiếp trong source và áp dụng migration lên PostgreSQL hiện tại. API, Next.js và worker đã được khởi động lại. Truy cập `http://localhost:3000/login/`.

**Bước khởi tạo còn cần chủ hệ thống thực hiện:** tạo ADMIN đầu tiên với tên/email/mật khẩu tự chọn. Database thật hiện chưa có tài khoản hay Follow thử nghiệm. Tài khoản và Ads giả chỉ xuất hiện trong database kiểm thử tách biệt.

```powershell
cd D:\ToolSpy\apps\api
.venv/Scripts/python.exe -m app.workspace.cli create-admin
```

CLI hỏi mật khẩu bằng `getpass`; không có mật khẩu mặc định, không tự nâng quyền email đã tồn tại. Sau đó đăng nhập, mở **Quản lý tài khoản** để duyệt MKT đăng ký mới hoặc khóa tài khoản.

Máy hiện tại đã cài Argon2. Khi cập nhật checkout khác có sẵn virtualenv, chạy `.venv/Scripts/python.exe -m pip install -r requirements.txt` trong `apps/api` trước khi khởi động.

## Chức năng đã triển khai

- Đăng ký MKT chờ duyệt; đăng nhập/đăng xuất; Argon2id; opaque session được hash trong DB; cookie HttpOnly/SameSite=Lax; Secure cho HTTPS; CSRF ràng buộc phiên và kiểm tra Origin; rate limit lưu DB dùng chung các process.
- ADMIN duyệt/khóa MKT; khóa và đổi/reset mật khẩu thu hồi phiên. Người dùng sửa tên, giới thiệu và nhóm MKT. Client không được tự chỉ định `user_id` hoặc role.
- Quên mật khẩu: trả thông báo chung, ghi yêu cầu cho ADMIN; SMTP STARTTLS nếu được cấu hình. Liên kết ngẫu nhiên, hash trong DB, dùng một lần, hết hạn 30 phút. Nếu chưa có SMTP, ADMIN xác minh chủ tài khoản rồi cấp liên kết bằng CLI:

```powershell
.venv/Scripts/python.exe -m app.workspace.cli reset-password email@example.com
```

- `/workspace`: Dashboard riêng, số đối thủ, Ads mới 24h, hoạt động mới, nguồn/lần kiểm tra, thông báo chưa đọc.
- `/workspace/library`: Ads đã lưu/yêu thích/đã test và ghi chú riêng. Các nút có trên thẻ Ads và Ads Detail. Không sao chép Ads gốc sang database khác.
- Follow/Unfollow trong Search, Library, Ads Detail và Advertiser Detail. Lưu vĩnh viễn đến khi người dùng Unfollow; baseline đồng bộ dữ liệu đã có, không tính baseline thành Ads mới vừa phát hiện.
- `/competitors`: danh sách Follow riêng, phân trang, số Ads/Ads mới, nguồn, trạng thái, lịch kiểm tra, refresh thủ công. Advertiser không có thay đổi vẫn hiện trong danh sách.
- `/competitors/[id]`: tổng quan, active/new Ads, timeline, sản phẩm và snapshot có nguồn/thời điểm/số liệu. Trạng thái cũ chưa có bằng chứng được hiển thị `unknown`.
- Timeline/snapshot lưu lịch sử thay đổi copy, media, landing, thị trường và trạng thái. URL media chỉ đổi chữ ký CDN không tạo sự kiện creative giả. Views/likes/comments/shares chỉ lấy số do nguồn trả; không ước tính tiền, doanh thu, đơn hàng.
- Thông báo riêng, chống trùng theo user/event, đọc/chưa đọc; thông báo Ads mới, thay đổi, inactive được xác nhận và nguồn lỗi kéo dài.
- Whitelist/Blacklist và chế độ chỉ Follow lưu theo tài khoản. Blacklist được loại trừ trong Search, Library, ảnh bìa/advertiser/landing trên kết quả. Không xóa Ads gốc. Điểm thị trường chung vẫn là dữ liệu chung, chưa xây Personalized Feed.
- Bộ lọc tìm kiếm đã lưu và lịch sử hoạt động riêng. Search jobs, phiếu đánh giá và quyền đọc Lens jobs được ràng buộc tài khoản. MKT chỉ thấy danh mục connector đã lược bỏ cấu hình bí mật và các lịch tìm kiếm của người khác.
- Radar cũ giữ tại `/competitor-radar`; watch toàn hệ thống của Radar chỉ ADMIN được sửa. Không tự gán watch cũ cho MKT mới.

## Worker và giới hạn tài nguyên

Tái sử dụng process `app.worker`, không thêm Redis/Celery/Docker. Một hàng `advertiser_scan_jobs` cho mỗi advertiser; nhiều MKT cùng Follow dùng chung lịch/crawl. Chu kỳ thực tế là chu kỳ nhỏ nhất trong các Follow (6/12/24h, mặc định 24h).

- Lịch và lease/token lưu PostgreSQL; restart không làm mất job. Claim có advisory lock và row lock; process cũ không được xuất bản kết quả sau khi mất lease.
- Mặc định 1 lượt quét đồng thời, tối đa 1 advertiser trên mỗi nguồn. Quota mặc định 10 lượt/nguồn/giờ; quota gồm cả lần thử lại và refresh thủ công. Lịch kiểm tra chạy mỗi 30 giây, không phụ thuộc phiên trình duyệt.
- Hard timeout mặc định 180 giây: process crawl riêng bị kết thúc thật. Retry tối đa 3 lần trong một đợt, lùi 2 rồi 4 phút; sau đó chờ chu kỳ tiếp theo. Request mạng còn có timeout/rate limit của adapter.
- Mặc định tối đa 200 Ads/10 trang. Chạm giới hạn hoặc thiếu bằng chứng phân trang được ghi `partial`, không coi là đã quét đủ. Chỉ lần quét hoàn chỉnh không có thay đổi mới hiển thị “Không phát hiện thay đổi”.
- Liveness cũ bỏ qua advertiser đang Follow để tránh crawl thừa; không còn suy diễn inactive sau hai lần vắng mặt.
- Lỗi, thiếu token/quyền, nguồn bị chặn hoặc chưa hỗ trợ đều giữ lịch sử. Không vượt CAPTCHA. Các metadata/snapshot không chạy mã hóa lại video; tracker không gọi AI đặt tên.

Chạy như hiện tại:

```powershell
# root .env: DISABLE_SCHEDULER=1
cd D:\ToolSpy
.\start.ps1
```

Hoặc chạy riêng:

```powershell
cd D:\ToolSpy\apps\api
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
.venv/Scripts/python.exe -m app.worker
```

Mỗi lệnh dài hạn cần một process riêng. Chỉ chạy **một** `app.worker` vì các lịch cũ của ToolSpy vẫn dùng bộ nhớ; phần competitor đã an toàn khi nhiều process claim. Nếu chỉ cần tracker độc lập: `python -m app.workspace.worker` (không cần chạy thêm khi `app.worker` đã chạy). Tiến trình phải được giữ sống hoặc được Windows Task Scheduler/service khởi động lại sau khi máy reboot.

Cấu hình mẫu: [auth-competitor.env.example](auth-competitor.env.example). Giữ DATABASE_URL và API keys hiện có. Khi dùng HTTPS/domain thật: đặt `APP_URL`, `WEB_ORIGIN` đúng origin, `AUTH_COOKIE_SECURE=1`; không đặt admin token trong frontend. `ADMIN_TOKEN` cũ chỉ giữ cho tích hợp server-to-server đã có; session API cá nhân không chấp nhận token này.

## Migrations và dữ liệu

Sử dụng migration cộng thêm của SQLAlchemy, có version ledger và transaction/advisory lock:

1. `20261015_accounts_competitors_v1`: bảng account/workspace/tracker, cột `advertisers.source`, `source_advertiser_id`, `avatar_url`; index unique `(source, source_advertiser_id)`; `user_id` cho search jobs/votes. Định danh advertiser cũ theo source/page_id và tách nhóm có nhiều ID.
2. `20261015_advertiser_identity_v2`: tách Ads thiếu ID/khác nguồn đang nằm trong nhóm có ID đã xác minh. 305 liên kết cũ được tách sang danh tính chưa xác minh; không dùng tên làm bằng chứng cùng advertiser.

Migration chạy lúc khởi động API/worker hoặc bằng:

```powershell
.venv/Scripts/python.exe -m app.workspace.migrate
```

Kết quả thực tế trong [AUTH_COMPETITOR_MIGRATION_RESULT.json](AUTH_COMPETITOR_MIGRATION_RESULT.json):

| Lần migration | Ads trước/sau | Tổng ID Ads trước/sau | Sản phẩm trước/sau | Advertiser trước/sau |
|---|---:|---:|---:|---:|
| v1 | 15.983 / 15.983 | 157.562.639 / 157.562.639 | 11.670 / 11.670 | 4.556 / 4.576 |
| v2 | 15.983 / 15.983 | 157.562.639 / 157.562.639 | 11.657 / 11.657 | 4.576 / 4.881 |

Số sản phẩm giữa hai lần khác nhau do worker gom cụm cũ tiếp tục chạy giữa hai lần; từng migration đều giữ nguyên Ads và sản phẩm. Sau v2, không còn Ads thiếu ID/khác nguồn gắn vào danh tính advertiser đã xác minh. Không xóa advertiser cũ.

Bảng mới: `users`, `user_sessions`, `auth_csrf_tokens`, `auth_rate_limits`, `password_resets`, `mkt_profiles`, `mkt_ad_interactions`, `mkt_saved_searches`, `mkt_followed_advertisers`, `mkt_advertiser_filters`, `advertiser_scan_jobs`, `competitor_source_quotas`, `advertiser_tracked_ads`, `advertiser_ad_snapshots`, `advertiser_activity_events`, `mkt_notifications`, `mkt_activity`, `mkt_resource_access`, `workspace_migrations`.

Saved/favorite/tested dùng ba cờ trong **một** hàng `mkt_ad_interactions(user_id, ad_id)`, tránh ba bảng join trùng cấu trúc. Các quan hệ riêng đều dùng ID từ session. Advertiser không có platform-issued ID vẫn Follow được để giữ dữ liệu cũ nhưng không tự quét theo tên.

## API mới

Tất cả đường dẫn dưới đây có tiền tố `/api`. API private lấy người dùng từ session; request ghi phải gửi `X-CSRF-Token` khớp cookie do `/auth/csrf` cấp.

| Nhóm | API |
|---|---|
| Account | `GET /auth/csrf`, `POST /auth/register`, `/auth/login`, `/auth/logout`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/password`; `GET /auth/me`; `PATCH /auth/profile` |
| ADMIN | `GET /admin/users`, `/admin/reset-requests`; `PATCH /admin/users/{uid}` |
| Advertiser | `GET /advertisers`; `GET /competitors`, `/competitors/{id}` |
| Follow | `POST /competitors/{id}/follow` với `interval_hours`; `DELETE /competitors/{id}/follow` |
| Tracking | `POST /competitors/{id}/refresh`, `/competitors/{id}/seen`; `GET /competitors/{id}/status`, `/ads`, `/timeline`, `/products`, `/ads/{tracked_id}/snapshots` |
| Workspace | `GET /workspace/state`, `/workspace/dashboard`, `/workspace/ads`, `/workspace/activity`; `PUT /workspace/ads/{id}` |
| Filters | `GET/PUT /workspace/filters`; `PUT /workspace/filters/{advertiser_id}` |
| Saved search | `GET/POST /workspace/searches`; `DELETE /workspace/searches/{id}` |
| Notifications | `GET /workspace/notifications`; `PATCH /workspace/notifications/{id}` |

Các danh sách Ads, advertiser, Follow, timeline, snapshot, sản phẩm, thông báo và hoạt động giới hạn `limit` 1–200, offset không âm. API Search/Ads hiện có áp dụng bộ lọc private ở SQL trước khi tính tổng.

## Nguồn đã audit và kiểm tra

| Nguồn | Theo dõi tự động theo ID | Kiểm tra thực tế / giới hạn |
|---|---|---|
| Meta Library public | Có, page ID | Live read-only PASS: 10 Ads đúng page ID; giới hạn thử 1 trang nên `partial`. Không fallback SSR/access challenge trong tracker. Không bảo đảm đủ lịch sử hoặc engagement. |
| Meta Graph | Có, page ID | Chưa kiểm tra live vì connector đang tắt/thiếu access được xác minh. Cần token, quyền API, version và quốc gia hợp lệ. API không trả đầy đủ file media; connector chưa có bằng chứng hoàn tất nên báo partial. |
| TikTok public Library | Có adapter business ID, chưa xác nhận live thành công | Lần thử thực tế trả `ConnectorError`; không ghi nhận thành công. Phạm vi EU/UK; lỗi truy cập/schema/ID phải xử lý từ nguồn. |
| TikTok Commercial API | Chưa hỗ trợ truy vấn advertiser trực tiếp | Query Ads hiện không công bố business-ID filter. API key đã có không tự tạo khả năng này. Giữ dữ liệu nhập hiện có. |
| TikTok Creative Center / Top Ads / headless / Apify Top Ads | Chưa hỗ trợ | Dữ liệu tuyển chọn, thiếu advertiser ID/query được xác minh; không dùng tên để crawl thay thế. |
| Snapchat | Chưa hỗ trợ | Connector tìm theo tên, chưa đủ bằng chứng định danh. |
| Pipiads/Minea/export/nguồn khác | Chưa có adapter tracking đã xác minh | Dữ liệu cũ được giữ; không gọi dịch vụ trả phí hoặc giả định query ID. |

Tài liệu nguồn đã đối chiếu: [TikTok Query Ads](https://developers.tiktok.com/docs/en/commercial-content-api-query-ads), [TikTok Query Advertisers](https://developers.tiktok.com/docs/en/commercial-content-api-query-advertisers), [Meta reference client](https://github.com/facebookresearch/Ad-Library-API-Script-Repository/blob/main/python/fb_ads_library_api.py). Thử nghiệm live là snapshot, không cam kết endpoint bên ngoài luôn truy cập được.

## Kết quả kiểm thử

| Kiểm tra | Kết quả |
|---|---|
| Workspace/security/tracking trên SQLite tạm | PASS 16/16 |
| Cùng bộ kiểm thử trên PostgreSQL database tạm | PASS 16/16 |
| Hồi quy Hidden Winners, query intent, engagement, identity, content filter, Browser Bridge | PASS 114/114 |
| Browser Edge headless với API/Next/database tách biệt | PASS đăng ký, login/redirect, Follow, timeline, logout/login giữ Follow, whitelist, lưu Ads, lưu/mở lại đầy đủ bộ lọc, profile, ADMIN duyệt/khóa; không có lỗi JavaScript |
| TypeScript | PASS `npx tsc --noEmit --incremental false` |
| Production build | PASS `npm run build`, 43 trang |
| Ingest rules | PASS `python test_ingest_rules.py` |
| HTTP thật | `/api/health` 200; `/api/ads` chưa đăng nhập 401; `/login/` và CSRF qua Next 200 |
| Meta page-ID live | PASS dữ liệu 10 Ads đúng advertiser, partial |
| TikTok Library live | FAIL truy cập/query (`ConnectorError`); đã giữ trạng thái giới hạn, không báo cập nhật thành công |

16 bài test bao phủ: đăng ký/duyệt/khóa, CSRF/Secure/RBAC, reset một lần/thu hồi phiên, A Follow B/C và MKT khác Follow riêng, persistence, private library/search/jobs/notifications, sự kiện mới không trùng, metrics snapshots, missing→unknown, inactive xác nhận, timeline, database restart/lease stale, concurrency, quota, backoff 3 lần, hard timeout kết thúc process, identity/migration lặp lại, và loại trừ crawl trùng với liveness cũ.

Lệnh kiểm thử:

```powershell
cd D:\ToolSpy\apps\api
.venv/Scripts/python.exe -m pytest tests/test_workspace.py -q
.venv/Scripts/python.exe scripts/verify_workspace_postgres.py
.venv/Scripts/python.exe scripts/verify_workspace_browser.py
```

Script PostgreSQL tạo database `toolspy_test_<uuid>` rồi xóa đúng database đó trong `finally`; cần quyền CREATE DATABASE. Script browser dùng API :8011, Next :3001, SQLite tạm và headless Edge, không dùng database thật. Một lần chạy PostgreSQL bị Python/Windows native stack overflow; hai lần chạy lại liên tiếp đạt 16/16, chưa xác định nguyên nhân và chưa tái hiện lỗi đó.

## File được thêm/sửa trong lần này

Backend mới nằm trong `apps/api/app/workspace/`: `__init__.py`, `models.py`, `auth.py`, `accounts.py`, `access.py`, `cli.py`, `filters.py`, `identity.py`, `migrate.py`, `routes.py`, `sources.py`, `tracking.py`, `worker.py`.

Backend tích hợp: `apps/api/app/{db,models,main,worker,ingest,realtime,retention}.py`; `collector/adapters/{meta,ad_libraries}.py`; `services/connectors.py`; `routers/intel.py`; `visual/routes.py`; `apps/api/requirements.txt`.

Frontend mới: `apps/web/components/workspace/{session,auth-form,actions,common}.tsx`; các trang `login`, `register`, `forgot-password`, `reset-password`, `account`, `admin/users`, `workspace` và `workspace/{library,filters,notifications,activity,searches}`, `competitors/[id]`, `competitor-radar`.

Frontend tích hợp: `app/layout.tsx`, `app/competitors/page.tsx`, `app/search/page.tsx`, `app/ads/page.tsx`, `app/api/events/stream/route.ts`; `components/layout/{app-sidebar,nav-user}.tsx`, `components/layout/nav.ts`; `components/ads/{ad-card,ad-detail-sheet}.tsx`, `components/media.tsx`; `lib/api.ts`; `middleware.js`; `next.config.js`.

Test/chạy/tài liệu: `apps/api/tests/test_workspace.py`, `tests/conftest.py`, `tests/test_query_intent.py`, `tests/test_engagement.py`, `scripts/verify_workspace_{postgres,browser}.py`, `start.ps1`, `.gitignore`, `README.md`, tài liệu audit/report/migration JSON và env example này. Các test cũ được cấp credential test rõ ràng hoặc chuyển sang Radar legacy; không tắt lớp auth để làm test pass. Giữ nguyên các sửa đổi đã có trong working tree trước tác vụ.

## Phần chưa được xác nhận hoặc cần cấu hình

- Chưa tạo ADMIN thật: cần chạy CLI ở đầu tài liệu. Không lưu mật khẩu vào báo cáo hoặc tạo tài khoản dùng chung mặc định.
- SMTP chưa cấu hình/kiểm tra gửi thật; cơ chế reset qua CLI đã có. Không cần email để dùng Follow/notification trong ứng dụng.
- Meta Graph cần credentials/quyền; TikTok public live chưa thành công; các nguồn chưa có query advertiser ID được đánh dấu chưa hỗ trợ.
- Chưa chờ một chu kỳ 24 giờ ngoài đời: lịch/restart/offline/concurrency được kiểm thử bằng thời gian và provider fixture trong database tách biệt; worker thật đã chạy nhưng chưa có Follow của người dùng thật.
- Chưa chạy soak test nhiều ngày hoặc benchmark tải lớn. Tracker mặc định concurrency 1, tối đa 200 Ads/lượt; lịch sử lớn sẽ tăng dung lượng DB theo số snapshot. Media gốc có thể hết hạn theo nguồn và quy tắc retention hiện có.
- Không triển khai AI Recommendation, Personalized Feed hoặc phân phối sản phẩm độc quyền giữa MKT trong lần này.
