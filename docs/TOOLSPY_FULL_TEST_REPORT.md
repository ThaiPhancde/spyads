# ToolSpy — Báo cáo kiểm thử toàn hệ thống (2026-10-14)

Kiểm thử **thực tế** trên stack thật: PostgreSQL 18 (`toolspy`, 14.180 ads / 10.470 sản phẩm), API FastAPI chạy riêng ở :8010 (scheduler tắt), web Next.js ở :3100 trỏ vào API đó, worker thật của bạn (:8000 + `app.worker`) vẫn chạy song song. Nguồn dữ liệu được gọi thật với request nhỏ nhất đủ chứng minh. Bằng chứng thô (JSON, log, ảnh) ở `apps/api/data/audit/`.

Không chạy load test, không crawl hàng loạt, không mua credits, không xoá dữ liệu thật. Mọi thao tác ghi khi test (vote, đổi tên, theo dõi đối thủ) đã được hoàn tác; vote test bị xoá lại.

## 1. Tổng kết

| | Số lượng |
|---|---|
| Chức năng đã đánh giá | **69** |
| PASS (gồm 5 mục FAIL → đã sửa → PASS) | **44** (64 %) |
| PARTIAL | 9 |
| FAIL (còn lại) | 4 |
| BLOCKED | 8 |
| NOT TESTED | 2 |
| MOCK ONLY | 2 |
| Bug phát hiện | **10** |
| Bug đã sửa (kèm regression test) | **6** |
| Test tự động sau khi sửa | 234 pytest + 6 script tự kiểm: tất cả pass; `tsc` sạch |

Chi phí API của **riêng đợt audit này**: Bright Data MCP 3 request tính phí (search 1, scrape 1, search timeout 1; free tier 5.000/tháng) · SerpApi Shopping 1 search (free plan 250/tháng) · Apify 2 actor run (1688 + Taobao; tổng tháng $2,10 / ngân sách $10) · Gemini 1 lệnh (bị 429, không tính) · request sai credentials / hết hạn mức: bị từ chối, không tính. Ước tính < $0,30. Meter thật SerpApi: 19/250 đã dùng trong tháng.

## 2. Bảng chi tiết

| Module | Chức năng | Kết quả | Nguyên nhân lỗi / ghi chú | Đã sửa | Token/Chi phí |
|---|---|---|---|---|---|
| Meta Ads | Ad Library public — tìm "posture corrector" PH | PASS | 10 ads, 9/10 đúng từ khoá, ảnh tải được; nguồn không có like/comment/giá (hiện N/A) | — | free |
| Meta Ads | Live search từ UI/API (job 101) | PASS | 19 ads → 10 sản phẩm, 3 sản phẩm lệch từ khoá bị ẩn; 0 ad mới (dedupe đúng) | — | free |
| Meta Ads | Meta Graph API chính thức (EU/UK) | BLOCKED | thiếu `access_token` | — | — |
| Meta Ads | Apify Facebook Ads | BLOCKED | connector chưa cấu hình actor/token | — | — |
| Meta Ads | "Meta Ads — tài khoản công ty" (connector #4) | FAIL | không có adapter `meta_ads` → health check ném `KeyError` | NO | — |
| TikTok | Creative Center Top Ads (đăng nhập) PH | PASS | 11 ads, đủ like/comment/share; không có ngày chạy | — | free |
| TikTok | Creative Center headless | PASS | 30 top ads PH có like; không hỗ trợ từ khoá (đúng thiết kế, không dùng cho live search) | — | free |
| TikTok | Ad Library EU/UK | PASS | GB: 12 ads, 12/12 đúng từ khoá; PH không hỗ trợ (đúng) | — | free |
| TikTok | Commercial Content API (official) | PARTIAL | GB: 10 ads nhưng `title` rỗng — API trả rỗng (đã xem raw); không hỗ trợ PH | NO (giới hạn nguồn) | free |
| TikTok | Apify TikTok Top Ads / Ad Library | NOT TESTED | connector đang tắt (trả phí, trùng nguồn free) | — | — |
| Snapchat | Ads Library theo brand | PASS | "Nike" US: 10 ads | — | free |
| Pipiads / Minea | Nhập file export | MOCK ONLY | không có file export thật; importer chỉ chạy trên dữ liệu test | — | — |
| Pipiads | API doanh nghiệp | BLOCKED | chưa có url/mapping | — | — |
| BigSpy | Tích hợp | FAIL | không tồn tại trong repo | NO | — |
| AliExpress | Tìm listing + giá + đã bán | PASS | 10 listing, giá USD 10/10, đã bán 6/10, ảnh tải được (với header của app) | — | free |
| 1688 | API chính thức (AK) | PASS | 5 listing, giá CNY, tiêu đề TQ đúng sản phẩm; trạng thái "error" lưu trong DB là lỗi tạm thời cũ | — | free |
| 1688 / Taobao | Apify actors | PASS | 1688: 3 listing (69 s); Taobao: 9 listing (limit 3 → trả 9, actor không tôn trọng limit) | — | 2 run, trong $2,10/tháng |
| SerpApi | Google Lens (web image search) | PASS | job 16/18: 58–59 kết quả với `type=all`; key sai → 401, không tính | sửa ở đợt trước | 2 search (đợt trước) |
| SerpApi | Google Shopping | PARTIAL | 40 offer thật; query rộng → giá lệch > 6× → không lưu giá (chủ động chống giá ảo); link Google bị đọc như trang shop | YES (bỏ link google) | 1 search |
| Bright Data | Search engine (tìm giá) | PASS | ₱65 đọc từ JSON-LD của medsforall.com.ph (VERIFIED_MARKETPLACE) | — | 1 request |
| Bright Data | scrape_as_markdown | PARTIAL | đọc được trang nhưng giá từ markdown ($75) khác JSON-LD ($40) cùng trang → nếu dùng thì là giá tham khảo sai | NO (chỉ dùng khi trang chặn đọc trực tiếp, độ tin cậy 0,5) | 1 request |
| Bright Data | Posts (Facebook page posts → engagement) | PASS | page DavinesPh: 20 bài thật, khớp quảng cáo không gán nhầm | (đợt trước) | 1 request (~$0,014) |
| Bright Data | Posts TikTok | PARTIAL | snapshot chưa xong sau ~5 phút | NO | 1 request |
| Fallback | Bright Data hết hạn mức → SerpApi Shopping | PASS | thật: BD bị từ chối, Shopping trả 40 offer | — | (gộp ở trên) |
| Fallback | SerpApi tắt → Bright Data | PASS | thật: BD search + đọc trang shop → giá xác minh | — | (gộp ở trên) |
| Lỗi nguồn | Sai credentials (SerpApi, BD MCP, BD REST) | PASS | 401 cả ba, không tính quota | — | 0 |
| Lỗi nguồn | Timeout Bright Data | PASS | mã `timeout` sau 1 s, ladder đi tiếp | — | 1 request |
| LLM | Gemini (đặt tên, hiểu từ khoá, agent) | BLOCKED | free tier hết lượt hôm nay (429) | — | 0 |
| LLM | Claude | BLOCKED | không có `ANTHROPIC_API_KEY` | — | — |
| LLM | Fallback khi LLM chết | PASS | agent vẫn trả lời bằng rules; tìm kiếm dùng từ điển | YES (bug 1) | 0 |
| Tìm kiếm | Từ khoá trong kho | PASS | "posture corrector" 60 sản phẩm, 0,7 s | — | free |
| Tìm kiếm | Đa từ khoá | PASS | "posture corrector \| knee brace": 112 | — | free |
| Tìm kiếm | Đa ngôn ngữ (vi, ar) | PASS | "kính mắt" 36, "نظارة" 36, "vòng tay phong thủy" 38 — đều đúng sản phẩm | — | free |
| Tìm kiếm | Từ khoá lạ khi Gemini hết lượt | FAIL→PASS | `/api/ads?q=posture` treo 19,7 s | YES | 0 |
| Tìm kiếm | Lọc quốc gia PH, mạng, funnel | PASS | `/api/ads?country=PH&network=tiktok` | — | free |
| Tìm kiếm | Nhóm OFW | FAIL | không có khái niệm OFW trong code | NO (cần định nghĩa nghiệp vụ) | — |
| Tìm kiếm | Giữ trạng thái khi refresh | FAIL→PASS | từ khoá mất sau F5 | YES | — |
| Ảnh | Tìm bằng ảnh trong kho (hash + CLIP) | PASS | ảnh của ad tự tìm lại chính nó, EXACT 1.0, 0,9 s | — | free |
| Sản phẩm | Nhận diện + đặt tên | PARTIAL | tên theo bằng chứng chạy; 6.758 sản phẩm chưa có tên; đặt tên bằng LLM đang chết (quota) | — | — |
| Sản phẩm | Gom cụm | PASS | 104 tự gộp, 343 chờ MKT, 9 cụm nghi lẫn | — | free |
| Sản phẩm | Đổi tên / hoàn tác | PASS | đổi → hoàn tác về đúng "Initial Necklace" | — | — |
| Sản phẩm | Chấm điểm / xếp hạng / hidden winners / radar | PASS | trả dữ liệu; lần đầu 3–6 s, sau cache < 30 ms | — | — |
| Giá | Giá trên card / trang sản phẩm | PASS | hiện giá đối thủ, nguồn hàng, theo advertiser | — | — |
| Tương tác | Like/Comment/Share/View trên card quảng cáo | PASS | TikTok: số thật; Meta: N/A | — | — |
| Tương tác | Tổng tương tác trên trang sản phẩm | FAIL→PASS | hiện "0 / 0 · 0 share" cho sản phẩm không có số liệu | YES | — |
| Đối thủ | Danh sách + chi tiết | PASS | | — | — |
| Đối thủ | Theo dõi advertiser | FAIL→PASS | không hiện trong danh sách theo dõi tới 60 s | YES | — |
| Đối thủ | Loại advertiser không mong muốn | PARTIAL | chỉ có bộ lọc nội dung tự động (drama/phim) + ô "Loại trừ" theo từ khoá; không có nút chặn một advertiser | NO | — |
| MKT | Vote LOVE/TEST theo từng người | PASS | 2 tài khoản vote từ UI, lưu tách riêng | — | — |
| MKT | Lưu quảng cáo / yêu thích / đánh dấu đã test | PARTIAL | dùng vote sản phẩm (LOVE/TEST) + lifecycle; không có lưu / yêu thích từng quảng cáo | NO | — |
| MKT | Cá nhân hoá nguồn / đề xuất theo MKT | PARTIAL | có lọc theo team (Mess/Ladi) và taste model, nhưng taste model chung cho cả team, không theo từng người | NO | — |
| Bảo mật | Đăng nhập / phiên / phân quyền từng MKT | FAIL | không có đăng nhập; tên MKT tự khai trong trình duyệt; ai mở được web là đọc hết và (qua middleware) ghi được | NO (thay đổi kiến trúc) | — |
| Bảo mật | Ghi cần admin token | PASS | không token / sai token → 401 | — | — |
| Bảo mật | Token extension / ingest | PASS | sai → 401 | — | — |
| Chrome Extension | Allegro bridge → backend | MOCK ONLY | endpoint + token test thật; chạy extension trên allegro.pl cần thao tác tay | — | — |
| EU Market | Đọc listing từ URL (JSON-LD) | PASS | gymshark: $40, 5,4 s | — | free |
| EU Market | Trang tổng quan, sản phẩm, trend, rare, shops… | PASS | 20 GET đều 200 | — | free |
| EU Market | Kaufland / bol / BigBuy | BLOCKED | thiếu credentials; `/sources` vẫn ghi "available" | NO | — |
| EU Market | eMAG, OTTO, Cdiscount… | BLOCKED | `access_restricted` (không có API công khai) | — | — |
| EU Market | eBay / Amazon / Etsy / idealo | NOT TESTED | đã bị gỡ theo yêu cầu trước đây | — | — |
| Dữ liệu | Chống trùng / cập nhật | PASS | 0 id trùng trong mọi lần lấy; live search 19 ads, 0 bản ghi mới thừa | — | — |
| Dữ liệu | Còn nguyên sau khởi động lại API | PASS | restart :8010 hai lần; vote, tên, lịch sử còn | — | — |
| Hạ tầng | PostgreSQL | PASS | | — | — |
| Hạ tầng | Cloudflare D1 / R2 | NOT TESTED / BLOCKED | D1 không cấu hình (DB là PostgreSQL); R2 có khoá nhưng `STORAGE_BACKEND=local` | — | — |
| Worker | Giá, chỉ mục ảnh, đặt tên, gom cụm, tương tác | PASS | log worker thật: prices 4/6 có giá, image index chạy; đặt tên báo "llm failed" (Gemini) | — | — |
| Worker | `/api/collector/scheduler` | PARTIAL | báo trạng thái của process API, không phải worker (luôn "0001-01-01") | NO | — |
| UI | 27 trang | PASS | không lỗi runtime / 5xx; dev-mode lần đầu 3–22 s (biên dịch) | — | — |
| UI | Luồng MKT: tìm → lọc → chi tiết → giá/tương tác → link nguồn | PASS | xem `shots/flow_*.png` | — | — |
| Hiệu năng | `/api/meta` (gọi ở mọi trang) | FAIL→PASS | 2–3 s → 26 ms | YES | — |

## 3. Bug đã phát hiện

| # | Bug | Nguyên nhân gốc | Sửa |
|---|---|---|---|
| 1 | `/api/ads?q=` treo ~20 s | `query_intent` gọi Gemini 3 lần thử lại ngay trong request khi bị 429; lỗi không được nhớ | 1 lần thử, timeout 8 s, không hỏi lại trong 10 phút + test |
| 2 | Mọi trang chậm 2–3 s | `/api/meta` nạp toàn bộ 10k sản phẩm để tính một danh sách nước rồi ghi đè ngay | bỏ truy vấn thừa |
| 3 | Theo dõi đối thủ không hiện ngay | danh sách cache 60 s không bị xoá khi theo dõi | xoá cache khi theo dõi + test |
| 4 | Connector `meta_ads` báo `KeyError` | có bản ghi connector nhưng không có adapter | chưa sửa |
| 5 | Shopping đọc link Google như trang shop | `product_link` của Google Shopping là trang google.com | bỏ qua host không phải shop + test |
| 6 | Trang sản phẩm "0 / 0 · 0 share" | API cộng `None` thành 0 | `null` → N/A, thêm `ads_reporting` |
| 7 | Mất từ khoá khi refresh trang tìm kiếm | trạng thái chỉ nằm trong biến module | sessionStorage theo tab |
| 8 | Giá markdown Bright Data sai | trích giá từ markdown lấy nhầm số ($75 vs $40) | chưa sửa (chỉ là giá tham khảo, conf 0,5) |
| 9 | `/eu-market/sources` báo "available" khi thiếu khoá | trạng thái tĩnh, không phản ánh credentials | chưa sửa |
| 10 | Scheduler endpoint sai process | trạng thái scheduler nằm trong RAM của worker | chưa sửa |

Sửa ở: `services/llm.py`, `services/query_intent.py`, `main.py`, `pricing/router.py`, `web/app/products/[id]/page.tsx`, `web/app/search/page.tsx`; regression test trong `tests/test_query_intent.py`, `tests/test_engagement.py`, `tests/test_price_fixes.py`.

## 4. Còn lỗi / thiếu
- **Không có đăng nhập và phân quyền từng MKT** — rủi ro lớn nhất, nhất là khi mở web qua tunnel công khai.
- Không có: nhóm OFW, nút chặn advertiser, lưu/yêu thích từng quảng cáo, đề xuất theo từng MKT, tích hợp BigSpy, adapter `meta_ads`.
- Phụ thuộc LLM đang chết (Gemini hết lượt, không có Claude) → 6.758 sản phẩm chưa có tên vẫn đứng yên.
- TikTok Commercial API không trả nội dung ad; TikTok Posts snapshot chậm.

## 5. Đề xuất
1. Thêm đăng nhập (ít nhất Cloudflare Access hoặc tài khoản đơn giản + vai trò), thay `mi_user` tự khai bằng danh tính thật.
2. Thêm khoá Claude hoặc nâng gói Gemini, hoặc giảm `NAMING_LLM_DAILY` để không đốt lượt vào 429.
3. Định nghĩa "OFW" (quốc gia đích + ngôn ngữ Tagalog?) để thêm bộ lọc thật.
4. Thêm danh sách chặn advertiser (bảng `ContentExclusion` đã có, chỉ thiếu API/UI).
5. Sửa trích giá markdown (ưu tiên JSON-LD trong markdown, bỏ giá sản phẩm gợi ý).
6. Hiển thị trạng thái credentials thật cho nguồn EU; thêm endpoint trạng thái worker.
