# 04 — Audit Data + Operations (2026-10-26)

Phạm vi: worker/scheduler, retention, schema/migration, Postgres vs SQLite còn sót, file rác/backup/log, secrets, tests, script chạy, Docker/deploy, chất lượng dữ liệu.
Chế độ: chỉ đọc source. Không start/stop server. Lệnh đã chạy: `git ls-files`, `git grep`, `pytest apps/api/tests` (SQLite tạm), 7 script `apps/api/test_*.py` (SQLite tạm), liệt kê process, `schtasks /Query`.

> **Không đo được số liệu DB**: kết nối Postgres bằng `DATABASE_URL` trong `.env` bị từ chối (`FATAL: password authentication failed for user "postgres"`, xem F3). Vì vậy không có số đếm dòng / null. Mọi kết luận về dữ liệu bên dưới dựa trên code + log.

## Tóm tắt

| Mức | Số lượng | Điểm chính |
|---|---|---|
| HIGH | 3 | 113 MiB build cache của Next.js đã commit + push; API/worker không chạy và không có gì tự khởi động lại (dữ liệu cũ từ 23/10); `.env` không còn đăng nhập được Postgres + dùng superuser với mật khẩu yếu |
| MEDIUM | 9 | lặng lẽ fallback sang SQLite cũ; endpoint scheduler đọc sai process; API và worker khởi động lệch nhau, không chặn 2 worker chạy cùng lúc; EU sync vẫn chạy nền; 18 script test cũ đều hỏng; không có backup tự động; nhiều bảng phình mãi; migration chỉ thêm, không có version; Docker chưa deploy được |
| LOW | 6 | start.ps1 in ra lệnh `create-admin` không tồn tại; log/DB cũ vương vãi, không có log rotation; submodule hỏng; khoá trùng trong `.env`; tunnel công khai làm hỏng login; vòng lặp scheduler bị chặn |

Điểm tốt (đã kiểm chứng):
- **Secrets sạch**: `.env` không được track và chưa từng có trong lịch sử (`git log --all --diff-filter=A -- '*.env'` không ra gì). Không có mẫu khoá (sk-, AIza, gsk_, csk-, ghp_, AKIA, PRIVATE KEY, chuỗi URL Postgres kèm mật khẩu) trong file được track. Đã so khớp đúng giá trị thật của ADMIN_TOKEN, GEMINI, SERPAPI, APIFY, BRIGHTDATA, GROQ, CEREBRAS, EMAILJS_PRIVATE, TIKTOK_CLIENT_SECRET: 0 lần xuất hiện trong file được track và 0 trong mọi `*.log`.
- Toàn bộ `*.log`, `*.db*`, `apps/api/data/`, `_demo_backup/`, `_stale_backup/` đều đã được gitignore (`.gitignore:11-17,30`). Không có log nào bị commit.
- `pytest apps/api/tests`: **399 passed, 3 skipped, 72.6 s** (conftest ép dùng SQLite tạm và xoá trống các khoá dịch vụ trả phí, `tests/conftest.py:6-17`).
- Các mục CRITICAL/HIGH của `docs/AUDIT_2026-10-07.md` (C1–C4, H1–H13) đã được sửa trong code. Ví dụ: dedup creative theo URL bỏ chữ ký + host (`ingest.py:393-396`, commit 98d85c1), index được tạo trong `auto_migrate` (`db.py:155-165`), endpoint có phân trang (`routers/intel.py:270,304,713`).

---

## HIGH

### F1. 113.5 MiB build cache của Next.js đã commit và push lên origin
- `git ls-files`: có 83 file trong `apps/web/.next-library-test/` và 84 file trong `apps/web/.next-search-test/` (webpack `*.pack.gz`, `trace`, chunk). Tổng kích thước blob đo bằng `git cat-file` là **113.5 MiB**, lớn hơn mọi phần khác của repo cộng lại.
- Hai thư mục này vào repo từ commit `4185035 BIG UPDATE` và đã có trên `origin/main` (`git ls-tree origin/main` đếm được 167 file).
- `.gitignore:26-28` chỉ bỏ qua `.next/`, `.next-workspace-test/` và `.next-validation/`. `git check-ignore apps/web/.next-search-test/trace` trả về "not ignored".
- `apps/web/tsconfig.tsbuildinfo` cũng đang bị track, nên lần build nào cũng làm nó hiện trong `git status`.
- **Cách sửa**: thay các dòng trên trong `.gitignore` bằng `.next*/` và thêm `*.tsbuildinfo`, rồi chạy `git rm -r --cached apps/web/.next-library-test apps/web/.next-search-test apps/web/tsconfig.tsbuildinfo`. Việc dọn lịch sử bằng `git filter-repo` (để clone nhẹ hơn) là tuỳ chọn, cần anh quyết.

### F2. API và worker không chạy, không có gì tự khởi động lại. Dữ liệu đang cũ ("stale server" vẫn còn)
- Danh sách process lúc audit (26/10 sáng) không có `uvicorn app.main:app` và không có `app.worker`. Postgres khởi động lại lúc 25/10 15:28, tức máy đã reboot.
- Dòng log cuối của worker: `apps/api/worker_err.log`, `2026-10-23 17:25:30 ... spy: {...}`. Nghĩa là gần 3 ngày nay không có sync, liveness, re-score hay retention nào chạy.
- `schtasks`: các task `ToolSpyAPI`, `ToolSpyRestart`, `ToolSpyImageEmbed`, `ToolSpyImageIndex` đều `Next Run Time: N/A`, tức là task ONCE/chạy tay. Không task nào chạy khi đăng nhập/khởi động máy, cũng không có restart-on-failure. `start.ps1:23-28` dùng `Start-Process -WindowStyle Hidden`, chết là mất luôn.
- Trạng thái scheduler chỉ nằm trong RAM (`realtime.py:331-354`). Mỗi lần restart, mọi job chạy dồn ngay lập tức, gồm cả `score_all` toàn bộ (`_last_daily = None`, `realtime.py:331,445`).
- **Cách sửa**: tạo một scheduled task `ONLOGON`/`ONSTART` chạy `start.ps1`, có restart-on-failure (Settings → "If the task fails, restart every 1 minute"), hoặc NSSM. Worker ghi heartbeat (`app_settings.worker_heartbeat = utcnow`) ở mỗi tick. `/api/health` trả về tuổi của heartbeat, UI hiện banner "dữ liệu dừng cập nhật từ …" khi heartbeat cũ hơn 10 phút.

### F3. `.env` không còn đăng nhập được Postgres, và đang dùng superuser với mật khẩu yếu
- `create_engine(DATABASE_URL)` (`db.py:13-23`) với URL trong `.env` cho lỗi `password authentication failed for user "postgres"`. Postgres vẫn đang listen ở `0.0.0.0:5432` (theo `netstat`). Biến shell `DATABASE_URL` không được đặt, nên giá trị đúng là giá trị trong `.env`. Hệ quả: chạy `start.ps1` bây giờ thì API chết ngay ở startup (`workspace.migrate()` → `auto_migrate()`).
- Role đang dùng là `postgres`, tức superuser. Mật khẩu chỉ có 5 ký tự, dễ đoán, và nằm nguyên văn trong memory note `toolspy-postgres-two-process.md` (không có trong repo). Postgres lại listen trên mọi interface (`0.0.0.0`).
- **Cách sửa**: tạo role `toolspy_app` (chỉ có quyền owner trên DB `toolspy`), đặt mật khẩu mạnh rồi cập nhật `.env`. Đặt `listen_addresses = 'localhost'` nếu không cần truy cập từ LAN. Xoá mật khẩu khỏi memory note.

---

## MEDIUM

### F4. Lặng lẽ fallback sang file SQLite cũ
- `db.py:13`: `DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./market_intel.db")`. `start.ps1` chạy API với working dir là `apps/api`, và ngay ở đó đang có `market_intel.db` 235 MB (cũ từ 09/10, trước khi chuyển sang Postgres), cộng thêm `market_intel.backup-20261007.db` 199 MB và `market_intel.backup-20261009.db` 235 MB.
- Nếu `.env` mất dòng `DATABASE_URL` (hoặc container chạy thiếu env), app vẫn khởi động bình thường nhưng phục vụ dữ liệu của ngày 09/10, không có lỗi nào báo ra.
- **Cách sửa**: khi thiếu `DATABASE_URL` thì `raise SystemExit` ngay. Test vẫn chạy được vì `conftest.py:11` luôn đặt sẵn biến này. Sau khi xác nhận Postgres đủ dữ liệu, dời hoặc xoá 3 file `.db` (khoảng 670 MB).

### F5. `/api/collector/scheduler` báo trạng thái của process API, không phải worker (mục 10 trong FULL_TEST_REPORT vẫn chưa sửa)
- `routers/intel.py:1513-1521` trả về `realtime._running`, `_last_liveness`, `_last_cleanup`, `_last_daily`. Đây là biến RAM của process API, trong khi `.env` đặt `DISABLE_SCHEDULER=1` nên scheduler thật chạy trong `python -m app.worker`. Kết quả là endpoint luôn trả về rỗng, `0001-01-01` hoặc `None`.
- **Cách sửa**: dùng chung heartbeat với F2. Worker ghi `{job: last_run_at, running: [...]}` vào `app_settings`, endpoint đọc từ DB.

### F6. API và worker khởi động khác nhau, và không có gì ngăn 2 scheduler chạy cùng lúc
- Khi API tự chạy scheduler (`main.py:148-155`), nó chạy `scheduler_loop` và `tracker_loop` nhưng **không chạy** `preferences.loop` và `recommend.loop`. Worker (`worker.py:25-28`) thì chạy cả 4.
- Worker **không gọi** `similar.migrate()` (index pg_trgm) và `personalization_migrate.migrate()`. API gọi cả hai (`main.py:123-126`). `worker.py:10` import `auto_migrate` nhưng không dùng.
- `worker.py:5` ghi "Run exactly one worker" nhưng không có khoá nào đảm bảo điều đó. Chạy 2 worker, hoặc worker cộng với API bị quên `DISABLE_SCHEDULER`, thì mọi job chạy đôi (gọi Meta gấp đôi, cluster merge chạy song song).
- **Cách sửa**: tách một hàm `start_background()` để cả `main._startup` lẫn `worker` cùng gọi. Khi worker khởi động thì lấy `pg_try_advisory_lock(727003)` trên một connection giữ suốt đời process; không lấy được khoá thì log rồi thoát.

### F7. EU Market chỉ mới ẩn trên UI, backend vẫn chạy (phải xử lý thêm khi ẩn)
- UI: `apps/web/components/layout/nav.ts:8,42` (`NEXT_PUBLIC_EU_MARKET`) và `apps/web/app/eu-market/layout.tsx` (chặn khi vào bằng link trực tiếp) đã có, nhưng còn **chưa commit**.
- Backend vẫn bật: `.env` có `EU_MARKET_ENABLED=1`, và mặc định trong code cũng là `"1"` (`eu_market/jobs.py:33-34`). Router `/api/eu-market` và `/api/agent/v1` vẫn được mount (`main.py:85-91`).
- Scheduler vẫn chạy EU sync mỗi 360 phút (`realtime.py:429-435`). Lý do là `any_configured()` (`eu_market/jobs.py:179-180`) luôn trả về True, vì `jsonld.configured()` và `allegro.configured()` đều `return True` (`connectors/jsonld.py:207-208`, `connectors/allegro.py:16-17`), dù không có khoá Kaufland/bol/BigBuy nào.
- **Không được xoá package `eu_market`**: các module không thuộc EU vẫn import nó, cụ thể `pricing/extract.py:14` (jsonld), `pricing/router.py:22`, `visual/service.py:28-29`, `visual/routes.py:30-31`, `visual/lens.py:402-621` và `routers/bridge.py`. Bảng EU được đăng ký qua `models.py:538`.
- **Cách sửa**: đặt `EU_MARKET_ENABLED=0` trong `.env`. Việc này tắt route lẫn scheduler, và `visual/routes.py:109` đã tự bỏ qua `upsert_items` khi tắt. Cần chạy smoke test `/visual` và `/api/prices` sau khi đổi. Commit 2 file UI. Đánh dấu các test EU (xem F8) là skip.

### F8. 18 script `apps/api/test_*.py` ở thư mục gốc đã cũ và hỏng, pytest cũng không chạy tới
- Có 18 file được track, không file nào có hàm `def test_`. Chúng là script assert ở mức module, nằm ngoài `tests/`, nên `pytest apps/api/tests` bỏ qua hết. 6 file thuộc EU/Allegro.
- Đã chạy 7 file, cả 7 với `DATABASE_URL` trỏ vào SQLite tạm:
  - `test_ingest_rules.py:13`: `AssertionError`. Test mong `bare_url()` giữ lại host, nhưng từ commit 98d85c1 `ingest.py:396` đã bỏ host.
  - `test_image_search.py:172`: `AssertionError` (`stale`).
  - `test_price_intelligence.py:98`: `KeyError: 'prices'`.
  - `test_scoring_rules.py`, `test_search_keywords.py`, `test_media_rules.py`, `test_auth.py`: `no such table: external_api_calls`. Các script này không tự tạo schema, và nếu không ép env thì chúng chạy thẳng vào DB thật trong `.env`. `test_media_rules.py:81` còn mặc định trỏ vào `market_intel.db`.
  - `test_auth.py:5-22` vẫn test cơ chế cũ `X-Admin-Token`.
- `AUDIT_2026-10-07.md:195` coi mấy script này là "self-check xanh", nhưng điều đó không còn đúng.
- **Cách sửa**: chuyển những assert còn giá trị (scoring, ingest/funnel, search keywords) vào `tests/` dưới dạng `def test_*`, sửa lại theo hành vi hiện tại của `bare_url`. Xoá phần còn lại, gồm script live (`test_lens_live.py`, `test_meta_ssr.py`), script EU/Allegro và `test_auth.py`.

### F9. Không có backup DB tự động
- Backup duy nhất là 2 file `pg_dump` làm tay ngày 13/10: `apps/api/data/backups/toolspy-20261013-pre-naming.dump` và `toolspy-20261013-pre-shop.dump` (khoảng 300 MB mỗi file). Cả hai nằm trên cùng ổ D: với DB.
- `git grep pg_dump` chỉ ra docs và lời khuyên trong `backfill_names.py:13`. Trong khi đó vẫn có job tự động sửa dữ liệu chạy thường xuyên, ví dụ cluster auto-merge mỗi 180 phút (`realtime.py:352,421-424`; memory: 86 lần merge live).
- **Cách sửa**: một schtasks chạy hằng ngày lệnh `pg_dump -Fc toolspy > data/backups/toolspy-%date%.dump`, giữ 7 bản, và đẩy bản mới nhất lên R2 (đã có `app/r2_sync.py` và `CLOUDFLARE_*`).

### F10. Nhiều bảng phình mãi, không có retention
- `retention.run_cleanup` chỉ xoá bảng `events` (`retention.py:313-318`), file media và raw jsonl. `lens.py:517-519` chỉ xoá trắng cột `raw`.
- Không có đoạn `delete(...)` nào cho các bảng: `mkt_behavior_events`, `search_jobs`, `external_api_calls`, `page_fetches`, `advertiser_ad_snapshots`, `advertiser_activity_events`, `mkt_notifications`, `mkt_activity`, `price_jobs`, `engagement_jobs`, `raw_records`, `auth_rate_limits`, và `user_sessions` đã hết hạn (session chỉ bị xoá khi logout hoặc đổi mật khẩu, `workspace/accounts.py:89,104`, `auth.py:118`). Ba bảng `*_daily_snapshots` tăng theo công thức số product × số ngày.
- **Cách sửa**: một dict `{bảng: (cột thời gian, số ngày)}` và một vòng lặp `DELETE ... WHERE col < now() - N days` trong `run_cleanup`. Behavior events cần giữ đủ lâu cho decay của preference profile (Phase 4).

### F11. Migration chỉ thêm, không có version; schema thật lệch so với model
- `auto_migrate` (`db.py:135-165`) chỉ thêm bảng, thêm cột nullable và thêm index. Docstring ghi "use Alembic for anything destructive", nhưng Alembic không có trong `requirements.txt`.
- Có 4 module migrate riêng lẻ (`workspace/migrate.py`, `cluster_migrate.py`, `similar.migrate`, `personalization_migrate.py`), và API với worker gọi hai tập khác nhau (F6).
- Cột `product_images.embedding_vec vector(512)` được tạo tay theo `docs/sql/pgvector_migration.sql:5-7` và không có trong model. Vì thế mỗi lần khởi động đều ra `SAWarning: Did not recognize type 'vector'` (`apps/api/api_err.log`). Một DB mới tinh (Docker hoặc VPS) sẽ không có cột này; clustering vẫn tự fallback được (`services/clustering.py:161-168`).
- **Cách sửa**: chưa cần Alembic (YAGNI). Gom 4 hàm migrate vào một `migrate_all()` và ghi một dòng vào `workspace_migrations` cho từng bước. Đưa DDL pgvector vào một hàm, có `try` để bỏ qua khi thiếu extension.

### F12. Docker chưa sẵn sàng để deploy
- `apps/api/requirements.txt`: không có dòng nào được pin, toàn `>=`, nên mỗi lần build có thể ra bản khác nhau. Còn thiếu `onnxruntime` (`visual/embed.py:69`, trong khi `.env` đặt `IMAGE_EMBED_ENABLED=1`), `imageio-ffmpeg` (`media.py:55`) và `requests` (`collector/adapters/meta.py:88`, gói này đang có trong venv chỉ do phụ thuộc gián tiếp).
- `apps/api/Dockerfile`: không có Tesseract (`creative_enrich` OCR), không có browser cho Playwright (comment trong requirements ghi "drives the installed Edge/Chrome"), không có process worker, chạy bằng root, không có HEALTHCHECK. Repo cũng không có `docker-compose`, nên không có định nghĩa Postgres + API + worker + web.
- `apps/web/Dockerfile:14-16` nhúng `ADMIN_TOKEN` vào image qua `ARG`/`ENV`, nên token nằm trong layer của image. Thực tế web không dùng nó: `middleware.js:9` xoá header `x-admin-token`, và không file web nào đọc `ADMIN_TOKEN`.
- **Cách sửa**: tạo `requirements.lock` từ `pip freeze`, thêm 3 gói còn thiếu, thêm `CMD` cho worker (cùng image, khác command), viết một `compose.yaml` 4 service, và bỏ `ADMIN_TOKEN` khỏi web Dockerfile.

---

## LOW

### F13. Hướng dẫn trong script và docs mâu thuẫn với chính sách chỉ có tài khoản MKT
- `start.ps1:31` in ra `python -m app.workspace.cli create-admin`, nhưng CLI chỉ có `create-user | reset-password | lock | unlock` (`workspace/cli.py:21`), nên lệnh này lỗi. Nó cũng trái với quy định không có ADMIN (`workspace/cli.py:1`).
- `docs/auth-competitor.env.example:19` vẫn ghi "requests appear in ADMIN".
- **Cách sửa**: sửa câu chữ thành `create-user`.

### F14. Log, DB cũ và file rác vương vãi, không có log rotation
- Có hơn 40 file `*.log` rải ở 3 nơi: thư mục gốc (`api_run.log` 287 KB, `tunnel.log`, `web_run.log`), `apps/api/` (`worker_err.log` 1 MB và vẫn đang lớn lên, các file `*_hidden_v3_*`, `*_eu*`), và `apps/api/data/`. Không file nào bị commit, nhưng cũng không có rotation: `start.ps1` chuyển hướng output qua schtasks, và `logging.basicConfig` không có handler nào để xoay vòng.
- Các file rác đang bị track: `apps/api/calib_clip_full.out`, `eval_clip.out`, `headless_top_ads_sample.json` (340 KB dữ liệu ad cào được), `calib_cosine.py`, `eval_image_search.py`, `headless_top_ads.py`, `test/index.html`.
- **Cách sửa**: gom log về `apps/api/data/logs/` và dùng `RotatingFileHandler` (10 MB × 5). Chạy `git rm` các file rác (script eval nào còn dùng thì chuyển vào `scripts/`).

### F15. `demotheme/ui` là gitlink nhưng không có `.gitmodules`
- `git ls-files -s demotheme/ui` cho ra mode `160000`, nhưng repo không có file `.gitmodules`. Ai clone repo về sẽ thấy thư mục rỗng hoặc gặp lỗi submodule.
- **Cách sửa**: `git rm --cached demotheme/ui` (hoặc thêm vào `.gitignore` giống `reference/`).

### F16. `.env` khai báo trùng khoá và còn cấu hình cũ gây hiểu nhầm
- `CLOUDFLARE_API_TOKEN` xuất hiện 2 lần. Loader Python (`app/__init__.py:19-21`) lấy dòng **đầu**, còn `start.ps1:13` lấy dòng **cuối** vì `SetEnvironmentVariable` ghi đè. Vì vậy chạy qua start.ps1 và chạy uvicorn trực tiếp sẽ dùng 2 token khác nhau.
- `VIDEO_DAILY_QUOTA=3000` nhưng mặc định `VIDEO_DOWNLOAD=0` (`media.py:346`), nên không có video nào được tải, đúng quyết định của anh. Docstring `retention.py:3-8` vẫn mô tả "3000 video/ngày", dễ khiến người đọc hiểu sai.
- **Cách sửa**: xoá dòng trùng, sửa docstring.

### F17. Quick tunnel công khai làm hỏng thao tác ghi và login
- `run_tunnel.cmd` mở `:3000` ra internet qua `*.trycloudflare.com` (URL ghi trong `tunnel.log`). `.env` có `WEB_ORIGIN=http://localhost:3000` và không có `APP_URL`.
- `workspace/auth.py:163-168` từ chối mọi request ghi có `Origin` nằm ngoài `WEB_ORIGIN` và `APP_URL`, nên qua tunnel thì POST (kể cả login) rất có thể bị 403. Ngoài ra `auth.py:91-93` đặt cookie không có `Secure`.
- **Cách sửa**: khi dùng tunnel thì đặt `APP_URL=<url tunnel>` và `AUTH_COOKIE_SECURE=1`. Tốt hơn là dùng named tunnel kèm Cloudflare Access.

### F18. Vòng lặp scheduler bị chặn và giới hạn song song không áp cho mọi job
- `realtime.py:442-447`: `_cleanup_job` và `_daily_job` (gồm `score_all` và `build_snapshots` toàn bộ) chạy bằng `await asyncio.to_thread(...)` ngay trong vòng lặp. Trong lúc chúng chạy, không job nào khác được điều phối, kể cả tracked query PH 30 phút.
- Các wrapper spy, naming, creative, cluster, engagement, price và eu (`realtime.py:527-612`) không lấy `_slots`, nên `MAX_PARALLEL_JOBS` không giới hạn được chúng; có lúc tới khoảng 9 thread chạy cùng lúc.
- **Cách sửa**: chạy hai job này bằng `run_in_executor` với cờ `_running` như các job khác, và cho các wrapper nặng lấy `_slots`.

---

## Chất lượng dữ liệu (từ memory, đối chiếu với code)

| Vấn đề trong memory/audit cũ | Trạng thái theo code | Bằng chứng |
|---|---|---|
| Creative trùng do URL có chữ ký | Đã sửa cho dữ liệu mới (bỏ query, chữ ký TikTok và host). Có CLI dọn dữ liệu cũ, nhưng **không xác minh được** đã chạy trên DB live hay chưa (F3) | `ingest.py:393-405`, `app/dedupe_creatives.py`, commit 98d85c1 |
| Scoring phẳng (85% WATCH) | Đã recalibrate theo percentile (AUDIT §9). Chưa đo lại được phân phối | `AUDIT_2026-10-07.md:216` |
| Server cũ ("stale server") | **Vẫn còn**, nặng hơn: hiện không có process nào chạy | F2 |
| Endpoint scheduler báo sai process | **Vẫn còn** | F5 |
| Bảng mồ côi `idealo_clients`/`idealo_tokens` | Không còn model nào trong code tham chiếu (`grep __tablename__`). **Chưa xác minh** bảng còn trong PG hay không (F3). Code tín hiệu Etsy/idealo còn sót trong `eu_market/analytics.py:141-148` | memory `toolspy-eu-market-sources.md` |
| Connector `meta_ads` không có adapter | `ConnectorFactory.get` ném `KeyError` (`collector/factory.py:28-31`). **Chưa xác minh** dòng connector còn trong DB hay không | FULL_TEST_REPORT #4 |

Câu SQL nên chạy khi đã có quyền DB (F3):
```sql
SELECT relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_stat_user_tables ORDER BY 3 DESC;
SELECT count(*) FILTER (WHERE first_seen_at IS NULL), count(*) FILTER (WHERE landing_url IS NULL), count(*) FROM ads;
SELECT count(*) - count(DISTINCT (ad_id, sha256)) AS dup_rows FROM creatives WHERE status='stored';
SELECT to_regclass('idealo_clients'), to_regclass('idealo_tokens');
SELECT id, adapter FROM connectors WHERE adapter NOT IN (/* ConnectorFactory.connectors */);
```

## Phân loại test
- `apps/api/tests/` có 28 file, gồm `conftest.py`, chạy trên SQLite tạm hoặc DB `toolspy_test_*`. Kết quả: **399 passed / 3 skipped / 72.6 s**. Chỉ có cảnh báo `on_event deprecated` (`main.py:94,158`).
- `apps/api/test_*.py` có 18 script cũ, không chạy qua pytest. 7 file đã chạy thử đều hỏng (F8).
- `apps/extension/test/test_injected.js` và `extensions/allegro-bridge/test/*.test.js`: chưa chạy (ngoài phạm vi).
