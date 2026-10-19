# TOOLSPY — Personalized Ads Engine: tiến độ và Phase 0 audit

**Cập nhật 19/10/2026 — hoàn tất Phase 1–9:** Product Clustering (mục 10), Exclusive Product Allocation (11), MKT Behavior Tracking (12), MKT Preference Profile (13), Personalized Recommendation Engine (14), Similar Ads & Products (15), Personalized Search (16), Personalized Ads Library (17), Personalization Dashboard & Final QA (18), Chính sách tài khoản một vai trò MKT + Soft Deduplication (19). Tổng kết và trạng thái DONE / PARTIAL ở mục 9; QA, hiệu năng, migration và giới hạn ở mục 18. Mục 1–8 giữ nguyên kết quả audit lịch sử ngày 16/10. Chưa nghiệm thu trên server đang chạy với tài khoản thật.

Ngày audit: **16/10/2026, Asia/Saigon (UTC+7)**. Snapshot PostgreSQL bắt đầu lúc **13:37:32 +07:00**; các phép kiểm tra bổ sung thực hiện trong cùng lượt audit. Source: working tree tại `D:\ToolSpy`, HEAD `0e1881a`, có nhiều thay đổi tracked/untracked từ trước. Kết luận áp dụng cho working tree đang có, không chỉ commit HEAD.

## 1. Kết luận và điều kiện chuyển phase

**Phase 0 đã hoàn thành audit. Chưa đủ điều kiện xác nhận nền tảng sẵn sàng cho personalization. Không triển khai phase tiếp theo.**

- **Authentication: PARTIAL ở mức vận hành.** Core đăng ký/đăng nhập, session, CSRF, RBAC, đổi/reset mật khẩu đã có và kiểm thử riêng đạt. PostgreSQL có 4 MKT active, 4 session còn hạn nhưng **không có ADMIN**; `SMTP_HOST` chưa cấu hình. API/Web tại cổng mặc định chưa kết nối được nên chưa xác nhận luồng đăng nhập bằng trình duyệt trên runtime hiện tại. Không kết luận core login bị lỗi từ việc này.
- **Follow Competitor: PARTIAL.** Có quan hệ riêng từng user, lịch quét bền vững, baseline, snapshot, event và notification. Database hiện có **0 Follow, 0 scan job, 0 tracked ad, 0 snapshot/event/notification của tracker**. Chưa có bằng chứng vận hành thật; chỉ một số nguồn hỗ trợ truy vấn advertiser ID. Không được coi Follow là nguồn tín hiệu đã sẵn sàng.
- **Nhận diện sản phẩm trùng: PARTIAL.** Có ingest matching, hash, CLIP, pgvector, auto merge và review/undo. Có 471 cặp pending, trong đó 2 cặp thiếu product đầu mút; heuristic phát hiện 18 cụm nghi lẫn sản phẩm. Quyền MKT duyệt cluster đang bị chặn 403 dù UI quảng bá chức năng này.
- **Personalized Engine: MISSING.** Chưa thấy schema/API/worker cho Marketer DNA, personal fit, feed cá nhân, assignment, test outcome hay feedback học từ hiệu quả kinh doanh. Global/team scoring hiện có không tương đương cá nhân hóa.

Các điều kiện cần xử lý trước khi đánh giá lại: xác nhận runtime Auth và Follow trên nguồn hỗ trợ; xác định đường quản trị tài khoản; giải quyết quyền review và chất lượng canonical product; chốt ownership/feedback contract. Đây là kết luận audit, **không phải các thay đổi đã thực hiện**.

## 2. Phương pháp và phạm vi bằng chứng

- Đọc source API, frontend, worker, models, các bộ test hiện có; đối chiếu tài liệu thiết kế `marketer_dna_personalized_product_routing.md` với code thực tế.
- Truy vấn PostgreSQL bằng connection riêng có `default_transaction_read_only=on`, `statement_timeout`, transaction `READ ONLY`; snapshot schema dùng `REPEATABLE READ`. Không chạy migration, seed, sync, rescore hoặc worker trên database hiện tại.
- Database: `toolspy`, **PostgreSQL 18.6**, extension **vector 0.8.6**. Không ghi URL kết nối, email, password hash, session token hay giá trị bí mật từ `.env` vào báo cáo.
- Kiểm tra mọi FK khai báo trong schema thực tế: **48 FK, 0 orphan**. Có kiểm tra bổ sung quan hệ không có FK, NULL ownership, advertiser identity và consistency giữa ad/creative/product.
- Không thấy bảng/cột ORM bị thiếu so với PostgreSQL khi nạp metadata qua `app.models`, workspace và clusters. `embedding_vec` là cột SQL riêng, ORM không khai báo type vector; đã xác nhận qua `pg_attribute` là `vector(512)`, không phải cột NULL-type bị lỗi.
- Test dùng SQLite tạm theo `tests/conftest.py`, không dùng PostgreSQL hiện tại. Không chạy script `verify_workspace_postgres.py` vì script tạo rồi xóa database test; lượt này chỉ đọc PostgreSQL hiện tại.
- HTTP GET đến `/api/health`, `/api/auth/me`, `/api/competitors`, `/api/workspace/ads` tại `127.0.0.1:8000` và qua `127.0.0.1:3000` đều không kết nối được (`URLError`). Chỉ kết luận chưa truy cập được hai địa chỉ này, không suy ra mọi deployment đều ngừng chạy. Không restart vì startup chạy migration/backfill và có thể ghi DB (`apps/api/app/main.py:76`).
- Đọc process qua CIM bị môi trường từ chối (`Access denied`), nên không xác nhận được worker đang chạy. Không xin mở rộng quyền hoặc thay đổi runtime cho audit.

Quy ước: **PASS** = tiêu chí cụ thể có bằng chứng đáp ứng; **PARTIAL** = đã có nhưng còn lỗi, thiếu dữ liệu hoặc chưa kiểm chứng vận hành; **MISSING** = chưa tìm thấy implementation/schema trong phạm vi đã đọc/tìm kiếm. PASS của một thành phần không có nghĩa toàn hệ thống đã được nghiệm thu production.

## 3. Bảng đánh giá chức năng

Đường dẫn dưới đây tính từ repo root; số sau dấu `:` là dòng source tại thời điểm audit.

| Hạng mục | Đánh giá | Bằng chứng source / API / PostgreSQL | Phần còn thiếu hoặc giới hạn |
|---|---|---|---|
| Core Authentication | PASS | `apps/api/app/workspace/auth.py:23`, `:108`, `:136`; `workspace/accounts.py:56`, `:75`, `:100`; Argon2id, token session lưu dạng hash, cookie HttpOnly, kiểm tra active/expiry, logout/revoke. Bộ workspace 16/16 đạt khi chạy riêng. | PASS cho implementation và test cách ly; chưa nghiệm thu browser runtime. |
| CSRF, RBAC và account isolation | PASS | `workspace/auth.py:125`, `:136`, `:183`; `db.py:124`; `apps/web/lib/api.ts:1`, `apps/web/middleware.js:7`. Test anonymous 401, chống role injection/CSRF, ownership, revoke session. | MKT write dùng allowlist; có xung đột cluster nêu riêng dưới đây. |
| Auth vận hành, quản trị, recovery | PARTIAL | `workspace/accounts.py:161`, `:208`, `:217`; `workspace/cli.py`. DB 4 user active đều role MKT; 0 ADMIN. `SMTP_HOST` absent. | Chưa có ADMIN đăng nhập để quản trị/recovery; reset qua email chưa hoạt động nếu chưa cấu hình SMTP, có đường CLI trong source. Register hiện tạo MKT active ngay (`accounts.py:64`), không chờ duyệt; cần chốt đúng policy mong muốn. |
| MKT Workspace | PARTIAL | `workspace/models.py:55`, `routes.py:49`, `:299`; `apps/web/app/workspace/page.tsx`; `components/workspace/session.tsx`. Có dashboard, team/bio, filters, hoạt động riêng user. DB 4 profile, tất cả team rỗng và mode `all`. | Profile mới có team/bio/filter mode, chưa có category/market/price/angle preference hoặc DNA; chưa có sử dụng workspace ngoài register/login. |
| Follow/Unfollow riêng user | PASS | `workspace/models.py:84`, `routes.py:73`, `:96`, `:107`; PK `(user_id, advertiser_id)`, FK, interval 6/12/24h. `components/workspace/actions.tsx:9`; test persistence/isolation/shared job đạt. | PASS về đường code/test; live adoption và scan là các tiêu chí PARTIAL bên dưới. |
| Follow tracking và nguồn thật | PARTIAL | `workspace/sources.py:24`, `worker.py:38`, `:97`, `tracking.py`; API refresh/status/ads/timeline/products/snapshots. DB tất cả bảng follow/tracker đều 0. | Chưa có lượt Follow/quét thật để chứng minh end-to-end; support phụ thuộc source identity, connector enabled và quyền/cấu hình nguồn. |
| Notification / lịch sử Follow | PARTIAL | `workspace/models.py:153`, `:165`, `routes.py:316`, `:333`; fan-out theo follower, unique `(user_id,event_id)`. | 0 event, 0 notification thật; `mkt_activity` chỉ có 4 registered + 4 login. |
| Search local / live / saved searches | PARTIAL | `routers/intel.py:398`, `:551`, `:600`, `:635`; `workspace/routes.py:353`; `apps/web/app/search/page.tsx:199`. Job gắn `db.info.user_id`, đọc job có ownership check; advertiser filter có áp dụng. | 131 job nhưng chỉ 5 có user_id; 126 job legacy NULL; saved searches = 0. Không có event view/click/impression/search-result exposure để học preference đầy đủ. |
| Library riêng user | PARTIAL | `workspace/models.py:63`, `routes.py:268`, `:285`; `apps/web/app/workspace/library/page.tsx`. Save/favorite/tested/note theo `(user_id,ad_id)` và filter riêng. Test isolation đạt. | `mkt_ad_interactions` = 0. `tested` chỉ là boolean trên ad, không có kết quả test cấp product/market, CPA/ROAS/profit. |
| Advertiser identity | PARTIAL | `workspace/identity.py:1`, `workspace/migrate.py:12`, `models.py:110`. Unique `(source,source_advertiser_id)`; 0 duplicate non-NULL, 0 mismatch giữa ads và verified advertiser source/page ID. | 1.235/4.958 advertiser thiếu source hoặc source ID; 306 identity `unverified:*`. 6.069 ad thiếu advertiser_id, trong đó 2.283 thuộc channel ads, 3.786 commerce. |
| Ingest dedup / product resolution | PARTIAL | `ingest.py:148`, `:249`, `:330`; `services/entity_resolution.py:97`, `:167`, `:226`, `:294`; `services/product_identity.py:30`. Có provenance, fingerprint, alias, title/price, grounded identity. | 0 duplicate `(source,external_id)` hiện tại nhưng `ads` chưa có unique constraint cho cặp này. Product identity có dữ liệu trên 2.471/11.681 product; chưa có đo precision/recall hiện tại bằng bộ nhãn chuẩn. |
| Product Clustering và sửa cụm | PARTIAL | `services/clustering.py:41`, `:264`, `:386`, `:442`; `routers/clusters.py:33`; `apps/web/app/clusters/page.tsx`. 119 auto_merged, 2 accepted, 471 pending, 197 stale. Bộ clustering chạy riêng 15/15 đạt. | 2 pending có đầu mút product đã mất; 18 cụm nghi lẫn theo heuristic. MKT accept/reject bị 403. |
| CLIP runtime | PASS | `visual/embed.py:27`, `:68`, `:118`; model local fp32 có sẵn, `enabled=True`, onnxruntime 1.31.0. Inference ảnh kiểm tra trong RAM trả vector 512 chiều, finite, L2 norm 0,99999988. | Đây là xác nhận inference, chưa chứng minh độ đúng nhận diện sản phẩm. |
| Image index / vector search | PASS | `visual/models.py:14`, `visual/embed.py:190`, `:219`. 92.070 ảnh: 92.065 done có embedding BYTEA + vector + pHash, 5 no_image. `vector(512)` + HNSW cosine; truy vấn top-5 thực trả 5 kết quả, max cosine 1,0. | Embedding là vision-only; chưa có text encoder hay vector sở thích user. Không cần Python package `pgvector` vì dùng raw SQL. |
| Global opportunity / wave / confidence | PASS | `services/scoring.py:30`, `:132`, `:160`; `services/engine.py:21`, `:77`; toàn bộ 11.681 product đã có scored_at và wave_score. Unified scoring đạt 6/6 trong process scoring/query riêng. | PASS về tính sẵn có; score là heuristic dùng dữ liệu toàn cục, không phải xác suất thắng kinh doanh đã được hiệu chuẩn. |
| Team taste / market DNA hiện có | PARTIAL | `services/discovery.py:89`, `:149`; `services/engine.py:56`. `TasteModel` đọc tất cả Vote; ScoreContext cache dùng chung. | Chỉ 3 vote, toàn bộ user_id NULL. Đây là team/global aggregation; không phân user, chưa có confidence theo lịch sử hiệu quả từng MKT. |
| Worker / scheduling | PARTIAL | `worker.py:17`, `realtime.py:334`, `:365`, `:402`, `:421`; `workspace/worker.py:38`, `:69`, `:144`. Có pipeline media/embed/cluster/rescore và tracker lease/quota/backoff/process timeout. | `.env` có `DISABLE_SCHEDULER=1`: cần process `app.worker` riêng; chưa xác nhận process chạy. General scheduler giữ state trong RAM, yêu cầu chỉ một worker; tracker có lease trong DB. |
| Marketer DNA / Winner DNA | MISSING | Tìm source không có implementation `personal_fit`, `winner_dna`, `dna_version`, marketer score tables; schema không có các bảng đề xuất. Chỉ có bản thiết kế `docs/marketer_dna_personalized_product_routing.md`. | Cần preference, outcome, taxonomy, smoothing/time decay/version/confidence và contract bảo vệ dữ liệu từng user. |
| Personal feed / explanation / routing | MISSING | Không thấy routes marketer recommendations, product marketer-fit/best-marketer/assign/result, bảng assignments/tests/results hay worker personalization. | Chưa có score cá nhân, feed ranking, giải thích WHY THIS FITS YOU, workload routing hay exploration policy. |
| Feedback từ kinh doanh | MISSING | `Vote` ở `models.py:340`; `AdInteraction` ở `workspace/models.py:63` chỉ lưu ý kiến/trạng thái. Không có product_tests/test_results. | Thiếu winner/loser có căn cứ, orders/delivered/returned/CPA/ROAS/profit/market/time và nguyên nhân thất bại. |
| Dependency có thể tái dựng | PARTIAL | `apps/api/requirements.txt`; `visual/embed.py:69` import `onnxruntime` khi load model. Các dependency Auth/DB/image chính có trên máy; psycopg dùng theo requirements và kết nối thật thành công. | **onnxruntime đang được cài nhưng không có trong requirements.txt**; model ONNX cũng cần provisioning riêng. Thiếu package `pgvector`/`apscheduler` không phải blocker của kiến trúc hiện tại (raw SQL/async loop). |

## 4. Quan hệ user_id, ad_id, product_id, advertiser_id

| Quan hệ | Trạng thái schema thật và dữ liệu | Hệ quả cho personalization |
|---|---|---|
| `users.id → user_sessions.user_id`, `password_resets.user_id`, `mkt_profiles.user_id` | Có FK; profile PK=user_id; 4 profile và 4 session hợp lệ. | Có identity tài khoản chuẩn để làm chủ tín hiệu mới. |
| `users.id + ads.id → mkt_ad_interactions(user_id,ad_id)` | Hai FK, composite PK; 0 row. | Join sang `ads.product_id` để tạo tín hiệu product, không dùng `ads.saved_by` làm ownership. Cần phân biệt ý thích ad/creative với ý thích product. |
| `users.id + advertisers.id → mkt_followed_advertisers`, `mkt_advertiser_filters` | Hai FK, composite PK; đều 0 row. | Shared advertiser, private follow/filter. `is_competitor_watched` legacy không thay thế quan hệ user. |
| `users.id → mkt_saved_searches`, `mkt_activity`, `mkt_notifications`, `mkt_resource_access` | Có FK user; notification có FK event và unique user/event. Resource ID là tham chiếu đa loại, không có FK đích. | Có nền tảng ownership; metadata JSON không thay thế event contract typed/versioned. |
| `ads.product_id → products.id` | FK nullable, thực tế 16.131/16.131 ad có product, 0 orphan. 11.681 product đều có ad; 1.406 product có nhiều ad, lớn nhất 162 ad. | Quan hệ nhiều ad / một product đã có; độ đúng cụm vẫn cần đo. |
| `ads.advertiser_id → advertisers.id` | FK nullable; 10.062/16.131 có advertiser, 6.069 NULL; 0 orphan khác NULL. | Không thể gắn mọi ad vào Follow. Commerce listing thiếu advertiser không mặc nhiên là bug; phải xét riêng channel ads. |
| `creatives.ad_id`, `creatives.product_id` | Có FK; 99.547 creative; 0 trường hợp creative.product_id lệch ad.product_id khi join được ad. | Có đường nối creative/image → ad → canonical product. |
| `advertiser_scan_jobs → advertisers`; `tracked_ads → advertisers,ads`; snapshot/event/notification | FK + unique advertiser/source_ad và tracked_ad/run_token; `TrackedAd.ad_id` nullable. Hiện 0 dữ liệu. | `TrackedAd.id` khác `Ad.id`; không dùng nhầm ID ở Library. `source_ad_id` là ID nguồn, không phải PK nội bộ. |
| `search_jobs.user_id`, `votes.user_id` | Chỉ Integer + index, **không FK users**. 126/131 search job NULL owner, 3/3 vote NULL owner; non-NULL hiện không orphan. | Không thể coi dữ liệu legacy là lịch sử cá nhân đã xác minh. Cần chốt xử lý unknown owner và constraint cho dữ liệu mới. |
| `votes.product_id → products.id` | Có FK; thiếu unique `(user_id,product_id,market)`, route hiện xử lý upsert bằng query. | Có rủi ro duplicate khi ghi đồng thời; chưa quan sát duplicate cá nhân vì chưa có vote owned. Vote không phải outcome. |
| `product_images(source,source_id)` | Unique; tham chiếu polymorphic, không FK: creative → creatives.id, eu_listing → eu_listings.id. Đã kiểm tra: 0 owner bị mất. | Không có product_id trực tiếp; cần join owner đúng loại để aggregate vector. |
| `product_links.a_id/b_id`, `event_id`; `events.product_id` | Không FK; các ID product đã bị merge có thể tồn tại trong lịch sử có chủ đích. | 121 accepted/auto_merged link có đầu mút đã mất được UI dựng lại qua event; không đánh đồng với orphan hỏng. 197 stale là lịch sử; **2 pending thiếu product là vấn đề thực**. |

Identity advertiser hiện là **source + ID do nền tảng cung cấp**, không phải tên hiển thị. Cùng page chạy qua nhiều source có thể tạo các identity riêng; chưa thấy registry canonical advertiser liên nguồn. Không gộp advertiser theo tên để lấp chỗ thiếu.

## 5. Khả năng nhận diện sản phẩm trùng

### Cơ chế đã có

1. Ingest tìm provenance/source ad ID và creative fingerprint (`ingest.py:249`), rồi resolve product bằng tên/alias/giá/metadata, brand/model/GTIN khi có (`services/entity_resolution.py:97`, `:167`).
2. Identity theo landing page bỏ trang chủ/shop/collection/social link (`entity_resolution.py:294`); không dùng trang shop chung làm bằng chứng duy nhất cho product.
3. Clustering kết hợp landing, copy, pHash, CLIP và name bằng `1 - product(1 - weight)`; mặc định auto ≥ 0,93, review ≥ 0,60, CLIP candidate ≥ 0,90 (`services/clustering.py:41`). CLIP đơn lẻ không đủ auto merge.
4. Có stock/hub-image guard, name/category/price conflict, giới hạn cụm; rejected/split/undo tạo cannot-link. Có auto merge, pending review, split/undo và event history.
5. Merge chuyển ads, creatives, comments, stores và votes; dữ liệu snapshot/lifecycle của product nguồn có thể bị xóa theo implementation hiện tại (`entity_resolution.py:344`, `:365`, `:386`). Future outcome/assignment tables cần chính sách chuyển ID/lưu lineage trước khi dùng chung cơ chế xóa child theo FK.

### Bằng chứng hiện tại và giới hạn

- 11.681 product; 3.989 AUTO_CONFIRMED, 3.072 SUGGESTED, 4.620 UNKNOWN; **0 VERIFIED**. Không coi UNKNOWN/shop/service bucket là product đã nhận diện chắc chắn.
- 29 nhóm trùng normalized canonical name trong tập AUTO_CONFIRMED/VERIFIED, với 41 row vượt một row/nhóm. Đây chỉ là candidate, không chứng minh cùng SKU/variant; không merge trong audit.
- 92.065 ảnh được embed bằng `clip-vit-b32-fp32`; tất cả có hash và vector. Dữ liệu embedding từ `2026-10-09 05:15:54.852261` đến `2026-10-15 09:33:30.300687` UTC. Vision similarity không đủ phân biệt mọi góc chụp/SKU/variant.
- Chạy **chỉ hàm đọc** `detect_impure(db)` trên PostgreSQL: **18 product, 169 ad trong các nhóm nghi vấn**. Heuristic mặc định bỏ qua cụm >120 ad và phụ thuộc name evidence; do đó không phải kiểm tra đầy đủ mọi merge sai và không phải bộ nhãn chuẩn.
- Gọi hàm đọc `routers.clusters.candidates` trên connection READ ONLY, lấy toàn bộ pending: `total=471`, `len(rows)=469`. Code đếm trước rồi bỏ row thiếu hai product; cần sửa tính nhất quán danh sách ở phase được phép sửa.
- Grounded identity guard có trong ingest resolution; chưa thấy `product_identity.identity_match` được gọi trong `services/clustering.py`. Cần kiểm chứng riêng nguy cơ auto cluster vượt qua xung đột brand/model/variant; đây là rủi ro từ đọc code, chưa tái hiện merge sai.
- Không chạy `clustering.run`, kể cả `apply_auto=False`: hàm vẫn store candidates/mark stale. Không có auto merge hay thay đổi dữ liệu do audit.

**Đánh giá:** tái sử dụng được nền tảng dedup, nhưng chưa đủ căn cứ coi product_id là nhãn ground truth tuyệt đối để học Winner DNA. Cần bộ nhãn same/different, precision/recall theo nguồn và variant, xử lý pending/dead link, xác minh cụm lớn/UNKNOWN và cơ chế lineage khi merge/split.

## 6. Lỗi xác nhận và dependency trước cá nhân hóa

| ID / ưu tiên | Phát hiện | Bằng chứng và tác động | Điều kiện hoàn tất ở phase sau |
|---|---|---|---|
| A01 / P0 | Auth/Follow chưa được nghiệm thu runtime | Không kết nối hai cổng mặc định; DB không ADMIN, SMTP absent; Follow/tracker không dữ liệu. `main.py:76`, `workspace/accounts.py:161`, `worker.py:17`. | Xác nhận môi trường chạy, Auth qua trình duyệt, đường ADMIN/recovery, Follow/quét nguồn hỗ trợ và notification/isolation bằng dữ liệu kiểm thử được cho phép. |
| A02 / P1 | MKT bị chặn review cluster | Repro TestClient với user MKT active, CSRF hợp lệ: cả POST accept/reject trả **403 `Thao tác này cần quyền ADMIN`**. `auth.mkt_write` không chứa `/api/clusters/*`; UI/nav vẫn đưa MKT vào trang review. | Chốt thao tác nào MKT được phép, sửa quyền/UX phù hợp và kiểm thử account role thật. Không nới quyền trong audit. |
| A03 / P1 | Pending cluster có dead reference và lệch total | PostgreSQL có 2 pending thiếu product; hàm đọc trả 469/471. `routers/clusters.py:33`, `services/clustering.py` có cơ chế mark_stale nhưng dữ liệu hiện chưa đồng bộ. | Quy tắc stale/filter/count, bảo vệ race khi merge, kiểm chứng lại queue. |
| A04 / P1 | Ownership lịch sử thiếu, một số quan hệ chưa FK/unique | 126 search job và 3 vote NULL owner; `models.py:315`, `:340`; không FK user, vote chưa unique key nghiệp vụ. | Dữ liệu mới dùng user_id từ session; quyết định cách ly legacy unknown owner; FK/index/uniqueness và concurrency contract phù hợp. |
| A05 / P1 | Canonical product/taxonomy chưa đủ sạch | 4.620 UNKNOWN, 6.693 thiếu category, 18 cụm nghi lẫn; chỉ 2.471 có product_identity. | Nhãn chất lượng, taxonomy/category/variant và lineage để feedback không học lẫn sản phẩm. |
| A06 / P1 | Chưa có behavioral/outcome contract | Không view/click/impression history typed; save/test chỉ ad-level boolean; không performance tables. | Chốt user/ad/product/advertiser/market/time, event idempotency/version; phân biệt exposure/interest/test/result và attribution window. |
| A07 / P1 | Global/team aggregation chưa cá nhân | `TasteModel` đọc mọi Vote, `ScoreContext` cache dùng chung; score lưu trên Product. | Tách contract global/team/private; personal ranking/cache bắt buộc có user/version, không ghi score cá nhân lên product shared. |
| A08 / P2 | CLIP dependency chưa tái dựng đầy đủ | Máy có onnxruntime và model; requirements thiếu onnxruntime. | Dependency/model provisioning, model version/dimension và smoke test môi trường mới. Text encoder chỉ cần nếu sau này chọn text-image embedding, không phải dependency đang thiếu của vision search hiện có. |
| A09 / P2 | Test suite chạy chung không cô lập hoàn toàn | Chạy chung: 40 passed, 1 failed, 14 setup errors; fixture clustering `DELETE FROM ads` vướng FK workspace; unified scoring vướng duplicate product_code sau test trước. Chạy riêng 55/55 đạt. | Dọn fixture theo quan hệ mới và chứng minh chạy chung sạch; test quyền MKT thật cho cluster. Không coi suite hiện tại là green. |
| A10 / P2 | Chưa có outcome/assignment/DNA serving/evaluation | Không bảng/API đề xuất, không replay/offline metric/personalization jobs. | Thiết kế feedback, cold start, confidence/time decay, ranking/exploration, giải thích và đánh giá trước rollout. Chưa triển khai. |

Độ phủ scoring hiện có: 11.681/11.681 đã chấm, latest scored_at `2026-10-15 09:52:45.527516` UTC; 8.330 có market_scores không rỗng; 3.879 có price >0; **0 có cost >0**. Không đủ dữ liệu để suy ra lợi nhuận thật. Trên ad, 16.131 row có hook/angle nhưng chỉ 4.240 hook khác `statement`/rỗng và 4.407 angle khác `general`/rỗng; không coi default classifier là evidence đầy đủ cho DNA.

### Source support của Follow

`workspace/sources.py:24` khai báo hỗ trợ `meta_library`, `meta_graph`, `tiktok_ad_library` nếu có advertiser ID xác minh. DB đang bật meta_library và tiktok_ad_library; meta_graph đang tắt. TikTok library giới hạn phạm vi EU/UK theo contract trong code. Meta Graph thiếu chứng cứ pagination completeness nên lượt quét chỉ được đánh dấu partial. Meta Library/TikTok chỉ complete khi có chứng cứ kết thúc pagination và không chạm giới hạn.

Các source tiktok_commercial, tiktok_top_ads/headless/Apify Top Ads, Snapchat và Apify Meta không có hỗ trợ quét advertiser được xác minh trong tracker hiện tại. Chúng vẫn có thể cung cấp dữ liệu search; bật search connector không làm Follow scan tự nhiên được hỗ trợ. Chưa gọi nguồn ngoài để xác minh thay đổi API/provider trong audit này.

## 7. API có thể tái sử dụng và API chưa có

| Nhóm | API hiện có trong source | Điểm cần lưu ý |
|---|---|---|
| Auth/admin | `/api/auth/{csrf,register,login,me,logout,profile,password,forgot-password,reset-password}`; `/api/admin/users`, `/api/admin/users/{uid}`, `/api/admin/reset-requests` | Cookie/session + CSRF, quản trị theo ADMIN; chưa kiểm chứng HTTP runtime hiện tại. |
| Workspace/Library | `/api/workspace/{state,dashboard,ads,ads/{aid},filters,filters/{aid},searches,searches/{sid},activity,notifications,notifications/{nid}}` | Ownership từ session, không nhận user_id tùy ý từ client. |
| Follow | `/api/advertisers`; `/api/competitors`; `/api/competitors/{aid}` và `/follow`, `/refresh`, `/status`, `/seen`, `/ads`, `/timeline`, `/products`, `/ads/{tid}/snapshots` | Scan chia sẻ theo advertiser; quyền theo user cho follow/đọc lịch sử riêng. |
| Search/discovery | `/api/search`, `/api/search/live`, `/api/search/jobs`, `/api/search/jobs/{jid}` và `/more`; `/api/ads`; `/api/products/{pid}/discovery`, `/vote`; `/api/discovery/tab/{tab}`, `/market-dna` | Job ownership có; market-dna là tín hiệu team/market; advertiser filter không phải personal ranking. |
| Clustering | `/api/clusters/{candidates,stats,impure}`; `/api/clusters/candidates/{lid}/{accept,reject}`; product merge/split/undo routes trong `main.py` | MKT chưa được quyền ghi cluster; `decided_by` hiện là chuỗi, chưa FK user. |
| Visual | `/api/search/images/*`, `/api/products/{pid}/similar`, `/image-matches`, `/price-comparison`; visual match/compare routes | Lens cache access qua `mkt_resource_access`; shared image index không phải user preference store. |
| Pricing / engagement | `/api/prices`, `/api/prices/{enrich,lookup,quota,history}`; `/api/engagement`, `/api/engagement/{enrich,lookup,history,quota}` | Bổ sung dữ liệu giá/engagement bên ngoài, không thay thế kết quả kinh doanh theo MKT. Không gọi enrich trong audit. |
| Personalized Engine | **MISSING**: `/api/marketers/{id}/{profile,dna,recommendations}`, `/api/products/{id}/{marketer-fit,best-marketer,assign,result}` theo bản thiết kế | Chưa có schema/service/worker tương ứng; URL là đề xuất trong tài liệu, không phải endpoint hoạt động. |

## 8. Kết quả kiểm chứng

Lệnh chạy tại `apps/api`, Python `.venv/Scripts/python.exe`; dùng `-p no:cacheprovider`, không sửa test/source chức năng.

| Kiểm tra | Kết quả |
|---|---|
| `-m pytest tests/test_workspace.py -q --disable-warnings -p no:cacheprovider --tb=short` | **16 passed**. Auth/CSRF/RBAC, reset/revoke, isolation, follow/job, tracking, quota/backoff/timeout, identity/migration trên DB tạm. |
| `-m pytest tests/test_clustering.py -q --disable-warnings -p no:cacheprovider --tb=short` | **15 passed** khi chạy process riêng; vector fixture, không chứng minh precision trên dữ liệu thật. |
| `-m pytest tests/test_unified_scoring.py tests/test_query_intent.py -q --disable-warnings -p no:cacheprovider --tb=short` | **24 passed** (6 scoring + 18 query intent, tính cả parametrization). Tổng ba process: **55 passed**. |
| Bốn file trên chạy chung một process | **40 passed, 1 failed, 14 errors**. Không che kết quả bằng rerun; lỗi isolation fixture ghi tại A09. |
| Repro chạy chung workspace + clustering, `--tb=line --maxfail=1` | **16 passed, 1 error**: `sqlite3.IntegrityError: FOREIGN KEY constraint failed`, SQL `DELETE FROM ads` trong fixture clustering. |
| Repro MKT cluster có session + CSRF, DB tạm | POST accept/reject đều **403**, kiểm tra middleware trước khi route xử lý candidate. |
| Repro anonymous, DB tạm | GET `/api/auth/me`, `/api/workspace/ads`, `/api/competitors` đều **401**. |
| PostgreSQL metadata / FK / consistency | 48 FK không orphan; 0 ad duplicate source/external_id; 0 advertiser duplicate identity non-NULL; 0 verified source/page mismatch; 0 creative/ad product mismatch; 0 image owner bị mất. |
| PostgreSQL vector + ONNX inference | top-5 vector search thành công; inference 512 chiều finite, normalized. Không download model, không gọi LLM/provider. |
| Runtime/API/browser/worker | **Chưa xác minh** do HTTP không kết nối và process listing bị giới hạn; không thay thế bằng kết quả SQLite. |

### Truy vấn kiểm tra lại các phát hiện chính

Chỉ chạy bằng connection read-only; số liệu thay đổi khi ứng dụng có hoạt động mới. Ví dụ:

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SHOW transaction_read_only;
SELECT role, status, count(*) FROM users GROUP BY role, status;
SELECT count(*) AS follows FROM mkt_followed_advertisers;
SELECT count(*) AS scans FROM advertiser_scan_jobs;
SELECT count(*) AS interactions FROM mkt_ad_interactions;
SELECT count(*) AS total, count(user_id) AS owned FROM search_jobs;
SELECT count(*) AS total, count(user_id) AS owned FROM votes;
SELECT count(*) FILTER (WHERE product_id IS NULL) AS missing_product,
       count(*) FILTER (WHERE advertiser_id IS NULL) AS missing_advertiser
FROM ads;
SELECT processing_status, count(*) AS total,
       count(embedding) AS embedded, count(embedding_vec) AS vectorized
FROM product_images GROUP BY processing_status;
SELECT status, count(*) FROM product_links GROUP BY status;
SELECT count(*) AS pending_missing_product
FROM product_links l
WHERE l.status = 'pending'
  AND (NOT EXISTS (SELECT 1 FROM products p WHERE p.id = l.a_id)
    OR NOT EXISTS (SELECT 1 FROM products p WHERE p.id = l.b_id));
ROLLBACK;
```

## 9. Theo dõi tiến độ từng phase

Phase 1–9 đã được người dùng phê duyệt riêng và triển khai. **DONE** chỉ dùng cho phần có test tự động chứng minh (unit/integration trên SQLite + PostgreSQL cô lập, browser trên stack cô lập). **PARTIAL** là phần đã có nhưng chưa có test chứng minh. Chưa phase nào được nghiệm thu trên server đang chạy với tài khoản thật (xem mục 18).

| Phase | Phạm vi / điều kiện đầu ra | Tiến độ | Bằng chứng / phần còn thiếu |
|---|---|---|---|
| 0 — Personalization Audit | Audit source + PostgreSQL, chức năng, quan hệ, duplicate detection, dependency, báo cáo | **DONE — 16/10/2026** | Mục 1–8. |
| 1 — Product Clustering | Gom Ads bằng nhiều bằng chứng, cluster riêng, confidence, review/undo, bảo toàn dữ liệu gốc | **DONE (engine + API)** · UI `/clusters` **PARTIAL** | Mục 10; 83 test PostgreSQL (migration lặp, review MKT, undo, concurrent accept, pgvector). Trang `/clusters` chưa có test browser. |
| 2 — Exclusive Product Allocation | Sở hữu độc quyền theo Product Cluster, trạng thái, TTL, claim / tự trả về kho, audit, kết quả test | **DONE** | Mục 11 và 19; race 6 MKT trên PostgreSQL, 3 MKT qua HTTP, TTL, tự trả về kho giữ lịch sử, 409 kèm sản phẩm tương tự. ADMIN Reassign đã gỡ theo chính sách mục 19. |
| 3 — MKT Behavior Tracking | Lịch sử hành vi: xem/chi tiết, lưu, yêu thích, test, bỏ qua, không quan tâm, follow | **DONE (API + hook)** · view tracking phía client **PARTIAL** | Mục 12; dedupe, đồng thời, cách ly. IntersectionObserver chưa có test browser. |
| 4 — MKT Preference Profile | Hồ sơ sở thích, trọng số + decay, chỉnh tay / reset, mặc định | **DONE (API + worker)** · UI chỉnh sở thích **MISSING** | Mục 13; 11 test (cách ly, decay, reset, worker). |
| 5 — Personalized Recommendation Engine | Điểm 0–100, 6 thành phần, giải thích, cold start, khám phá, loại sản phẩm đã có chủ, batch + cache | **DONE** | Mục 14 và 18; 11 test (gồm hạn mức khám phá). Trọng số chưa hiệu chỉnh bằng kết quả kinh doanh. |
| 6 — Similar Ads & Products | Tương tự sản phẩm / Ads, theo lịch sử, từ advertiser Follow, tách duplicate | **DONE** | Mục 15; test + pgvector thật; đo trên dữ liệu thật (chỉ số đại diện, chưa có bộ nhãn). |
| 7 — Personalized Search | 4 chế độ, bộ lọc, nút Nhận, debounce + cache | **DONE** | Mục 16; 8 test + browser 9 bước. |
| 8 — Personalized Ads Library | 7 mục, bộ lọc, điểm + lý do, hành động, đồng bộ trạng thái | **DONE** | Mục 17; 7 test + browser. |
| 9 — Personalization Dashboard & Final QA | Dashboard cá nhân, E2E nhiều MKT, quyền riêng tư, hiệu năng, index | **DONE** | Mục 18; E2E 5 test, browser dashboard, benchmark `docs/PHASE9_PERF.json`. |
| Chính sách tài khoản & Soft Dedup | Một vai trò MKT, đăng ký active ngay, không ADMIN; Save/Favorite là tín hiệu chống trùng mềm; Claim/Test giữ chống trùng cứng | **DONE** | Mục 19; 10/10 tiêu chí nghiệm thu PASS; SQLite 390 passed, PostgreSQL 174 passed, browser 3 script PASS; migration tài khoản đã chạy trên DB thật. |
| Đề xuất sau Phase 9 — Routing / feedback learning | Phân bổ theo workload, học từ kết quả test, Winner DNA | **NOT STARTED** | Cần dữ liệu hiệu quả kinh doanh thật. |
| Đề xuất sau Phase 9 — Evaluation & rollout | Đánh giá offline, nghiệm thu trên server thật, theo dõi vận hành | **NOT STARTED** | Cần restart server, tài khoản ADMIN, dữ liệu hành vi thật. |

### Trạng thái các phát hiện của Phase 0

| ID | Phát hiện (mục 6) | Trạng thái 19/10/2026 |
|---|---|---|
| A01 | Auth/Follow chưa nghiệm thu runtime; không có ADMIN; SMTP | **MỘT PHẦN.** Theo chính sách mục 19 không còn vai trò ADMIN: vận hành tài khoản qua CLI / service token nội bộ. Auth/Follow có test E2E + browser trên stack cô lập; SMTP chưa cấu hình; chưa nghiệm thu trên server đang chạy. |
| A02 | MKT bị 403 khi review cluster | **ĐÃ SỬA** (Phase 1, test review bằng session MKT). |
| A03 | Pending cluster có tham chiếu chết / lệch tổng | **ĐÃ SỬA** (Phase 1). |
| A04 | Ownership lịch sử thiếu, thiếu FK / unique | **MỘT PHẦN.** Dữ liệu cá nhân mới đều có FK `users` và khóa duy nhất. `search_jobs` / `votes` cũ chưa đổi. |
| A05 | Canonical product / taxonomy chưa sạch | **CÒN MỞ.** Sản phẩm UNKNOWN bị loại khỏi đề xuất; nhiều sản phẩm thiếu category / subcategory. |
| A06 | Chưa có hợp đồng hành vi / kết quả | **ĐÃ SỬA** (Phase 2–3: `mkt_behavior_events`, `product_test_results`). |
| A07 | Global/team chưa tách khỏi cá nhân | **ĐÃ SỬA** (Phase 5: điểm cá nhân lưu riêng, không ghi lên product). |
| A08 | CLIP dependency chưa tái dựng đầy đủ | **CÒN MỞ** (ngoài phạm vi; các phase mới chỉ đọc vector đã lưu). |
| A09 | Test suite chạy chung không cô lập | **ĐÃ SỬA**: toàn bộ thư mục `tests` chạy chung 377 passed, 0 failed. |
| A10 | Chưa có outcome / assignment / evaluation | **MỘT PHẦN**: có assignment (allocation) và kết quả test; chưa có đánh giá offline. |

Nhật ký Phase 0: đọc working tree và config theo danh sách cho phép; kiểm tra PostgreSQL read-only; chạy các bộ test sẵn có trên DB tạm; tái hiện hai lỗi quyền/count; lập báo cáo. **Không sửa chức năng, không ghi PostgreSQL hiện tại, không triển khai phase khác.**

## 10. Phase 1 — Product Clustering (19/10/2026)

### Kết quả và bằng chứng

| Yêu cầu | Đánh giá | File / bằng chứng |
|---|---|---|
| Nhiều Ads → một Product Cluster | PASS | `apps/api/app/cluster_models.py`, `services/cluster_membership.py`: bảng membership riêng, cluster redirect và revision; giữ `ads.product_id` gốc. |
| Kết hợp tên, hash, CLIP, landing, SKU, metadata | PASS | `services/clustering.py`, `services/cluster_evidence.py`: đọc vector/hash sẵn có; GTIN có check digit, brand/model, SKU theo shop; bỏ tracking query nhưng giữ product/variant query. |
| Duplicate Ads / Products / Similar Products | PASS | Duplicate Ad dùng định danh nguồn/network + external ID, lưu `duplicate_ad_links`; nhiều creative giống nhau chưa đủ kết luận trùng Ad. Candidate có `relation_kind`, similar không auto-group. |
| Không gom chỉ vì cùng shop | PASS | Shop chỉ là ngữ cảnh của SKU/tín hiệu; test cùng advertiser khác sản phẩm, SKU khác shop, GTIN cùng sản phẩm khác shop. |
| PostgreSQL và migration | PASS | `cluster_migrate.py`: additive, gọi lại an toàn; chạy hai lần trên DB hiện tại, 16.131 memberships / 11.681 cluster ban đầu. |
| Confidence, review, undo | PASS | Điểm heuristic 0–1, auto từ 0,93 khi đủ bằng chứng và không có guard; review từ 0,60; metadata/variant xung đột chặn. Legacy confidence NULL, không mặc định chắc chắn. Undo kiểm tra revision; cannot-link được giữ qua group trung gian. |
| Bảo toàn dữ liệu gốc | PASS | Migration không đổi checksum của Ads, Products, creatives, advertisers, snapshots, lifecycle, ad_sources; xem `PHASE1_CLUSTERING_RESULT.json`. Test auto/manual grouping giữ nguyên rows gốc và lịch sử. |
| Worker/ingest/API | PASS | `cluster_jobs.py`, `ingest.py`, `routers/clusters.py`: worker dùng cached evidence; ingest đồng bộ membership; request review không chạy inference. MKT có quyền review hẹp, vẫn yêu cầu session/CSRF. |
| Giao diện `/clusters` | PARTIAL về nghiệm thu UI | Có danh sách cluster/Ads, duplicate Ads, similar/review/undo; TypeScript kiểm tra đạt. Chưa nghiệm thu bằng trình duyệt trên runtime hiện tại. |

Confidence là điểm tổng hợp bằng chứng, **không phải xác suất đã được hiệu chuẩn**. Cluster legacy được kế thừa từ `ads.product_id`, chưa chứng minh mọi Ads trong đó là cùng một sản phẩm; các cluster cũ lẫn sản phẩm ghi nhận ở Phase 0 không tự động bị tách/xóa. Candidate cũ cần worker đánh giá lại trước khi MKT accept. Mâu thuẫn GTIN/model/variant và cannot-link được kiểm tra trên mọi cặp giữa hai nhóm để tránh gộp bắc cầu sai.

### Phạm vi file Phase 1

Đường dẫn sau tương đối với `apps/api/`, trừ đường dẫn web/docs ghi rõ. Working tree có nhiều thay đổi có trước; danh sách này chỉ nêu file đã can thiệp cho Phase 1, không nhận toàn bộ diff repo là của phase này.

- Schema/migration mới: `app/cluster_models.py`, `app/cluster_migrate.py`; đăng ký model và classification trong `app/models.py`.
- Engine: `app/services/cluster_evidence.py`, `app/services/cluster_membership.py`, `app/services/clustering.py`, `app/cluster_jobs.py`.
- Tích hợp: `app/ingest.py`, `app/main.py`, `app/worker.py`, `app/routers/clusters.py`, `app/workspace/auth.py`; `app/services/entity_resolution.py` bảo vệ Product/history đã được overlay tham chiếu khỏi legacy deletion.
- UI: `apps/web/app/clusters/page.tsx`, `apps/web/components/cluster-dialog.tsx`.
- Test: `tests/test_cluster_phase1.py`, `tests/test_clustering.py`, `tests/conftest.py`, `tests/test_unified_scoring.py`, `scripts/verify_product_clustering_postgres.py`.
- Báo cáo: `docs/personalization_progress.md`, `docs/PRODUCT_CLUSTERING.md`, `docs/PHASE1_CLUSTERING_RESULT.json`.

### Kiểm thử

**Kết quả chạy PostgreSQL hiện tại:** migration hai lần, mô phỏng rollback, sau đó chạy `--apply`. Đánh giá 894 cặp: 1 auto, 89 pending, 768 similar, 36 blocked. Đã gom 1 cặp, còn **11.680 root clusters / 16.131 Ad memberships**, lưu **8 liên kết Duplicate Ad**. Số verdict là kết quả scan, không phải tổng toàn bộ candidate lịch sử. Sau apply, checksum của cả 7 bảng gốc trong `PHASE1_CLUSTERING_RESULT.json` vẫn bằng trước migration; không mất Ads/Product/snapshot/lifecycle. Chỉ cluster overlay, candidate và sự kiện grouping mới được ghi.

- PostgreSQL cô lập: `python scripts/verify_product_clustering_postgres.py` → **83 passed**, gồm pgvector/HNSW thật, migration lặp lại, concurrent accept, review bằng session MKT + CSRF, undo theo revision. Database test có tên UUID riêng và đã dọn sau chạy.
- SQLite regression: `python -m pytest tests/test_workspace.py tests/test_clustering.py tests/test_cluster_phase1.py tests/test_unified_scoring.py tests/test_query_intent.py tests/test_naming.py tests/test_price_evidence.py -q --disable-warnings -p no:cacheprovider --tb=short` → **115 passed, 1 skipped**. Skip là test vector chỉ dành PostgreSQL, đã chạy ở bộ trên.
- Frontend: `apps/web/node_modules/.bin/tsc.cmd --noEmit --incremental false` → exit 0.
- API smoke in-process (không chạy startup/lifespan): groups, group detail, duplicate-ads, pending candidates trên PostgreSQL hiện tại trả HTTP 200. Đây không phải nghiệm thu browser hoặc xác nhận server đang phục vụ bản code mới.
- Test bao gồm Ads trùng, creative giống nhưng Ad khác, sản phẩm gần giống/chỉ CLIP, khác sản phẩm cùng shop, cùng sản phẩm khác shop, SKU scope, model/variant xung đột, query URL, cannot-link bắc cầu, không gọi AI/inference, bảo toàn dữ liệu và idempotency.

### Hạn chế còn lại

Authentication/Follow vẫn **PARTIAL ở mức vận hành** theo Phase 0: chưa nghiệm thu ADMIN/recovery và follow thực tế; việc mở quyền review cluster không hoàn thành các hạng mục đó. Không sửa personal scoring, DNA, recommendation/feed hoặc triển khai Phase 2. API `/api/clusters/groups` cung cấp quan hệ mới; các màn hình/scoring cũ dùng Product gốc chưa được chuyển sang aggregate theo cluster.


## 11. Phase 2 — Exclusive Product Allocation (19/10/2026)

### Thiết kế

- **Khóa nguyên tử bằng primary key.** Bảng `product_allocation_locks(product_id PK, allocation_id)`: mỗi sản phẩm tối đa một dòng khóa, nên hai MKT không thể cùng sở hữu dù claim đồng thời ở hai transaction. Claim chèn khóa theo thứ tự product_id (tránh deadlock giữa hai claim chồng nhau); vi phạm PK → rollback toàn bộ transaction → HTTP 409. Bước kiểm tra trước (`owners`) chỉ để trả lỗi rõ ràng, không phải cơ chế bảo vệ.
- **Theo Product Cluster của Phase 1.** Claim một sản phẩm thuộc cụm **đủ tin cậy** sẽ khóa toàn bộ sản phẩm trong cụm trong cùng một transaction (all-or-nothing). Cụm đủ tin cậy khi mọi sản phẩm được gộp vào root có `method = manual` hoặc `confidence ≥ ALLOCATION_GROUP_MIN_CONFIDENCE` (mặc định = `CLUSTER_AUTO_AT` = 0,93). Cụm **chưa đủ tin cậy** (confidence thấp hoặc NULL legacy) chỉ khóa đúng sản phẩm được claim; MKT khác vẫn nhận được các sản phẩm còn lại.
- **Trạng thái.** `available` (không có allocation còn hiệu lực, suy ra), `reserved`, `claimed`, `testing`, `tested`, `released`. Chủ sở hữu chuyển: reserved→claimed/testing, claimed→testing/tested, testing→claimed/tested, tested→testing; release từ mọi trạng thái đang hoạt động. Kết quả test lưu riêng ở `product_test_results` (outcome winner/loser/inconclusive, market, spend, orders, revenue, note, người chạy test); ghi kết quả đưa allocation sang `tested`; kết quả vẫn giữ sau release/reassign.
- **TTL.** Claim tạo `reserved` với `reserved_until = now + ALLOCATION_RESERVE_HOURS` (mặc định 48 giờ, cấu hình qua env). Reservation hết hạn được giải phóng bằng một câu `UPDATE … WHERE status='reserved' AND reserved_until < now` ở đầu mỗi claim/reassign (an toàn khi nhiều request chạy đồng thời) và ghi audit `expire` với actor = hệ thống. API đọc trạng thái coi reservation quá hạn là `available` mà không ghi DB.
- **Saved/Favorite/Tested giữ quyền.** `PUT /api/workspace/ads/{aid}` với saved/favorite chuyển reserved → `claimed`; tested chuyển reserved/claimed → `testing`. Các trạng thái này không có TTL nên giữ quyền tới khi chủ release hoặc ADMIN điều chuyển. Tương tác **không bao giờ tự tạo** quyền sở hữu khi chưa claim.
- **Không khóa từ Search.** Search/listing không gọi code allocation; chỉ `POST /api/allocations/claim` (hoặc ADMIN reassign) tạo quyền. Có test xác nhận gọi `/api/ads`, `/api/products` và lưu/tested ad không tạo allocation.
- **Follow độc lập.** Không thay đổi Follow/competitor; test xác nhận MKT B vẫn follow và xem advertiser có sản phẩm đang thuộc MKT A.
- **Cụm thay đổi sau khi claim.** Nếu cụm của chủ sở hữu được gộp thêm sản phẩm, lần claim lặp lại của chính chủ sẽ khóa thêm phần còn thiếu (audit `extend`); claim của MKT khác vào sản phẩm mới bị 409 vì cụm đã có chủ. Nếu hai cụm có hai chủ khác nhau bị gộp, API trạng thái trả `conflict: true`; ADMIN xử lý bằng Reassign. Hệ thống không tự tước quyền của ai.

### API

| Method / path | Quyền | Chức năng |
|---|---|---|
| `POST /api/allocations/claim` `{product_id}` | MKT/ADMIN, session + CSRF | Giữ chỗ nguyên tử; idempotent với chủ sở hữu; 409 nếu đã thuộc người khác, 404 nếu không có sản phẩm. |
| `POST /api/allocations/{id}/release` `{reason?}` | Chủ sở hữu hoặc ADMIN | Xóa khóa, status `released`, lý do `released` / `admin_released`. |
| `PATCH /api/allocations/{id}/status` `{status}` | Chủ sở hữu | claimed / testing / tested theo bảng chuyển trạng thái; 409 nếu không hợp lệ hoặc giữ chỗ đã hết hạn. |
| `POST /api/allocations/{id}/results` | Chủ sở hữu | Ghi kết quả test, chuyển `tested`. |
| `GET /api/allocations/status/{product_id}` | Đã đăng nhập | Allocation Status: status, scope, cluster_id, group_confident, mine, reserved_until, conflict. MKT không thấy ID chủ/allocation của người khác; ADMIN thấy `owner_id`. |
| `GET /api/allocations`, `GET /api/allocations/{id}` | MKT chỉ của mình; ADMIN tất cả | Danh sách; chi tiết kèm khóa, kết quả test và audit log. |
| `POST /api/admin/allocations/reassign` `{product_id, user_id, reason}` | Chỉ ADMIN (middleware `/api/admin/` + `admin_user`) | Chuyển toàn bộ allocation sang MKT khác (reserved → claimed), hoặc cấp trực tiếp ở trạng thái claimed nếu sản phẩm đang trống. |

Audit log `product_allocation_audit`: mỗi claim / extend / status / result / release / expire / reassign một dòng với actor, from/to status, from/to user và dữ liệu kèm theo.

### File

- Mới: `apps/api/app/allocation.py` (models + service), `apps/api/app/routers/allocations.py`, `apps/api/tests/test_allocation.py`.
- Sửa: `app/models.py` (đăng ký bảng cho `auto_migrate`), `app/main.py` (router), `app/workspace/auth.py` (allowlist ghi cho MKT), `app/workspace/routes.py` (Saved/Favorite/Tested → giữ quyền), `scripts/verify_product_clustering_postgres.py` (nhận danh sách test qua argv, mặc định gồm test Phase 2).
- PostgreSQL hiện tại: đã tạo 4 bảng mới (`product_allocations`, `product_allocation_locks`, `product_test_results`, `product_allocation_audit`), đều rỗng; không đụng bảng cũ. Khi server khởi động lại, `auto_migrate` cũng tự tạo nếu thiếu.

### Kiểm thử

- PostgreSQL cô lập (`python scripts/verify_product_clustering_postgres.py`): **99 passed** (83 Phase 1 + 16 Phase 2). Bộ Phase 2 chạy lặp thêm 3 lần trên DB PG riêng: 16/16 mỗi lần.
- SQLite regression (bộ lệnh mục 10 + `tests/test_allocation.py`): **131 passed, 1 skipped** (skip là test vector chỉ chạy trên PG).
- Phạm vi: 6 MKT claim cùng một sản phẩm đồng thời (barrier) → đúng 1 thành công, 5 nhận 409, 1 khóa; 2 MKT claim hai sản phẩm khác nhau của cùng một cụm tin cậy đồng thời → 1 chủ; TTL mặc định 48h và cấu hình 1h; hết hạn → sản phẩm trống, MKT khác claim được, audit `expire`; Saved/Favorite/Tested giữ quyền sau 30 ngày; tương tác/search không tạo quyền; cụm confidence thấp/NULL chỉ khóa sản phẩm; merge manual coi là tin cậy; ngưỡng cấu hình được; luồng status/result/release và audit; anonymous 401, thiếu CSRF 403, MKT khác nhận 404 trên allocation không phải của mình, MKT gọi reassign 403; ADMIN reassign giữ người chạy test trong kết quả, chủ cũ mất quyền; Follow độc lập.

### Số liệu dữ liệu thật (chỉ đọc)

Với ngưỡng 0,93 trên PostgreSQL hiện tại: 11.679 cụm chỉ có một sản phẩm, 1 cụm nhiều sản phẩm đủ tin cậy (khóa cả cụm), 0 cụm nhiều sản phẩm chưa đủ tin cậy. Trên dữ liệu hiện nay, claim gần như luôn là claim một sản phẩm.

### Hạn chế còn lại

- **Chưa có UI** cho claim/release/status; mới có API. Chưa nghiệm thu bằng trình duyệt hay trên server đang chạy (API/worker chưa restart trong lượt này).
- Reservation hết hạn được dọn lười ở lần claim/reassign kế tiếp, không có job nền; trạng thái đọc vẫn đúng nhờ so `reserved_until`. Thêm sweep trong worker nếu dashboard cần số "reserved" chính xác.
- DB thật chưa có ADMIN active (Phase 0, A01) nên Reassign mới được kiểm thử trên DB test.
- Không triển khai Recommendation, DNA, routing hay personal scoring.


## 12. Phase 3 — MKT Behavior Tracking (19/10/2026)

### Thiết kế

- **Một bảng append-only `mkt_behavior_events`** (PostgreSQL): `user_id` (FK users, lấy từ session), `kind`, `ad_id`, `product_id`, `advertiser_id`, `allocation_id`, `data`, `dedupe_key` (UNIQUE), `created_at`; index `(user_id, created_at, id)` và `(user_id, kind)`. `kind` có CHECK constraint với 14 giá trị: `ad_view`, `ad_detail_view`, `ad_save`, `ad_unsave`, `ad_favorite`, `ad_unfavorite`, `test_start`, `test_success`, `test_fail`, `test_inconclusive`, `ad_skip`, `ad_not_interested`, `advertiser_follow`, `advertiser_unfollow`.
- **Liên kết user đã xác thực.** `user_id` luôn lấy từ session; request dùng service token (không có MKT) không bao giờ được ghi. Client không gửi được user_id.
- **Không ghi trùng.**
  - Hành vi dạng bật/tắt (Lưu, Yêu thích, Đã test, Follow) chỉ ghi khi trạng thái lưu trong DB **thực sự đổi**, trong cùng transaction của API gốc (API gốc đã giữ advisory lock `lock_scheduler` trên PostgreSQL nên các click đồng thời được tuần tự hóa). Click Lưu 3 lần → 1 sự kiện; đổi interval Follow không phải Follow mới.
  - Tín hiệu do client báo (xem, xem chi tiết, bỏ qua, không quan tâm) có `dedupe_key` xác định theo khung thời gian, chèn bằng `INSERT … ON CONFLICT DO NOTHING`: xem/chi tiết một lần mỗi 30 phút mỗi Ads (`BEHAVIOR_VIEW_WINDOW_MIN`), bỏ qua một lần mỗi ngày, không quan tâm một lần mỗi Ads.
  - Kết quả test có key theo `result_id`.
- **Tận dụng API hiện có.** `PUT /api/workspace/ads/{aid}` (save/favorite/tested), `POST|DELETE /api/competitors/{aid}/follow`, `PATCH /api/allocations/{id}/status` (chuyển `testing` → `test_start`) và `POST /api/allocations/{id}/results` (winner → `test_success`, loser → `test_fail`, inconclusive → `test_inconclusive`) tự ghi sự kiện. Nhấn "Đã test" trên Ads của sản phẩm đang giữ chỗ ghi `test_start` kèm `allocation_id`.
- **Xem Ads không phải quyền sở hữu.** Tín hiệu xem/chi tiết/bỏ qua chỉ ghi vào bảng hành vi, không gọi code allocation; có test xác nhận không tạo allocation và trạng thái sản phẩm vẫn `available`.
- **Không ảnh hưởng tốc độ tải Ads Card.** API danh sách Ads không bị sửa và không ghi gì (có test). Phía web (`apps/web/lib/track.ts`): một IntersectionObserver dùng chung, Ads tính là đã xem khi ≥ 50% card hiện trên màn hình, gom tối đa 100 ID mỗi POST sau 3 giây hoặc khi rời trang (`keepalive`), không await, lỗi bị bỏ qua. Mỗi lô ở server là 1 SELECT + 1 INSERT.

### API

| Method / path | Quyền | Chức năng |
|---|---|---|
| `POST /api/behavior/events` `{kind, ad_ids[1..100]}` | Đã đăng nhập, CSRF | Chỉ nhận `ad_view`, `ad_detail_view`, `ad_skip`, `ad_not_interested`; trả số sự kiện mới. Client không giả được save/test/follow (422). ID Ads không tồn tại bị bỏ qua. |
| `GET /api/behavior/events` | MKT chỉ của mình; ADMIN tất cả hoặc `user_id` | Lịch sử, lọc theo `kind`, `ad_id`, `product_id`, `advertiser_id`, `since`; phân trang, mới nhất trước. MKT truyền `user_id` của người khác vẫn chỉ nhận dữ liệu của mình. |
| `GET /api/behavior/summary` | Như trên | Đếm theo từng `kind`. |

UI: card Ads có thêm nút "Bỏ qua" và "Không quan tâm"; nút "Chi tiết" ghi `ad_detail_view`.

### File

- Mới: `apps/api/app/behavior.py`, `apps/api/app/routers/behavior.py`, `apps/api/tests/test_behavior.py`, `apps/web/lib/track.ts`.
- Sửa: `app/models.py`, `app/main.py`, `app/workspace/auth.py` (allowlist `/api/behavior/events`), `app/workspace/routes.py` (interact, follow, unfollow), `app/routers/allocations.py` (status testing, results), `apps/web/components/ads/ad-card.tsx`, `apps/web/components/workspace/actions.tsx`, `scripts/verify_product_clustering_postgres.py` (thêm test Phase 3).
- PostgreSQL hiện tại: đã tạo bảng `mkt_behavior_events` (rỗng) cùng các index; không đụng bảng cũ.

### Kiểm thử

- PostgreSQL cô lập (`python scripts/verify_product_clustering_postgres.py`): **110 passed** (83 Phase 1 + 16 Phase 2 + 11 Phase 3). Bộ Phase 3 chạy lặp thêm 2 lần trên PG: 11/11. Lần đầu lộ lỗi chỉ có trên PG (psycopg trả `rowcount = -1` với INSERT nhiều dòng), đã sửa bằng `RETURNING`.
- SQLite regression: **142 passed, 1 skipped**.
- Frontend: `tsc --noEmit` exit 0.
- Phạm vi: xem/chi tiết dedupe theo khung và khung cấu hình được; Ads không tồn tại bị bỏ qua; xem không tạo quyền sở hữu; Lưu/Yêu thích/Bỏ yêu thích chỉ ghi khi đổi trạng thái; 4 click đồng thời giống nhau chỉ ghi 1; bỏ qua/không quan tâm dedupe; client không giả được kind server; test_start từ Ads và từ allocation, ba kết quả test tách biệt kèm market; Follow/Unfollow lặp không trùng; lịch sử tách biệt giữa MKT, ADMIN đọc được tất cả/từng MKT; anonymous 401, thiếu CSRF 403, lô rỗng hoặc > 100 bị 422; GET danh sách Ads không ghi sự kiện.

### Hạn chế còn lại

- Chưa nghiệm thu trên trình duyệt/server đang chạy (API, worker và web chưa restart trong lượt này); IntersectionObserver mới được kiểm tra bằng typecheck, chưa có test trình duyệt.
- "Xem" là tín hiệu do client báo: đủ cho gợi ý, không phải bằng chứng chống gian lận.
- "Không quan tâm" chỉ được ghi lại, chưa ẩn Ads khỏi feed (thuộc phần Recommendation).
- Dữ liệu cũ (`mkt_ad_interactions`, `mkt_followed_advertisers`, `mkt_activity`) không được backfill thành sự kiện vì không có thời điểm chuyển trạng thái đáng tin; lịch sử bắt đầu từ khi triển khai.
- Chưa xây thuật toán gợi ý.


## 13. Phase 4 — MKT Preference Profile (19/10/2026)

### Thiết kế

- **Bảng `mkt_preference_profiles`** (PK `user_id`): `learned` (hồ sơ tự học), `manual` (MKT tự chỉnh), `learned_reset_at`, `last_event_id`, `computed_at`. Hai phần lưu riêng: chỉnh tay luôn được ưu tiên; đặt lại phần này không đụng phần kia.
- **Nguồn dữ liệu.** Sự kiện Phase 3 (`mkt_behavior_events`) nối với Ads/Product để lấy danh mục, quốc gia, ngôn ngữ, giá + tiền tệ; cộng với danh sách Follow **hiện tại** (trạng thái, không phải lịch sử).
- **Trọng số hành vi:** xem 0,2; xem chi tiết 0,5; lưu 2 (bỏ lưu −2); yêu thích 3 (bỏ −3); bắt đầu test 2; test thành công 6; thất bại −1; chưa xác định 0,5; bỏ qua −0,5; không quan tâm −3. Mỗi advertiser đang Follow cộng 2, chia theo tỷ lệ danh mục / quốc gia / ngôn ngữ của các Ads của advertiser đó.
- **Giảm ảnh hưởng tương tác cũ:** trọng số × 0,5^(tuổi / half-life), half-life mặc định 30 ngày (`PREFERENCE_HALF_LIFE_DAYS`); bỏ qua sự kiện cũ hơn 365 ngày (`PREFERENCE_MAX_AGE_DAYS`).
- **Kết quả học:** top 10 mỗi chiều kèm score 0–1 (so với mục mạnh nhất) và trọng số thô: `categories`, `countries`, `markets`, `languages`, `advertisers` (có tên); `price_range` (phân vị có trọng số 25/50/75 trong tiền tệ chiếm trọng số lớn nhất, cần ≥ 3 mẫu); `tests` (đếm thành công / thất bại / chưa xác định, tổng và theo danh mục). Giá trị âm ròng (ví dụ "không quan tâm") bị loại khỏi top.
- **Thị trường:** `OFW_PH`, `PH`, `SA`, `ME`, `US`, `EU`, `AU`, `VN`. Quốc gia → region theo `markets.REGIONS`; SA tính thêm `SA`; Ads tiếng Tagalog/Filipino chạy ở Trung Đông tính là `OFW_PH`.
- **Hồ sơ mặc định:** tài khoản chưa có sự kiện và chưa Follow → `is_default: true`, thị trường ưu tiên `OFW_PH, PH, SA, US, EU` (cấu hình bằng `PREFERENCE_DEFAULT_MARKETS`).
- **Hồ sơ hiệu lực (`effective`):** thị trường = chỉnh tay → học được → mặc định; danh mục = chỉnh tay + học được, trừ danh mục bị loại; quốc gia, ngôn ngữ, khoảng giá = chỉnh tay nếu có, không thì học được; advertiser = danh sách đang Follow.
- **Xử lý nhẹ, không AI.** Chỉ SQL + số học (đọc tối đa 20.000 sự kiện mới nhất mỗi MKT). Worker (`python -m app.worker`) chạy `refresh_stale` mỗi 15 phút (`PREFERENCE_REFRESH_MIN`, 0 = tắt) và chỉ lưu lại hồ sơ cũ: có sự kiện mới, Follow đổi, hoặc quá 24 giờ (để cập nhật decay). `GET` không ghi DB: nếu bản lưu đã cũ thì tính lại trong bộ nhớ. Trên DB thật, tính cho 4 MKT hiện có mất 2–42 ms mỗi người.

### API

| Method / path | Quyền | Chức năng |
|---|---|---|
| `GET /api/preferences` | Đã đăng nhập; `?user_id=` chỉ ADMIN | learned, manual, effective, is_default, computed_at, danh sách thị trường hỗ trợ. MKT hỏi hồ sơ người khác → 404. |
| `PUT /api/preferences` | Chính MKT, CSRF | Sửa từng phần: `priority_markets`, `categories`, `excluded_categories`, `countries` (ISO-2), `languages`, `price_min`, `price_max`, `currency`. Gửi `null` để xóa một trường. Thị trường lạ, mã quốc gia sai, giá min > max → 422. |
| `POST /api/preferences/reset` `{scope: manual\|learned\|all}` | Chính MKT | manual: xóa phần chỉnh tay; learned: bỏ qua mọi sự kiện trước thời điểm reset (lịch sử Phase 3 vẫn giữ nguyên, Follow hiện tại vẫn tính); all: cả hai. |
| `POST /api/preferences/refresh` | Chính MKT | Tính lại và lưu ngay. |

### File

- Mới: `apps/api/app/preferences.py`, `apps/api/app/routers/preferences.py`, `apps/api/tests/test_preferences.py`.
- Sửa: `app/models.py`, `app/main.py`, `app/worker.py` (vòng `preference_loop`), `app/workspace/auth.py` (allowlist), `scripts/verify_product_clustering_postgres.py` (thêm test Phase 4).
- **Sửa lỗi Phase 2 phát hiện khi test:** claim một sản phẩm chưa được cluster sync (chưa có dòng `product_clusters`) trả 500 do FK. Giờ claim tự tạo node bằng `INSERT … ON CONFLICT DO NOTHING` (an toàn khi claim đồng thời); thêm test `test_claim_works_before_cluster_sync`.
- PostgreSQL hiện tại: đã tạo bảng `mkt_preference_profiles` (rỗng). Cả 4 MKT active hiện có hồ sơ mặc định vì chưa có sự kiện hành vi nào.

### Kiểm thử

- PostgreSQL cô lập: **122 passed** (83 Phase 1 + 17 Phase 2 + 11 Phase 3 + 11 Phase 4); chạy lặp Phase 2 + 4 thêm một lần: 28/28.
- SQLite regression: **154 passed, 1 skipped**.
- Phạm vi: hồ sơ mặc định và GET không ghi; học danh mục từ lưu/yêu thích, "không quan tâm" đẩy danh mục ra khỏi top; phân tích thành công / thất bại theo danh mục; Follow cộng advertiser, danh mục, thị trường (gồm OFW_PH), ngôn ngữ, và mất khi Unfollow; quốc gia / thị trường / ngôn ngữ / khoảng giá PHP; decay (lưu 90 ngày trước = 0,25 thua xem chi tiết hôm nay 0,5), bỏ sự kiện > 365 ngày, half-life cấu hình được; chỉnh tay thắng, validate, xóa từng trường; reset manual / learned / all và học lại sau reset; hồ sơ tách biệt giữa MKT, MKT không đọc được hồ sơ người khác, ADMIN đọc được; worker chỉ lưu hồ sơ cũ (sự kiện mới, quá 24 giờ); anonymous 401, thiếu CSRF 403; mọi test chặn gọi AI.

### Hạn chế còn lại

- Chưa có UI chỉnh sở thích; mới có API. Chưa restart API/worker nên vòng refresh trong worker chưa chạy trên runtime thật.
- Danh mục lấy từ `products.category`; Phase 0 ghi nhận 6.693 sản phẩm thiếu category nên tín hiệu danh mục sẽ thưa cho tới khi taxonomy được bổ sung.
- Ngôn ngữ và tiền tệ phụ thuộc dữ liệu nguồn; nhận diện `OFW_PH` là heuristic (Tagalog/Filipino ở Trung Đông).
- Trọng số và half-life là giá trị khởi đầu hợp lý, chưa được hiệu chỉnh bằng kết quả kinh doanh.
- Chưa triển khai Recommendation Feed.


## 14. Phase 5 — Personalized Recommendation Engine (19/10/2026)

### Thiết kế

- **Điểm 0–100 riêng từng `user_id`** giữa MKT và Product Cluster root (sản phẩm đã gộp vào cụm khác không được đề xuất riêng). Sáu thành phần với trọng số khởi tạo:

| Thành phần | Trọng số | Cách tính (chỉ dữ liệu đã lưu) |
|---|---|---|
| Category Preference | 25% | Điểm danh mục từ hồ sơ Phase 4 (danh mục MKT tự chọn = 1,0; danh mục bị loại trừ thì bỏ). |
| Saved/Favorite/Test Similarity | 25% | Giống "seed" theo danh mục 0,5 + subcategory 0,2 + giá cùng tiền tệ trong ±30% 0,3. Seed = Ads đã yêu thích (1,0), lưu (0,8), đánh dấu test (0,5); kết quả test winner 1,2, **inconclusive 0,5 (chỉ là quan tâm, không phải thành công)**, **loser −1 (seed âm: trừ điểm sản phẩm tương tự)**. Advertiser đang Follow chạy sản phẩm → tối thiểu 0,8. |
| Semantic Similarity | 15% | Cosine CLIP đã lưu (ảnh cover hoặc ảnh đầu tiên có embedding) với seed tích cực, 0,70 → 0 … 0,95 → 1; không có vector thì dùng token tên sản phẩm. Không chạy inference. |
| Market/Language Matching | 15% | Quốc gia chính của sản phẩm (×1,0) và các thị trường khác (×0,6) so với thị trường hồ sơ (gồm OFW_PH, SA, US, EU…); 25% theo ngôn ngữ nếu cả hai phía có dữ liệu. |
| Verified Ads Signals | 10% | Chỉ từ Ads đã lưu: `unique_ads` (log), `advertiser_count`, `longevity_days`. Không dùng engagement ước tính. |
| Product Freshness | 10% | 0,5^(tuổi/30 ngày) × 0,5^(số ngày không thấy Ads mới quá 7 ngày / 14). |

- **Thiếu dữ liệu (yêu cầu 7)** được xử lý theo phía:
  - MKT chưa có dữ liệu cho một thành phần (chưa có danh mục, seed, Follow — cold start): bỏ thành phần đó và **chuẩn hóa lại trọng số**, như nhau cho mọi sản phẩm.
  - Sản phẩm thiếu dữ liệu cho một thành phần: thành phần đó **không được điểm** (không bịa giá trị) và `coverage` giảm.
  - Mỗi thành phần trả `score`, `weight`, `effective_weight`, `missing: mkt|product`. Phiên bản đầu từng chuẩn hóa lại cả khi sản phẩm thiếu dữ liệu; test cho thấy sản phẩm không có dữ liệu Ads lại được điểm cao hơn sản phẩm có tín hiệu thật, nên đã sửa như trên.
- **Loại khỏi nguồn cơ hội:** sản phẩm đang thuộc MKT khác (lọc lại **tại thời điểm đọc**, nên claim xảy ra sau lần batch vẫn bị ẩn ngay), sản phẩm của chính mình, sản phẩm đã có trong lịch sử (lưu / yêu thích / test), đã đánh dấu "không quan tâm", danh mục bị loại trừ. `explain` trả lý do loại cụ thể.
- **Giải thích:** mỗi đề xuất có danh sách lý do tiếng Việt, ví dụ: danh mục quan tâm, giống sản phẩm đã lưu/test, advertiser đang Follow, chạy ở thị trường ưu tiên, số Ads thật / advertiser / ngày chạy, mới xuất hiện, cảnh báo giống sản phẩm test thất bại, hồ sơ mới, khám phá.
- **Cold start:** hồ sơ mặc định chỉ có thị trường ưu tiên (OFW_PH, PH, SA, US, EU) → điểm dựa trên thị trường, tín hiệu Ads thật và độ mới; có lý do "Hồ sơ mới".
- **Khám phá:** 20% vị trí (`RECO_EXPLORE_SHARE`; cứ 5 vị trí có một) dành cho sản phẩm ngoài danh mục quen thuộc nhưng có tín hiệu Ads thật ≥ 0,4 và độ mới ≥ 0,2. Thứ tự chọn ổn định theo user và ngày (hash), điểm giữ nguyên là điểm thật, được gắn cờ `explore` và lý do "Khám phá". Có thể tắt bằng `?explore=false`.
- **Cache và batch:**
  - Kho ứng viên có giới hạn: top 1.500 theo opportunity_score (in_target, đã có tên, có Ads trong 120 ngày), cộng 500 sản phẩm thuộc 5 danh mục mạnh nhất, cộng sản phẩm của advertiser đang Follow.
  - Worker (`recommendation_loop`, mỗi 20 phút, `RECO_REFRESH_MIN`) lưu top 200 vào `mkt_recommendations` cùng trạng thái `mkt_recommendation_state` (signature hồ sơ/seed/follow, số ứng viên, cold_start, version).
  - Cache cũ khi signature đổi, quá 6 giờ (`RECO_TTL_HOURS`) hoặc đổi version.
  - GET dùng cache khi còn mới; khi chưa có cache thì tính trong bộ nhớ, ghi nhớ 10 phút theo signature, không ghi DB.
  - Vector CLIP cache trong process 1 giờ.
  - Trên DB thật: chấm 1.500 ứng viên mất 0,51 s, refresh 0,45 s, đọc feed từ cache 35 ms.

### API

| Method / path | Quyền | Chức năng |
|---|---|---|
| `GET /api/recommendations?limit&offset&explore` | Đã đăng nhập; `?user_id=` chỉ ADMIN | Feed cá nhân: score, coverage, components, reasons, explore, thông tin sản phẩm; `cached`, `cold_start`, `computed_at`, `weights`, `version`. MKT hỏi feed người khác → 404. |
| `GET /api/recommendations/explain/{product_id}` | Như trên | Chấm trực tiếp một sản phẩm (theo cluster root) kèm thành phần, lý do và lý do bị loại nếu có. |
| `POST /api/recommendations/refresh` | Chính MKT, CSRF, tối đa 12 lần/giờ | Batch-score và lưu ngay. |

Không sửa giao diện Search/Library.

### File

- Mới: `apps/api/app/recommend.py`, `apps/api/app/routers/recommendations.py`, `apps/api/tests/test_recommend.py`.
- Sửa: `app/models.py`, `app/main.py`, `app/worker.py` (`recommendation_loop`), `app/workspace/auth.py` (allowlist refresh), `scripts/verify_product_clustering_postgres.py` (thêm test Phase 5).
- PostgreSQL hiện tại: đã tạo `mkt_recommendations` và `mkt_recommendation_state` (rỗng; lần đo hiệu năng chạy trong transaction đã rollback).

### Kiểm thử

- PostgreSQL cô lập: **132 passed** (83 + 17 + 11 + 11 + 10); chạy lặp Phase 5 thêm một lần: 10/10.
- SQLite regression: **164 passed, 1 skipped**.
- Phạm vi: điểm khác nhau theo MKT và nằm trong 0–100; ưu tiên sản phẩm của advertiser đang Follow; sản phẩm MKT khác claim sau batch vẫn bị lọc khi đọc, sản phẩm của mình không còn trong feed; test thất bại → similarity 0 và cảnh báo, điểm thua winner; inconclusive nằm giữa thất bại và thành công; semantic dùng vector CLIP đã lưu; cold start (thành phần phía MKT có effective_weight 0, thị trường ưu tiên xếp trên); khám phá gắn cờ, ngoài danh mục quen, tắt được; thiếu dữ liệu sản phẩm không được thưởng, tổng effective_weight = 1, sản phẩm không có dữ liệu = 0 điểm; loại danh mục bị loại trừ / không quan tâm / đã lưu; cache miss chỉ tính một lần, cache mới không tính lại, cache cũ khi hành vi đổi hoặc quá TTL; phân quyền và CSRF; mọi test chặn gọi AI.
- Hai lỗi phát hiện và đã sửa trong lúc test: (1) chuẩn hóa lại khi sản phẩm thiếu dữ liệu làm sản phẩm thiếu dữ liệu được điểm cao hơn; (2) bộ nhớ đệm 10 phút không kiểm tra signature nên vừa lưu Ads xong feed vẫn ở trạng thái cold start.

### Hạn chế còn lại

- Chưa có UI feed; mới có API. Chưa restart API/worker nên batch worker chưa chạy trên runtime thật.
- Trọng số, ngưỡng CLIP và công thức độ mới là giá trị khởi đầu, chưa được hiệu chỉnh bằng kết quả kinh doanh hoặc đánh giá offline (Phase 7).
- Sản phẩm `UNKNOWN` (chưa có tên) không được đề xuất vì MKT không hành động được; trên DB thật chỉ 3.750/8.219 sản phẩm in_target có tên.
- 4 MKT hiện đều cold start vì chưa có dữ liệu hành vi; chất lượng cá nhân hóa chỉ đo được khi có dữ liệu thật.
- "Verified" nghĩa là đếm từ Ads đã lưu trong hệ thống, không phân biệt nguồn Ad Library chính thức với nguồn thu thập khác.


## 15. Phase 6 — Similar Ads & Products (19/10/2026)

### Thiết kế

- **Hai bước.**
  1. **Prefilter bằng SQL/PostgreSQL** cho ra tập ứng viên giới hạn (thực tế p50 ≈ 556, tối đa ≈ 600), lấy từ:
     - cùng danh mục (top 300 theo opportunity);
     - Ads có `search_text` chứa **mọi biến thể đa ngôn ngữ** của khái niệm lexicon (Anh / Việt / Tagalog / Ả Rập / Trung…) hoặc token đặc trưng của tên;
     - cùng Product Cluster / cùng advertiser (với Ads);
     - láng giềng **pgvector HNSW** của ảnh nguồn (chỉ trên PostgreSQL).
  2. **Tính similarity chỉ trên tập đó**, từ dữ liệu đã lưu: vector CLIP (ảnh có id nhỏ nhất mỗi sản phẩm/Ads), khái niệm lexicon chuẩn hóa, token tên/nội dung, pHash. Không chạy inference, không gọi AI.
- **Similar Products:** ảnh 35%, khái niệm/công dụng 30% (cùng "danh từ sản phẩm" trong lexicon ≥ 0,8), danh mục 20% (+ subcategory), token 15%. Tín hiệu mà sản phẩm nguồn không có thì bị bỏ và chuẩn hóa lại trọng số; ứng viên thiếu tín hiệu thì được 0 cho tín hiệu đó. Ngưỡng 0,35 (`SIMILAR_MIN`). Mỗi kết quả có điểm 0–100, từng tín hiệu và lý do tiếng Việt.
- **Similar Ads:** ảnh/creative 45%, nội dung 40% (khái niệm + token), pHash 15%. Bộ lọc advertiser của MKT (whitelist / blacklist / chỉ Follow) áp dụng cho ứng viên.
- **Similar ≠ Duplicate (yêu cầu 5):** ứng viên có bằng chứng *cùng một sản phẩm* được trả riêng trong `duplicates` kèm `duplicate_evidence`, không bao giờ nằm trong `similar`:
  - Sản phẩm: link `duplicate_product` của Phase 1 (pending / accepted / auto_merged), cùng URL trang sản phẩm (bỏ tham số tracking), hoặc pHash ≤ 6 bit kèm tên giống ≥ 0,5.
  - Ads: `duplicate_ad_links` của Phase 1, cùng source + external id, hoặc pHash ≤ 4 + nội dung ≥ 0,9 + cùng page/advertiser.
  - Sản phẩm đã cùng cluster là cùng một sản phẩm nên không xuất hiện; hỏi từ sản phẩm thành viên thì trả lời cho cluster root.
- **Based on Saved/Favorite/Tested:** seed lấy từ Phase 5 (lưu, yêu thích, đánh dấu test, test winner; inconclusive chỉ là quan tâm), tối đa 10 seed × 15 sản phẩm tương tự mỗi seed, gộp theo điểm cao nhất, kèm `because_of` (sản phẩm gốc). Sản phẩm giống ≥ 60 điểm với sản phẩm **test thất bại** bị ẩn.
- **More From Followed Advertisers:** sản phẩm khác của advertiser đang Follow, mới nhất trước, tối đa 10 sản phẩm mỗi advertiser, có lý do "Sản phẩm khác của … bạn đang Follow".
- **Quyền sở hữu và bộ lọc MKT (yêu cầu 9):**
  - "For me" và "Followed" là nguồn cơ hội độc quyền: loại sản phẩm đang thuộc MKT khác, sản phẩm của mình, sản phẩm đã có trong lịch sử, đã "không quan tâm", danh mục bị loại trừ (dùng chung quy tắc `exclusion` của Phase 5) và áp bộ lọc advertiser.
  - Similar Products là chế độ nghiên cứu: mỗi dòng ghi `allocation: available|mine|taken`; `?exclusive=true` chỉ giữ sản phẩm còn trống.
- **Hiệu năng:**
  - Cache đặc trưng văn bản theo sản phẩm trong process (1 giờ); chỉ select các cột cần; chỉ lấy landing URL cùng domain với nguồn.
  - Migration idempotent `similar.migrate()` (gọi khi API khởi động) bật `pg_trgm` và tạo index GIN trigram `ix_ads_search_text_trgm`, để prefilter LIKE đa ngôn ngữ dùng được index. Nếu không đủ quyền thì bỏ qua: API vẫn chạy, chỉ chậm hơn.
  - Bản đầu đo được p50 926 ms; sau tối ưu còn khoảng 250 ms.

### API (chỉ đọc, cần đăng nhập)

| Method / path | Chức năng |
|---|---|
| `GET /api/similar/products/{id}?limit&exclusive` | Sản phẩm tương tự + danh sách duplicate riêng, tín hiệu dùng, số ứng viên, `took_ms`. |
| `GET /api/similar/ads/{id}?limit` | Ads tương tự (nội dung / hình ảnh / creative) + duplicate Ads riêng; áp bộ lọc advertiser của MKT. |
| `GET /api/similar/for-me?limit` | Tương tự theo lịch sử Saved/Favorite/Tested của chính MKT; chỉ cơ hội còn trống. |
| `GET /api/similar/followed?limit` | Sản phẩm khác của advertiser đang Follow; chỉ cơ hội còn trống. |

Không sửa Search UI.

### Kết quả đo trên PostgreSQL thật (chỉ đọc, `scripts/eval_similar.py`, chi tiết ở `docs/PHASE6_SIMILAR_EVAL.json`)

Chưa có bộ nhãn similar chuẩn, nên dùng các chỉ số đại diện:

| Chỉ số | Kết quả |
|---|---|
| Similar Products — thời gian phản hồi (60 sản phẩm ngẫu nhiên) | p50 251 ms, p95 483 ms, max 603 ms |
| Similar Ads — thời gian phản hồi (60 Ads) | p50 186 ms, p95 410 ms, max 459 ms |
| Top-10 cùng danh mục với nguồn | 96,1% (50/60 sản phẩm có kết quả) |
| Top-10 có chung khái niệm lexicon (khi nguồn có khái niệm) | 100% |
| Sản phẩm cùng cluster bị trả là "similar" | 0 |
| Cặp duplicate_product pending của Phase 1 được gắn nhãn duplicate | 42/50; **0/50 bị đưa vào similar** |
| Cặp duplicate_ad_links được gắn nhãn duplicate | 8/8; 0 bị đưa vào similar |

8/50 cặp duplicate pending không được gắn nhãn vì không có bằng chứng nhận dạng mạnh (không cùng URL, pHash không đủ gần); chúng cũng không lọt vào danh sách similar.

### File

- Mới: `apps/api/app/similar.py`, `apps/api/app/routers/similar.py`, `apps/api/tests/test_similar.py`, `apps/api/scripts/eval_similar.py`, `docs/PHASE6_SIMILAR_EVAL.json`.
- Sửa: `app/main.py` (router + `similar.migrate()` khi khởi động), `scripts/verify_product_clustering_postgres.py` (thêm test Phase 6).
- PostgreSQL hiện tại: đã bật extension `pg_trgm` và tạo index `ix_ads_search_text_trgm` (mất 1,9 s). Không tạo bảng mới, không sửa dữ liệu.

### Kiểm thử

- PostgreSQL cô lập: **141 passed**, gồm test pgvector HNSW thật: tìm được sản phẩm/Ads giống ảnh dù không chung từ hay danh mục.
- SQLite regression: **172 passed, 2 skipped** (2 test chỉ chạy trên PG).
- Phạm vi: tìm theo công dụng xuyên ngôn ngữ (nguồn tiếng Anh tìm ra tiếng Việt và tiếng Ả Rập; vòi tưới cùng danh mục bị loại; kính cận xếp sau kính mát); tách duplicate (cùng URL, link Phase 1) khỏi similar và ẩn thành viên cùng cluster; CLIP + pHash (ảnh y hệt + tên giống → duplicate, ảnh khác → loại); Similar Ads có creative/copy duplicate, link Phase 1, Ads tiếng Việt tương tự, blacklist của MKT có hiệu lực; "for me" dùng lịch sử, loại sản phẩm đã có chủ, ẩn sản phẩm giống sản phẩm test thất bại; research hiển thị `taken`, exclusive loại bỏ; Followed loại sản phẩm đã có chủ / đã lưu; thời gian phản hồi với 600 ứng viên; phân quyền; mọi test chặn AI và inference.
- Lỗi phát hiện và đã sửa trong lúc làm: prefilter chỉ lấy 6 biến thể đầu mỗi khái niệm nên mất kết quả tiếng Ả Rập; sản phẩm không có danh mục hay khái niệm không vào được tập ứng viên (đã thêm prefilter theo token tên); độ trễ ban đầu 926 ms.

### Hạn chế còn lại

- Chưa có bộ nhãn similar/không similar do người duyệt, nên độ chính xác mới đo bằng chỉ số đại diện (danh mục, khái niệm, duplicate đã biết).
- Mỗi sản phẩm/Ads chỉ dùng một ảnh (id nhỏ nhất); creative khác của cùng sản phẩm không được so.
- Đa ngôn ngữ dựa vào lexicon hiện có: sản phẩm ngoài lexicon chỉ khớp theo token cùng ngôn ngữ hoặc theo ảnh.
- Trên SQLite (môi trường test) không có láng giềng ảnh trong bước prefilter; ảnh chỉ được dùng để chấm điểm.
- Chưa sửa Search UI; API chưa được gọi từ giao diện. Chưa restart API.


## 16. Phase 7 — Personalized Search (19/10/2026)

### Thiết kế

Nâng cấp **trang Search hiện có** và **`GET /api/search` hiện có**, không tạo trang hay API search mới. Các thành phần lấy từ các phase trước: điểm phù hợp (Phase 5), tương tự (Phase 6), sở hữu (Phase 2), hồ sơ sở thích (Phase 4). Code ghép nằm ở `apps/api/app/personal_search.py`.

- **Chế độ (`mode`):**
  - *Tất cả*: như trước, sản phẩm đã có chủ vẫn hiện nhưng có nhãn.
  - *Dành cho tôi*: feed Phase 5 (đã cache) giao với từ khóa/bộ lọc.
  - *Có thể nhận*: chỉ sản phẩm chưa ai nhận.
  - *Đối thủ đang Follow*: chỉ Ads của advertiser đang Follow.
  - *Sản phẩm tương tự*: theo `similar_to` (Phase 6, chế độ độc quyền), hoặc theo lịch sử lưu/yêu thích/test khi chưa chọn gốc.
  - Mọi chế độ là nguồn cơ hội độc quyền: **không hiện sản phẩm MKT khác đã nhận**.
- **Nhiều từ khóa:** giữ cơ chế tag sẵn có (OR giữa từ khóa, AND trong một từ khóa, đa ngôn ngữ/đồng nghĩa), dùng được với mọi chế độ.
- **Bộ lọc:**
  - Có sẵn: thị trường, funnel, nguồn Ads (chip nguồn), số ngày chạy, số advertiser, video, ≥ 2 nền tảng, trạng thái tên.
  - Mới: **danh mục** (từ `/api/meta`), **ngôn ngữ quảng cáo** (`language`), **nhu cầu thị trường** (`min_demand`, từ `market_demand` tính trên dữ liệu Ads thật), **độ phù hợp** (`min_fit` 40/60/80, kèm sắp xếp "Độ phù hợp với tôi"), **tương tác thực** (`min_engagement`: chỉ giữ sản phẩm có Ads đang chạy với like/comment/share thật từ nguồn; nhập 0 = chỉ cần có dữ liệu).
  - "Nhu cầu" được hiểu là *nhu cầu thị trường* vì kho chưa có trường công dụng riêng; công dụng sản phẩm đã được tìm qua từ khóa đa ngôn ngữ và chế độ tương tự.
  - Bộ lọc mức Ads (nguồn, ngôn ngữ, advertiser của chế độ Follow) áp dụng cả cho sản phẩm khớp theo tên. Trước đây đường khớp tên bỏ qua bộ lọc ngôn ngữ; test đã phát hiện và sửa.
- **Whitelist/Blacklist riêng từng MKT:** dùng bộ lọc advertiser sẵn có (`ad_filter`, nút "Advertiser … / Quản lý Whitelist / Blacklist" vốn có trên trang Search), áp dụng trong mọi chế độ; blacklist của A không ảnh hưởng B.
- **Không tự claim:** search chỉ đọc. Mỗi sản phẩm có nút **"Nhận"** riêng; một lần bấm nhận đúng một sản phẩm qua `POST /api/allocations/claim` (giữ chỗ 48h theo Phase 2). Không có nhận hàng loạt.
- **Điểm phù hợp và lý do:** dải nhỏ dưới mỗi card gồm:
  - badge "Phù hợp NN" (tooltip: lý do, độ tương tự, mức đủ dữ liệu);
  - nhãn "Khám phá" khi có;
  - trạng thái **Còn trống / Của bạn / MKT khác đã nhận**;
  - lý do đầu tiên;
  - nút "SP tương tự" (chuyển sang chế độ tương tự với sản phẩm làm gốc) và "Nhận".
- **Giữ UI/theme:** dùng lại ToggleGroup, Select, Badge, Button, Tooltip của theme hiện tại. `ProductMediaCard` (dùng chung với các trang khác) **không bị sửa**: dải cá nhân hóa nằm bên ngoài card và chỉ có trên trang Search. Library/Dashboard không đổi; phía web chỉ sửa `apps/web/app/search/page.tsx`.
- **Phân trang, debounce, cache:**
  - Giữ phân trang 36/trang ("Tải thêm").
  - Mọi thay đổi bộ lọc qua debounce 350 ms (gõ 3 ký tự → 1 request, đo trên trình duyệt).
  - Server: hàm search tách thành `_search_all` (tính và sắp xếp toàn bộ) và route `search` (dựng trang). Kết quả đầy đủ cache 60 s trong process theo user + bộ lọc + phiên bản allocation (claim/release hiện ngay) + bộ lọc advertiser của MKT + signature hồ sơ (khi dùng độ phù hợp). Kết quả của lượt quét trực tiếp đang chạy (`ids`) không cache.
  - Điểm phù hợp có memo 10 phút theo signature, tối đa 1.500 sản phẩm mỗi truy vấn (theo opportunity).
  - Trạng thái sở hữu đọc cả bảng lock (nhỏ) thay vì `IN` hàng nghìn id.
- **Sửa nhỏ Phase 5:** khi cold start không gắn nhãn "Khám phá" nữa (hồ sơ rỗng thì mọi sản phẩm đều "ngoài sở thích", nhãn thành nhiễu; phát hiện qua screenshot).

### Thời gian phản hồi trên PostgreSQL thật (in-process, transaction chỉ đọc, 1 MKT thật đang cold start)

| Truy vấn | Kết quả | Trang đầu | Trang 2 (cache) |
|---|---|---|---|
| 2 từ khóa, Tất cả | 1.024 | 1.397 ms | 25 ms |
| Không từ khóa, Tất cả (toàn kho) | 11.034 | 2.914 ms | 253 ms |
| Dành cho tôi | 198 | 862 ms | 40 ms |
| Có thể nhận (toàn kho, sắp xếp phù hợp) | 11.034 | 3.019 ms | 61 ms |
| Có thể nhận + từ khóa | 878 | 1.267 ms | 56 ms |
| Phù hợp ≥ 60 | 926 | 2.756 ms | 24 ms |

Trang đầu khi không có từ khóa mất ~3 s, phần lớn là chi phí có sẵn của search cũ (quét toàn bộ Ads và tải 11 nghìn sản phẩm; trước Phase 7 đo được ~3,3 s). Phần cá nhân hóa thêm khoảng 0,2–0,3 s. Các trang sau và lần lọc lặp lại trong 60 s đọc từ cache.

### File

- Mới: `apps/api/app/personal_search.py`, `apps/api/tests/test_personal_search.py`, `apps/api/scripts/verify_search_browser.py`.
- Sửa: `apps/api/app/routers/intel.py` (tham số mới, `_search_all` + cache, lọc mức Ads cho đường khớp tên), `apps/api/app/recommend.py` (không khám phá khi cold start), `apps/api/tests/conftest.py` (xóa cache search giữa các test), `apps/api/tests/test_recommend.py`, `scripts/verify_product_clustering_postgres.py`, `apps/web/app/search/page.tsx`.
- Không thay đổi schema hay dữ liệu PostgreSQL.

### Kiểm thử

- `tests/test_personal_search.py` (8 test):
  - nhiều từ khóa và từng bộ lọc (danh mục, ngôn ngữ, nguồn, nhu cầu, tương tác thực, thị trường);
  - "Có thể nhận" ẩn sản phẩm MKT khác đã nhận, chế độ Tất cả gắn nhãn taken/mine/available, và **không lệnh search nào tạo allocation**;
  - "Dành cho tôi" xếp theo hồ sơ riêng của từng MKT, có lý do, `min_fit` lọc đúng;
  - chế độ Follow, blacklist riêng từng MKT;
  - chế độ tương tự (có gốc / theo lịch sử / gốc không tồn tại → 404);
  - phân trang không chấm lại điểm;
  - cache: trang 2 không quét lại, MKT khác không dùng chung cache, claim / blacklist / hành vi mới làm cache mất hiệu lực;
  - quyền truy cập: chế độ cá nhân cần đăng nhập, service token không dùng được chế độ cá nhân, mode lạ → 422.
- PostgreSQL cô lập: **149 passed**. SQLite toàn bộ thư mục `tests`: **365 passed, 2 skipped, 1 failed**. Test fail là `test_naming_ai.py::test_dashboard_numbers`: lỗi có sẵn, không liên quan Search (test gọi `/api/naming/stats` không đăng nhập nên nhận 401 từ lớp Auth; fail cả khi chạy riêng).
- **Trình duyệt** (`scripts/verify_search_browser.py`: SQLite/API/Next cô lập ở cổng 8012/3002, Edge headless, 2 MKT, không đụng DB thật):
  - Tất cả hiện sản phẩm đã có chủ kèm nhãn;
  - Có thể nhận ẩn sản phẩm đó;
  - bấm "Nhận" một lần → đúng 1 allocation;
  - Dành cho tôi hiện điểm phù hợp + tooltip lý do, nút chế độ đang chọn được đánh dấu;
  - chế độ Follow chỉ hiện sản phẩm của advertiser đang Follow;
  - "SP tương tự" chuyển sang chế độ tương tự kèm sản phẩm gốc;
  - debounce 3 phím → 1 request;
  - lọc ngôn ngữ;
  - MKT B thấy sản phẩm của mình là "Của bạn" và của A là "MKT khác đã nhận";
  - không có lỗi JavaScript.
- Lỗi phát hiện và đã sửa:
  - đường khớp tên bỏ qua bộ lọc ngôn ngữ / advertiser của chế độ;
  - nút chế độ đang chọn không được tô (Tooltip của Radix ghi đè `data-state`);
  - nút "Tương tự" trùng tên với nút tìm theo ảnh sẵn có trên card (đổi thành "SP tương tự");
  - mọi sản phẩm bị gắn "Khám phá" khi cold start;
  - chủ sở hữu được tra bằng `IN` 11 nghìn id.
- Typecheck frontend `tsc --noEmit`: exit 0.

### Hạn chế còn lại

- Trang đầu không từ khóa trên toàn kho vẫn ~3 s do chi phí quét có sẵn của search; tối ưu triệt để cần chuyển lọc/sắp xếp xuống SQL (ngoài phạm vi phase này).
- Cache search nằm trong process API (tối đa 64 truy vấn, 60 s); chạy nhiều process API thì mỗi process có cache riêng.
- Điểm phù hợp chỉ tính cho tối đa 1.500 sản phẩm opportunity cao nhất mỗi truy vấn; sản phẩm ngoài nhóm này không có điểm, nên bị loại khi dùng `min_fit`.
- Chưa restart API/web chính của người dùng; trình duyệt chỉ được kiểm tra trên stack cô lập với dữ liệu test, chưa với dữ liệu thật.
- Lỗi chọn "Tất cả funnel" không được tô màu là lỗi có sẵn của nhóm funnel (giá trị rỗng); chưa sửa vì nằm ngoài phần được yêu cầu.


## 17. Phase 8 — Personalized Ads Library (19/10/2026)

### Thiết kế

- **Library là một "view", không phải bản sao.** `apps/api/app/library.py` dựng thư viện khi đọc, bằng cách join các dòng riêng của MKT (`mkt_ad_interactions`, `product_allocations`, `product_test_results`, `mkt_followed_advertisers`) với bảng Ads/Products dùng chung. Không có bảng hay cột mới, không sao chép Ads. Mỗi sản phẩm hiển thị theo cluster root; danh sách "Ads của tôi" trong card trỏ tới Ads gốc.
- **Các mục:** Thư viện của tôi (mọi sản phẩm có tương tác, đang sở hữu hoặc có kết quả test), Ads đã lưu, Yêu thích, Đang test, Đã test, Gợi ý tương tự (Phase 6, theo lịch sử), Đối thủ đang Follow (danh sách advertiser + sản phẩm còn trống của họ). Mỗi tab có số đếm.
- **Trạng thái test của sản phẩm:**
  - *Đang test*: allocation của mình ở trạng thái `testing`, hoặc đã bấm "Đã test" trên Ads nhưng chưa ghi kết quả.
  - *Đã test*: có kết quả và không còn đang test.
  - Kết quả mới nhất (Thành công / Thất bại / Chưa kết luận) giữ nguyên sau khi trả sản phẩm về kho.
- **Đồng bộ Save / Favorite / Test / quyền sở hữu:**
  - Mọi thao tác lưu/yêu thích, kể cả từ Library, đi qua **một đường ghi duy nhất** `set_interaction` (tách ra từ `PUT /api/workspace/ads/{id}`). Nhờ vậy vẫn ghi lịch sử hành vi Phase 3 và giữ quyền sở hữu Phase 2 (lưu một sản phẩm đang giữ chỗ → `claimed`).
  - Lưu/yêu thích ở mức sản phẩm gắn vào một Ads của sản phẩm đó: Ads MKT đã từng tương tác, nếu không có thì Ads của ảnh cover, nếu không có thì Ads mới nhất. Bỏ lưu/bỏ yêu thích xóa cờ trên mọi Ads của sản phẩm mà MKT đang giữ.
  - "Bắt đầu test" = claim (409 nếu MKT khác sở hữu) + chuyển `testing` + ghi `test_start`, trong một transaction; gọi lại không tạo gì thêm.
  - "Cập nhật kết quả" và "Trả về kho" dùng lại API allocation sẵn có.
  - Nút lưu/yêu thích/đã test theo từng Ads (đã có từ trước) cũng làm danh sách Library tải lại ngay.
- **Bộ lọc:** danh mục, thị trường (vùng như Search), loại sản phẩm (`subcategory`), trạng thái test (chưa test / đang test / đã có kết quả / thành công / thất bại / chưa kết luận), advertiser, độ phù hợp (≥ 40/60/80). Ô gõ có debounce 300 ms.
- **Product Card:** dùng lại `ProductMediaCard` chung, không sửa. Dải bên dưới hiển thị:
  - điểm "Phù hợp NN" (tooltip: lý do, độ tương tự, "vì bạn đã chọn …");
  - nhãn Đã lưu / Yêu thích / trạng thái allocation / Kết quả / Còn trống / MKT khác đã nhận;
  - lý do đề xuất đầu tiên.
- **Hành động trên card:** Lưu, Yêu thích, Bắt đầu test, Cập nhật kết quả test (kết quả, thị trường, chi phí, số đơn, doanh thu, ghi chú), Trả về kho (có xác nhận), "Sản phẩm tương tự" mở ngay trong card (chỉ sản phẩm còn trống) kèm nút Lưu vào thư viện, và "Ads của tôi (n)" với nút theo từng Ads, Follow, xem Ads gốc, ghi chú riêng (giữ các chức năng có sẵn).
- **Không lộ dữ liệu MKT khác:**
  - API luôn dùng user của session, không có tham số `user_id` (kể cả ADMIN).
  - Với sản phẩm của MKT khác chỉ trả `ownership: "taken"`, không có allocation, kết quả, ghi chú hay danh tính chủ sở hữu.

### API

| Method / path | Quyền | Chức năng |
|---|---|---|
| `GET /api/library?section&category&market&product_type&test_status&advertiser_id&min_fit&limit&offset` | Đã đăng nhập (dữ liệu của chính mình) | Sản phẩm theo mục + bộ lọc, điểm phù hợp, trạng thái, số đếm từng mục; mục Follow có thêm danh sách advertiser. Mục hoặc trạng thái test không hợp lệ → 422. |
| `POST /api/library/products/{id}/save` `{on}` | MKT, CSRF | Lưu / bỏ lưu sản phẩm (qua Ads gốc). |
| `POST /api/library/products/{id}/favorite` `{on}` | MKT, CSRF | Yêu thích / bỏ yêu thích. |
| `POST /api/library/products/{id}/start-test` | MKT, CSRF | Nhận (nếu cần) và bắt đầu test; 409 nếu MKT khác sở hữu. |
| `POST /api/allocations/{id}/results`, `POST /api/allocations/{id}/release` | Chủ sở hữu | Cập nhật kết quả, trả về kho (Phase 2). |

### File

- Mới: `apps/api/app/library.py`, `apps/api/app/routers/library.py`, `apps/api/tests/test_library.py`, `apps/api/scripts/verify_library_browser.py`.
- Sửa: `apps/api/app/workspace/routes.py` (tách `set_interaction`, hành vi không đổi), `apps/api/app/main.py` (router), `apps/api/app/workspace/auth.py` (allowlist 3 thao tác ghi), `apps/web/app/workspace/library/page.tsx` (viết lại trang Library theo theme hiện có), `scripts/verify_product_clustering_postgres.py` và `scripts/verify_search_browser.py` (chờ request xong thay vì chờ thời gian cố định).
- Không thay đổi schema hay dữ liệu PostgreSQL. Phía web chỉ sửa trang Library; Dashboard/Search không đổi trong phase này.

### Kiểm thử

- `tests/test_library.py` (7 test):
  - CRUD đầy đủ: lưu → yêu thích → bỏ yêu thích ở trang Ads thì Library cập nhật → bắt đầu test (gọi lại không tạo gì thêm) → ghi kết quả → trả về kho (mất quyền sở hữu, giữ kết quả) → bỏ lưu, có đối chiếu đúng dòng `mkt_ad_interactions`;
  - lưu sản phẩm đang giữ chỗ chuyển thành `claimed`;
  - không thấy dữ liệu của MKT khác: ghi chú, allocation, kết quả; cả ADMIN cũng không đọc được Library người khác; không bắt đầu test được sản phẩm người khác (409);
  - mọi bộ lọc;
  - mục tương tự và mục Follow;
  - dữ liệu giữ nguyên sau đăng xuất / đăng nhập lại;
  - không phát sinh dòng Ads / Products mới (hai cờ trên cùng một Ads = một dòng tương tác);
  - anonymous 401, thiếu CSRF 403, sản phẩm không tồn tại 404.
- PostgreSQL cô lập: **156 passed**. SQLite toàn bộ thư mục `tests`: **372 passed, 2 skipped, 1 failed**. Test fail vẫn là lỗi có sẵn `test_naming_ai.py::test_dashboard_numbers` (gọi API không đăng nhập), không liên quan Library.
- **Trình duyệt** (`scripts/verify_library_browser.py`: SQLite/API/Next cô lập cổng 8013/3003, Edge headless, 2 MKT):
  - thư viện chỉ có sản phẩm của mình;
  - Bắt đầu test → Đang test → form kết quả → "Kết quả: Thành công";
  - Trả về kho → "Còn trống", kết quả vẫn còn;
  - tab Yêu thích;
  - tương tự ngay trong card → Lưu → sản phẩm vào thư viện;
  - bỏ yêu thích bằng nút có sẵn trong "Ads của tôi" → Library cập nhật ngay;
  - tab Đối thủ đang Follow;
  - lọc trạng thái test;
  - dữ liệu giống hệt sau đăng xuất / đăng nhập;
  - MKT B chỉ thấy thư viện của mình;
  - không có lỗi JavaScript.
- Kiểm thử trình duyệt Search (Phase 7) có một lần fail khi máy đang chạy nặng (chờ thời gian cố định); đã đổi cả hai script sang chờ request xong và chạy lại đạt.
- Typecheck frontend `tsc --noEmit`: exit 0.

### Hạn chế còn lại

- Bộ lọc advertiser trên giao diện chỉ liệt kê advertiser đang Follow; API nhận bất kỳ `advertiser_id` nào.
- Danh sách "Sản phẩm tương tự" trong card không tự làm mới sau khi bấm Lưu (sản phẩm vẫn được thêm vào thư viện).
- "Loại sản phẩm" dựa trên `subcategory`, trường này còn thưa trên dữ liệu thật.
- Chưa restart API/web chính; giao diện mới chỉ được kiểm tra trên stack cô lập với dữ liệu test.


## 18. Phase 9 — Personalization Dashboard & Final QA (19/10/2026)

### Dashboard cá nhân

`GET /api/library/dashboard` (app/library.py) và trang `/workspace` (Dashboard cá nhân). Các thẻ số và khối cũ ("Đối thủ đang Follow / có hoạt động / Ads mới 24h / Thông báo", "Thay đổi quảng cáo gần đây", "Lần kiểm tra gần nhất") và API `/api/workspace/dashboard` **giữ nguyên**. Phần thêm mới:

- **KPI Sản phẩm (đếm theo Product Cluster root):** được đề xuất (kèm số TEST / TEST_NOW trong top), đã lưu, yêu thích, đã nhận (giữ chỗ + đã nhận), đang test, test thành công / thất bại / chưa kết luận.
- **KPI Ads (đếm từng quảng cáo):** Ads đã lưu, yêu thích, đánh dấu đã test, Ads mới từ đối thủ 24h / 7 ngày (đếm từ bảng `ads` thật theo `first_seen_at`, áp bộ lọc advertiser của MKT).
- **Khối:**
  - sản phẩm được đề xuất (điểm phù hợp, lý do, nhãn TEST/TEST_NOW, nhãn Khám phá);
  - Ads đang chạy của các sản phẩm được đề xuất;
  - danh sách Đã lưu / Yêu thích / Đã nhận / Đang test / Test thành công / Test thất bại (liên kết tới đúng mục Library qua `?section=`);
  - đối thủ đang Follow;
  - Ads mới từ đối thủ;
  - danh mục quan tâm nhất (thanh điểm);
  - sản phẩm tương tự được đề xuất (kèm "vì bạn đã chọn …").
- **Không phục hồi Win / Rare Winner:** chỉ dùng nhãn TEST/TEST_NOW; có test kiểm tra không có khóa hoặc chữ Win/Rare trong API và trên trang.
- Dữ liệu được lấy với số câu SQL cố định (tối đa 40 mỗi lần gọi, không N+1); gợi ý tương tự ở dashboard giới hạn 3 sản phẩm gốc × 6 gợi ý.

### Kết quả QA theo nhóm

| Nhóm | Kết quả | Bằng chứng |
|---|---|---|
| KPI dữ liệu thật, phân biệt Ads / Product Cluster | **PASS** | `test_dashboard_kpis_are_real_and_separate_ads_from_clusters`: 3 Ads của 1 cluster → Ads đã lưu = 3, sản phẩm đã lưu = 1. Browser: số liệu dashboard khớp thao tác thật của MKT. |
| Giữ TEST/TEST_NOW, không phục hồi Win/Rare Winner | **PASS** | Test API (không có khóa win/rare), browser (trang không có chữ Win/Rare), `test_unified_scoring.py` vẫn đạt. |
| E2E nhiều MKT dùng đồng thời | **PASS** | `test_full_day_of_two_mkts`, `test_three_mkts_claim_the_same_product_concurrently_over_http`; browser Search, Library và Dashboard với 2 MKT. |
| Chống trùng khi claim đồng thời | **PASS** | 3 MKT claim cùng sản phẩm qua HTTP song song → `[200, 409, 409]`, 1 lock; PostgreSQL cô lập: 6 MKT cùng sản phẩm + 2 MKT hai sản phẩm cùng cluster (Phase 2). |
| Cá nhân hóa, Follow, Search, Library, đăng xuất / đăng nhập lại | **PASS** | Hai MKT có đề xuất và danh mục quan tâm khác nhau; Follow → Ads mới từ đối thủ; Search "Có thể nhận" → Library "Bắt đầu test" → kết quả; KPI và danh sách giống hệt sau đăng nhập lại (API + browser). |
| Quyền riêng tư, chống truy cập chéo | **PASS** | `test_cross_access_matrix`: MKT B nhận 404 trên allocation / preferences / recommendations của A, `behavior?user_id=A` chỉ trả dữ liệu của B, reassign → 403, dashboard / library / workspace của B không chứa dữ liệu A (kể cả ghi chú), ADMIN cũng không đọc được Library của MKT; anonymous nhận 401 trên 10 endpoint cá nhân và search cá nhân hóa. |
| Đo thời gian API, CPU, RAM, query chậm | **PASS (đã đo)** | `scripts/bench_personalization.py` trên PostgreSQL thật (chỉ đọc) → `docs/PHASE9_PERF.json`; bảng bên dưới. |
| Tối ưu index, cache, batch, N+1 | **PASS** | 3 index mới chọn theo `EXPLAIN ANALYZE`; số SQL mỗi lần gọi cố định (không N+1); cache search / fit / feed / vector / text; batch worker cho preferences / recommendations (Phase 4–5). |
| Không thêm video encoding, Docker, AI API trả phí | **PASS** | Không thêm dependency. Mọi test cá nhân hóa chặn `llm.complete_json` và inference CLIP. Đo RAM dùng `ctypes` thay vì cài `psutil`. |
| Không thay đổi chức năng đang ổn định | **PASS** | Toàn bộ thư mục `tests`: 377 passed, 3 skipped, 0 failed. API `/api/workspace/dashboard`, `ProductMediaCard`, luồng search cũ giữ nguyên. |
| Nghiệm thu trên server đang chạy với tài khoản thật | **CHƯA LÀM** | API/worker/web chính chưa restart trong các phase; browser test chạy trên stack cô lập với dữ liệu test. DB thật vẫn chưa có ADMIN (A01). |

### Lỗi phát hiện và đã sửa trong Phase 9

- **Khám phá quá tay khi ít ứng viên:** hạn mức 20% tính trên 200 dòng lưu cache, nên khi chỉ có 3 ứng viên thì cả 3 bị gắn "Khám phá" (thấy trên screenshot dashboard). Giờ tính trên số dòng thực có; thêm test `test_exploration_share_counts_listed_rows_not_the_cache_size`.
- **Liên kết "Chỉnh sở thích" trỏ tới `/account`**, nơi chưa có trình chỉnh sở thích; đã thay bằng ghi chú đúng.
- **Test có sẵn `test_naming_ai::test_dashboard_numbers`** gọi API khi chưa đăng nhập (fail từ khi có Auth); test giờ dùng service token. Không sửa code chức năng.
- Liên kết "Xem tất cả" từ dashboard sang Search dùng `saved_filter` để chế độ trên URL có hiệu lực; Library đọc `?section=` sau khi mount (không lệch hydration).

### Hiệu năng đo được (PostgreSQL thật, transaction chỉ đọc, 1 MKT thật, 5 lần warm/endpoint)

| Endpoint | Lần đầu (cache trống) | Warm p50 | Warm p95 | SQL / lần |
|---|---|---|---|---|
| `/api/search` 2 từ khóa | 2.089 ms | 64 ms | 65 ms | ≤ 16 |
| `/api/search` không từ khóa (toàn kho) | 4.208 ms | 119 ms | 122 ms | ≤ 15 |
| `/api/search` Dành cho tôi | 722 ms | 51 ms | 52 ms | ≤ 25 |
| `/api/search` Có thể nhận (trang 1 / trang 2) | 3.381 / 3.061 ms | 69 / 62 ms | 73 / 64 ms | ≤ 24 |
| `/api/recommendations` | 354 ms | 14 ms | 15 ms | ≤ 12 |
| `/api/preferences` | 2 ms | 1 ms | 2 ms | ≤ 4 |
| `/api/library` / `?section=similar` | 10 / 7 ms | 3 / 7 ms | 3 / 7 ms | ≤ 5 / 14 |
| `/api/library/dashboard` | 536 ms | 28 ms | 29 ms | ≤ 40 |
| `/api/workspace/dashboard` (cũ) | 19 ms | 4 ms | 5 ms | ≤ 6 |
| `/api/similar/products/{id}` (5 sản phẩm khác nhau) | 1.256 ms | 508 ms | 914 ms | ≤ 17 |
| `/api/similar/ads/{id}` (5 Ads khác nhau) | 255 ms | 188 ms | 426 ms | ≤ 14 |

- **Tiến trình:** 22,4 s đồng hồ, 12,6 s CPU (≈ 0,56 lõi trung bình); RAM 103 MB lúc đầu → 248 MB cuối, đỉnh 702 MB (lúc search lần đầu tải toàn bộ ~11 nghìn sản phẩm vào bộ nhớ, phần có sẵn của search).
- **Query chậm nhất (≥ 100 ms):**
  - tải toàn bộ `products` của search cũ (1.550 ms, chỉ lần đầu);
  - quét Ads theo từ khóa của search cũ (603 ms);
  - láng giềng pgvector HNSW (468 ms);
  - lấy một ảnh mỗi sản phẩm khi dữ liệu còn nguội (438 ms; khi đã nóng 57 ms).
- **Index thêm trong Phase 9** (`app/personalization_migrate.py`, idempotent, gọi khi API khởi động, đã áp dụng trên DB thật, mất 5,1 s):

| Index | Truy vấn | Trước → sau (EXPLAIN ANALYZE) |
|---|---|---|
| `ix_products_category_opportunity (category, opportunity_score DESC)` | Ứng viên cùng danh mục của Similar Products | 195 ms (seq scan bảng `products` rộng) → **1,2 ms** |
| `ix_product_images_creative_done (source_id, id) WHERE source='creative' AND processing_status='done'` | Một ảnh mỗi sản phẩm / Ads | 297 ms → **57,6 ms** (index-only) |
| `ix_products_target_opportunity (opportunity_score DESC) WHERE in_target` | Kho ứng viên của Recommendation | sort toàn bảng → index scan + incremental sort (263 ms khi heap còn nguội) |

- Similar Products warm p50: **816 ms → 508 ms** (trước / sau index, cùng script).
- Lần đầu không từ khóa của search cũ vẫn 2–4 s (phụ thuộc tải máy). Tối ưu triệt để cần viết lại phần lọc/sắp xếp của search sang SQL, đã có cache 60 s cho các lần sau; không làm trong phase này để không đổi hành vi search đang ổn định.

### Migration trên PostgreSQL thật (toàn bộ đều additive, không xóa / sửa dữ liệu cũ)

| Phase | Thay đổi |
|---|---|
| 1 | Bảng `product_clusters`, `ad_cluster_memberships`, `duplicate_ad_links`; cột `product_links.relation_kind`, `algorithm_version` (`cluster_migrate.py`). |
| 2 | Bảng `product_allocations`, `product_allocation_locks`, `product_test_results`, `product_allocation_audit` (rỗng). |
| 3 | Bảng `mkt_behavior_events` (rỗng). |
| 4 | Bảng `mkt_preference_profiles` (rỗng). |
| 5 | Bảng `mkt_recommendations`, `mkt_recommendation_state` (rỗng). |
| 6 | Extension `pg_trgm`, index `ix_ads_search_text_trgm`. |
| 9 | Index `ix_products_category_opportunity`, `ix_products_target_opportunity`, `ix_product_images_creative_done`. |
| 7, 8 | Không đổi schema. |

### File thay đổi trong Phase 9

- Mới: `apps/api/app/personalization_migrate.py`, `apps/api/tests/test_e2e_personalization.py`, `apps/api/scripts/bench_personalization.py`, `docs/PHASE9_PERF.json`.
- Sửa: `apps/api/app/library.py` (hàm `dashboard`), `apps/api/app/routers/library.py` (`GET /api/library/dashboard`), `apps/api/app/main.py` (gọi migration index), `apps/api/app/recommend.py` (hạn mức khám phá), `apps/web/app/workspace/page.tsx` (Dashboard cá nhân), `apps/web/app/workspace/library/page.tsx` (`?section=`), `apps/api/tests/test_recommend.py` (+1 test), `apps/api/tests/test_naming_ai.py` (xác thực bằng service token), `apps/api/scripts/verify_library_browser.py` (kiểm tra dashboard), `apps/api/scripts/verify_product_clustering_postgres.py` (thêm test Phase 9).

### Kiểm thử cuối cùng

- SQLite, toàn bộ thư mục `apps/api/tests` trong một process: **377 passed, 3 skipped, 0 failed** (3 skip là test chỉ chạy PostgreSQL). Lỗi A09 của Phase 0 (chạy chung không cô lập) không còn.
- PostgreSQL cô lập (`scripts/verify_product_clustering_postgres.py`, Phase 1–9): **162 passed**, gồm pgvector HNSW thật, claim đồng thời, index Phase 9.
- Browser cô lập (Edge headless): `verify_search_browser.py` PASS (9 bước), `verify_library_browser.py` PASS (11 bước, gồm Dashboard và MKT thứ hai), không lỗi JavaScript.
- Typecheck frontend `tsc --noEmit`: exit 0.
- Lưu ý vận hành: một lượt chạy song song bị dừng do máy hết RAM (20 GB, nhiều tiến trình khác); các lượt chạy lại tuần tự đều đạt. Không phải lỗi của code.

### Giới hạn còn lại

- **Chưa nghiệm thu trên server đang chạy:** API, worker (vòng refresh preferences / recommendations) và web chính chưa restart; giao diện mới chỉ được kiểm tra trên stack cô lập. DB thật chưa có ADMIN (A01), SMTP chưa cấu hình.
- **Chưa có dữ liệu hành vi thật:** 4 MKT đều ở cold start; trọng số, ngưỡng và half-life chưa được hiệu chỉnh bằng kết quả kinh doanh.
- **Chưa có UI** chỉnh hồ sơ sở thích (chỉ có API), quản trị allocation cho ADMIN, và trang `/clusters` chưa được kiểm tra bằng browser.
- **View tracking** phía client (IntersectionObserver) mới qua typecheck, chưa có test trình duyệt.
- Search lần đầu không từ khóa 2–4 s; Similar Products 0,5–0,9 s (HNSW).
- Cache trong process (search 60 s, fit 10 phút, feed 10 phút); nhiều process API sẽ không dùng chung cache.
- Chưa có bộ nhãn chuẩn cho tương tự / duplicate; độ chính xác Phase 6 dựa trên chỉ số đại diện.


## 19. Chính sách tài khoản mới & Soft Deduplication (19/10/2026)

> Mục này **thay thế** mọi mô tả về ADMIN ở các mục trước: duyệt tài khoản, ADMIN đọc dữ liệu MKT, ADMIN Reassign / Release, `owner_id` trong Allocation Status. Đồng thời thay quy tắc Phase 2 "Saved / Favorite giữ quyền sở hữu". Các mục cũ giữ nguyên làm lịch sử.

### Audit Phase 0–9 theo chính sách mới

| Chức năng đã có | Không phù hợp chính sách | Xử lý |
|---|---|---|
| Middleware RBAC (`workspace/auth.py`) | Nhánh ADMIN được đọc khu vực hệ thống và ghi mọi API | Chỉ còn một loại tài khoản. Allowlist ghi của MKT **giữ nguyên** (không mở thêm API). Khu vực hệ thống (`/api/admin`, `/api/collector/…`, `/api/storage`, `/api/agent`, `/api/eu-market`) và mọi thao tác ghi ngoài allowlist chỉ chạy bằng **service token nội bộ** (`X-Admin-Token` = `ADMIN_TOKEN`, web middleware luôn xóa header này khỏi request trình duyệt). |
| `/api/admin/users`, `PATCH /api/admin/users/{id}`, `/api/admin/reset-requests`; trang `/admin/users` | Duyệt / khóa tài khoản bằng ADMIN | **Gỡ bỏ.** Đăng ký vốn đã `active` ngay. Khóa / mở khóa / cấp link reset chuyển sang CLI nội bộ (`python -m app.workspace.cli lock|unlock|reset-password|create-user`). MKT tự quản lý hồ sơ, mật khẩu, đăng xuất, thư viện, lịch sử, sở thích (API sẵn có). |
| `POST /api/admin/allocations/reassign`, ADMIN release, `owner_id` | Điều chuyển bởi ADMIN | **Gỡ bỏ** (route + hàm `reassign`). Chủ sở hữu tự "Trả về kho"; lịch sử test và kết quả kinh doanh được giữ. |
| `?user_id=` của behavior / preferences / recommendations / allocations | ADMIN đọc dữ liệu người khác | Luôn chỉ dữ liệu của chính tài khoản; `user_id` của người khác → 404 hoặc bị bỏ qua. |
| Live search `track` | ADMIN đặt lịch crawl toàn hệ thống | Không tài khoản nào đặt được (403); lịch crawl thuộc scheduler / dịch vụ nội bộ. Bỏ checkbox trên trang Search. |
| `users.role` CHECK `('ADMIN','MKT')`, CLI `create-admin` | Còn khái niệm ADMIN | CHECK `role = 'MKT'`; CLI tạo tài khoản MKT. |
| Phase 2 `on_interaction`: Save / Favorite biến giữ chỗ thành sở hữu vĩnh viễn | Save / Favorite không được tạo / kéo dài quyền sở hữu | Save / Favorite **không chạm** allocation; giữ chỗ hết hạn theo TTL. Đánh dấu "Đã test" trên sản phẩm mình đã nhận vẫn chuyển sang `testing`. |
| Recommendation / Search / Library | Chưa có tín hiệu chống trùng mềm | Thêm `app/interest.py` (mô tả bên dưới). |
| Sidebar: "Quản lý tài khoản", "AI Agent", "Data & Connectors" | Màn hình ADMIN | Ẩn khỏi sidebar MKT (API là khu vực hệ thống). |

Không đổi: Claim / Bắt đầu test nguyên tử, khóa theo cụm đủ tin cậy, TTL, Follow Competitor, Behavior, Preference, Similar, toàn bộ dữ liệu Ads.

### Soft Deduplication

- **Tín hiệu:** đọc trực tiếp từ `mkt_ad_interactions` (không thêm bảng). Tính theo **Product Cluster root** và theo **người**: một MKT lưu 4 Ads của cùng sản phẩm = 1 tín hiệu; mỗi người chỉ tính ở mức mạnh nhất (Favorite > Saved).
- **Điểm phạt** (cấu hình được qua env):
  - `RECO_INTEREST_PENALTY_SAVED` = 0,15;
  - `RECO_INTEREST_PENALTY_FAVORITE` = 0,30;
  - `RECO_INTEREST_PENALTY_PER_EXTRA` = 0,05 cho mỗi người thêm;
  - tối đa `RECO_INTEREST_PENALTY_MAX` = 0,5.
  - Điểm cá nhân = điểm phù hợp × (1 − phạt). Không bao giờ phạt chính người đang xem. Phản hồi có `score_before_interest`, `interest_penalty` và lý do "Đã có MKT khác quan tâm — giảm ưu tiên…".
- **Áp lúc đọc:**
  - feed Recommendation (xếp lại, vị trí Khám phá giữ nguyên) và `explain`;
  - độ phù hợp trong Search (mọi chế độ, sắp xếp / lọc `min_fit`) và Library.
  - Unsave / Unfavorite có hiệu lực ngay. Cache kết quả Search được làm mới khi bảng tương tác thay đổi.
- **Hiển thị:** `interest: {"label": "Sản phẩm đã có người quan tâm", "level": "saved"|"favorite"}`. Không có danh tính, số lượng, ghi chú hay kết quả test của người khác.
- **Không ẩn:**
  - Search thường, "Có thể nhận", Library vẫn hiện sản phẩm có người quan tâm;
  - MKT khác vẫn xem, lưu, yêu thích và **nhận** được;
  - chỉ sản phẩm đang có chủ mới bị loại khỏi chế độ cơ hội độc quyền.
- **Tách hai trạng thái:**
  - `interest` = người khác quan tâm (mềm);
  - `allocation` / `taken_state` = đang được nhận / giữ chỗ / test (cứng, chỉ trạng thái, không danh tính).
  - Giao diện: "Sản phẩm đã có người quan tâm" và "MKT khác đang test / đang giữ chỗ / đã nhận".
- **Claim bị trùng:** HTTP 409 `{"code": "taken", "state": "testing"|…, "message": "Sản phẩm đang được MKT khác test — xem sản phẩm tương tự còn trống.", "similar": [≤ 5 sản phẩm tương tự còn trống]}`. Search và Library hiện cảnh báo kèm nút "Xem SP tương tự".

### API thay đổi

| API | Thay đổi |
|---|---|
| `GET /api/admin/users`, `PATCH /api/admin/users/{id}`, `GET /api/admin/reset-requests`, `POST /api/admin/allocations/reassign` | **Gỡ bỏ** (403 với mọi tài khoản; service token không mở được dữ liệu tài khoản). |
| `POST /api/allocations/claim`, `POST /api/library/products/{id}/start-test` | 409 trả `code`, `state`, `message`, `similar`. |
| `POST /api/allocations/{id}/release` | Chỉ chủ sở hữu (không còn ADMIN release). |
| `GET /api/allocations/status/{id}` | Không còn `owner_id`. |
| `GET /api/allocations`, `/api/behavior/events|summary`, `/api/preferences`, `/api/recommendations` | Chỉ dữ liệu của chính tài khoản. |
| `GET /api/search`, `GET /api/library` | Thêm `interest`, `taken_state`; `fit` có điểm phạt chống trùng. |
| `GET /api/recommendations`, `/explain/{id}` | Thêm `interest`, `interest_penalty`, `score_before_interest`. |
| `POST /api/search/live` với `track=true` | 403 với mọi tài khoản. |

### Migration

- `workspace.migrate.single_role` (chạy trong `workspace.migrate()` khi API / worker / CLI khởi động; idempotent; phiên bản `20261019_single_mkt_role_v1`): `role` ≠ MKT → MKT, `pending` → `active`, đổi CHECK thành `role = 'MKT'`. Không xóa user, session, lịch sử, tương tác hay Ads; tài khoản `locked` giữ nguyên.
- **Đã chạy trên PostgreSQL thật:** trước và sau đều 4 MKT active, 4 session, 8 dòng `mkt_activity`, 0 tương tác, 16.131 Ads; CHECK đổi từ `role IN ('ADMIN','MKT')` sang `role = 'MKT'`. Không có tài khoản ADMIN nào để chuyển.
- Không thêm bảng hay index mới cho Soft Dedup.

### Kiểm thử nghiệm thu

| # | Trường hợp | Kết quả | Bằng chứng |
|---|---|---|---|
| 1 | Đăng ký MKT mới và đăng nhập ngay | **PASS** | `test_register_and_use_immediately_without_approval`, `test_registration_is_active_at_once_…`; browser `verify_workspace_browser.py` (đăng ký → đăng nhập ngay). |
| 2 | Không còn luồng ADMIN phê duyệt | **PASS** | Route và trang `/admin/users` đã gỡ; browser: không có menu "Quản lý tài khoản", `/api/admin/users` → 403. |
| 3 | API quản trị cũ không tạo lỗ hổng | **PASS** | `test_old_admin_api_is_closed_for_everyone`: 7 endpoint quản trị / hệ thống → 403 với MKT; service token vẫn không đọc được `/api/admin/users` hay Library; `test_csrf_permissions_…`. |
| 4 | Hai MKT cùng lưu / yêu thích một sản phẩm | **PASS** | `test_two_mkts_can_save_and_favorite_the_same_product_and_nothing_is_hidden` (không tạo allocation, không ẩn khỏi Search / "Có thể nhận" / Library). |
| 5 | Recommendation giảm ưu tiên sản phẩm người khác quan tâm | **PASS** | `test_recommendation_prefers_…` (chưa ai quan tâm > Saved > Favorite; không phạt chính mình), `test_penalty_weights_are_configurable`, `test_unsave_and_unfavorite_update_the_signal_immediately`, `test_many_duplicate_ads_of_one_product_count_as_one_interest`. |
| 6 | Hai MKT không claim đồng thời cùng sản phẩm đủ tin cậy | **PASS** | Race 6 MKT + 2 MKT cùng cluster (PostgreSQL), 3 MKT qua HTTP; `test_conflict_names_the_state_and_suggests_open_look_alikes`; cụm chưa đủ tin cậy chỉ khóa đúng sản phẩm. |
| 7 | Theo dõi cùng advertiser | **PASS** | `test_follow_and_advertiser_research_stay_independent_of_ownership`, E2E Follow → Ads mới. |
| 8 | Library và lịch sử hành vi tách theo user | **PASS** | `test_history_is_isolated_per_mkt_and_nobody_reads_another_account`, `test_other_mkts_data_never_shows…`, `test_cross_access_matrix`, `test_label_shows_interest_but_never_who…`. |
| 9 | Dữ liệu cũ không mất sau migration | **PASS** | `test_single_role_migration_keeps_everything_and_is_idempotent` (SQLite + PostgreSQL với CHECK thật); số liệu trước / sau trên DB thật như trên. |
| 10 | Regression Phase 0–9 | **PASS** | SQLite toàn bộ `tests`: **390 passed, 3 skipped, 0 failed**. PostgreSQL cô lập: **174 passed**. Browser cô lập: workspace, search, library + dashboard đều PASS, không lỗi JavaScript. `tsc --noEmit` exit 0. |

### File thay đổi

- **Backend:**
  - mới: `app/interest.py`;
  - sửa: `app/workspace/auth.py`, `app/workspace/accounts.py`, `app/workspace/models.py`, `app/workspace/migrate.py`, `app/workspace/cli.py`, `app/allocation.py`, `app/routers/allocations.py`, `app/routers/behavior.py`, `app/routers/preferences.py`, `app/routers/recommendations.py`, `app/routers/library.py`, `app/routers/intel.py` (track, annotate, cache key), `app/recommend.py` (`soften`, explain), `app/personal_search.py` (`ownership`, `personal_fit`, annotate), `app/library.py`.
- **Frontend:**
  - xóa `apps/web/app/admin/users/page.tsx`;
  - sửa: `components/layout/nav.ts`, `components/layout/app-sidebar.tsx`, `components/workspace/session.tsx`, `components/workspace/auth-form.tsx`, `app/search/page.tsx`, `app/workspace/library/page.tsx`.
- **Test / script:**
  - mới: `tests/test_soft_dedup.py`;
  - sửa: `tests/test_allocation.py`, `test_behavior.py`, `test_preferences.py`, `test_recommend.py`, `test_library.py`, `test_e2e_personalization.py`, `test_workspace.py`, `scripts/verify_workspace_browser.py`, `scripts/verify_search_browser.py`, `scripts/verify_product_clustering_postgres.py`.

### Giới hạn còn lại

- Các trang hệ thống (`/data`, `/agent`, phần quản trị EU Market) vẫn tồn tại trong mã nhưng không còn trong sidebar. API của chúng chỉ chạy bằng service token, nên mở trực tiếp bằng tài khoản MKT sẽ nhận 403. Muốn vận hành bằng giao diện thì cần một công cụ nội bộ riêng, không đi qua tài khoản MKT.
- Khóa / mở khóa tài khoản và cấp link reset chỉ qua CLI trên server; SMTP vẫn chưa cấu hình.
- Trọng số phạt chống trùng là giá trị khởi đầu, chưa hiệu chỉnh bằng dữ liệu thật (DB thật chưa có tương tác nào).
- API / worker / web chính chưa restart; chính sách mới mới được kiểm chứng trên stack cô lập. Migration tài khoản đã chạy trên DB thật.
