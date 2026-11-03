# 06 — Completeness critic (2026-10-26)

Phạm vi: đọc 01–05, tìm vùng các auditor **chưa xem** hoặc kết luận **chưa được kiểm chứng**, rồi tự kiểm lại trong code.
Chỉ đọc source. Không sửa app, không bật/tắt server. Ba lỗi bên dưới được tái hiện bằng một file pytest tạm, chạy trên SQLite dùng một lần
(`tests/conftest.py` nạp bằng `-p conftest`, không chạm Postgres thật). File repro nằm ngoài repo:
`C:/Users/ADMIN/AppData/Local/Temp/claude/d--ToolSpy/b64424c8-4f59-4faf-8caf-ba323f7db439/scratchpad/crit/test_critic_repro.py` (kết quả: 3 passed, nghĩa là lỗi có thật).

## 0. Các vùng 01–05 chưa phủ

| Vùng | Ai đã xem | Lỗ hổng trong audit |
|---|---|---|
| Cache in-process `cached()` của `routers/intel.py` (pulse, discovery tab, radar, product-radar, competitors, hidden) | 02 chỉ nói tới hiệu năng (B8) | Không ai kiểm **key cache có chứa user không**. 02 §1 kết luận "không thấy lỗ đọc chéo dữ liệu giữa các MKT". Kết luận này **sai** (xem C1) |
| Allocation độc quyền khi gặp cluster merge (auto-merge 180 phút/lần + MKT accept candidate) | 02 B6 chỉ xét TTL và trần mỗi user | Không ai xét chuyện hai sản phẩm do **hai MKT khác nhau** giữ bị gộp lại về sau (C2) |
| Vote MKT ảnh hưởng điểm toàn cục (`mkt_appeal` = 10% opportunity, đồng thời train taste model) | Không ai xem | Chưa ai kiểm việc dedupe vote (C3) |
| Chuỗi nhân quả "F3 làm F2" của 05 | 05 | Chưa có log nào chứng minh, chỉ là suy luận (mục 2) |
| `next build` | 03 tự ghi là chưa chạy | Vẫn chưa kiểm. Mình cũng không chạy vì build nặng |
| Số liệu DB (trùng creative, UNKNOWN, phân phối điểm, bảng mồ côi) | 01/02/04 | Cả bốn auditor đều không vào được PG. Mọi nhận định về dữ liệu live đều **chưa kiểm chứng** |
| Extension (`apps/extension`) | 01 §1.6 nói qua | Đã xem `manifest.json` và `background.js`: token lấy từ `chrome.storage.sync`, không hard-code. Không có gì mới ngoài phần 01 đã nêu (token chung, `saved_by` do client tự khai) |
| href `javascript:` (stored XSS) | Không ai xem | Xem mục 3: chưa đủ bằng chứng để thành finding |

## 1. Findings đã xác nhận

### C1 — MEDIUM (security/privacy) — `/api/discovery/tab/{tab}` cache dùng chung giữa các MKT, nên lộ "Yêu thích" và "Đã lưu" của người khác
- `routers/intel.py:908`: `cached(("tab", tab, country, funnel, limit), ...)`. Key **không có user id**.
- Hàm bên trong lại lọc theo người gọi: `routers/intel.py:917` (`Vote.user_id == db.info.get("user_id")`, tức tập `loved`), `:918` (`AdInteraction.user_id == ...`, tức tập `saved`). Tab `favorites` (`:926`) và `saved` (`:932`), cùng cờ `loved_untested` trên **mọi** tab (`:938`), đều là dữ liệu riêng của từng MKT.
- `db.info["user_id"]` được đặt theo từng request (`db.py:127`), nên ai gọi đầu tiên trong cửa sổ 60 giây thì kết quả của người đó được trả cho mọi người gọi sau (TTL 60 s, `intel.py:49-56`).
- **Repro** (`test_discovery_favorites_cache_crosses_users`): A vote LOVE cho P. A gọi `/api/discovery/tab/favorites` → `[P]`. Ngay sau đó B (chưa từng vote) gọi → cũng nhận `[P]`.
- UI đang dùng endpoint này: `apps/web/app/radar/page.tsx:75`.
- Điều này mâu thuẫn với nguyên tắc ở `library.py:4-5` ("an MKT only ever sees their own interactions"), và với 02 §1.
- Các `cached()` khác (`main.py:225,314,717`, `intel.py:945,1432`) đã kiểm bằng AST: không đọc `user_id`, nên an toàn.
- **Sửa** (một dòng): thêm `db.info.get("user_id")` vào key, ít nhất cho `favorites`/`saved`. Có thể bỏ luôn 2 tab này như 03 F4 đề xuất (đã có `/workspace/library`). `loved_untested` thì tính sau khi lấy từ cache.

### C2 — MEDIUM (bug) — Cluster merge sau khi hai MKT đã claim làm vỡ tính độc quyền và khoá chủ sở hữu ra khỏi sản phẩm của chính mình
- Merge (`services/cluster_membership.py:60-95`, được gọi từ `clustering.auto_merge` `:369` và từ accept candidate) chỉ đổi `merged_into_id` và `AdClusterMembership.cluster_id`. Code này **không đụng tới** `AllocationLock`/`ProductAllocation`. Grep `alloc` trong `services/` ra rỗng.
- `allocation.claim` (`allocation.py:171-178`) lấy `scope_of` của cluster mới. `owners()` (`:135-138`) thấy lock của MKT khác, nên ném 409 `taken` (`:177-178`). `library.start_test` (`library.py:200-207`) gọi `A.claim` ngay trước khi chuyển sang `testing`.
- **Repro** (`test_merge_after_two_claims_breaks_exclusivity`): A claim P, B claim Q, sau đó gộp Q vào P (confidence 0.99, đúng cách `CM.join` ghi dữ liệu). Kết quả: cluster có **2 chủ** `{1, 2}`, và `POST /api/library/products/P/start-test` của A trả **409** "Sản phẩm đã được MKT khác nhận". B cũng gặp tình trạng tương tự.
- Test hiện có chỉ merge **trước** khi claim (`tests/test_allocation.py:44-51`, `test_e2e_personalization.py:33-44`), nên không bắt được lỗi này.
- Hiện lỗi đang tiềm ẩn: memory ghi bảng allocation live còn rỗng. Nó sẽ xảy ra ngay khi MKT bắt đầu claim, vì auto-merge chạy mỗi 180 phút (`realtime.py:352`).
- **Sửa**: (a) `auto_merge` bỏ qua cặp mà hai bên có lock của hai user khác nhau. Ghi `row.blockers += ["allocation_conflict"]` để đẩy sang hàng đợi MKT. (b) Accept thủ công cũng chặn như vậy. (c) Nếu cluster đã có allocation của chính người gọi, `claim()` trả allocation đó thay vì 409. Kèm một test: claim A, claim B, merge, rồi assert bị chặn.

### C3 — LOW (bug/security) — Một MKT tự nhân được vote của mình bằng cách đổi trường `market`
- `routers/intel.py:852-858`: `VoteIn.market: str | None`, không có whitelist. Dedupe theo `(product, user, market)` (`:868`), nên mỗi giá trị `market` khác nhau tạo thêm một dòng `Vote` mới.
- `services/discovery.py:272-276`: `mkt_appeal = mean(votes)` trên mọi vote của sản phẩm, chiếm 10% opportunity (`:293`), và cũng dùng để train taste model (`:162`). Mỗi vote còn `ScoreContext.invalidate()` và rescore đồng bộ (`intel.py:876-877`), không có rate limit.
- **Repro** (`test_one_mkt_can_stack_votes_via_market_field`): 5 lần POST với `market` = M0..M4 trả 5 lần 200, tạo ra **5 dòng vote** của cùng một user trên cùng một sản phẩm.
- Kết hợp với đăng ký mở (B2), người lạ cũng đẩy được thứ hạng sản phẩm.
- **Sửa**: validate `market` theo danh sách thị trường (`markets.py`) hoặc bỏ `market` khỏi khoá dedupe. Tính `mkt_appeal` theo trung bình **mỗi user**. Thêm `rate_limit(f"vote:{uid}", 120, 3600)`.

## 2. Kết luận chưa được kiểm chứng: "F3 là nguyên nhân gốc của F2" (05 §F2/F3)
- Đúng là kết nối bằng `.env` **bây giờ** bị từ chối. Mình đã tái kiểm bằng chính loader của app (`import app` rồi `create_engine`): `password authentication failed`.
- Nhưng `.env` sửa lần cuối lúc **2026-10-19 14:40**, còn worker vẫn chạy và ghi log tới **2026-10-23 17:25** (`apps/api/worker_err.log`). Vậy với cùng `.env` đó, DB đã từng kết nối được. Phía Postgres đã đổi (mật khẩu role `postgres` hoặc `pg_hba`) trong khoảng 23/10 đến lần reboot 25/10, hoặc worker chạy bằng env khác.
- Không log nào ghi lại một lần khởi động thất bại vì DB: `api_err.log` dừng ở 19/10, `api_run.log` dừng ở 15/10. Log cuối của worker có `psycopg.errors.OutOfMemory` (`worker_err.log:99`) chứ không có lỗi xác thực.
- Kết luận đúng hơn: F2 = **không có autostart sau reboot** (đã đúng như 05 viết), cộng thêm **credential lệch** sau ngày 23/10. Khi sửa F3, cần xác định phía PG đã đổi gì, chứ không chỉ sửa `.env`. OOM của worker cũng chưa ai điều tra (04 F18 nói vòng lặp bị chặn, nhưng chưa nói tới OOM).

## 3. Đã xem nhưng không thành finding
- **href `javascript:`**: backend không kiểm scheme của `landing_url`/`snapshot_url` (`ingest.py:355`, `routers/intel.py:295`), và React 18.3.1 (`apps/web/package.json:20`) chỉ cảnh báo chứ không chặn `javascript:`. Tuy nhiên mọi link ngoài đều có `target="_blank" rel="noreferrer"` (vd `components/ads/ad-detail-sheet.tsx:49`, `ad-card.tsx:119`), nên script khó chạy trong origin của app. Nguồn dữ liệu cũng chủ yếu là Meta/TikTok, nơi đã tự validate. Nên coi là việc phòng thủ thêm: một helper `safeHref()` chỉ cho `http(s)`. Không xếp hạng.
- **GET có side-effect** (vượt allow-list ghi và CSRF): đã quét mọi route GET bằng `inspect`. Không thấy GET nào gọi API trả phí hay LLM. Chỉ `/api/auth/csrf` ghi DB, và đó là chủ ý.
- **Cache search cá nhân** (`intel.py:552-575`) và recommend memo (`recommend.py:389`): key đã có `uid`, an toàn.
- **Follow/refresh đối thủ**: có rate limit theo user (`workspace/routes.py:76,145`) và cooldown 5 phút (`:156`).
- **Content filter giải trí**: mọi đường tạo `Ad` đều đi qua `ingest.ingest_ads` (`ingest.py:162`) hoặc `services/connectors.py:283`.
- **OTP reset**: 6 số, giới hạn 5 lần sai/email/10 phút (`accounts.py:228`), bind theo email, dùng một lần, không lộ có hay không có tài khoản. Ổn.

## 4. Đề xuất bổ sung cho bản thiết kế
1. Quy tắc chung: **mọi cache in-process phải có `uid` trong key nếu hàm đọc `db.info["user_id"]`**. Thêm một test chạy hai user cho mỗi endpoint dùng `cached()`.
2. Allocation × clustering: hai module phải biết nhau. Merge tự động không được gộp hai cụm có hai chủ khác nhau.
3. Vote: mỗi user một phiếu cho mỗi sản phẩm (hoặc mỗi thị trường hợp lệ), và có rate limit.
4. Trước khi coi các nhận định về dữ liệu live là đúng, cần chạy bộ SQL ở 04 §"Câu SQL nên chạy" sau khi đã có lại quyền DB.
