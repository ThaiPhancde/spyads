# Meta Collector Upgrade

Spec: [Meta_Collector_Reliability_and_Video_Intelligence_Upgrade.md](Meta_Collector_Reliability_and_Video_Intelligence_Upgrade.md).
Trạng thái: **Phase 1–5 xong, đã nghiệm thu** (2026-11-03). Kết quả nghiệm thu: mục "Nghiệm thu toàn bộ".
Gia cố Phase 1 sau gap audit (nhánh `meta-phase1-hardening`): mục cuối, chi tiết ở [META_RELIABILITY_GAP_AUDIT.md](META_RELIABILITY_GAP_AUDIT.md).

## Phase 1 — Meta Collector Reliability

### Audit: các lỗi tìm thấy trong code cũ

| # | Chỗ | Lỗi | Hậu quả |
|---|-----|-----|---------|
| 1 | `realtime._run_search` | `state[key] = st` được ghi **trước** khi ingest batch | Batch lưu lỗi nhưng cursor vẫn tiến → mất trang đó vĩnh viễn |
| 2 | `ingest_ads` | Commit giữa chừng (trước lookup, sau screen, cuối hàm) và publish event / enqueue media trước khi caller commit | Không có ranh giới transaction; batch lỗi để lại ads dở dang; event "đã lưu" phát ra cho dữ liệu bị rollback |
| 3 | `meta.fetch_page` | Dedup collation chỉ trong 1 trang (`ponytail: dedupe within a page only`) | Sibling nằm ở trang khác / job khác thành ad trùng |
| 4 | `meta.fetch_page` | Response lỗi (`error`, `rate_limited`, `session_expired`) hoặc thiếu `page_info` bị coi là trang rỗng / hết trang | Lỗi nguồn bị hiểu là "hết kết quả", job báo `done` |
| 5 | `fetch_ads` / `_run_search` | 3 trang rỗng liên tiếp → `done = True` | Meta trả trang rỗng giữa dòng; tự tuyên bố hết trang làm mất phần đuôi |
| 6 | `fetch_ads` | `break` khi đạt `limit` giữa trang, rồi checkpoint cursor của trang kế | Phần còn lại của trang bị bỏ qua mãi mãi |
| 7 | `ssr_search` | Tự POST URL verify khi gặp access challenge | Vượt cơ chế kiểm soát truy cập (trái ràng buộc của spec) |
| 8 | Extension `content.js` | Hàng đợi trong bộ nhớ tab, bỏ sau 3 lần thử | Đóng tab / mất mạng / API tắt = mất batch |
| 9 | `/api/ingest/meta-library` | Không có idempotency | Gửi lại sau khi mất ACK → đếm trùng, ghi trùng |

### Kiến trúc sau khi sửa

```
Extension                          API                                    DB (một transaction)
content.js ── batch 20 ads ──▶ background.js
            (idempotency_key)    IndexedDB outbox ── POST + key ──▶ ingest_batch(key)
                                 retry 1s→2s→…→1h   ◀── ACK(key) ──   ├─ IngestBatch(key, hash, result)   ← receipt
                                 xoá row chỉ khi ACK                  ├─ ads / ad_sources / meta_collations
                                                                      └─ (search) job.state checkpoint
                                                                      commit → events + media enqueue
```

- **`collector/batches.py` — `ingest_batch(db, recs, key)`**: chạy `ingest_ads(..., atomic=True)` không commit bên trong; ghi receipt `IngestBatch`. Caller commit receipt + dữ liệu + checkpoint cùng lúc. Key đã có: payload giống → trả kết quả cũ; payload khác → `BatchConflict` (HTTP 409).
- **Event/media sau commit**: trong chế độ atomic, `events.publish` và `media.enqueue` được hoãn vào `db.info`, chỉ phát ở `after_commit` của transaction ngoài; `after_rollback` thì bỏ.
- **Checkpoint**: `_run_search` chỉ ghi `job.state[key]` trong cùng commit với batch. Batch lỗi → rollback state/counters, bỏ qua các batch sau của nguồn đó, job `error` + `has_more` để "load more" chạy lại đúng trang. `run_connector` (scheduled) chỉ lưu `TrackedQuery.cursor` khi batch commit thành công.
- **Collation xuyên trang/xuyên job**: bảng `meta_collations (source, page_id, collation_id) → ad_id`. Sibling giữ lại làm record, được map vào cùng 1 Ad dưới dạng `AdSource` alias. Refresh thiếu `collation_id` vẫn giữ alias đã xác minh.
- **Trạng thái trang** (`st["page_status"]`): `page` / `empty` / `end`. Chỉ `page_info.has_next_page = false` mới là `end`. Error / rate limit / session expired / thiếu `ads` hoặc `page_info` / cursor không tiến → `ConnectorError`. 3 trang rỗng liên tiếp → **tạm dừng** (không đánh dấu `done`).
- **Access challenge** → `ConnectorError("user action required")`, không tự vượt.
- **Extension**: `outbox.js` (IndexedDB `toolspy-outbox`), mỗi batch có `idempotency_key` UUID cố định qua mọi lần retry; backoff `min(1h, 2^n s)`; drain khi worker start, `chrome.alarms` mỗi phút, và ngay sau khi nhận batch. Batch gắn với API endpoint lúc tạo, đổi API thì không gửi nhầm sang server khác.

### File thay đổi

| File | Thay đổi |
|------|----------|
| `apps/api/app/collector/batches.py` | Mới — `ingest_batch`, `digest`, `BatchConflict`, hook after_commit/after_rollback |
| `apps/api/app/meta_collector_migrate.py` | Mới — migration additive, lặp lại được, advisory lock trên PG |
| `apps/api/app/models.py` | Bảng `collector_ingest_batches`, `meta_collations` |
| `apps/api/app/db.py` | `migrate_all()` gọi migration mới |
| `apps/api/app/ingest.py` | `atomic=` (không commit, không LLM, hoãn media), lookup/ghi collation, sibling không được đánh dấu ad đã dừng |
| `apps/api/app/events.py` | `publish` hoãn trong atomic; tách `deliver()` |
| `apps/api/app/realtime.py` | Checkpoint sau commit, rollback khi batch lỗi, không tự `done` sau trang rỗng |
| `apps/api/app/collector/adapters/meta.py` | Phân loại lỗi/rỗng/hết trang, bỏ dedup trong trang, không vượt challenge, không cắt giữa trang |
| `apps/api/app/routers/intel.py` | `/api/ingest/meta-library`: `idempotency_key`, 409 khi trùng key khác payload, 422 khi thiếu `ad_archive_id`, tối đa 500 items |
| `apps/api/app/workspace/sources.py` | Bỏ code `siblings` đã chết |
| `apps/extension/outbox.js`, `background.js`, `content.js`, `manifest.json` | Outbox IndexedDB + retry; quyền `alarms`; version 0.2.0 |

### Đánh đổi đã biết

- **Batch atomic không gọi LLM naming** (Meta search, scheduled Meta, extension). Gọi mạng hàng phút trong lúc giữ transaction là tệ hơn. Ad mới thiếu tên vẫn được đặt tên bằng rule; còn UNKNOWN thì `python -m app.backfill_names --llm N` xử lý sau.
- **Nguồn khác Meta** vẫn dùng `ingest_ads` cũ (không atomic). Batch lỗi của nguồn khác không làm cả job `error` (Meta là nguồn chính, như trước).
- Sau khi một batch Meta lỗi, thread fetch của nguồn đó vẫn chạy tới hết target nhưng kết quả bị bỏ; lượt "load more" sau sẽ fetch lại từ checkpoint.
- Bỏ tự vượt access challenge có thể làm fallback SSR (chỉ dùng khi GraphQL trang 1 hỏng) thất bại nhiều hơn — đó là chủ đích.
- Receipt `collector_ingest_batches` chưa có TTL; bảng tăng 1 dòng/batch. Thêm job dọn khi bảng lớn (Phase 3 job queue là chỗ hợp lý).

### Migration

Additive, không đụng dữ liệu cũ. Chạy tự động khi API/worker start (`auto_migrate` + `migrate_all`), hoặc tay:

```
cd apps/api && .venv/Scripts/python -m app.meta_collector_migrate
```

Rollback: `DROP TABLE meta_collations, collector_ingest_batches;` (code cũ không đọc 2 bảng này).

### Kết quả kiểm thử (2026-11-03, chạy thật)

| Bộ test | Kết quả |
|---------|---------|
| `tests/test_meta_reliability.py` trên SQLite | 16 passed |
| `tests/test_meta_reliability.py` trên PostgreSQL 18 (`toolspy_test_meta`) | 16 passed |
| `tests/test_meta_outbox_browser.py` (Chromium thật, IndexedDB qua restart) | 1 passed |
| `apps/extension/test/test_outbox.js` (`node --test`) | 2 passed |
| Toàn bộ `apps/api/tests` | 457 passed, 3 skipped |

Các test failure-recovery chính:
- lỗi ghi giữa batch → không còn ad / receipt / collation nào; retry cùng key → lưu đủ; gửi lại → kết quả cũ; key cũ + payload mới → `BatchConflict`
- lỗi ở bước commit → receipt và ads đều rollback, retry thành công
- search job: batch trang 2 lỗi → cursor dừng ở trang 1, job `error` + `has_more`; "load more" → đi tiếp tới trang 3, `done`
- scheduled sync lỗi → `TrackedQuery.cursor` giữ nguyên
- không có commit / event nào trước checkpoint
- collation trùng qua 4 session/job → 1 Ad, 2 AdSource
- sibling `is_active=false` không làm dừng nhóm đang chạy
- 5 kiểu response lỗi không bị coi là hết trang; trang rỗng có `has_next_page` → `empty`, không `done`
- outbox: offline → giữ row, backoff; mất ACK → gửi lại cùng key; browser restart → row còn

Chưa kiểm thử: chạy live với Meta thật và extension cài trong Chrome thật (cần đăng nhập + thao tác tay).

## Phase 2 — Video Fingerprint

### Audit: hiện trạng trước khi sửa

- `media.dhash()` chỉ hash **1 poster** (ảnh preview của nguồn, hoặc frame ở giây thứ 1). `assign_family` gộp creative theo poster dHash ≤ 10 bit → nhiều bản cắt khác nhau dùng chung 1 thumbnail bị gộp thành 1 family; cùng video nhưng đổi cover thì bị tách.
- `videos.py` (nhóm "cùng video" giữa các page) dựa trên pHash ảnh cover — ghi chú `ponytail` đã nêu hướng nâng cấp là hash nhiều frame.
- **Lỗi phát hiện thêm:** máy chỉ có ffmpeg của `imageio_ffmpeg`, không có `ffprobe` → `_probe()` trả `{}`; video đã lưu không có `duration_sec` / `width` / `height`.

### Thiết kế

- `media.video_fingerprint(path, duration)` → `"h20:h50:h80"` (dHash 64-bit tại 20 / 50 / 80 % thời lượng) và `confidence` = số frame dùng được / 3. Frame trắng/đen (popcount ≤ 2 hoặc ≥ 62) để trống, không thành bằng chứng.
- `media.same_video(a, b)` → `True` / `False` khi có ≥ 2 vị trí dùng được ở cả hai (mỗi vị trí ≤ `FAMILY_DISTANCE` bit), ngược lại `None` (chưa biết).
- `assign_family`: fingerprint khớp → cùng family (kể cả khác cover); poster giống nhưng fingerprint **khẳng định khác** → tách family; thiếu fingerprint → poster quyết như cũ.
- **Không tải thêm, không re-encode:** fingerprint chỉ được tính trong `_process`, đúng lúc file video đã tải theo luồng hiện có (`VIDEO_DOWNLOAD=1` hoặc lưu theo yêu cầu `POST /api/creatives/{id}/retry`). ffmpeg chỉ decode 1 frame ra JPEG mỗi vị trí. Không mở URL nguồn bằng ffmpeg (sẽ bỏ qua kiểm tra SSRF của `netsafe`). Video chưa tải giữ tín hiệu poster như trước.
- **Không gộp sản phẩm:** fingerprint chỉ vào `Creative.family_id` (dùng để đếm creative khác nhau *trong* 1 sản phẩm: `discovery`, `product_evidence`, `hidden`). Product clustering (`services/clustering.py`) đọc `ProductImage.phash`, không đọc fingerprint. Test khẳng định 2 sản phẩm vẫn là 2 sau khi creative của chúng cùng family.
- `_probe()` fallback: không có ffprobe thì đọc `Duration` / kích thước từ banner `ffmpeg -i`.
- Bản sao cùng sha256 (`_SHARED`) copy luôn fingerprint.

### File thay đổi

| File | Thay đổi |
|------|----------|
| `apps/api/app/media.py` | `video_fingerprint`, `same_video`, `_frame(at=)`, `_probe` fallback, `assign_family`, `_SHARED`, gọi trong `_process` |
| `apps/api/app/models.py` | `Creative.video_fp` (VARCHAR 50), `Creative.video_fp_confidence` (FLOAT) |
| `apps/api/app/meta_collector_migrate.py` | Thêm 2 cột nullable vào `creatives` nếu chưa có |
| `apps/api/app/videos.py` | Cập nhật ghi chú `ponytail` |
| `apps/api/tests/test_video_fingerprint.py` | Mới — 5 test |

### Migration

Additive: `ALTER TABLE creatives ADD COLUMN video_fp VARCHAR(50)`, `video_fp_confidence FLOAT` (nullable, không rewrite bảng trên PG). Tự chạy khi API/worker khởi động, hoặc `python -m app.meta_collector_migrate`. Rollback: `ALTER TABLE creatives DROP COLUMN video_fp, DROP COLUMN video_fp_confidence;`.

### Đánh đổi / chưa làm

- Video đã nằm trong vault từ trước chưa có fingerprint. Chưa viết backfill vì trên R2 việc đó = tải lại hàng loạt; với vault local có thể thêm CLI đọc file đã lưu khi cần.
- `videos.py` (`video_key` giữa các page) vẫn dựa trên cover; chưa dùng fingerprint để nối / tách key.
- `assign_family` vẫn quét tuyến tính mọi creative có hash (như trước). Đủ cho vài chục nghìn video; lớn hơn thì cần index (BK-tree / bucket theo prefix).

### Kết quả kiểm thử (2026-11-03, chạy thật)

| Bộ test | Kết quả |
|---------|---------|
| `tests/test_video_fingerprint.py` trên SQLite | 5 passed |
| Phase 1 + 2 trên PostgreSQL 18 (`toolspy_test_meta`) | 21 passed |
| Toàn bộ `apps/api/tests` | 462 passed, 3 skipped |

Test dùng video tạo bằng ffmpeg `lavfi` (`testsrc2` 5 s): cùng video ở độ phân giải khác → khớp; video lật → khác; video đen → không fingerprint; không có duration → không fingerprint; poster giống + frame khác → tách family; cover khác + frame giống → cùng family, sản phẩm vẫn tách; `_process` lưu fingerprint với đúng 1 lần tải; migration thêm cột vào bảng cũ có dữ liệu, chạy 2 lần không lỗi. Mutation check: gỡ điều kiện phủ quyết poster → test family fail.

## Phase 3 — Persistent Jobs

### Audit: hiện trạng trước khi sửa

| Loại job | Chạy ở đâu | Chống chạy trùng | Retry | Lịch sử |
|----------|-----------|------------------|-------|---------|
| Connector sync / tracked query (`c:` / `q:`) | worker `app.worker` | set `_running` trong RAM + advisory lock 1 scheduler/DB | **Không có** — lỗi thì chờ tới lịch kế (`every_minutes`, có thể vài giờ) | Chỉ `last_sync_at` / `last_error` |
| Nút "Sync" / "Chạy" trên Data & Connectors | API `BackgroundTasks` | **Không có** — chạy song song với worker trên cùng connector / query, cả hai cùng ghi `TrackedQuery.cursor` | Không | Không |
| Live search (`SearchJob`) | thread trong API | `LIVE_JOBS` (API chạy 1 process) | Người dùng bấm lại | `job.state` (checkpoint từ Phase 1) |
| Competitor tracker (`ScanJob`) | worker | **đã có** lease + `run_token` + `SKIP LOCKED` | đã có backoff | đã có |

Thêm: `db.scheduler_lock` ghi chú rằng nếu PostgreSQL restart thì advisory lock mất → worker thứ hai có thể chạy cùng lúc; khi đó `_running` (RAM, mỗi process một bản) không ngăn được job trùng.

### Thiết kế

`app/crawl_queue.py` (cùng mẫu với `ScanJob`, engagement / pricing jobs):

- **`crawl_jobs`** — 1 dòng / key scheduler (`c:<connector>:`, `q:<connector>:<query>`): `status` (`idle` / `running` / `retry_wait` / `error`), `run_token`, `lease_owner` (`host:pid`), `lease_until`, `heartbeat_at`, `retry_count`, `next_retry_at`, `last_error`, `checkpoint` (cursor đã commit gần nhất).
- **`crawl_runs`** — lịch sử từng lần chạy: worker, `attempt`, `status` (`running` / `ok` / `error` / `lost`), `fetched`, `new_ads`, `error`, `checkpoint_before`, `checkpoint_after`.
- **`claim`**: `SELECT … FOR UPDATE SKIP LOCKED` trên dòng của key; từ chối nếu lease khác còn hạn hoặc đang trong backoff; cấp `run_token` mới, lease `CRAWL_LEASE_S` (120 s). Hai worker cùng chèn dòng đầu tiên → một bên `IntegrityError` → `None`.
- **Heartbeat**: `leased()` chạy thread gia hạn lease mỗi `LEASE_S/4` trong lúc job chờ slot và chạy.
- **Fencing**: `heartbeat` / `finish` / `fail` chỉ có hiệu lực với đúng `run_token` đang giữ — worker chết dậy muộn không ghi đè được kết quả của worker mới.
- **Phục hồi**: `recover()` mỗi tick scheduler (cũng là lúc worker khởi động): job `running` hết lease → run `lost`, job `retry_wait` với `next_retry_at = now`. `claim` cũng tự thu hồi lease hết hạn.
- **Retry**: `fail` → `retry_count += 1`, `next_retry_at = now + min(1h, 60s·2^(n-1))`; job retry được scheduler coi là due ngay cả trước lịch thường. Quá `CRAWL_MAX_RETRIES` (3) → `error`, lịch thường chạy lại; lần thành công đặt lại về 0.
- **Checkpoint**: sau khi chạy xong, `TrackedQuery.cursor` (Phase 1 chỉ ghi sau khi batch commit) được chép vào `crawl_jobs.checkpoint` và `crawl_runs.checkpoint_after`.
- **Nút thủ công**: `POST /api/collector/connectors/{id}/sync`, `/queries/{id}/run`, `/export-upload` đi qua cùng `claim` (`realtime.start_crawl`). Đang chạy → `{"queued": false, "already_running": true}`; trang Data & Connectors báo "đang chạy sẵn".
- Scheduler vẫn quyết định **khi nào** job tới hạn (`last_sync_at + every_minutes`); queue quyết định **ai** chạy.

### File thay đổi

| File | Thay đổi |
|------|----------|
| `apps/api/app/crawl_queue.py` | Mới — models `CrawlJob`, `CrawlRun`; `claim`, `heartbeat`, `leased`, `finish`, `fail`, `recover`, `retry_due`, `backoff` |
| `apps/api/app/realtime.py` | `scheduler_loop` gọi `recover` / `retry_due` / `claim`; `_job_wrapper` chạy trong lease và ghi kết quả; `job_key`, `claim_crawl`, `start_crawl` |
| `apps/api/app/routers/intel.py` | 3 endpoint chạy tay đi qua `start_crawl` |
| `apps/api/app/models.py` | Đăng ký `crawl_queue` |
| `apps/api/app/meta_collector_migrate.py` | Tạo `crawl_jobs`, `crawl_runs` |
| `apps/web/app/data/page.tsx` | Hiện trạng thái "đang chạy sẵn" |
| `apps/api/tests/test_crawl_queue.py` | Mới — 9 test |

### Migration

Chỉ tạo 2 bảng mới (`checkfirst`), không đụng bảng cũ. Tự chạy khi API/worker khởi động. Rollback: `DROP TABLE crawl_runs, crawl_jobs;` — code sẽ cần revert cùng lúc, vì scheduler cần 2 bảng này.

### Giới hạn đã biết

- Fencing áp dụng cho dòng job / lịch sử. Bên trong `run_connector` vẫn ghi trực tiếp `Connector.*` và `TrackedQuery.cursor`. Một lần chạy cũ chỉ ghi đè được khi lease hết hạn trong lúc process **còn sống** (heartbeat lỗi DB liên tục > 120 s). Nâng cấp: truyền token vào `run_connector` và ghi cursor có điều kiện `run_token`.
- Thread của một lần chạy mất lease không bị kill (Python không kill được thread); nó chạy nốt, kết quả bị bỏ qua.
- Live search và liveness không đi qua queue: search chạy trong API 1 process (`LIVE_JOBS` đúng), checkpoint đã nằm trong `job.state` → "load more" chạy tiếp. Liveness là job dọn dẹp định kỳ, idempotent.
- `services/pipeline.run_pipeline` (admin chạy tay toàn bộ pipeline) vẫn gọi `run_connector` trực tiếp.
- `crawl_runs` chưa có TTL — khoảng 1 dòng / job / lần chạy. Nên dọn trong `retention` khi bảng lớn.

### Kết quả kiểm thử (2026-11-03, chạy thật)

| Bộ test | Kết quả |
|---------|---------|
| `tests/test_crawl_queue.py` trên SQLite | 8 passed, 1 skipped (test tranh chấp chỉ chạy trên PG) |
| Phase 1 + 2 + 3 trên PostgreSQL 18 (`toolspy_test_meta`) | 30 passed |
| Toàn bộ `apps/api/tests` | 470 passed, 4 skipped |
| `tsc --noEmit` (apps/web) | không lỗi ở `data/page.tsx` |

Test failure-recovery: worker chết giữa chừng → `recover` đánh dấu run `lost`, job retry ngay, worker mới claim token mới, token cũ không ghi được heartbeat / finish / fail; lease hết hạn được thu hồi kể cả khi chưa có lượt `recover`; backoff 1 → 2 → 4 phút rồi `error`, thành công đặt lại; exception trong job không để lại lease kẹt; 8 thread claim cùng 1 key mới trên PG → đúng 1 thắng; dòng đang bị khoá → `SKIP LOCKED` trả `None` ngay, không chờ; heartbeat thật gia hạn lease; checkpoint trước/sau lưu đúng; nút "Sync" khi worker đang giữ lease → `already_running`. Mutation check: bỏ kiểm tra token trong `finish` → test fencing fail; bỏ kiểm tra lease còn hạn trong `claim` → 2 test fail.

## Phase 4 — Browser Collector

### Audit: hiện trạng trước khi sửa

3 chỗ tự mở Edge/Chrome qua Playwright, mỗi chỗ chép lại cùng đoạn launch: `TikTokTopAdsHeadlessConnector._scrape` (Top Ads công khai), `.video` (lấy link video mới khi CDN hết hạn, `media.refresh_source`), `harvest_cc_headers` (profile Creative Center). Tất cả chạy **trong thread của API/worker**:

- Không giới hạn số trình duyệt chạy cùng lúc (scheduler, media refresh, nhiều quốc gia…).
- Chỉ `goto` có timeout 60 s; tổng thời gian không giới hạn. Thread treo không kill được → trình duyệt treo theo.
- Lỗi nguồn không phân loại: CAPTCHA, 403, 429 đều thành `ConnectorError` chung hoặc kết quả rỗng.

### Thiết kế — `app/browser_worker.py`

- **Process riêng mỗi tác vụ**: `run(task, args)` → `python -m app.browser_worker <task> <in.json> <out.json>`. Kết quả trao qua file JSON.
- **Hạn chót cứng**: `TIMEOUTS` theo tác vụ (health 60 s, top ads 240 s, video 90 s, headers 120 s). Quá hạn → **kill cả cây process** (`taskkill /F /T` trên Windows, `killpg` trên POSIX) — gồm Playwright driver và mọi process Edge/Chrome. `proc.kill()` đơn thuần để lại process cháu (đã kiểm chứng bằng mutation test).
- **Giới hạn concurrency**: `capacity()` = min(`BROWSER_MAX_CONCURRENCY` (2), CPU/2, RAM trống / `BROWSER_RAM_MB` (700)), tối thiểu 1; RAM đọc bằng stdlib (`GlobalMemoryStatusEx` / `/proc/meminfo`). Không có slot sau 30 s → `BROWSER_BUSY`.
- **Dọn tài nguyên**: trình duyệt đóng trong `finally` của process con; parent luôn `kill_tree` + xoá file tạm, kể cả khi lỗi.
- **Trạng thái**: `ok` / `blocked` / `rate_limited` / `timeout` / `busy` / `not_configured` / `error`. `Blocked` và `RateLimited` (mới, trong `collector/base.py`) là lớp con của `ConnectorError`, nên mọi chỗ bắt lỗi cũ vẫn chạy.
- **Không vượt kiểm soát truy cập**: trang có CAPTCHA / verify / challenge form, hoặc tài liệu chính trả 401/403 → `blocked`, dừng. Source API trả 429 mà chưa có dữ liệu → `rate_limited`.
- **Chỉ nguồn được phép**: chỉ chạy tác vụ trong `TASKS` (`health` + 3 tác vụ Creative Center công khai); không nhận URL tuỳ ý.
- **Áp vào connector**: `Connector.health` = `blocked` / `rate_limited`. Crawl job (Phase 3) bị `blocked` → trạng thái `blocked`, **không retry nhanh** (gõ lại challenge không làm nó biến mất), lịch thường thử lại; `rate_limited` đi đường retry + backoff.
- Meta (Phase 1) dùng cùng loại lỗi: access challenge → `Blocked`, payload `rate_limited` → `RateLimited`.
- Không cần mẹo `ThreadPoolExecutor(1)` nữa (sync Playwright trong thread asyncio) vì Playwright chạy ở process khác.

### File thay đổi

| File | Thay đổi |
|------|----------|
| `apps/api/app/browser_worker.py` | Mới — parent (`run`, `capacity`, `kill_tree`) + child (`launch`, `challenge`, `Watch`, `TASKS`, `main`) |
| `apps/api/app/collector/adapters/ad_libraries.py` | `_scrape`, `video`, `harvest_cc_headers` gọi `browser_worker.run`; xoá 3 bản launch trùng |
| `apps/api/app/collector/base.py` | `Blocked`, `RateLimited` |
| `apps/api/app/collector/adapters/meta.py` | Challenge → `Blocked`, rate limit → `RateLimited` |
| `apps/api/app/realtime.py` | health `blocked` / `rate_limited`; truyền `blocked` cho crawl queue |
| `apps/api/app/crawl_queue.py` | `fail(blocked=True)` → trạng thái `blocked` |
| `apps/api/tests/test_browser_worker.py` | Mới — 12 test |

### Cấu hình

| Env | Mặc định | Ý nghĩa |
|-----|----------|---------|
| `BROWSER_MAX_CONCURRENCY` | 2 | Trần số trình duyệt mỗi process |
| `BROWSER_RAM_MB` | 700 | RAM dự trù cho mỗi trình duyệt |
| `BROWSER_CHANNEL` | (Edge → Chrome) | Ép channel; `chromium` = bản Playwright (dùng trong test) |

### Giới hạn đã biết

- Trần concurrency tính **theo process**: API và worker mỗi bên tối đa `capacity()`. Cần trần chung thì dùng pool advisory lock trên PG.
- `scripts/tiktok_login.py` (đăng nhập tay một lần, có giao diện) vẫn tự mở trình duyệt — đó là bước người dùng thao tác, không phải collector.
- Mỗi tác vụ tốn thêm ~1–2 s để khởi động process Python con; không đáng kể so với 10–60 s của một lần tải trang.

### Kết quả kiểm thử (2026-11-03, chạy thật)

| Bộ test | Kết quả |
|---------|---------|
| `tests/test_browser_worker.py` | 12 passed |
| `browser_worker.run("health")` với **Edge đã cài trên máy** | `{'browser': '156.0.4314.58', 'text': 'ok'}`, capacity 2 |
| Toàn bộ `apps/api/tests` | 482 passed, 4 skipped |

Test gồm: process con chạy Chromium thật; quá hạn 3 s → process cháu (giả lập trình duyệt) bị kill thật (kiểm tra PID bằng `OpenProcess`); con trả `blocked` / `rate_limited` / `error` → đúng loại exception; con crash không có kết quả → `BROWSER_ERROR` kèm stderr; hết slot → `BROWSER_BUSY`; tác vụ không đăng ký bị từ chối; trang thật có 429 → `RateLimited`, có phần tử CAPTCHA → `Blocked`; connector bị `Blocked` → health `blocked`, crawl job `blocked` không lên lịch retry; Meta `rate_limited` → `RateLimited`. Mutation: thay `taskkill /T` bằng `proc.kill()` → test kill cây process fail.

## Phase 5 — Monitoring

### Audit: hiện trạng trước khi sửa

Trang Data & Connectors chỉ có `health`, `last_error`, `last_sync_count` của lần sync **cuối**. Không biết đã lấy bao nhiêu trang, bao nhiêu ad trùng / không hợp lệ, cursor đang ở đâu, có đang chờ retry không, cũng không biết bao nhiêu media còn xem được. Live search (lượng Meta lớn nhất) chỉ hiện trong từng job.

### Thiết kế

Không thêm bộ đếm mới — đọc lại dữ liệu đã có:

| KPI | Nguồn dữ liệu |
|-----|---------------|
| Trang đã lấy / trang rỗng | `MetaLibraryConnector.fetch_page` đếm `pages_fetched`, `empty_pages` → `run_connector` → `crawl_runs.pages`, `empty_pages` |
| Bản ghi nhận | `crawl_runs.fetched` (+ `search_jobs.sources[*].fetched` cho live search) |
| Ads chấp nhận | `new_ads + updated_ads` của `ingest_ads` → `crawl_runs.accepted` |
| Trùng | `updated_ads` (bản ghi hợp lệ của ad đã có trong kho, chỉ cập nhật) → `crawl_runs.duplicates` |
| Không hợp lệ / bị lọc | `ingest.invalid`, `excluded_entertainment` → `crawl_runs.invalid`, `excluded` |
| Cursor | `crawl_jobs.checkpoint` → số nước còn trang / đã xong |
| Retry | `crawl_jobs.status`, `retry_count`, `next_retry_at`, `last_error`; lượt chạy ok / lỗi / mất lease |
| Health | `Connector.health` (gồm `blocked`, `rate_limited` của Phase 4) |
| Media availability | `creatives` nhóm theo `network × status`; "xem được" = max(đã lưu file, có ảnh bìa) / tổng |

- `app/collector/kpis.py` — `collector_kpis(db, hours)`; `GET /api/collector/kpis?hours=1..720` (kèm `browser_capacity`).
- Tab **Giám sát** trên Data & Connectors: 8 ô tổng (chọn 1 giờ / 24 giờ / 7 ngày / 30 ngày), bảng theo nguồn (health + lỗi gần nhất, trang, nhận, chấp nhận, trùng, không hợp lệ, live search, từng job với cursor / retry / lease), bảng media theo nền tảng.

### File thay đổi

| File | Thay đổi |
|------|----------|
| `apps/api/app/collector/kpis.py` | Mới |
| `apps/api/app/routers/intel.py` | `GET /api/collector/kpis` |
| `apps/api/app/crawl_queue.py` | `CrawlRun.pages`, `empty_pages`, `accepted`, `duplicates`, `invalid`, `excluded`; `finish` ghi các số này |
| `apps/api/app/realtime.py` | `run_connector` trả số trang + `invalid` / `excluded_entertainment` |
| `apps/api/app/collector/adapters/meta.py` | Đếm trang / trang rỗng |
| `apps/api/app/meta_collector_migrate.py` | Thêm 6 cột INTEGER vào `crawl_runs` nếu bảng đã tồn tại |
| `apps/web/app/data/page.tsx` | Tab Giám sát, nhãn health `blocked` / `rate_limited` |
| `apps/api/tests/test_collector_kpis.py` | Mới — 5 test |

### Giới hạn đã biết

- Số trang chỉ có với nguồn phân trang (Meta Ad Library); nguồn trả một lần hiện "—".
- Số liệu live search lấy từ `search_jobs.sources`: có nhận / mới / lỗi, không có trùng / không hợp lệ (job không lưu riêng các số đó).
- Tổng hợp live search duyệt các search job trong cửa sổ bằng Python (vài trăm job/ngày). Nếu nhiều hơn thì cần bảng rollup.

### Kết quả kiểm thử (2026-11-03, chạy thật)

| Bộ test | Kết quả |
|---------|---------|
| `tests/test_collector_kpis.py` | 5 passed |
| `tsc --noEmit` (apps/web) | không lỗi ở `data/page.tsx` |

Test chạy qua `run_connector` + `ingest_batch` thật: 2 lượt × 3 ad × 2 trang (1 rỗng) → trang 4, rỗng 2, nhận 6, mới 3, chấp nhận 6, trùng 3, không hợp lệ 0, cursor 1/2 nước còn trang; nguồn lỗi → health `error`, job `retry_wait` với `retry_count` 1; live search chỉ đếm job trong cửa sổ (job 3 ngày trước bị loại); media 4 creative → xem được 50 %; endpoint 200 với `hours=168`, 422 với `hours=0`.

## Nghiệm thu toàn bộ (2026-11-03)

### Cách làm

1. **Review độc lập**: một agent review khác đọc `git diff e8cfee4..HEAD`, chỉ được đưa code, không được đưa kết luận của người viết. Mọi phát hiện được kiểm chứng lại với code trước khi sửa.
2. **Kiểm thử tự động**: toàn bộ suite trên SQLite; các test của nhánh trên PostgreSQL 18 với DB sạch; toàn bộ suite trên PostgreSQL; extension; typecheck toàn bộ web.
3. **Smoke test end-to-end**: `apps/api/scripts/verify_collector_browser.py` chạy API (8014) + Next (3004) thật trên SQLite tạm, gọi HTTP thật và mở Edge thật. Không đụng DB thật.

### Phát hiện của review và cách xử lý

| # | Phát hiện | Kết luận | Xử lý |
|---|-----------|----------|-------|
| 1 | 1 bản ghi bị DB từ chối làm hỏng cả batch atomic → cursor kẹt mãi, outbox retry mãi | **Đúng** | Lỗi dữ liệu (`IntegrityError`, `DataError`, `ValueError`) bị bỏ qua theo từng bản ghi (savepoint), như hành vi cũ; lỗi hạ tầng vẫn huỷ batch |
| 2 | SSR fallback đánh dấu `done` → không bao giờ "load more" | **Đúng** | SSR = `page_status: partial`, không `done`; mọi vòng phân trang (scheduled, live search, liveness, competitor scan) tạm dừng, lần sau thử lại GraphQL |
| 3 | `Blocked` bị bọc thành `ConnectorError`; `RateLimited` bị coi là cursor hết hạn → quay lại trang 1 | **Đúng** | Giữ nguyên loại lỗi; rate limit giữ cursor |
| 4 | `recover()` không tính lượt thử → job làm sập worker bị chạy lại vô hạn | **Đúng, nặng hơn reviewer mô tả**: crash không cập nhật `last_sync_at`, nên kể cả ở `error` lịch thường vẫn claim lại *mỗi tick* | Lượt bị mất lease tính như 1 lần lỗi: backoff; quá `MAX_RETRIES` → `error` với thời gian chờ 1 h mà lịch thường không bỏ qua được |
| 5 | Fencing không bảo vệ dữ liệu: worker mất lease vẫn commit ads + cursor | **Đúng** | `crawl_queue.holds()` khoá dòng job trong **cùng transaction** và kiểm tra token ngay trước commit; mất lease → rollback toàn bộ, không ghi cả status connector |
| 6 | Outbox retry mãi batch bị từ chối vĩnh viễn; server 422 cả batch nếu 1 item thiếu id | **Đúng** | Server bỏ qua item thiếu id và đếm vào `invalid` (như trước Phase 1), chỉ 422 khi không còn item nào hợp lệ. Outbox: 400/404/409/413/422 → row `dead` (giữ lại, không retry); 401/403/429/5xx vẫn retry |
| 7 | Batch atomic tắt LLM naming | Đúng — **đánh đổi đã ghi ở Phase 1** | Giữ nguyên; chờ quyết định sản phẩm (xem bên dưới) |
| 8 | `collector_ingest_batches` tăng vô hạn, key UUID vô nghĩa | **Đúng** | Crawl phía server không ghi receipt (`ingest_batch(key=None)`); TTL: receipt 30 ngày, `crawl_runs` 90 ngày (`retention.table_ttls`) |
| 9 | `assign_family` quét O(N) | Đúng — đã ghi giới hạn ở Phase 2 | Giữ nguyên |
| 10 | Rescore lỗi sau khi data đã commit → crawl bị coi là lỗi, retry nhảy trang | **Đúng** | Rescore chạy trong `ingest_lock`, lỗi chỉ ghi log |
| 11 | TikTok: 1 nước lỗi làm mất dữ liệu các nước khác | **Đúng** | Lỗi theo nước được cô lập; chỉ raise khi mọi nước đều lỗi |
| — | "`/api/collector/kpis` không có auth" | **Sai** | `SessionGuard`: `/api/collector/*` chỉ cho service token; smoke test xác nhận 401 khi không có token |
| — | "`meta_ads_collector` chưa cài, không chắc có `page_info`" | **Sai** | Đã cài; parser luôn trả `page_info` (`{}` ở nhánh lỗi) |

Mỗi bản sửa có test hồi quy trong `tests/test_collector_review_fixes.py` (9 test), `tests/test_crawl_queue.py::test_crash_loop_is_capped`, `test_outbox.js`. Mutation check: bỏ phần bỏ qua lỗi dữ liệu → test poison fail; bỏ `holds()` → test stale lease fail.

### Kết quả kiểm thử cuối

| Bộ | Kết quả |
|----|---------|
| Toàn bộ `apps/api/tests`, SQLite | **497 passed**, 4 skipped |
| Test của nhánh (7 file), PostgreSQL 18, DB sạch | **58 passed** |
| Toàn bộ `apps/api/tests`, PostgreSQL | 489 passed, **2 failed — có sẵn từ trước** (xem dưới) |
| Extension `node --test` | 3 passed |
| `tsc --noEmit` toàn bộ apps/web | 0 lỗi |
| `browser_worker.run("health")` với Edge đã cài | OK (Edge 156) |
| `scripts/verify_collector_browser.py` (API + Next + Edge thật) | **22/22 kiểm tra pass**, không có lỗi trang |

Smoke test kiểm tra: extension gửi lại cùng key → cùng receipt, 1 ad; cùng key khác payload → 409; không token → 401; worker chết → run `lost` + backoff; sau backoff worker mới nhận job; nút Sync khi worker đang giữ lease → `already_running`; KPI (trang 4/2 rỗng, nhận 30, chấp nhận 28, trùng 18, không hợp lệ 2, lọc 1, 2 ok + 1 lost, live search 120, TikTok blocked, media 75 %); tab Giám sát hiển thị đúng các số đó.

### Vấn đề có sẵn, ngoài phạm vi nhánh (chưa sửa)

- **2 test PostgreSQL fail**: `test_clustering.py::test_clip_alone_is_never_enough_to_merge` và `::test_stock_picture_hash_shared_by_many_ads_is_ignored` fail khi chạy sau `test_cluster_phase1.py` trên PG. Fail y hệt trên commit gốc `e8cfee4`; chạy riêng thì pass. Đây là lỗi cô lập dữ liệu giữa các test trên PG, không phải lỗi code của nhánh.
- **Trang Data & Connectors không mở được từ trình duyệt trong môi trường thật**: account policy chỉ có tài khoản MKT, và `/api/collector/*` chỉ nhận service token. `web/middleware.js` cố ý xoá `x-admin-token` khỏi request của trình duyệt. Khi request có cả phiên MKT, `/api/collector` trả bản rút gọn → trang crash (`data.storage` undefined). Tab Giám sát mới thừa hưởng giới hạn này. Operator xem KPI bằng CLI: `python -m app.collector.kpis [giờ]`, hoặc `GET /api/collector/kpis` với `X-Admin-Token`. Muốn xem trên web thì cần quyết định chính sách: cho phép một vai trò operator, hoặc mở riêng KPI read-only cho MKT.

### Cần quyết định

- **LLM naming trong batch atomic** (phát hiện #7): hiện tắt để không giữ transaction trong lúc gọi mạng, tên sản phẩm được bù bằng `backfill_names --llm`. Nếu cần tên LLM ngay lúc ingest, phải gọi LLM *trước* khi mở transaction — làm được nhưng là thay đổi riêng.

## Phase 1 — gia cố sau gap audit (2026-11-03, nhánh `meta-phase1-hardening`)

Audit lại code Phase 1 theo `docs/TOOLSPY_PROMPT_ADDENDUM.md` (bản cục bộ, chưa đưa vào Git) (P0). Ticket, bước tái hiện và tiêu chí nghiệm thu nằm ở [META_RELIABILITY_GAP_AUDIT.md](META_RELIABILITY_GAP_AUDIT.md). Chỉ sửa phần còn thiếu, không làm lại những gì Phase 1 đã có. P1/P2 của addendum chỉ ghi backlog.

### Đã sửa

| Mã | Lỗi | Sửa |
|---|---|---|
| GAP-03 (cao) | `run_connector` gom mọi trang rồi mới ingest: trang N lỗi thì bỏ trang 1…N−1. Lỗi lặp lại được thì truy vấn không bao giờ lưu được gì | Lưu các trang đã lấy cùng checkpoint của trang trọn vẹn cuối cùng, rồi vẫn báo lỗi (health, retry, `partial=True`). Lỗi ngay trang đầu thì đi đường cũ |
| GAP-01 | Vòng cursor A→B→A: 1 000 request cho 3 quảng cáo trong test; "Tải thêm" lặp mãi | `st["seen"]` (mã băm của 200 cursor gần nhất, lưu cùng checkpoint) → `page_status="cycle"`: giữ quảng cáo của trang đó, tạm dừng, lần sau bắt đầu lại từ trang 1, ghi log và `cursor_cycles` |
| GAP-02 | Bộ lọc `media_type` làm rỗng mọi trang thì `fetch_ads` đi hết các trang của nguồn | Giới hạn trang `max(5, 3·⌈limit/30⌉)` (hoặc `extra["max_pages"]`), hết giới hạn thì tạm dừng, giữ cursor |
| GAP-04 | `crawl_queue.fail` không ghi số liệu và checkpoint của lần chạy dở | `fail(result=, checkpoint=)` dùng chung `_counts()` với `finish()` |

Kiểm chứng là đúng, không cần sửa: worker chết sau commit (chạy tiếp từ cursor đã commit, không trùng); extension gửi lại cùng khoá khi request đầu chưa xong (`ingest_lock` xếp tuần tự, 1 biên nhận); cursor lặp ngay lập tức vẫn là lỗi nguồn.

### File thay đổi

| File | Thay đổi |
|---|---|
| `apps/api/app/collector/adapters/meta.py` | Phát hiện vòng cursor (`seen`, `cycle`, `cursor_cycles`), `RESUME_KEYS`, giới hạn trang trong `fetch_ads` |
| `apps/api/app/realtime.py` | `run_connector`: giữ các trang đã lấy khi trang sau lỗi; `_run_search` và liveness tạm dừng khi gặp `cycle`; `_job_wrapper` truyền kết quả và checkpoint của lần chạy dở |
| `apps/api/app/crawl_queue.py` | `fail(result=, checkpoint=)`, `_counts()` |
| `apps/api/tests/test_meta_phase1_hardening.py` | Mới: 9 test (vòng cursor ×2, cursor lặp ngay, giới hạn trang, trang sau lỗi ×2, lịch sử chạy dở, worker chết sau commit, gửi lại khi request đầu chưa xong) |
| `docs/META_RELIABILITY_GAP_AUDIT.md` | Mới: audit P0, ticket, đối chiếu addendum, backlog |

### Migration

Không có thay đổi schema. `seen` nằm trong cột JSON checkpoint đã có (`TrackedQuery.cursor`, `SearchJob.state`, `crawl_jobs.checkpoint`). Checkpoint cũ không có `seen` vẫn chạy bình thường.

### Kết quả kiểm thử (2026-11-03, chạy thật)

| Bộ | Kết quả |
|---|---|
| Test mới, **trước khi sửa** (TDD) | 5 failed, 4 passed: đúng 3 lỗi thật (vòng cursor: 1 000 bản ghi; giới hạn trang: đi tới `c500`; trang sau lỗi: lưu 0) |
| Test mới, sau khi sửa | 9 passed |
| Toàn bộ `apps/api/tests`, SQLite | **526 passed**, 4 skipped, 0 failed |
| 5 file độ tin cậy (`test_meta_phase1_hardening`, `test_meta_reliability`, `test_crawl_queue`, `test_collector_review_fixes`, `test_collector_kpis`), PostgreSQL 18 (`toolspy_test_meta`) | **49 passed**, chạy 2 lần |
| Extension `node --test test/test_outbox.js` | 3 passed |
| Extension `node test/test_injected.js` (response Meta thật, nhiều dòng / SSR / payload hỏng) | 3 passed |

Chưa kiểm thử: Meta thật và extension cài trong Chrome thật; chạy tải 24–72 giờ. **Chưa nói là sẵn sàng cho production.**

## P2 — GraphQL schema health và chẩn đoán phân trang (2026-11-03, nhánh `meta-p2-graphql-health`)

Chồng lên `meta-phase1-hardening`. Chi tiết và lý do: [META_RELIABILITY_GAP_AUDIT.md §6](META_RELIABILITY_GAP_AUDIT.md#6-p2--graphql-schema-health-và-chẩn-đoán-phân-trang-nhánh-meta-p2-graphql-health). P1 của addendum chưa làm (vẫn trong backlog).

### Đã làm

- **Phát hiện đổi cấu trúc phía server:** thư viện trả 0 quảng cáo mà dữ liệu thô vẫn có quảng cáo → cứu bằng `extract_ads`, health `schema_changed`, sự kiện `SOURCE_SCHEMA_CHANGED`. Có kết quả mà không đọc được → `SchemaChanged`. Response không phải một JSON duy nhất → tính là đổi cấu trúc.
- **Phát hiện đổi cấu trúc phía extension:** gửi tên khoá (không gửi giá trị) tới `POST /api/ingest/meta-library/drift`, mỗi trang một lần, tối đa một lần mỗi 10 phút.
- **Chẩn đoán:** `crawl_runs.requests / cursor_cycles / cursor_restarts / schema_drift`, hiện trên KPI và tab Giám sát.

### File thay đổi

| File | Thay đổi |
|---|---|
| `apps/api/app/collector/base.py` | `SchemaChanged(ConnectorError)` |
| `apps/api/app/collector/adapters/meta.py` | `_drifted()`, `_has_results()`, đếm `requests`/`cursor_restarts`/`schema_drift`, `JSONDecodeError` tính là đổi cấu trúc, `SchemaChanged` không làm quay lại trang 1 |
| `apps/api/app/realtime.py` | `DIAGNOSTICS`, health và sự kiện `schema_changed` cho crawl theo lịch và tìm kiếm trực tiếp |
| `apps/api/app/crawl_queue.py`, `meta_collector_migrate.py`, `collector/kpis.py` | 4 cột chẩn đoán (migration cộng thêm), `_counts`, tổng KPI |
| `apps/api/app/routers/intel.py` | `POST /api/ingest/meta-library/drift` (token, giới hạn đầu vào) |
| `apps/extension/injected.js`, `content.js`, `background.js` | Báo đổi cấu trúc |
| `apps/web/app/data/page.tsx` | Nhãn "Nguồn đổi cấu trúc", số request / vòng cursor / lần quay lại trang 1 |
| `apps/api/tests/test_meta_p2_schema_health.py` | Mới: 12 test |
| `apps/extension/test/test_injected.js` | Thêm 2 ca (báo đổi cấu trúc chỉ với tên khoá; response bình thường không báo) |

### Migration

`python -m app.meta_collector_migrate`, tự chạy khi API/worker khởi động. Thêm 4 cột `INTEGER` vào `crawl_runs` và không đổi dữ liệu cũ. Các dòng cũ có giá trị NULL, KPI tính là 0, và UI ẩn số request khi bằng 0. Đã chạy 2 lần liên tiếp trên PostgreSQL test: cả 4 cột có mặt, không lỗi.
Rollback: `ALTER TABLE crawl_runs DROP COLUMN requests, DROP COLUMN cursor_cycles, DROP COLUMN cursor_restarts, DROP COLUMN schema_drift;`.

### Kết quả kiểm thử (2026-11-03, chạy thật)

| Bộ | Kết quả |
|---|---|
| `tests/test_meta_p2_schema_health.py`, trước khi cài đặt | Lỗi import (`SchemaChanged` chưa có) |
| `test_injected.js` ca báo đổi cấu trúc chạy với `injected.js` **cũ** | FAIL, đúng là lỗi im lặng ra 0 |
| Toàn bộ `apps/api/tests`, SQLite | **538 passed**, 4 skipped, 0 failed |
| 6 file độ tin cậy + P2, PostgreSQL 18 | **61 passed**, chạy 2 lần |
| Extension `test_injected.js` / `test_outbox.js` | 9/9 · 3/3 |
| `tsc --noEmit` apps/web | 0 lỗi |
| `scripts/verify_collector_browser.py` (API + Next + trình duyệt thật, SQLite tạm) | **22/22** kiểm tra pass, tab Giám sát hiển thị chỉ số mới, không lỗi trang |

Các ca kiểm thử bắt buộc của addendum:
- Response nhiều dòng: extension parse được; server tính là đổi cấu trúc.
- Response đổi cấu trúc: cứu dữ liệu và báo động, hoặc báo `SchemaChanged`.
- Thiếu `collation_id`: vẫn lưu.
- Trang rỗng có cursor kế tiếp: vẫn là `empty`, không phải hết (`test_a_truly_empty_page_with_a_next_cursor_stays_empty`, `test_empty_page_is_not_end`).
- Cursor lặp: Phase 1 hardening.
- Rate limiting: `test_rate_limit_keeps_the_stored_cursor`.

Chưa kiểm thử với Meta thật hay extension cài trong Chrome thật; chưa chạy tải 24–72 giờ.

