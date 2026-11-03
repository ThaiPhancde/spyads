# Meta Collector Upgrade

Spec: [Meta_Collector_Reliability_and_Video_Intelligence_Upgrade.md](Meta_Collector_Reliability_and_Video_Intelligence_Upgrade.md).
Trạng thái: **Phase 1 xong** (2026-11-03). Phase 2–5 chưa làm.

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
