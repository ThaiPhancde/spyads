# Meta Collector — Gap audit (P0)

Ngày: 03/11/2026 · Nhánh: `meta-phase1-hardening` (tách từ `main` cục bộ @ `3fbf402`, đã có Phase 1–5).
Đầu vào: `docs/TOOLSPY_PROMPT_ADDENDUM.md` (bản cục bộ, chưa đưa vào Git), `SpyWinAds_ToolSpy_Deep_Addendum.zip` (bản addendum trong zip giống hệt file trên), `SpyWinAds_0.6.6_deep_source_reconstruction.zip`.
Phạm vi lượt này: **chỉ Phase 1** (mất batch, cursor, phân trang GraphQL, retry, checkpoint, dedup xuyên trang, đồng bộ an toàn). P1/P2 của addendum chỉ ghi backlog.

**Về hai file zip.** Chỉ đọc phần phân tích: `README.md`, `analysis/workflow.md` và README của addendum. Không đọc, không chuyển, không chạy mã gốc SpyWinAds: `original_electron/`, `original_embedded_js/`, `bytecode_3_12/` đều là mã độc quyền đã trích xuất. Mọi thay đổi trong nhánh này do ToolSpy tự viết.

**Đã đọc trong lượt này:**
- `collector/adapters/meta.py`
- `realtime.py` (`run_connector`, `_run_search`, `_job_wrapper`, liveness)
- `collector/batches.py`
- `crawl_queue.py`
- `routers/intel.py` (`/api/ingest/meta-library`)
- `db.ingest_lock`
- Extension: `injected.js`, `content.js`, `background.js`, `outbox.js`
- Các test hiện có

**Không đọc lại trong lượt này:** `media.py`, `dedupe_creatives.py`, vì thuộc P3 (fingerprint video), ngoài phạm vi Phase 1.

## 1. Kết luận

Phase 1 cũ đã sửa 9 lỗi gốc, và đọc lại code thấy đúng như báo cáo ([META_COLLECTOR_UPGRADE.md](META_COLLECTOR_UPGRADE.md#phase-1--meta-collector-reliability)). Audit này tìm thêm **3 lỗi thật và 1 thiếu sót ghi nhận**, cả bốn đều đã sửa trong nhánh này và mỗi lỗi có test tái hiện:

| Mã | Mức rủi ro | Tóm tắt |
|---|---|---|
| GAP-03 | **Cao** | Crawl theo lịch bỏ toàn bộ các trang đã lấy khi một trang sau lỗi. Nếu trang đó lỗi đều đặn, crawl không bao giờ lưu được gì |
| GAP-01 | Trung bình | Vòng cursor A→B→A không bị phát hiện: test đo được **1 000 request** cho 3 quảng cáo; "Tải thêm" lặp mãi |
| GAP-02 | Trung bình | Bộ lọc `media_type` làm rỗng các trang thì vòng lặp đi hết mọi trang của nguồn |
| GAP-04 | Thấp | Lần crawl lỗi giữa chừng không ghi số liệu và checkpoint vào lịch sử, nên KPI báo 0 dù dữ liệu đã lưu |

Ba trường hợp khác đã kiểm chứng là **đúng, không cần sửa**, nay có thêm test: V-01, V-02, V-03.

## 2. Ticket

### GAP-01 — Vòng cursor A→B→A · mới · đã sửa

- **File / symbol:** `collector/adapters/meta.py` (`MetaLibraryConnector.fetch_page`, `fetch_ads`); `realtime._run_search`; `realtime` liveness.
- **Hiện trạng:** code chỉ chặn cursor lặp ngay lập tức (`cursor == st["cursor"]`). Khi Meta trả lại một cursor cũ, vòng lặp đi lại các trang đã đọc cho tới khi đủ `limit`.
- **Tái hiện:** `search_ads` trả `None→A`, `A→B`, `B→A`. Gọi `fetch_ads(limit=1000)` thì code cũ trả 1 000 bản ghi (997 bản trùng) và gọi Meta 1 000 lần.
- **Rủi ro:**
  - Tốn hạn mức request Meta, dễ bị giới hạn tốc độ.
  - "Tải thêm" của tìm kiếm trực tiếp không bao giờ ra quảng cáo mới.
  - Crawl theo lịch tiếp tục đúng vòng lặp ở lần sau.
- **Sửa:** `st["seen"]` giữ 200 mã băm (sha1, 12 ký tự) của các cursor đã đi qua, và được lưu cùng checkpoint (`RESUME_KEYS`). Gặp lại một cursor đã có:
  - các quảng cáo của trang đó vẫn được giữ;
  - `page_status = "cycle"`, `done = False`, `cursor = None`, nên lần sau bắt đầu lại từ trang 1;
  - tăng `cursor_cycles` và ghi cảnh báo vào log.

  Cả ba vòng lặp (`fetch_ads`, `_run_search`, liveness) đều tạm dừng khi gặp `cycle`.
- **Tiêu chí nghiệm thu:** gọi đúng 3 request, ra 3 quảng cáo, `done=False`, `cursor=None`, `cursor_cycles=1`. Cursor lặp ngay lập tức vẫn là lỗi nguồn (`ConnectorError`) như trước.
- **Test đầu vào:**
  - `test_cursor_cycle_is_detected_and_pauses_without_claiming_the_end`
  - `test_live_search_page_loop_stops_on_a_cycle`
  - `test_an_immediately_repeated_cursor_is_still_a_source_error`
- **Giới hạn:** vòng dài hơn 200 trang không bị phát hiện, nhưng vẫn dừng nhờ giới hạn số trang của GAP-02 (trong `fetch_ads`) hoặc `target` (trong tìm kiếm trực tiếp). Chưa thấy Meta trả cursor vòng ngoài thực tế; xem mục Chưa xác nhận.

### GAP-02 — Trang bị bộ lọc làm rỗng không có giới hạn · mới · đã sửa

- **File / symbol:** `collector/adapters/meta.py` (`fetch_ads`).
- **Hiện trạng:** `got` chỉ tăng với quảng cáo qua được bộ lọc `media_type`. Trang có quảng cáo nhưng bị lọc hết thì không tính là "trang rỗng", nên điều kiện "3 trang rỗng thì dừng" không bao giờ đạt.
- **Tái hiện:** `media_type="video"`, nguồn chỉ trả quảng cáo ảnh với 500 trang. Code cũ gọi hết 500 trang.
- **Rủi ro:** crawl theo lịch chạy rất lâu, tốn request, giữ slot crawl.
- **Sửa:** `max_pages` = `params.extra["max_pages"]`, mặc định `max(5, 3·⌈limit/30⌉)` (Meta trả 30 quảng cáo/trang). Hết số trang thì tạm dừng và giữ cursor, không đánh dấu hết.
- **Tiêu chí nghiệm thu:** `limit=30` gọi tối đa 6 trang, `done=False`.
- **Test:** `test_pages_that_the_filter_empties_stop_at_a_page_budget`.

### GAP-03 — Trang sau lỗi làm mất các trang trước · mới · đã sửa · **cao**

- **File / symbol:** `realtime.run_connector`.
- **Hiện trạng:** mọi trang được gom vào `recs` rồi mới ingest một lần. Exception ở trang N làm bỏ toàn bộ trang 1…N−1, và cursor giữ nguyên. Các nguyên nhân có thể gây ra: `RateLimited`, timeout, `Blocked`, `ConnectorError`.
- **Tái hiện:** nguồn trả trang 1–2 (cursor `c1`, `c2`), trang 3 bị `RateLimited`. Code cũ lưu 0 quảng cáo, cursor vẫn ở `c0`.
- **Rủi ro:** mỗi lần thử lại lấy lại trang 1…N−1 rồi lỗi đúng chỗ cũ. Nếu trang N lỗi theo cách lặp lại được, truy vấn đó **không bao giờ lưu dữ liệu**, đồng thời đốt hạn mức request.
- **Sửa:** khi đã có dữ liệu mà một trang sau lỗi, các trang đã lấy được lưu cùng checkpoint `resume_out`. Checkpoint này chỉ tiến sau mỗi trang trọn vẹn: `fetch_ads` cập nhật nó trước khi yield trang và không bao giờ yield nửa trang. Sau đó lỗi vẫn được báo như thất bại: health `rate_limited`/`blocked`/`error`, hàng đợi thử lại theo backoff, kết quả có `partial=True`. Nếu lỗi ngay trang đầu thì đi đường cũ, không lưu gì.
- **Tiêu chí nghiệm thu:**
  - Ba quảng cáo của trang 1–2 được lưu.
  - Cursor tracked query là `c2`.
  - Health là `rate_limited`; job ở trạng thái `retry_wait`.
  - Lỗi ngay trang 1 thì cursor giữ `c0` và không lưu gì.
- **Test:**
  - `test_later_page_failure_keeps_the_pages_already_fetched`
  - `test_first_page_failure_still_stores_nothing`

### GAP-04 — Lịch sử crawl của lần chạy dở · mới · đã sửa

- **File / symbol:** `crawl_queue.fail`, `realtime._job_wrapper`.
- **Hiện trạng:** `fail()` không ghi `fetched/new_ads/...` và không cập nhật `crawl_jobs.checkpoint`.
- **Sửa:** `fail(..., result=, checkpoint=)` dùng chung hàm `_counts()` với `finish()`. `_job_wrapper` truyền kết quả và cursor khi `partial`.
- **Tiêu chí nghiệm thu:** `crawl_runs` có `status=error`, `fetched=3`, `new_ads=3`, `checkpoint_after` là `c2`; `crawl_jobs.checkpoint` là `c2`.
- **Test:** `test_partial_run_is_recorded_with_its_counts_and_checkpoint`.

### V-01 — Worker chết sau commit, trước `finish()` · đã đúng · thêm test

- **File / symbol:** `crawl_queue.recover/claim`, `run_connector(lease=)`.
- **Hành vi:**
  - Dữ liệu và cursor đã commit trong cùng một transaction, có kiểm tra `holds()`.
  - Lease hết hạn thì `recover()` coi đó là một lần thử thất bại.
  - Worker khác nhận job và chạy tiếp từ cursor đã commit (`c1`).
  - Quảng cáo xuất hiện lại ở trang sau được cập nhật, không thêm bản ghi mới.
- **Test:** `test_crash_after_commit_before_finish_resumes_from_the_committed_cursor`. Ghi chú: 3 quảng cáo, 3 `AdSource`, không trùng.

### V-02 — API hết thời gian chờ: extension gửi lại khi request đầu chưa xong · đã đúng · thêm test

- **File / symbol:** `routers/intel.ingest_meta_library`, `collector/batches.ingest_batch`, `db.ingest_lock`.
- **Hành vi:** extension bỏ chờ sau 30 giây (`AbortSignal.timeout`) rồi gửi lại cùng `idempotency_key`. `ingest_lock` (advisory lock trên PG, thread lock trên SQLite) xếp hai request thành tuần tự; request thứ hai đọc biên nhận `IngestBatch` và trả lại kết quả cũ.
- **Test:** `test_retry_while_the_first_request_is_still_running_stores_once`. Cả hai lần đều trả 200 với cùng `new_ads=2`, có 1 biên nhận, quảng cáo chỉ được lưu một lần.

### V-03 — Cursor lặp ngay lập tức · đã đúng

Đã có từ Phase 1 (`Meta pagination did not advance`). Trường hợp này được phân biệt rõ với GAP-01 bằng test `test_an_immediately_repeated_cursor_is_still_a_source_error`.

## 3. Đối chiếu 10 phát hiện của addendum (chỉ phần thuộc Phase 1)

| # | Phát hiện | Hiện trạng trong code | Kết luận |
|---|---|---|---|
| 1 | Parse JSON nhiều dòng / đệ quy; không bắt buộc `collation_id` | Extension (`injected.js`): parse cả khối, không được thì parse từng dòng, duyệt đệ quy, nhận node có `ad_archive_id` + `snapshot`/`page_id`, không đòi `collation_id`. Test `test_injected.js`: 3/3 pass. Server (`extract_ads`): cùng quy tắc. GraphQL phía server do thư viện `meta-ads-collector` parse; response lệch cấu trúc thì báo `ConnectorError` (không thành "0 kết quả") | **Đã có**. Cảnh báo schema drift cho extension (đang im lặng ra 0) → backlog P2 |
| 2 | Tách ad_id / collation_id / creative_family | `meta_collations` + `AdSource` (alias anh em), `Ad.variants` = `collation_count`, anh em đã dừng không làm dừng cả nhóm (test Phase 1) | **Đã có** |
| 6 | Outbox gzip, ACK idempotent, DLQ, không retry vô hạn, không xoá trước ACK | IndexedDB, chỉ xoá khi có ACK, khoá cố định, lỗi vĩnh viễn (400/404/409/413/422) giữ lại dạng `dead` (DLQ), lỗi tạm thời backoff tối đa 1 giờ | **Đã có** phần Phase 1. Gzip, jitter, giới hạn dung lượng, nút thử lại trên UI → backlog P1 |
| 7 | Tách "hết thật" khỏi "tạm dừng"; 3 trang rỗng không phải hết; giới hạn thời gian/trang; ghi chẩn đoán | Chỉ `has_next_page=false` mới là hết; 3 trang rỗng chỉ tạm dừng (Phase 1). **Thêm:** `cycle`, giới hạn trang (GAP-01/02). `pages`/`empty_pages` đã vào `crawl_runs` (Phase 5) | **Đã sửa phần thiếu**. Ghi `cursor_cycles`/`end_cursor`/số request vào `crawl_runs` → backlog P2 |
| 9 | Lease + heartbeat, chia đều theo thị trường | Lease, heartbeat, `SKIP LOCKED`, fencing (Phase 3) | **Đã có**. Chia đều theo thị trường → backlog P5 |
| 3, 4, 5 | efg, fingerprint cell-mean, `can-van-tay` | Thuộc P3 (fingerprint video) | Backlog, ngoài lượt này |
| 8 | Worker kiểm tra URL (allowlist, SSRF) | `netsafe.safe_stream` đã có kiểm tra SSRF cho mọi lần tải media | Backlog P5 (worker riêng) |
| 10 | Không sao chép cơ chế dấu vân tay máy / bản quyền | Không có trong ToolSpy | Không làm |

## 4. Chưa xác nhận

- **Meta có thật sự trả cursor theo vòng ngoài thực tế không:** addendum cho thấy SpyWinAds có cơ chế chống "cursor lặp", nhưng chưa quan sát trực tiếp trên ToolSpy. Bản sửa GAP-01 vô hại nếu điều đó không xảy ra.
- **Response GraphQL thật** (dạng nhiều dòng, cấu trúc mới) phía server do thư viện `meta-ads-collector` parse. Chưa chạy với Meta thật trong lượt này.
- **Extension cài trong Chrome thật và đăng nhập Facebook:** chưa kiểm thử (cần thao tác tay).

## 5. Backlog (chưa triển khai)

**P1 — ingest transaction và checkpoint bền vững:**
- Mỗi batch có thêm `job_id`, `page_cursor_before/after`, `batch_id`, `fetched_at`, `schema_version`; ràng buộc UNIQUE(`job_id`, `batch_id`). Hiện idempotency dựa trên khoá batch và biên nhận `IngestBatch`.
- Bảng trạng thái đầy đủ: `complete/partial/rate_limited/schema_changed/auth_required/blocked/failed/cancelled`. Hiện có `page/empty/end/partial/cycle` và health `blocked/rate_limited/error`.
- Outbox của extension: jitter, giới hạn dung lượng, nút thử lại cho batch `dead` trên popup, gzip.

**P2 — phân trang GraphQL và theo dõi cấu trúc:**
- Cảnh báo schema drift: extension bắt response có chữ `ad_archive_id` nhưng không ra quảng cáo nào thì phải báo, không im lặng ra 0.
- Ghi `first_observed_at/last_observed_at` tách khỏi `delivery_start/stop`.
- Ghi `end_cursor`, `has_next_page`, `cursor_cycles` và số request vào `crawl_runs` (hiện `cursor_cycles` chỉ có trong kết quả và log).
- Tách "độ phủ crawl" khỏi mọi tuyên bố phủ 100% Meta.

**P3–P5:** xem addendum. Gồm `video_fp_v2` dạng cell-mean có version, efg chỉ là metadata không xác minh, `creative_reuse_observations`, worker kiểm tra URL, hạn mức theo thị trường, alert khi nguồn âm thầm trả 0.
