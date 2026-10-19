# Product Clustering (2026-10-14)

## Phase 1 — cập nhật 19/10/2026

**Phần mô tả ngày 14/10 bên dưới là thiết kế legacy.** Phase 1 thay thế việc merge/xóa Product trong clustering tự động hoặc review mới bằng overlay PostgreSQL: `product_clusters`, `ad_cluster_memberships`, `duplicate_ad_links`. `ads.product_id`, Product gốc, snapshots và lifecycle được giữ nguyên. Chi tiết, manifest file và test: [personalization_progress.md](personalization_progress.md#10-phase-1--product-clustering-19102026).

Tín hiệu bổ sung: tên chuẩn hóa giữ model/variant, GTIN hợp lệ, brand/model, SKU trong shop và metadata biến thể. Landing URL giữ tham số sản phẩm, loại tracking. CLIP chỉ đọc embedding đã lưu. Không inference/AI API trên request. `similar_product` không tự gom; mâu thuẫn định danh/variant và cannot-link chặn gom cả qua nhóm trung gian. Confidence heuristic không phải xác suất hiệu chuẩn; membership legacy có confidence NULL.

Chạy từ `apps/api` bằng Python venv:

```powershell
./.venv/Scripts/python.exe -m app.cluster_migrate
./.venv/Scripts/python.exe -m app.cluster_jobs              # mô phỏng, rollback
./.venv/Scripts/python.exe -m app.cluster_jobs --queue-only # lưu hàng chờ
./.venv/Scripts/python.exe -m app.cluster_jobs --apply      # lưu + gom cặp chắc chắn
./.venv/Scripts/python.exe scripts/verify_product_clustering_postgres.py
```

Startup API/worker chạy migration additive; worker chạy scan cached evidence, ingest đồng bộ membership. Review MKT vẫn cần session/CSRF; actor lấy từ session, không tin `user` trong body. Undo chỉ hợp lệ khi revision chưa bị thao tác mới thay đổi; undo từ mới nhất về trước. Candidate legacy phải được worker đánh giá lại trước accept.

- `GET /api/clusters/groups`, `GET /api/clusters/groups/{id}`: cluster và Ads gốc, phân trang.
- `GET /api/clusters/duplicate-ads`: liên kết trùng Ad theo định danh nguồn.
- `GET /api/clusters/candidates`: classification/version/khả năng accept.
- `POST /api/clusters/candidates/{id}/accept` hoặc `/reject`: review.
- `POST /api/clusters/events/{id}/undo`: hoàn tác grouping mới.
- `/clusters`: danh sách nhóm, Ads trùng, similar và hàng chờ. Những luồng merge/split legacy riêng vẫn có semantics cũ; không dùng chúng thay cho Phase 1 clustering.

## Thiết kế legacy ngày 14/10/2026

Gom các sản phẩm thực chất là một: tự động gộp khi chắc chắn, còn lại đưa MKT xác nhận ở `/clusters`; Gộp / Tách / Hoàn tác ở **Xếp hạng sản phẩm**.

## Tái sử dụng, không xây lại pipeline
| Tín hiệu | Nguồn có sẵn |
|---|---|
| Trang sản phẩm | `entity_resolution.landing_key` (bỏ shop front, link social/app, collection) |
| Nội dung quảng cáo | `ads.creative_fingerprint`, `ads.variation_key` (cùng page + cùng copy ≥ 20 ký tự) |
| Ảnh trùng | `product_images.phash` (đã index); hash dùng chung > 8 ad = ảnh stock, bỏ |
| Ảnh giống | `product_images.embedding_vec` CLIP + pgvector HNSW (fallback ma trận RAM của `visual.embed`) |
| Tên | `entity_resolution.title_similarity`, `naming.best / absorb` |
| Gộp / tách / hoàn tác | `merge_products`, `split_ads`, sự kiện `PRODUCT_MERGED` / `PRODUCT_SPLIT` — chỉ bổ sung cannot-link |

Mã: `services/clustering.py` (bộ máy), `cluster_jobs.py` (CLI + worker), `routers/clusters.py` (API), bảng mới `product_links`.

## Quyết định
Mỗi họ bằng chứng cộng một trọng số, điểm cặp = 1 − Π(1 − w): landing .85 · cùng nội dung .85 · ảnh hash .70 (cùng page .90) · CLIP .30–.70 (≥ .97 → .60; nhiều cặp ảnh độc lập / cùng page cộng thêm) · tên .45–.75.
- **auto** (điểm ≥ 0.93 và không rào chắn nào): gộp ngay, ghi sự kiện để hoàn tác.
- **pending** (0.60–0.93, hoặc có rào *mềm*): MKT xác nhận. Rào mềm: tên khác nhau, khác ngành hàng, giá lệch > 3×, cụm gộp > 40 ad, cụm "chưa xác định" ≥ 8 ad, **mơ hồ** (một sản phẩm khớp nhiều sản phẩm khác nhau — shop front).
- **blocked** (rào *cứng*: MKT đã bấm "không phải", đã tách / hoàn tác, hai tên MKT khác nhau): không bao giờ đề xuất.
- Một cặp bị từ chối / tách / hoàn tác là **cannot-link vĩnh viễn**. Sản phẩm giữ lại là sản phẩm có tên tốt hơn (VERIFIED > AUTO_CONFIRMED > SUGGESTED > chưa có tên), rồi do MKT sở hữu, rồi nhiều ad hơn.
- Tên: bình chọn theo bằng chứng của các ad trong cụm — tên hai page khác nhau cùng nêu = AUTO_CONFIRMED; một ad chỉ giữ độ chính xác của nguồn của nó; không có bằng chứng thì không đặt tên. Sản phẩm chưa có tên mà có cặp chờ duyệt với sản phẩm đã có tên nhận **gợi ý** ("Sản phẩm giống") để MKT xác nhận, không bị ghi thành tên.

## Đếm lại sau khi gom
`rescore_products` chạy cho cụm vừa gộp → `active_ads`, `advertiser_count`, Demand, Competition (saturation), Opp Score v2.
- **Nhà quảng cáo** = số page khác nhau (`page_id`, nếu không có thì advertiser row). Cùng một page lưu dưới 2 advertiser row không còn tính 2 (hiện 53 page, 12 sản phẩm bị ảnh hưởng).
- **Quảng cáo**: `active_ads` = số dòng ad; `unique_ads` = số quảng cáo khác nhau (cùng page + cùng nội dung chạy ở nhiều nước chỉ tính 1; trên DB hiện tại 12.896 dòng → 11.174 ad khác nhau, −13%). Demand và Competition dùng `unique_ads`; bảng xếp hạng hiện cả hai.
- Chỉ sản phẩm đã gộp được tính lại ngay; phần còn lại nhận số mới ở lượt re-score 6 giờ của worker.

## Báo cáo trước / sau (DB thật, 2026-10-14)
Trước = ảnh chụp ngay trước lượt gộp tự động; sau = DB hiện tại (13.648 ad ở cả hai thời điểm). Chi tiết: `apps/api/data/cluster_report_*.json`.

| | Trước | Sau | Δ |
|---|---:|---:|---:|
| Số sản phẩm | 10.071 | 9.985 | −86 |
| Sản phẩm chỉ có 1 ad | 8.941 (88,8%) | 8.798 (88,1%) | −143 |
| Sản phẩm chưa có tên | 6.796 | 6.711 | −85 |
| Trong thị trường mục tiêu: sản phẩm | 7.998 | 7.913 | −85 |
| Trong thị trường mục tiêu: 1 ad | 7.019 (87,8%) | 6.878 (86,9%) | −141 |
| Trong thị trường mục tiêu: chưa có tên | 6.650 | 6.566 | −84 |
| Cặp chờ MKT xác nhận | 0 | 357 | +357 |

- 500 cặp có bằng chứng chung trên toàn kho; 135 đủ chắc để tự gộp, 86 đã gộp (81 cặp chưa-có-tên + chưa-có-tên, 4 cặp chưa-có-tên vào sản phẩm đã có tên, 1 cặp trùng tên). Phần còn lại bị chặn trong cùng lượt (sản phẩm vừa gộp) và sẽ được xét lại ở lượt sau.
- **Cụm gộp nhầm phát hiện được: 10** (ví dụ "Zodiac Sign Bracelet" 20 ad lẫn "12 Zodiac Signs Coin Necklace" 4 ad; "Back Brace Posture Corrector" 9 ad lẫn "Knee Brace" 1 ad) — hiện ở tab *Nghi gộp nhầm*, MKT tách bằng **Tách**; không tự tách. Độ chính xác chưa đo: vài cụm là hai cách đặt tên của cùng sản phẩm.
- **Gộp nhầm bị ngăn: 48 cặp** điểm ≥ 0.93 (cùng trang + cùng nội dung + ảnh trùng) nhưng tên / ngành hàng / giá khác nhau hoặc khớp nhiều sản phẩm — ví dụ "Button-up Dress" ↔ "Women's Sandals", "3 X 120mm Fan" ↔ "Led Light Up Sneakers". Quy tắc cũ chỉ dựa landing + hash sẽ gộp chúng; giờ chúng ở hàng chờ MKT.

## Giới hạn, nói thẳng
- Gom cụm chỉ giảm được ~1% tỷ lệ single-ad: 88% sản phẩm đơn lẻ vì ảnh / trang / nội dung của chúng thật sự không trùng ai. Muốn giảm thêm phải thu thập thêm ad hoặc nới ngưỡng CLIP (rủi ro gộp nhầm), chưa làm.
- Hai con số trong yêu cầu (82% single-ad, 491 chưa có tên) không tái hiện được trên DB hiện tại: 88,8% và 6.796 (6.650 trong thị trường mục tiêu; 1.218 nếu loại SKIP). Có thể lấy từ ảnh chụp khác.
- 81/86 lượt gộp tự động là cặp hai sản phẩm chưa có tên khớp ảnh hash + CLIP ≥ 0.97 giữa các advertiser. Rủi ro thấp (không có tên để sai) nhưng chưa có nhãn MKT để đo precision; `accepted` / `rejected` của `product_links` chính là nhãn để hiệu chỉnh trọng số sau này.
- Trọng số và ngưỡng chọn tay, hiệu chỉnh bằng cách đọc cặp mẫu trên DB này.

## Vận hành
```
python -m app.cluster_jobs                # mô phỏng: gộp trong transaction, in trước/sau, rollback
python -m app.cluster_jobs --queue-only   # chỉ ghi hàng chờ MKT
python -m app.cluster_jobs --apply        # gộp tự động thật + rescore
python -m app.cluster_jobs --undo-auto    # hoàn tác mọi lượt gộp tự động
```
Worker chạy mỗi `CLUSTER_EVERY_MIN` (mặc định 180, 0 = tắt). Ngưỡng: `CLUSTER_AUTO_AT` (0.93), `CLUSTER_REVIEW_AT` (0.60), `CLUSTER_CLIP_MIN` (0.90), `CLUSTER_MAX_ADS` (40), `CLUSTER_MAX_AUTO` (1000 / lượt).
Hoàn tác từng lượt: trang sản phẩm đích → "Gộp / hoàn tác", dialog Gộp/Tách ở Xếp hạng sản phẩm, hoặc `/clusters` → *Đã tự động gộp* / *MKT đã gộp*.
