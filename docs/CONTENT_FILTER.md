# Content Relevance Filter — chặn Ads phim / drama / giải trí

Rules `ent-2026.10.13` · code: `apps/api/app/services/content_filter.py` · dọn dữ liệu: `apps/api/app/purge_entertainment.py`

## Chạy ở đâu
`ingest_ads()` gọi `content_filter.screen()` **trước** raw batch, LLM, Product Cluster và creative — chung cho Meta, TikTok,
extension, `/api/ingest/ads` và mọi connector. Đường CSV/raw cũ (`normalize_pending`) cũng lọc. Marketplace listings
(channel=commerce) không bị lọc. Chỉ dùng metadata: không tải video, không gọi model, 0 request mạng (~0,7 ms/ad).

## Quyết định
| Verdict | Khi nào | Hệ quả |
|---|---|---|
| block | bằng chứng mạnh (app drama, host streaming/podcast, app id giải trí) hoặc ≥2 tín hiệu yếu, **và không có bằng chứng bán hàng** | không lưu; ghi `content_exclusions` |
| review | 1 tín hiệu yếu, hoặc bằng chứng mạnh nhưng có dấu hiệu bán hàng | **vẫn lưu**; vào hàng đợi người duyệt |
| keep | còn lại | bình thường |

Bằng chứng bán hàng (giá, link trang sản phẩm/shop, CTA mua/nhắn tin, từ như projector/máy chiếu/COD/freeship…) luôn thắng
từ khóa phim. Tín hiệu yếu: tên advertiser có drama/movie/phim…, cụm từ "continue watching / full episodes / phim bộ /
مسلسل / 短剧 / 視聴を続ける…", tropes ("billionaire's bride"), page/advertiser từng bị block. CTA watch/install chỉ cộng điểm
khi đã có tín hiệu nội dung (shop cũng dùng WATCH_MORE).

## Không quay lại
`content_exclusions` lưu (source, source_ad_id, page_id, advertiser, host, lý do, rules_version), cho **mọi provider** từng
báo ad đó. Lần đồng bộ sau: id đã block → bỏ, không phân loại lại. Quyết định của người (`allow`/`block`) thắng luật mãi mãi.

## Lệnh
```
python -m app.purge_entertainment              # dry run + CSV mọi verdict (data/cleanup/)
python -m app.purge_entertainment --apply      # backup JSON (data/backups/) → kiểm tra backup → exclusions → xóa → re-score
python -m app.purge_entertainment --reviews    # hàng đợi review
python -m app.purge_entertainment --allow ID…  # ad hợp lệ, không bao giờ gắn cờ lại
python -m app.purge_entertainment --block ID…  # xác nhận giải trí → loại + xóa
```
Xóa: ads, ad_sources, creatives, product_images, product_matches, eu_ad_matches, cluster chỉ còn ad giải trí (+ alias,
snapshot, events…), advertiser không còn ad/store. File media chỉ xóa khi không ad/ảnh nào khác dùng, và **sau** khi DB commit.

## Lần dọn 2026-10-13 (DB local)
- 12 408 ads → 301 block (đã xóa), 26 review (giữ). 1 034 creatives, 999 ảnh index, 323 file (158,7 MB video + thumbnail),
  153 product cluster rỗng, 87 advertiser. Backup: `data/backups/entertainment-20261013-044112.json.gz`.
- Kiểm tra tay toàn bộ 301 ad bị block: 0 ad sản phẩm (ReelShort/ShortTV/ShortMax/FlareFlow/NetShort/DramaBox/Melolo…,
  podcast Spotify, Paramount+, 5 ad "xem drama trên Shopee"). Review 26 = 17 drama chưa đủ bằng chứng + 9 ad hợp lệ.
- Re-score 6 909 sản phẩm: Hidden Winners 1199 → 1199, top Opp Score không đổi.
- Chưa làm: OCR/CLIP thumbnail (CLIP local chỉ có vision tower, không zero-shot được); OCR text có sẵn trên creative đã được dùng.
