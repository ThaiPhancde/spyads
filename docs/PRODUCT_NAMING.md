# Đặt tên sản phẩm & Tìm Ads đa ngôn ngữ — báo cáo 2026-10-13

Phạm vi: DB local (Postgres `toolspy`). Server/VPS **chưa** chạy backfill — xem §8.

## 1. Kết quả chính

| Chỉ số | Trước | Sau |
|---|---|---|
| Sản phẩm mang tên placeholder (“… (chưa đặt tên)”, “Unknown…”, “… creative”) | 1.476 | 1 |
| Cụm “Unknown (chưa đặt tên)” gom ads không liên quan | 5 cụm / 1.546 ads | 0 |
| Sản phẩm có tên **đã kiểm chứng** từ listing / trang sản phẩm (AUTO_CONFIRMED) | 0 (không có trạng thái) | 1.896 |
| Tên **đề xuất**, cần MKT xác nhận (SUGGESTED) | — | 832 |
| Chưa đủ bằng chứng → hiển thị “Chưa xác định sản phẩm” (UNKNOWN) | — | 6.022 (5.892 trong thị trường mục tiêu) |
| Ads bị mất / ads không có sản phẩm | — | 0 / 0 (12.107 → 12.109, worker ingest thêm 2) |
| Recall tìm kiếm (20 truy vấn, 6 ý định, vi/en/tl/ar/zh) | 0,27 | **0,94** |
| Precision@10 (20 truy vấn, ô trống tính là sai) | 0,24 | **0,60** |
| Tìm theo từ khoá (HTTP, 36 kết quả) | ~2,5 s (chưa mở rộng) | 0,8 s (đã mở rộng đa ngôn ngữ) |
| Liệt kê không từ khoá | ~6 s | 2,7 s |
| Lượt gọi AI dùng cho backfill | — | 0 (chỉ luật + dữ liệu sẵn có) |

“vòng tay phong thủy” trước đây trả **0** sản phẩm / 0 ads; giờ trả 213 sản phẩm / 663 ads, gồm Feng Shui Bracelet, Pixiu Bracelet, Lucky Bracelet.

## 2. Audit — nguyên nhân gốc

1. **Tên dự phòng = tên shop.** `ingest.guess_product_name` trả `"{advertiser} (chưa đặt tên)"` khi không đoán được → 1.404 sản phẩm mang tên shop/advertiser (“TikTok Shop (Chưa Đặt Tên)”, “Alibaba.com (chưa đặt tên)”).
2. **Không có advertiser → một cụm chung.** TikTok Top Ads thường thiếu `brand_name` → mọi ad không tên rơi vào “Unknown (chưa đặt tên)”: cụm lớn nhất 938 ads của hàng trăm sản phẩm khác nhau (đầm, nước hoa, bút cảm ứng…) → “0 advertiser nhưng nhiều ads”.
3. **Listing bị loại oan.** Tiêu đề AliExpress bắt đầu bằng “New …” bị luật CTA (`^new\b`) loại → listing có tên sản phẩm hoàn hảo vẫn thành “Unknown”.
4. **Tên = từ khoá tìm kiếm.** Mapper TikTok gán `product_name = query` → mọi ad tìm theo “fengshui” gom vào sản phẩm “Fengshui” (116 ads, 43 advertiser).
5. **Tên cũ phần lớn là caption.** Đo trên mẫu: chỉ **10/40** tên cũ (không chứa danh từ sản phẩm) là tên sản phẩm thật (“Tiktokshop Foryou Fyp”, “Born To Lead”, “Free Shipping Free Returns”…). TEST_NOW duy nhất trước đây là cụm “Cash On Delivery & Free Delivery” gồm 4 ads không liên quan.
6. **Tìm kiếm chỉ khớp chữ.** Không có từ đồng nghĩa / bản dịch; tiếng Việt bị tách theo âm tiết (“nước hoa” khớp mọi ad có “nước” và “hoa” — ghế massage, ấm trà).
7. Sẵn có để tái dùng: pHash ảnh (81k ảnh), fingerprint creative, variation key, Gemini (free). **Không có**: OCR / transcript (0/90k creative), CLIP text encoder (model chỉ có nhánh ảnh) → không làm tầng “semantic” bằng embedding văn bản.

## 3. Đặt tên theo bằng chứng (`app/services/naming.py`)

Thứ tự nguồn (nguồn đầu tiên đạt chuẩn thắng):

| Nguồn | Ví dụ | Độ chính xác đo được* | Trạng thái |
|---|---|---|---|
| `listing` — tiêu đề listing AliExpress/1688/Taobao (bỏ “2024 New Hot Sale Wholesale…”) | Lucky Fortune Transfer Dragon Bracelets… | 40/40 = 1,00 | AUTO_CONFIRMED |
| `slug` — URL trang sản phẩm (`/products/…`, Amazon `/…/dp/`, Shopee `…-i.x.y`) | Neck Massager Pro | 72/80 = 0,90 | AUTO_CONFIRMED |
| `adapter` / `ad_title` — headline/caption sau khi bỏ CTA, giá, hashtag, emoji, câu có đại từ/động từ; **bắt buộc chứa danh từ sản phẩm** | Universal Stylus Pen | 26/40 = 0,65 (không có cổng danh từ: 6/40) | SUGGESTED |
| `llm` — Gemini, chỉ giữ khi **dùng chữ của chính ad** (chống bịa) | Portable Turbo Fan | đặt = 0,65 đến khi có mẫu riêng | SUGGESTED |
| `legacy` — tên cũ có danh từ sản phẩm | — | 24/40 = 0,60 | SUGGESTED |
| `manual` — MKT đặt / xác nhận | — | 1,00 | VERIFIED |

\* Nhãn tay trên ads thật: `apps/api/scripts/naming_labels.json`; chạy lại `python scripts/eval_naming.py score --labels`. Độ tin cậy = độ chính xác đo được, không phải xác suất AI tự khai. Hai nguồn độc lập cùng ra một tên → nâng lên AUTO_CONFIRMED.

Không bao giờ là tên: advertiser/shop, nền tảng (TikTok Shop, Shopee, Temu, Alibaba…), CTA/khuyến mãi (Order Now, Flash Sale, Mua ngay, اطلب الآن). Không có bằng chứng → `UNKNOWN`, thẻ ghi “Chưa xác định sản phẩm”.

**Cột mới (additive, nullable — `auto_migrate`)**: `products.name_vi, name_status, name_source, name_confidence, name_evidence, name_history, manual_override`; `ads.ad_type, name_source`. `canonical_name` vẫn là khoá gom cụm (thường là tên gốc / tiếng Anh); `name_vi` là tên MKT đọc (thẻ ưu tiên `name_vi`). Tên gốc khác nằm trong `product_aliases` (original names).

**Phân loại ad** (`ads.ad_type`, chỉ luật): PRODUCT_AD 3.951 · SERVICE_AD 1.431 · MULTI_PRODUCT_AD 439 · BRAND_AD 213 · UNKNOWN 6.075 (CONTENT_AD đã bị bộ lọc giải trí chặn từ ingest).

## 4. Gom cụm

- Theo **danh tính**, không theo tên shop: cùng trang sản phẩm (host+path), cùng copy + page + link (fingerprint, copy ≥ 20 ký tự), cùng ảnh (pHash; hash dùng chung > 8 ads coi là khung stock, bỏ qua). Link mạng xã hội / chat / app store / trang chủ shop / collection **không** được dùng để gom (đó là advertiser).
- Ad UNKNOWN không bao giờ khớp mờ theo tên; mỗi nhóm danh tính là một sản phẩm. Nếu danh tính trỏ đúng một sản phẩm đã có tên → nhập vào đó.
- Tên một từ / đúng một từ khoá (“Fengshui”, “Massage Gun”, “Back Brace”) trên nhiều shop → coi là cụm-từ-khoá, tách lại từng ad (9 cụm, 201 ads).
- Tên MKT (VERIFIED) bị khoá: crawler chỉ thêm bằng chứng, không đổi tên. Nguồn tốt hơn chỉ nâng tên SUGGESTED/UNKNOWN.
- Sửa lỗi kèm theo: `merge_products` trước đây không chuyển `creatives`/`votes` → xoá sản phẩm nguồn lỗi khoá ngoại trên Postgres; gộp xong còn chấm lại **toàn bộ** catalogue (`score_all`) — nay chỉ chấm sản phẩm đích.
- 53 cụm nghi vấn (≥ 3 tên bằng chứng khác nhau) liệt kê trong `apps/api/data/backfill_names_apply.json` để MKT xem (vd. Instant Posture Corrector 143 ads, Korean Silk Collagen Ampoule 47, Cross Bracelet 46). Không tự tách — dùng công cụ Tách cụm.

## 5. Product Card & thao tác MKT

- Thẻ hiển thị riêng: **Tên sản phẩm** (+ trạng thái, nguồn tên), **Advertiser** (+N), **Nguồn** (mạng), **Danh mục**; UNKNOWN in nghiêng “Chưa xác định sản phẩm” kèm gợi ý tên cũ nếu có.
- ✎ Sửa nhanh (tên tiếng Việt + tên gốc) → VERIFIED, khoá; báo nếu trùng tên sản phẩm khác (gợi ý gộp, không tự gộp). ✓ Xác nhận tên đề xuất. ↶ Hoàn tác (lịch sử 20 lần). ⓘ Bằng chứng (nguồn + ad id). “Tương tự” → tìm bằng ảnh (tái dùng `/visual`, chạy khi bấm — không tốn CPU trên mỗi lượt xem).
- Trang sản phẩm → tab Overview → “Tên & cụm sản phẩm”: bằng chứng, lịch sử tên, bảng mọi ad của cụm (tên theo bằng chứng của từng ad, loại ad), **Tách** ads đã chọn, **Gộp vào** sản phẩm khác, **Hoàn tác** gộp/tách.
- Bộ lọc “Chưa xác định sản phẩm (cần đặt tên)” / “Tên đề xuất” / “Đã xác nhận” ở trang Tìm sản phẩm (`name_status=` trên `/api/search`, `/api/products`).
- API: `PATCH /api/products/{id}/name`, `POST …/name/confirm`, `POST …/name/undo`, `POST …/split`, `GET …/merges`, `POST …/merges/{event}/undo`.

## 6. Tìm kiếm đa ngôn ngữ (`app/services/lexicon.py`, `/api/search`, `/api/ads`)

- Từ điển ~85 khái niệm (trang sức & phong thủy, làm đẹp, sức khoẻ, gia dụng, gadget, thời trang, thú cưng) × vi / en / tl / ar / zh + khái niệm liên quan.
- Thứ hạng: **Exact → Synonym (bản dịch/đồng nghĩa) → Related (liên quan)**; tầng Semantic bằng embedding văn bản **không có** (model ảnh không có nhánh text) — dùng “Tương tự” bằng ảnh.
- Mở rộng có giới hạn: tầng Related chỉ thêm khi Exact+Synonym < 30 kết quả; từ ngắn (“ring”) phải đứng đầu từ (không khớp “spring”); tiếng Việt khớp cả cụm (“nước hoa”). Nhiều từ khoá: `a | b`, xếp theo số từ khoá khớp thật (không tính khớp Related).
- Khớp đồng thời: nội dung ad / headline / tên bằng chứng của ad, tên chuẩn, `name_vi`, aliases (tên gốc).
- Internal vs External: tìm trong kho mở rộng mặc định (tắt bằng ô “Đa ngôn ngữ / đồng nghĩa”); quét nguồn ngoài **không** mở rộng trừ khi bật “Quét thêm từ khoá tiếng Anh tương đương” (≤ 2 từ khoá thêm / từ, trần 10 — mỗi từ là một lượt quét mọi nguồn).

Kết quả đo (`apps/api/scripts/eval_search.py`, nhãn tay `scripts/search_eval.json`, quy tắc nhãn `scripts/label_search_20261013.py`):

| Truy vấn | Recall trước → sau | P@10 trước → sau |
|---|---|---|
| vòng tay phong thủy / 风水手链 | 0,00 → 1,00 | 0 → 7 |
| feng shui bracelet | 1,00 → 1,00 | 7 → 7 |
| pulseras swerte / سوار حظ (= *lucky* bracelet) | 0,00 → 0,84 | 0 → 6 |
| đai chống gù / مصحح القوام | 0,00 → 0,89 | 0 → 6 |
| posture corrector | 0,67 → 0,89 | 8 → 8 |
| kem chống nắng | 0,00 → 1,00 | 3 → 6 |
| sunscreen / واقي شمس | 0,96 → 1,00 / 0,00 → 1,00 | 5 → 5 / 0 → 3 |
| súng massage / مسدس مساج / massage gun | 0,00 → 1,00 / 1,00 → 1,00 | 0 → 6 / 6 → 6 |
| nước hoa / pabango / عطر / perfume | 0,00 → 1,00 / 0,00 → 1,00 / 0,09 → 1,00 / 0,80 → 1,00 | 0 → 6 / 0 → 6 / 9 → 9 / 7 → 7 |
| làm trắng răng / teeth whitening | 0,00 → 0,46 / 0,92 → 1,00 | 0 → 4 / 3 → 3 |

Recall = tỉ lệ “pool” (sản phẩm mà từ khoá tiếng Anh gốc tìm ra theo cách khớp cũ) được trả về. Truy vấn tiếng Anh không giảm ở chỉ số nào.

## 7. Opp Score v2

`discovery.evidence()["unnamed"]` nay đọc `name_status == UNKNOWN` (trước: regex trên chuỗi tên) — sản phẩm chưa xác định có tín hiệu mạnh vẫn bị chặn ở WATCH (trần 55), đúng như thiết kế cũ.

| | Trước | Sau |
|---|---|---|
| TEST_NOW / TEST / WATCH / SKIP | 1 / 202 / 1.101 / 5.604 | 0 / 132 / 1.219 / 7.398 |
| Opportunity TB (trong thị trường mục tiêu) | 29,51 | 28,85 |

Trong 203 sản phẩm TEST/TEST_NOW cũ: 72 giữ nguyên tên và TEST; phần còn lại là cụm tên CTA / shop / dịch vụ / caption (CareCredit financing, Linktree, “Turn Your Ideas Into Profits”, “Cash On Delivery & Free Delivery”…) — bị tách thành sản phẩm thật hoặc “Chưa xác định”. Cụm thật bị tách nhầm (vd. ReliefWave Pro 46 ads) đã được gom lại theo danh tính. Số TEST sẽ tăng lại khi pass AI đặt tên cho các nhóm UNKNOWN.

## 8. Còn lại / giới hạn

- **Quota AI**: Gemini free = **20 request/ngày/model** (hết từ trưa 13/10). Vì vậy `name_vi` (tên tiếng Việt) = 0 và 5.892 sản phẩm trong thị trường mục tiêu còn UNKNOWN. Worker tự chạy (`NAMING_EVERY_MIN=3`, tối đa `NAMING_LLM_DAILY=12` lượt/ngày, chừa phần còn lại cho ingest): ~300 sản phẩm UNKNOWN + ~400 tên tiếng Việt / ngày → ~3 tuần. Có `ANTHROPIC_API_KEY` thì `llm.py` tự dùng Claude: toàn bộ ~240 lượt đặt tên + ~30 lượt dịch ≈ **$0,25** với Claude Haiku 5.5 ($0,10 / $0,50 mỗi 1M token; cần bỏ tham số `fallbacks` trong `llm.py` khi dùng Haiku) hoặc ≈ $9 với model mặc định hiện tại (`claude-opus-5-5`).
- Pass AI chưa có mẫu nhãn riêng — gắn độ tin cậy bằng ad_title (0,65) và luôn SUGGESTED; đo lại khi đã chạy được vài trăm.
- Không OCR / không phân tích video (không có dữ liệu sẵn, và spec cấm FFmpeg hàng loạt). Không fetch JSON-LD landing ở ingest (tốn mạng) — slug URL đã phủ phần lớn.
- Từ điển ~85 khái niệm: thêm một dòng khi một truy vấn bị hụt (`lexicon.py`).
- Backfill mới chạy trên DB local; server cần: pg_dump → `python -m app.backfill_names --apply` → `--recheck-generic --apply` → khởi động lại API + worker.
- DB phụ `toolspy_pre_naming` (bản khôi phục bảng products/ads trước backfill, dùng để so sánh) có thể xoá khi không cần.

## 9. Vận hành

```
cd apps/api
python -m app.backfill_names                     # dry run
python -m app.backfill_names --apply             # backup bảng bak_naming_* → các pha → chấm lại
python -m app.backfill_names --recheck-generic --apply
python -m app.backfill_names --regroup --apply   # gom lại ads UNKNOWN theo danh tính
python -m app.backfill_names --llm 100           # pass AI cho UNKNOWN (theo quota)
python -m app.backfill_names --translate 400     # tên tiếng Việt
python -m app.backfill_names --rollback          # trả tên + liên kết ad→sản phẩm từ bak_naming_*
python scripts/eval_naming.py coverage | sample | score --labels
python scripts/eval_search.py [score]
pytest tests/test_naming.py
```

Checkpoint = chính các cột (`ads.name_source`, `products.name_status IS NULL`): chạy lại là tiếp tục; sản phẩm lỗi được rollback riêng, ghi `backfill_names.log`, thử lại ở lần chạy sau. Backup đầy đủ trước backfill: `apps/api/data/backups/toolspy-20261013-pre-naming.dump` (`pg_restore`). Rollback bằng bảng `bak_naming_*` không tạo lại các sản phẩm bucket đã bị xoá — dùng pg_dump nếu cần trả nguyên trạng.

## 10. Shop-Based Naming Fallback (2026-10-13, lần 2)

**Tên tạm** — sản phẩm UNKNOWN mà ≥ 80 % ads đến từ một shop rõ ràng hiển thị “Sản phẩm từ <Shop>” (badge “tên tạm theo shop”). Chỉ là trường hiển thị (`products.shop_name` → `naming.card_fields`): `canonical_name` vẫn là “Chưa xác định sản phẩm”, `name_status` vẫn `UNKNOWN`, nên entity index (bỏ UNKNOWN), tìm theo tên sản phẩm và Opp Score (`evidence.unnamed`) **không thấy** tên shop; test hồi quy `test_provisional_shop_label_is_display_only`. Không gán cho: nền tảng/sàn (TikTok Shop, Shopee, Temu…), tên trang giống CTA, cụm đa số là quảng cáo dịch vụ/giải trí, cụm nhiều shop.

**Kế thừa tên từ cụm đã xác nhận** (`services/shop_naming.py`, `backfill_names --shop`) — chỉ SQL trên pHash + vector CLIP đã lưu, không gọi AI, không đụng video:
- Cùng shop **và** ảnh khớp (CLIP ≥ 0,95 + pHash ≤ 14, hoặc bản sao pHash ≤ 6) **và** tín hiệu độc lập thứ hai (cùng landing host+path, hoặc ≥ 4 từ caption đặc trưng, Jaccard ≥ 0,4).
- Chỉ khớp **đúng một** sản phẩm đã đặt tên của shop mới gộp; khớp ≥ 2 sản phẩm = mơ hồ → để nguyên (shop bán nhiều sản phẩm không bị gộp). Advertiser là sàn bị loại hẳn.
- Bài học đo được: chỉ dùng ảnh thì sai nhiều (shop dùng một ảnh/banner cho nhiều sản phẩm: ảnh máy trợ thính cho “Happy Drain”, banner Temu cho váy). Vòng 1 (ảnh + ≥ 3 từ chung) vẫn lọt cặp sai → siết còn 5 cặp, đã soát tay.
- Nhóm UNKNOWN ↔ UNKNOWN cùng shop được xác minh cũng gom lại (vẫn chưa có tên).

**Kết quả (DB local, trước → sau)**

| | Trước | Sau |
|---|---|---|
| UNKNOWN thật sự | 6.022 | **6.007** (−15) |
| Tên tạm “Sản phẩm từ <Shop>” | 0 | **3.024** (trong số UNKNOWN) |
| UNKNOWN không có tên tạm (không có advertiser rõ / sàn / dịch vụ / nhiều shop) | 6.022 | 2.983 |
| Sản phẩm được kế thừa tên đã xác nhận | 0 | 5 |
| Nhóm UNKNOWN gom lại | – | 10 sản phẩm → 9 nhóm |
| Ads mất | – | 0 (12.109) |
| TEST / WATCH | 132 / 1.219 | 132 / 1.217 |

Số UNKNOWN giảm ít là kết quả trung thực: nếu chỉ chấp nhận khi có hai tín hiệu độc lập thì rất ít ad đủ bằng chứng; phần còn lại cần tên từ AI (quota) hoặc MKT. Caption/hashtag đã khai thác hết ở vòng trước; thumbnail chỉ dùng được để so khớp (CLIP không có nhánh text); OCR chưa có dữ liệu (0 / 90k creative) nên không xử lý.

Vận hành: `python -m app.backfill_names --shop` (dry-run) · `--shop --apply`. Backup: `data/backups/toolspy-20261013-pre-shop.dump`, bảng `bak_shop_products` / `bak_shop_ads`. Tên tạm tự cập nhật khi ingest ad mới và khi gộp/tách (`shop_naming.refresh`).
