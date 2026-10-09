# ToolSpy — Thiết kế 1688 Live Search Hybrid (Local First, Apify Fallback)

## 1. Mục tiêu

Cho phép MKT tìm sản phẩm mới trên **1688.com** bằng từ khóa (ví dụ `lucky bracelet`), ưu tiên **connector local Python + Playwright** để giảm phụ thuộc credits Apify. Chỉ gọi Apify khi local không đáp ứng yêu cầu **và** ngân sách được cho phép. Dữ liệu trả về được chuẩn hóa, chống trùng và lưu vào database ToolSpy.

> **Lưu ý:** Đây là thiết kế triển khai, **chưa xác minh** rằng 1688 cho phép crawl ổn định từ máy/mạng hiện tại. Không cam kết lấy được mọi sản phẩm miễn phí. Tuân thủ điều khoản website; không tự vượt CAPTCHA, xác thực hay các cơ chế hạn chế truy cập.

## 2. Phân biệt hai hành động trên UI

- **Lọc**: chỉ lọc sản phẩm **đã lưu** trong database, không phát sinh phí Apify.
- **Tìm mới trên 1688**: tạo một job tìm kiếm live, lấy dữ liệu từ 1688.com bằng Playwright trước; nếu thất bại hoặc thiếu trường quan trọng thì mới cân nhắc Apify.

Nếu muốn giữ **một nút Lọc**: trả kết quả database ngay; tùy cấu hình, tạo job tìm kiếm mới ở background và cập nhật UI qua polling/SSE. Không tự crawl lại khi mỗi lần đổi bộ lọc; dùng TTL theo từ khóa và khóa chống job trùng.

## 3. Nguồn dữ liệu thực tế

### Nguồn chính: 1688.com trực tiếp

1. Nhận từ khóa `lucky bracelet`.
2. Chuẩn hóa và bổ sung từ khóa tiếng Trung `幸运手链` (có thể tìm cả hai; không mặc định bản dịch luôn chính xác).
3. Dùng **Playwright** điều khiển Edge/Chrome cài sẵn trên Windows để mở **trang tìm kiếm công khai của 1688** bằng URL được xác minh từ phiên trình duyệt thật. **Không hardcode endpoint chưa kiểm chứng**.
4. Đợi trang hiển thị sản phẩm; kiểm tra đăng nhập, CAPTCHA, chuyển hướng, lỗi HTTP, kết quả rỗng.
5. Đọc DOM và dữ liệu có cấu trúc công khai do trang hiển thị; có thể khảo sát phản hồi mạng do chính phiên trình duyệt tạo ra để xây adapter đọc dữ liệu công khai nếu phù hợp quyền truy cập.
6. Trích xuất `product_id`, `title`, `product_url`, `image_url`, `price_min_cny`, `price_max_cny`, `moq`, `supplier_name`, `shop_url`, `sales_count`, `video_url` **chỉ khi có dữ liệu thật**. Không suy đoán trường thiếu.
7. Chuẩn hóa, chống trùng, lưu DB, trả kết quả cho frontend.

**Không có API miễn phí chính thức nào được bảo đảm trong thiết kế này.** Nguồn chính là **website 1688 hiển thị trong trình duyệt local**, không phải Apify.

### Nguồn phụ: Apify Actor 1688 hiện có

- Gọi đúng Actor ID và input schema đang dùng trong ToolSpy.
- Chỉ gọi khi local `blocked`, `login_required`, `timeout`, `insufficient_data` hoặc không tìm thấy kết quả đáng tin cậy.
- Trước khi chạy: kiểm tra cờ cho phép fallback, hạn mức tháng, dự toán phí mỗi run và job trùng.
- Nếu Actor hỗ trợ `max_total_charge_usd`, truyền giới hạn phí phù hợp; vẫn phải tự theo dõi ngân sách nội bộ vì không phải mọi Actor có cùng mô hình tính phí.
- Không đủ ngân sách: trả dữ liệu DB/local có sẵn và trạng thái `fallback_skipped_budget`.

## 4. Luồng xử lý

```text
MKT nhập từ khóa: lucky bracelet
         |
         v
ToolSpy FastAPI: normalize keyword
         |
         v
DB/cache: kết quả cũ còn hạn?
    | Có               | Không / yêu cầu refresh
    v                   v
Trả kết quả       Tạo search job (dedupe)
                        |
                        v
                 Local 1688 Playwright
                        |
               +--------+---------+
               |                  |
           Đủ dữ liệu      Bị chặn/thiếu dữ liệu
               |                  |
               v                  v
           Upsert DB       Kiểm tra ngân sách Apify
               |             | Có        | Không
               |             v           v
               |        Apify Actor    Trả partial
               |             |
               +------+------+ 
                      |
                 Merge + dedupe
                      |
                  Upsert DB
                      |
               UI nhận kết quả
```

## 5. Kiến trúc trong dự án

Stack hiện tại: **Python FastAPI** (`apps/api`), **Next.js 14 / React / TypeScript / Tailwind** (`apps/web`), chạy trên Windows tại `D:\ToolSpy`.

```text
apps/api/
  app/
    connectors/
      supplier_1688/
        local_playwright.py     # Mở trang tìm kiếm 1688 và lấy kết quả công khai
        parser.py               # Parser DOM/JSON, kiểm tra trường dữ liệu
        apify_adapter.py        # Tích hợp Actor Apify hiện có
        router.py               # Local-first + fallback theo ngân sách
        schemas.py              # Product, SearchRequest, SearchResult
        normalize.py            # Từ khóa, ID, URL, giá, chống trùng
    services/
      supplier_search.py        # Job orchestration + cache + persist
      supplier_budget.py        # Ngân sách Apify và ghi nhận chi phí
    routes/
      supplier_1688.py         # API cho frontend
apps/web/
  ...                          # Gắn vào trang Nguồn hàng Trung Quốc hiện có
```

**Đường dẫn module là đề xuất**: IDE cần đọc cấu trúc repo hiện tại và tái sử dụng service, DB model, worker, auth, logging đã có; tránh tạo framework thứ hai.

## 6. API đề xuất

- `GET /api/suppliers/1688/products?q=lucky%20bracelet&source=1688` — tìm trong DB.
- `POST /api/suppliers/1688/search-jobs` — tạo job live, body mẫu:

```json
{
  "keyword": "lucky bracelet",
  "translated_keywords": ["幸运手链"],
  "max_items": 30,
  "mode": "hybrid",
  "allow_paid_fallback": false
}
```

- `GET /api/suppliers/1688/search-jobs/{job_id}` — trạng thái và thống kê.
- `GET /api/suppliers/1688/search-jobs/{job_id}/results` — kết quả chuẩn hóa.
- `POST /api/suppliers/1688/import-url` — lấy chi tiết từ URL sản phẩm 1688.

Các endpoint phải được bảo vệ bằng cơ chế auth/permission hiện có; hạn chế số job đồng thời và giới hạn theo người dùng.

## 7. Schema chuẩn hóa

```json
{
  "source": "1688",
  "source_provider": "local_playwright",
  "product_id": "<id-thuc-te>",
  "product_url": "https://detail.1688.com/offer/<id>.html",
  "title": "<ten-thuc-te>",
  "image_url": null,
  "video_url": null,
  "price_min_cny": null,
  "price_max_cny": null,
  "moq": null,
  "supplier_name": null,
  "shop_url": null,
  "sales_count": null,
  "status": "partial",
  "fetched_at": "<UTC-ISO-8601>"
}
```

- Unique key: `(source, product_id)`.
- Lưu provenance theo **từng trường** nếu Apify và local trả khác nhau; không ghi đè giá mới bằng giá cũ.
- Chỉ lưu URL ảnh/video gốc và metadata trước; **không tự động tải hoặc mã hóa video**.
- Các trường thiếu giữ `null`, không điền dữ liệu giả.
- `source_provider`: `local_playwright`, `apify`, `cache`; có thể lưu bảng lịch sử thu thập riêng.

## 8. Quy tắc Smart Router

```python
async def live_search_1688(request):
    key = normalize_query(request.keyword, request.max_items)
    cached = await db.get_fresh_search(key)
    if cached and not request.force_refresh:
        return cached

    job = await jobs.get_or_create_unique(key)
    if job.already_running:
        return job

    local = await local_connector.search(request.keyword, request.max_items)
    await db.upsert_many(local.products)

    if local.is_sufficient:
        return await jobs.complete(job, local.products)

    if not request.allow_paid_fallback:
        return await jobs.complete_partial(job, local.products)

    if not await budget.reserve_estimated_cost(job):
        return await jobs.complete_partial(job, local.products, reason="budget")

    try:
        paid = await apify_adapter.search(request.keyword, request.max_items)
        merged = merge_by_product_id(local.products, paid.products)
        await db.upsert_many(merged)
        await budget.reconcile_actual_cost(job, paid.cost)
        return await jobs.complete(job, merged)
    except Exception:
        await budget.release_or_reconcile_reservation(job)
        raise
```

> Pseudocode, không phải file chạy độc lập. Cần xử lý trạng thái lỗi, retry có giới hạn, timeout, phí thực tế và idempotency theo hạ tầng hiện có.

## 9. Cấu hình `.env` đề xuất

```dotenv
SUPPLIER_1688_MODE=hybrid
SUPPLIER_1688_LOCAL_ENABLED=true
SUPPLIER_1688_CACHE_HOURS=72
SUPPLIER_1688_MAX_ITEMS_PER_JOB=30
SUPPLIER_1688_MAX_CONCURRENT_JOBS=1
SUPPLIER_1688_REQUEST_DELAY_SECONDS=3

APIFY_TOKEN=<set-in-local-env-only>
APIFY_1688_ACTOR_ID=<actual-actor-id>
APIFY_1688_AUTO_FALLBACK=false
APIFY_1688_MAX_CHARGE_PER_RUN_USD=0.20
APIFY_1688_MONTHLY_BUDGET_USD=5
```

- Không commit token vào Git.
- Delay và concurrency là cấu hình bảo vệ tài nguyên, không bảo đảm website cho phép tự động truy cập.
- Ngân sách là mức thử nghiệm do quản trị viên quyết định, không phải giá Apify.

## 10. UI trong trang “Nguồn hàng Trung Quốc”

Hiện có tab AliExpress / 1688 / Taobao, ô tìm kiếm và nút **Lọc**.

Đề xuất:

1. Giữ **Lọc** để tìm trong 987 listing hiện có (con số chỉ phản ánh ảnh chụp, không hardcode).
2. Thêm nút **Tìm mới trên 1688** bên cạnh.
3. Khi tìm mới: hiển thị `Đang kiểm tra DB` → `Đang tìm trên 1688` → `Đang lưu sản phẩm`.
4. Nếu local thất bại: thông báo `Không thể lấy thêm từ 1688 trực tiếp`; hiển thị tùy chọn **Dùng Apify (có thể phát sinh phí)** khi được phép.
5. Kết quả hiển thị nhãn `1688 · Local`, `1688 · Apify`, hoặc `Dữ liệu đã lưu` và thời điểm cập nhật.
6. Bộ lọc thị trường Philippines, Trung Đông, Mỹ... là **đánh giá mức độ phù hợp thị trường**, không phải vị trí nhà cung cấp 1688.

## 11. Kiểm thử tính khả thi — bắt buộc trước khi chọn local làm nguồn chính

- Chọn 30–50 URL sản phẩm và 5–10 từ khóa thuộc nhiều danh mục; thử bằng Edge/Chrome trên máy thực tế.
- Ghi tỷ lệ: mở được trang, có kết quả tìm kiếm, lấy được `product_id`, `title`, `image`, `price`, `moq`, `supplier`, `video`.
- Phân loại lỗi: `login_required`, `captcha_required`, `blocked`, `timeout`, `parser_changed`, `empty_results`.
- So sánh mẫu với dữ liệu Actor Apify hiện tại để xác định chất lượng.
- Nếu local chỉ lấy được thumbnail/tên nhưng thiếu giá và MOQ, chỉ dùng local làm **discovery**, còn Apify hoặc nguồn hợp lệ khác cho **enrichment**.
- Không khẳng định local thay thế Apify cho đến khi có số liệu kiểm thử.

## 12. Kế hoạch triển khai

**P0 — Không tốn thêm phí crawl:**
- Xác định model và API nút Lọc hiện tại.
- Import các Apify Dataset đã trả phí vào DB, chống trùng.
- Xây search cache và job dedupe.

**P1 — Local MVP:**
- Viết Playwright search + URL detail.
- Parser cho các trường thực sự quan sát được.
- Chạy bộ kiểm thử 30–50 URL và 5–10 từ khóa.

**P2 — Hybrid:**
- Nối Actor Apify hiện tại bằng schema input/output thực tế.
- Budget guard, reservation, fallback có xác nhận.
- Merge theo product ID và nguồn từng trường.

**P3 — MKT workflow:**
- Nút Tìm mới, job progress, lọc kết quả, nguồn và thời gian cập nhật.
- Liên kết sản phẩm 1688 với ads Meta/TikTok theo tên/ảnh, có mức độ tin cậy.

## 13. Tiêu chí nghiệm thu

- Bấm **Lọc** không gọi Apify hoặc 1688.
- Bấm **Tìm mới** ưu tiên local; log cho thấy nguồn đã được gọi.
- Nếu local đủ dữ liệu thì **không gọi Apify**.
- Nếu Apify bị tắt/hết ngân sách, không phát sinh run trả phí.
- Kết quả được lưu DB, tìm lại không cần crawl trong TTL.
- Không tạo bản ghi trùng và không tự tải/encode toàn bộ video.
- Khi bị yêu cầu đăng nhập/CAPTCHA, báo trạng thái rõ ràng; không tự vượt hạn chế.

## 14. Thông tin cần IDE kiểm tra trước khi code

1. Đường dẫn thực tế của API và component trang “Nguồn hàng Trung Quốc”.
2. Database/schema hiện tại cho supplier products và search jobs.
3. Actor Apify 1688 ID, input schema, output schema và pricing đang dùng.
4. Có worker/queue sẵn hay cần bổ sung job runner tối thiểu.
5. Các URL 1688 thật để kiểm thử trong phiên trình duyệt hợp lệ.

**Nguyên tắc cuối:** `1688.com (Local Playwright) → DB` là nguồn chính **nếu kiểm thử thành công**; `Apify Actor → DB` là fallback theo ngân sách; `DB → UI` là nguồn trả kết quả nhanh nhất.
