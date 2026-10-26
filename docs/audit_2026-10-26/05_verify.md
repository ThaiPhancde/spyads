# 05 — Adversarial verification (2026-10-26)

## Verification

Phương pháp: mở đúng file/dòng được trích dẫn và tìm cách bác bỏ từng phát hiện (đã sửa chưa, trích nhầm file, không chạy tới được, hoặc là quyết định có chủ đích của user). Chỉ đọc. Không sửa code, không bật/tắt server.

| ID | Kết luận | Mức độ (đã chỉnh) | Ghi chú |
|---|---|---|---|
| CMP-F1 | REAL | high | Không có code nào chuyển `is_active` sang False khi quảng cáo biến mất |
| CMP-G1 | REAL | medium | Là gap tính năng, không phải bug; evidence đúng |
| B1 | REAL | high | Trùng với FE-01; số dòng trong auth.py bị lệch |
| B2 | REAL | medium | Đăng ký mở là có thật; mức lộ ra Internet chỉ xảy ra lúc chạy tunnel |
| FE-01 | REAL | high | Đã kiểm từng path so với `mkt_write` |
| FE-02 | REAL | medium | Hai mô hình "theo dõi"; nút Radar còn trả 403 (trùng FE-01) |
| FE-03 | REAL | medium | Copy sai so với retention.py; Ghim trả 403 |
| F1 | REAL | medium | 167 file, 113.54 MiB, đã có trên origin/main |
| F2 | REAL | high | Không có process nào chạy; log worker dừng lúc 2026-10-23 17:25:30 |
| F3 | REAL | high | Kết nối bằng `.env` bị từ chối (password auth failed); đây là nguyên nhân gốc của F2 |

### CMP-F1: quảng cáo đã dừng vẫn giữ `is_active=True` (REAL, high)
- `ingest.py:282-288`: chỉ đặt `is_active=False` khi `rec.active is False`. Grep `is_active\s*=\s*False|inactive_at\s*=` trên toàn bộ `apps/api/app` chỉ ra `ingest.py:284,287,360`, nên không có job nào khác đánh dấu quảng cáo là đã dừng.
- `realtime.py:674` quét liveness với `active_only=True`, nên kết quả không bao giờ chứa quảng cáo đã dừng. Tại `realtime.py:717-725`, quảng cáo không còn thấy chỉ bị `verify_misses += 1`, và comment ghi rõ là cố ý ("Absence ... is not source-confirmed inactive evidence").
- Chỉ có một nơi ghi `verify_misses` (`realtime.py:722`) và một nơi reset nó (`ingest.py:290`). Không chỗ nào đọc giá trị này.
- `realtime.py:646,652` chỉ quét `meta_library` và bỏ qua page đang follow (`:654`). `ad_libraries.py:477` gán cứng `active=True` cho TikTok Top Ads.
- Hệ quả: `adsignals.py:33` đặt `end = now` khi `is_active`; `features.py:42-54` (`_active_at`) coi ad chưa bị dừng là đang chạy; `features.py:158` tính `longevity_days` đến `end`. Các nơi dùng giá trị này: `discovery.py:209` (35% điểm demand), `discovery.py:344` (TEST_NOW cần ≥14 ngày), `hidden.py:29,35` (cần ≥7 ngày).
- Comment ở `features.py:49-50` ("Live ads are confirmed by the liveness job, so silence alone no longer makes an ad look removed") mâu thuẫn với hành vi thật: liveness có xác nhận ad còn chạy, nhưng không bao giờ xác nhận ad đã dừng.
- Không có quyết định nào của user cho phép giữ trạng thái này. Page đang follow thì có dữ liệu đúng nhờ `workspace/sources.py:60,80` (`active_only=False`).

### CMP-G1: không có tuổi video thật (REAL, hạ xuống medium)
- `collector/base.py:67` mặc định `active_only=True`. `realtime.py:44-51` (`_params_from`), `:133-135` và `:195` không bao giờ đặt lại giá trị này. Chỉ `workspace/sources.py:60,80` dùng False.
- `meta.py:145-149` chỉ lưu URL video và preview, không lưu video id hay ngày upload. Grep `video_id|video_key|upload_date` chỉ ra `engagement/mapping.py:50`.
- `ingest.py:382-383` chỉ hạ `product.first_seen_at` khi tạo ad mới.
- Hạ xuống medium vì đây là gap tính năng so với SpyWin, không phải lỗi sai dữ liệu. Phần sai dữ liệu đã nằm trong CMP-F1.

### B1: allow-list ghi cho MKT chặn các nút trên UI (REAL, high)
- `mkt_write` nằm ở `workspace/auth.py:121-136` (finding ghi 105-124, bị lệch dòng). Danh sách này không có `/products/{id}/name|name/confirm|name/undo|split|scan|explain|lifecycle`, `/products/merge`, `/products/{id}/merges/{eid}/undo`, `/competitor-radar/{id}/watch`, `/creatives/{id}/pin`, `/products/image-matches/review`, `/search/images/watch`, `/pipeline/run`.
- `auth.py:182-183` trả 403 cho mọi lệnh ghi không nằm trong danh sách. Tất cả user đều là MKT.
- `/api/agent/` thuộc `SYSTEM` (`auth.py:31`). Tuy vậy trang `/agent` có cờ `admin: true` (`nav.ts:57`) và bị `app-sidebar.tsx:45` lọc khỏi sidebar, nên phần agent trong B1 không áp dụng.
- Việc gắn tên người làm lấy từ `body.user` ở `main.py:549,584,598,624,647`. Finding ghi 574/590/616 là lệch dòng. Trong khi đó `routers/clusters.py:86` lấy đúng từ `db.info['user_id']`.

### B2: ai cũng tự đăng ký được và active ngay (REAL, hạ xuống medium)
- `workspace/accounts.py:58-73` tạo `User(role="MKT", status="active")` mà không có invite, giới hạn domain hay xác minh. Grep `REGISTRATION|invite|allowed_domain` không có kết quả.
- `run_tunnel.cmd` chạy `cloudflared tunnel --url http://localhost:3000`, và có schtask `ToolSpyTunnel`. Tuy nhiên `tunnel.log` cho thấy tunnel đã dừng lúc 2026-10-15 10:02Z.
- Yếu tố giảm nhẹ: `.env` đặt `WEB_ORIGIN=http://localhost:3000`, nên trình duyệt đi qua tunnel bị `auth.py:167-168` trả 403 vì Origin. Nhưng điều kiện là `if origin and ...`, nên request không có header Origin (curl) vẫn qua được. CSRF token cho người chưa đăng nhập lấy công khai ở `/api/auth/csrf`. Vì vậy người lạ vẫn có thể đăng ký bằng script.
- Giới hạn theo user: live search có (`intel.py:650`, 30/giờ). Visual chỉ giới hạn theo IP trong process (`visual/routes.py:46`). `/api/prices/lookup` (`pricing/routes.py:72` → `jobs.py:157-167 paid=True`) và `/api/engagement/lookup` (`engagement/routes.py:55`) không có giới hạn theo user, chỉ có quota toàn cục (`pricing/quota.py`).
- Hạ xuống medium vì chỉ bị lộ khi tunnel đang chạy (hoặc từ LAN, do PG/Next cùng bind 0.0.0.0) và chi phí đã có trần toàn cục.

### FE-01: 14 mutation luôn trả 403 (REAL, high)
- Đã kiểm từng lệnh gọi với regex ở `auth.py:121-136`: `app/page.tsx:21` (`/pipeline/run`), `competitor-radar/page.tsx:28`, `products/[id]/page.tsx:91,178,183`, `components/media.tsx:244`, `product-cluster.tsx:31,41,46`, `product-name.tsx:45,46,80`, `visual/results.tsx:129`, `visual/web-results.tsx:77`, `visual/watch-dialog.tsx:33`. Không path nào khớp.
- `app/clusters/page.tsx:49` chỉ bị 403 ở nhánh merge cũ (`/products/{keep}/merges/{eid}/undo`). Nhánh `/clusters/events/{id}/undo` thì được phép (`auth.py:124`).
- Các nút này không bị ẩn theo role: grep `role|isAdmin|canEdit` trong các file trên không có kết quả.
- Trùng với B1. Nên gộp làm một khi sửa.

### FE-02: hai khái niệm theo dõi đối thủ (REAL, medium)
- `main.py:773-778` ghi cờ toàn cục `Advertiser.is_competitor_watched`. `main.py:739` lọc theo cờ này.
- `competitor-radar/page.tsx:23,28,37-38,68` dùng cờ toàn cục, còn `/competitors` dùng follow theo từng user. `nav.ts:15,40` có hai mục cùng icon `Users`.
- Đây là mâu thuẫn UX. Phần 403 đã được tính trong FE-01, nên chỉ để medium.

### FE-03: Creative Vault ghi "lưu vĩnh viễn" (REAL, medium)
- `vault/page.tsx:51` ghi "lưu vĩnh viễn trong kho". `ads/page.tsx:63` ghi "video/ảnh tải về được".
- `retention.py:5-8,36` đặt `VIDEO_KEEP_DAYS` mặc định là 1. `.env` không ghi đè giá trị này (chỉ có `VIDEO_DAILY_QUOTA=3000`). Copy hứa nhiều hơn chính sách thực tế, và cũng đi ngược quyết định của user là không lưu video hàng loạt.
- Ghim (`intel.py:1349`) không nằm trong `mkt_write`, nên lối thoát mà `retention.py:19-20` gợi ý ("pin what you want to keep") cũng trả 403.

### F1: build cache của Next.js bị commit (REAL, hạ xuống medium)
- `git ls-files`: 83 file trong `.next-library-test` và 84 file trong `.next-search-test`. `git cat-file` đo được 113.54 MiB. Có 167 file trên `origin/main`. Được thêm ở commit `4185035`.
- `.gitignore:25-28` chỉ bỏ qua `.next/`, `.next-workspace-test/`, `.next-validation/`. `git check-ignore` báo not-ignored. `apps/web/tsconfig.tsbuildinfo` cũng đang được track.
- Đây là vấn đề vệ sinh repo, không ảnh hưởng runtime, nên để medium.

### F2: API và worker không chạy (REAL, high)
- `Win32_Process` không có `uvicorn`, `app.worker` hay `next`. Lần ghi cuối của `worker_err.log` là 2026-10-23 17:25:30.
- Mọi task `ToolSpy*` đều có `Next Run Time: N/A` và chế độ "Interactive only".
- `start.ps1:23-28` chỉ dùng `Start-Process` một lần, không giám sát hay tự khởi động lại. Thời điểm chạy job của scheduler chỉ nằm trong RAM (`realtime.py:331-354`), nên mỗi lần khởi động lại `_daily_job` chạy ngay (`realtime.py:445`).
- `/api/health` (`main.py:851-857`) không có heartbeat của worker.
- Nguyên nhân gốc trực tiếp là F3: API không kết nối được DB nên chết ngay khi khởi động.
- Ghi chú phụ: `start.ps1:31` vẫn in hướng dẫn `create-admin`, mâu thuẫn với quyết định không có ADMIN.

### F3: DATABASE_URL bị Postgres từ chối, dùng superuser và mật khẩu yếu (REAL, high)
- `start.ps1:6` nạp `.env` ở thư mục gốc. Có `DATABASE_URL=postgresql+psycopg://postgres:<5 ký tự>@localhost:5432/toolspy`. Không có `apps/api/.env`.
- Chạy `create_engine(...).connect()` với URL đó ra `FATAL: password authentication failed for user "postgres"`.
- `netstat` cho thấy PG đang lắng nghe ở `0.0.0.0:5432` và `[::]:5432`.
- `memory/toolspy-postgres-two-process.md:12` lưu mật khẩu dạng chữ thường. Báo cáo này không chép lại mật khẩu.
- Không đọc được `pg_hba.conf` ở vị trí mặc định, nên chưa xác nhận được có cho phép kết nối từ xa hay không.

### Thứ tự sửa đề xuất
1. F3, sau đó F2: sửa thông tin đăng nhập DB, rồi giám sát process và thêm heartbeat cho worker.
2. B1 + FE-01 (gộp làm một): mở `mkt_write` cho naming, split, merge, undo, scan, explain, lifecycle, pin, image-match review, image watch. Lấy user từ `db.info['user_id']`. Ẩn nút `/pipeline/run`.
3. CMP-F1: giới hạn `days_running`/`longevity` theo `last_verified_at + grace`. Chạy tracked query định kỳ với `active_only=False`.
4. B2: đóng đăng ký (ví dụ `REGISTRATION_MODE=closed`) hoặc chỉ cho domain `@levelupvn.com`. Bắt buộc có header Origin cho lệnh ghi, thay vì `if origin and`.
5. FE-02, FE-03, F1, CMP-G1.
