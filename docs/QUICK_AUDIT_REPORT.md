# ToolSpy — Quick Audit Report

Ngày: 03–04/11/2026 · Code: nhánh `meta-p1-p5` @ `ea8d8bf` (local) · DB thật: PostgreSQL 18.6, db `toolspy` (chỉ đọc).
Phạm vi: chạy các bộ test, typecheck và build sẵn có, E2E trình duyệt trên DB tạm, và truy vấn chỉ đọc trên DB thật. Không sửa code, không đổi dữ liệu thật, không gọi API trả phí, không push.

## 1. Kết quả kiểm tra

| Mảng | Kiểm tra | Kết quả |
|---|---|---|
| Backend | `pytest apps/api/tests` (SQLite) | **PASS**: 590 passed, 4 skipped, 0 failed |
| Backend | `pytest` (PostgreSQL, DB test `toolspy_test_meta`) | **WARNING**: dừng ở khoảng 60% theo yêu cầu. Có **2 FAIL**, chưa lấy được tên test |
| Backend | `compileall app scripts`, `pip check` | **PASS** |
| Backend / Frontend | Lint (ruff / eslint) | **NOT TESTED**: dự án chưa cài/cấu hình linter |
| Frontend | `tsc --noEmit` | **PASS**: 0 lỗi |
| Frontend | `next build` | **PASS** |
| Extension | `node --test test/test_outbox.js` · `node test/test_injected.js` | **PASS**: 6/6 · 10/10 |
| E2E (DB tạm) | `verify_search_browser.py` (tìm kiếm, claim độc quyền giữa MKT) | **PASS** |
| E2E (DB tạm) | `verify_library_browser.py` (Library riêng từng MKT) | **PASS** |
| E2E (DB tạm) | `verify_workspace_browser.py` (đăng ký, đăng nhập, Follow, whitelist, saved search, không màn ADMIN) | **PASS** |
| E2E (DB tạm) | `verify_collector_browser.py` (Data & Connectors, Giám sát) | **PASS**: 22/22 (chạy 03/11 sau commit P5, cùng code hiện tại) |
| Personalization | Claim / Library / saved search / profile (qua E2E) | **PASS** |
| Database | Kết nối PostgreSQL thật | **PASS** |
| Database | Đăng ký migration sau merge (`db.migrate_all`: 6 migration, API và worker cùng gọi) | **PASS** |
| Database | Schema thật so với code | **WARNING**: DB thật chưa có cột/bảng/UNIQUE index của P1–P5, vì nhánh chưa deploy. Bản ghi trùng: ads = 0, ad_sources = 0, nên tạo UNIQUE index được |
| API (thật) | `GET :8000/api/health` | **FAIL**: API không chạy |
| Worker (thật) | Heartbeat | **FAIL**: worker không chạy, heartbeat cuối lúc 2026-11-03 09:48 UTC |
| Web (thật) | `:3000` / `:3001` | **FAIL**: không chạy |
| Ads Collector | Logic qua test (Meta, cursor, drift, outbox) | **PASS** (bằng test) |
| Ads Collector | Crawl Meta thật / extension trong Chrome thật | **NOT TESTED** |
| AI/LLM | `llm.status()` (offline) | **PASS**: 8/8 provider sẵn sàng, engine `cerebras`. Tesseract và ffmpeg có |
| AI/LLM | Tỉ lệ lỗi gọi model trong 24 giờ (sổ `external_api_calls`) | **WARNING**: Groq lỗi cao, xem lỗi #8 |
| Đăng nhập Playwright `admin@gmail.com` trên app thật | — | **NOT TESTED**: phải khởi động API trên DB thật, mà khởi động là chạy migration P1–P5 (đổi schema dữ liệu thật). Tài khoản này đang có vai trò `MKT` |

## 2. Danh sách lỗi

| # | Mức | Lỗi | Nguyên nhân | File / chỗ liên quan | Phương án |
|---|---|---|---|---|---|
| 1 | **Cao** | API và worker thật đã dừng: không crawl, không đặt tên, không làm mới media | Hai task `ToolSpyAPI`/`ToolSpyWorker` kết thúc với `0xC000013A` (bị đóng kiểu Ctrl-C), không có cơ chế tự khởi động lại | `apps/api/data/run/run_api.ps1`, `run_worker.ps1`, Task Scheduler | Khởi động lại sau Phase A. Bật restart-on-failure cho task, hoặc chạy dưới dạng Windows service |
| 2 | **Cao** | Code đang chạy, `main` cục bộ và GitHub lệch nhau. Dịch vụ thật chạy `main` cục bộ `3fbf402`; nhánh `meta-phase1-hardening` → `meta-p2-graphql-health` → `meta-p1-p5` chưa merge; `origin/main` vẫn là `98d85c1`, chưa có 12 commit merge | Merge cục bộ, chưa push; các nhánh xếp chồng chưa có PR | git | Review và merge 3 nhánh theo thứ tự; `pg_dump` trước; khởi động lại để migration chạy |
| 3 | Trung bình | **691 ad / 675 sản phẩm** kẹt naming (UNKNOWN, `naming_attempts ≥ 3`, `name_source` trống). Hàng đợi AI = 0, nên không bao giờ được thử lại (con số 594 trước đây là tập con Meta in-target) | Đợt Groq lỗi 400 trước đây đã đốt hết lượt thử. Code đã sửa, dữ liệu chưa sửa | `products.naming_attempts`, `app/naming_ai.py` | Đặt `naming_attempts = 0` cho nhóm này (thay đổi dữ liệu, cần duyệt), rồi để worker xử lý lại |
| 4 | Trung bình | 1 `crawl_jobs` ở trạng thái `running` trong khi worker đã chết (lease treo) | Worker dừng giữa chừng một lần chạy | `crawl_jobs` | `crawl_queue.recover()` sẽ tự xử lý khi worker chạy lại. Kiểm tra sau khi khởi động |
| 5 | Trung bình | **843 lần `thumb grab failed`** trong `worker.log`: 802 "cannot identify image file", 41 "> 20 MB" | Có khả năng (chưa phân tích sâu): link ảnh bìa trả về nội dung không phải ảnh (link ký hết hạn hoặc file video), và ảnh quá 20 MB. Mỗi tick lại thử lại | `app/media.py` (`grab_thumb`, khoảng dòng 440–460) | Ghi nhận lỗi vĩnh viễn (không phải ảnh / quá lớn) để không thử lại mỗi tick; thêm backoff hoặc giới hạn số lần |
| 6 | Trung bình | 2 test FAIL trên PostgreSQL (lần chạy dừng ở 60%) | Chưa xác định. Trước đây đã ghi nhận 2 lỗi `test_clustering` chạy sau `test_cluster_phase1` (dữ liệu test không cô lập trên PG) | `apps/api/tests/test_clustering.py` (giả thuyết) | Chạy lại PG đầy đủ với `-rf` để lấy tên; sửa phần cô lập dữ liệu test |
| 7 | Trung bình | Commit `e8cfee4` đã xoá 13 script test rời; **12 script không có bản thay thế** trong `tests/`: allegro_bot, allegro_import, eu_market, free_sources, image_search, ingest_rules, lens, lens_live, media_rules, price_intelligence, scoring_rules, search_keywords (`test_auth` → `tests/test_auth_mkt.py`) | Dọn dẹp trong đợt SpyWin rebuild. Một số script đã hỏng từ trước | Lấy lại được bằng `git show 98d85c1:apps/api/<file>` | Chuyển sang pytest các script còn giá trị (scoring_rules, ingest_rules, media_rules, search_keywords, price_intelligence); bỏ script gọi dịch vụ live (lens_live) |
| 8 | Thấp | Tỉ lệ lỗi Groq trong 24 giờ: `groq` 33/60, `gpt-oss-120b` 13/45, `gpt-oss-20b` 14/27, `qwen3.8-27b` 15/51 | Chủ yếu `json_validate_failed` (model trả JSON lệch schema); chuỗi provider vẫn fallback được. Mục `llm_groq` không kèm model có thể đến từ chuỗi cũ hoặc lần chạy thử trước đó | `app/services/llm_providers.py`, `.env LLM_PROVIDERS` | Theo dõi sau deploy; cân nhắc bỏ Groq 20b khỏi chuỗi văn bản hoặc thử lại 1 lần với `bad_output` |
| 9 | Thấp | Chưa có linter Python/JS | Dự án chưa cấu hình | `apps/api`, `apps/web/package.json` | Thêm `ruff` và `next lint` (hoặc eslint) vào quy trình kiểm tra |
| 10 | Thấp | Trang Data & Connectors (hệ thống) không mở được trên trình duyệt thật bằng tài khoản MKT (đã biết) | Chính sách chỉ có MKT; `/api/collector/*` chỉ nhận service token | `web/middleware.js` | Chờ quyết định: thêm vai trò operator, hoặc mở KPI chỉ đọc |
| 11 | Info | Có thay đổi chưa commit `apps/web/app/search/page.tsx` (có từ trước); 3 file chưa track: 2 zip SpyWinAds và `TOOLSPY_PROMPT_ADDENDUM.md` | — | working tree | Giữ các file này ngoài git; xử lý riêng file `search/page.tsx` |

## 3. Kế hoạch sửa

**Phase A — Đưa hệ thống chạy lại (ưu tiên ngay)**
1. `pg_dump` DB thật.
2. Review và merge `meta-phase1-hardening` → `meta-p2-graphql-health` → `meta-p1-p5` vào `main` (lỗi #2).
3. Khởi động API, worker và web; bật tự khởi động lại (lỗi #1).
4. Kiểm tra: `/api/health`, heartbeat worker, migration (2 bảng mới, 3 UNIQUE index), crawl job treo được `recover()` (lỗi #4).

**Phase B — Dữ liệu (cần duyệt vì đổi dữ liệu)**
5. Đặt lại `naming_attempts` cho 675 sản phẩm kẹt, rồi theo dõi hàng đợi `/naming` (lỗi #3).
6. Thu thập vài mẫu `thumb grab failed`, thêm backoff hoặc đánh dấu lỗi vĩnh viễn trong `media.py` (lỗi #5).

**Phase C — Chất lượng kiểm thử**
7. Chạy lại toàn bộ test PG với `-rf`, sửa phần cô lập dữ liệu test (lỗi #6).
8. Chuyển các script test bị xoá còn giá trị sang `tests/` (lỗi #7).
9. Thêm ruff và eslint (lỗi #9). Theo dõi và chỉnh chuỗi Groq (lỗi #8).

**Phase D — Kiểm thử thật**
10. Playwright với tài khoản thật trên bản sao DB (staging).
11. Smoke test Meta thật và extension trong Chrome thật.
12. Chạy liên tục 24–72 giờ.
13. Quyết định về lỗi #10.

## 4. Phase A — đã thực hiện (04/11/2026)

| Bước | Kết quả |
|---|---|
| 1. Sao lưu | `apps/api/data/backups/toolspy_pre_phaseA_20261104_081931.dump` (213,4 MB, `pg_dump -Fc`, 90 bảng dữ liệu đọc lại được bằng `pg_restore -l`) |
| 2. Merge | `main` cục bộ fast-forward `3fbf402` → `ea8d8bf` (7 commit: Phase 1 hardening, P2, P1, P3, P4, P5, docs). **Chưa push** (`origin/main` vẫn là `98d85c1`) |
| 3. Khởi động + tự phục hồi | Task `ToolSpyAPI` / `ToolSpyWorker` / `ToolSpyWeb`: bỏ giới hạn chạy 72 giờ (`ExecutionTimeLimit` → 0, đây là một nguyên nhân khiến tiến trình bị dừng), lặp trigger mỗi 5 phút với `IgnoreNew` (tiến trình chết thì được chạy lại trong tối đa 5 phút), thử lại 3 lần khi lỗi. API `:8000` ok, worker heartbeat 3,5 s, web `:3000` → 200 |
| 4. Kiểm tra | Migration trên DB thật: đủ bảng `creative_reuse_observations` và `ingest_quarantine`, đủ cột P1/P2/P3, 3 UNIQUE index đã tạo (không có bản ghi trùng). Crawl job treo được `recover()` ghi nhận (`lost` / `cancelled` → `retry_wait`), 3 crawl mới đang chạy. Worker re-queue 1 259 creative chờ media. Số ads/products không đổi (26 798 / 18 487). Log API/worker: 0 Traceback, 0 ERROR |

Lỗi #1, #2 (phần deploy), #4: **đã xử lý**. Còn lại: push hoặc PR lên GitHub (lỗi #2), Phase B (cần duyệt vì đổi dữ liệu), Phase C, Phase D.
