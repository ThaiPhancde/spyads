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

**P1 — ingest transaction và checkpoint bền vững:** ✅ phần còn thiếu đã làm ở nhánh `meta-p1-p5`, xem mục 7.

**P2 — phân trang GraphQL và theo dõi cấu trúc:** ✅ đã làm ở nhánh `meta-p2-graphql-health`, xem mục 6.

**P3–P5:** ✅ đã làm ở nhánh `meta-p1-p5`, xem các mục 8–10.

## 6. P2 — GraphQL schema health và chẩn đoán phân trang (nhánh `meta-p2-graphql-health`)

| Yêu cầu P2 | Trước | Sau |
|---|---|---|
| Parse JSON, JSON nhiều dòng, node có `ad_archive_id`, không đòi `collation_id` | Extension đã có. Server: thư viện `meta-ads-collector` đọc theo một đường cố định `edges[].node.collated_results[]`; khi Meta đổi tên trường thì thư viện trả `ads=[]` nhưng `page_info` vẫn hợp lệ, và **ToolSpy hiểu là "trang rỗng": im lặng ra 0** | **GAP-05:** `_drifted()` chạy bộ duyệt `extract_ads` trên dữ liệu thô của thư viện. Tìm thấy quảng cáo thì cứu dữ liệu và gắn cờ đổi cấu trúc. Có kết quả (`edges` không rỗng) mà không đọc được quảng cáo nào thì báo `SchemaChanged`. `edges` rỗng thì vẫn là trang rỗng thật. Response không phải một JSON duy nhất (`JSONDecodeError`, ví dụ dạng nhiều dòng) cũng tính là đổi cấu trúc |
| Schema drift là cảnh báo, không phải 0 giả | Không có | Health `schema_changed` (nhãn "Nguồn đổi cấu trúc"), sự kiện `SOURCE_SCHEMA_CHANGED` ghi vào bảng `events`, áp dụng cho crawl theo lịch, tìm kiếm trực tiếp và extension. `SchemaChanged` không bị hiểu nhầm là cursor hết hạn (không quay lại trang 1) |
| Extension: đổi cấu trúc thì báo | Im lặng ra 0 | **GAP-06:** `injected.js`: response là JSON hợp lệ, có `ad_archive_id`, nhưng không đọc ra quảng cáo nào → gửi **tên khoá**, không gửi giá trị. `content.js` gửi một lần mỗi lần tải trang; `background.js` gửi tối đa một lần mỗi 10 phút tới `POST /api/ingest/meta-library/drift` (cần token, tối đa 40 khoá, `path` ≤ 200 ký tự) |
| Ghi `end_cursor`, `has_next_page`, vòng cursor, chuỗi trang rỗng, số request, lỗi nguồn | `end_cursor`/`done` có trong `crawl_runs.checkpoint_after`; `pages`, `empty_pages`, `error` đã có | **GAP-07:** thêm cột `crawl_runs.requests` (kể cả request lỗi), `cursor_cycles`, `cursor_restarts`, `schema_drift`. Migration cộng thêm, chạy lặp lại được. Hiện trên KPI và tab Giám sát |
| Cursor cũ → chạy lại có giới hạn, không coi là phủ hết | Đã có quay lại trang 1 và giới hạn trang (GAP-02) | Nay được đếm (`cursor_restarts`) |
| `first_observed_at/last_observed_at` tách khỏi `delivery_start/stop` | — | **Không cần cột mới:** thời điểm *ta thấy* đã có theo từng nguồn ở `AdSource.first_collected_at/last_collected_at`. `Ad.first_seen_at/last_seen_at` là ngày chạy do nguồn báo (lùi về ngày thu thập nếu nguồn không có ngày). Không đổi công thức TEST/TEST_NOW |
| Tách độ phủ crawl khỏi tuyên bố phủ 100% | KPI cursor "x/y nước còn trang · z xong", `has_more` của tìm kiếm | Giữ nguyên. Chỉ `has_next_page=false` mới là hết; `cycle`, `partial`, giới hạn trang và 3 trang rỗng chỉ là tạm dừng |

Chưa làm trong P2:
- Phía server chưa tự parse JSON nhiều dòng (việc đó nằm trong thư viện), mới chỉ phát hiện và dùng SSR cho trang 1.
- Chưa ghi chuỗi trang rỗng liên tiếp riêng; `empty_pages` là tổng.
- Chưa gửi cảnh báo ra ngoài (email/Slack); hiện chỉ có health, bảng `events` và tab Giám sát.

## 7. P1 — phần còn thiếu (nhánh `meta-p1-p5`)

Đánh giá lại ngày 03/11/2026: Phase 1 cũ đã làm **phần lõi** của P1 nhưng chưa đủ. Ở lượt trước tôi bỏ qua P1 theo chỉ dẫn "P1/P2 chỉ đưa vào backlog" mà không đối chiếu code; lượt này đã đối chiếu lại.

| Yêu cầu P1 | Đã có từ Phase 1 | Bổ sung |
|---|---|---|
| Batch có `job_id`, `page_cursor_before/after`, `batch_id`, `source`, `fetched_at`, `schema_version` | Crawl phía server: `crawl_runs` (`job_key`, `run_token`, `checkpoint_before/after`, `started_at`). Extension: chỉ có khoá idempotency | Biên nhận `collector_ingest_batches` lưu đủ các trường. Extension 0.3.0 gửi `job_id` (một phiên tab), `batch_id`, `fetched_at`, `schema_version` (`adlib-capture-1`), `cursor_before/after` (lấy từ `page_info.end_cursor` của chính response). `crawl_runs.schema_version` (`adlib-graphql-1`) |
| Lưu ads + nguồn + ACK + checkpoint trong một transaction; checkpoint chỉ tiến sau ACK | ✅ | — |
| UNIQUE(source, source_ad_id), UNIQUE(job_id, batch_id) | **Không có**: chống trùng chỉ dựa vào tra cứu dưới `ingest_lock` | `uq_ads_source_external`, `uq_ad_sources_source_ad`, `uq_ingest_batches_job_batch`. Migration đếm bản ghi trùng trước, có trùng thì **bỏ qua kèm cảnh báo, không xoá**. DB thật: 0 trùng trên 26 777 ads / 27 266 ad_sources. Cùng (job, batch) dưới khoá khác → 409. Không dùng `INSERT … ON CONFLICT` vì việc tra cứu dưới khoá đã tuần tự; UNIQUE là lưới an toàn |
| Bảo toàn ID anh em và `collation_count` | ✅ (`meta_collations`, `AdSource`, `Ad.variants`) | — |
| Outbox IndexedDB, ACK từng batch, không bỏ sau 3 lần lỗi, backoff mũ | ✅ | **jitter ±20%**; giới hạn **5 000 batch** (đầy thì từ chối rõ ràng, không âm thầm bỏ); popup hiện số batch chờ/bị từ chối và nút **"Gửi lại ngay"** |
| Bảng trạng thái `complete/partial/rate_limited/schema_changed/auth_required/blocked/failed/cancelled` | Một phần (health, `page_status`) | `crawl_runs.outcome`. `complete` chỉ khi mọi cursor đều hết trang; `cancelled` = mất lease; lỗi ánh xạ theo health. Hiện trên tab Giám sát |

Test: `tests/test_meta_p1_envelope.py` (16), `test_outbox.js` (+3: jitter, đầy, gửi lại), `test_injected.js` (+1: `end_cursor`). Hai test trong `test_cluster_phase1.py` đã đổi sang trường hợp thực tế: cùng ID Facebook đến từ nguồn thứ hai. Việc gom cụm nhận diện trùng theo network, nên UNIQUE theo source vẫn cho phép trường hợp này.

## 8. P3 — fingerprint video có version

- **`creatives.video_fp_v2`** (JSON) gồm `{algorithm: "cellmean-dhash", version: 2, grid: [9, 8], frame_positions: [0.2, 0.5, 0.8], frame_hashes, duration, source, observed_at, confidence}`. Mỗi khung chia lưới 9×8, lấy độ sáng trung bình mỗi ô rồi so các ô cạnh nhau, được 64 bit. Cách này khác v1 (PIL thu nhỏ rồi so pixel) và độc lập với cách thu nhỏ của PIL.
- **Tính ở đâu:** từ chính các khung đã giải mã cho v1, lúc video được lưu. Không bao giờ ghi đè `video_fp` (v1) hay `phash` (ảnh bìa).
- **So sánh:** `same_video_v2` chỉ so khi cùng thuật toán + version, khác thì trả `None`. Khi gom family video, dùng v2 nếu cả hai đều có, nếu không thì v1 với v1; **không bao giờ so v1 với v2**. Family video không gộp sản phẩm.
- **`efg`:** `creatives.media_claims` = `{age_claim_days, xpv_asset_id, duration_s, claimed_at, evidence_type: "efg_url_metadata", confidence: "unverified"}`. Parse có giới hạn (≤ 4 KB, kiểm tra kiểu, không bao giờ raise). **Không** phải `verified_upload_date`, và không phần chấm điểm nào đọc.
- **Không tải video hàng loạt:** chỉ backfill từ file **đã lưu** của ToolSpy (`python -m app.media --fingerprint-v2 N`), không tải từ nguồn, không re-encode, có giới hạn số video mỗi lần.
- **Bench FP/FN:** `scripts/bench_video_fp.py pairs.csv` chạy trên **cặp video do người gán nhãn**. Repo chưa có bộ nhãn nào, nên **chưa có số FP/FN thật**.
- **Ngưỡng `V2_DISTANCE = 10`/64 bit** được chọn từ fixture tự tạo; cần bench thật để chỉnh.
- **Chưa giải quyết:** video bị cắt đầu làm lệch vị trí 20/50/80% nên có thể không khớp.

Test: `tests/test_video_fp_v2.py` (20). Fixture là ảnh tự vẽ qua các biến đổi: nén lại, thu nhỏ, đổi tỉ lệ, chèn chữ, cắt viền, khung khác nhau. Có thêm video tạo bằng ffmpeg: bản render khác, chèn chữ, đổi tỉ lệ, một video khác, video đen.

## 9. P4 — insight tái sử dụng video/creative

- **Bảng `creative_reuse_observations`:** mỗi dòng là một (family, ad) với page, nhà quảng cáo, thị trường, nguồn, thời điểm ta *thấy* đầu/cuối (từ `AdSource`, không phải ngày delivery của Meta), và bằng chứng (`video_fp_v2` > `video_fp_v1` > `poster_phash`, độ tin cậy, link Ad Library). Worker làm mới mỗi `REUSE_EVERY_MIN` (60 phút); chạy lại nhiều lần không sinh dòng trùng.
- **Chỉ số thử nghiệm** `ad_count`, `collation_variants`, `distinct_pages`, `distinct_shop_id`, `new_distinct_pages_7d`, `reuse_velocity_7d`, `earliest_observed_video_at`. Định nghĩa nằm trong docstring của `app/creative_reuse.py`, để không nhầm 4 bộ đếm. Bộ chấm điểm không đọc các chỉ số này; có test xác nhận điểm và khuyến nghị của sản phẩm không đổi.
- **API và giao diện:** `GET /api/products/{id}/creative-reuse`, và thẻ "Video được tái sử dụng (thử nghiệm)" trong tab Creative Library (bằng chứng, độ tin cậy, link Ad Library, thời điểm quan sát).
- **Theo dõi đối thủ theo Page ID:** đã có sẵn. Feed của MKT không đổi.

Test: `tests/test_creative_reuse.py` (6).

## 10. P5 — bảo mật và vận hành

| Yêu cầu | Kết quả |
|---|---|
| SSRF trước khi worker đọc URL | **Audit: đã có.** Mọi URL lấy từ dữ liệu (media, trang đích, trang sản phẩm EU/giá) đi qua `netsafe` (http(s), IP public, kiểm tra lại ở từng bước redirect, giới hạn dung lượng và thời gian). Các lời gọi `httpx` khác tới endpoint cố định của nhà cung cấp. Không cần sửa |
| Cách ly dữ liệu lỗi | **Mới:** bảng `ingest_quarantine` (bản ghi bị kiểm tra dữ liệu hoặc DB từ chối, đã lọc credential, ≤ 20 KB, TTL 30 ngày) |
| Không lưu token/cookie/bearer trong raw batch hay log | **Mới:** `storage.scrub()` bỏ các khoá cookie / authorization / fb_dtsg / lsd / token / sessionid…, và che tham số `access_token=` / `api_key=` trong URL. Áp dụng cho raw lake và bản ghi cách ly. Header TikTok tự thu thập vốn chỉ nằm trong bộ nhớ |
| Mã hoá thông tin đăng nhập | **Mới:** `app/secrets_box.py` (Fernet, thư viện `cryptography`). Có `CREDENTIALS_KEY` thì các trường secret được niêm phong khi ghi và chỉ mở trong `ConnectorFactory.get`. Không có khoá thì hành vi y như cũ, và KPI `credentials_unencrypted` báo số secret còn để nguyên văn |
| Lease, heartbeat, SKIP LOCKED, phục hồi sau crash | Đã có (Phase 3) |
| Hạn mức tốc độ chung và theo thị trường | **Mới:** `MARKET_RPM` (mặc định 10 request/phút cho mỗi nước) dùng chung cho mọi crawl trong tiến trình, chồng lên bucket chung của adapter. Giới hạn theo tiến trình: API và worker mỗi bên có bucket riêng |
| Giám sát | **Mới:** độ tươi dữ liệu theo nguồn, ad/trang, tỉ lệ trùng, outbox extension (gửi kèm mỗi batch và loại khỏi mã băm payload), độ lệch vân tay v1/v2, số bản ghi bị cách ly, số secret chưa mã hoá. Sự kiện `SOURCE_ZERO_RESULTS` khi nguồn âm thầm trả 0. Các chỉ số đã có: số trang, vòng cursor, độ khả dụng video, job lỗi |

Test: `tests/test_meta_p5_security_ops.py` (10).

## 11. Việc còn mở sau P0–P5

- **Bench FP/FN trên video thật:** cần bộ cặp video do người gán nhãn. Ngưỡng v2 chưa được chỉnh theo dữ liệu thật.
- **Chạy với Meta thật và extension cài trong Chrome thật:** chưa thử. Chưa chạy tải 24–72 giờ, nên **chưa gọi là sẵn sàng production**.
- **Mã hoá secret:** chỉ có hiệu lực khi đặt `CREDENTIALS_KEY` và chạy `--seal-existing`. Mất khoá thì phải nhập lại secret.
- **Hạn mức tốc độ:** hiện theo từng tiến trình. Muốn dùng chung giữa API và worker thì cần một bộ đếm trong DB hoặc Redis.
- **Cảnh báo:** hiện qua health, bảng `events` và tab Giám sát; chưa gửi ra email hay Slack.

