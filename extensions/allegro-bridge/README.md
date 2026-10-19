# ToolSpy Browser Bridge (Allegro)

Extension Chrome (Manifest V3) chạy trong **Chrome của bạn**, gửi dữ liệu listing Allegro về API ToolSpy trên máy (`127.0.0.1`). Hai cách dùng:
- **Thủ công (popup)**: đọc trang listing **bạn tự mở**, khi bạn bấm nút (mục 4).
- **Từ web ToolSpy** (mục 5): web gửi từ khoá → extension mở **1 tab listing nền / 1 job**, đọc, đóng tab, gửi về API.

Extension **không**: phân trang, vào trang chi tiết sản phẩm, giải/vượt CAPTCHA hay DataDome, xuất cookie, giả fingerprint hay tạo dữ liệu giả. Server ToolSpy không bao giờ tự gọi allegro.pl. `seller` chỉ có khi thẻ sản phẩm trên listing hiển thị người bán, nếu không thì `null`.

## 1. Cài extension (Load unpacked)
1. Mở `chrome://extensions`
2. Bật **Developer mode** (góc trên bên phải)
3. Bấm **Load unpacked** → chọn thư mục `D:\ToolSpy\extensions\allegro-bridge`
4. (Tuỳ chọn) ghim biểu tượng extension lên thanh công cụ.

## 2. Chạy API
```powershell
cd D:\ToolSpy\apps\api
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Nếu API đang chạy sẵn, **khởi động lại** để nạp `TOOLSPY_BRIDGE_TOKEN` và endpoint mới.

## 3. Token
Token nằm trong `D:\ToolSpy\.env`, dòng `TOOLSPY_BRIDGE_TOKEN=...` (đã được tạo ngẫu nhiên). Mở popup extension → dán giá trị đó vào ô **Token**. Token, API URL, limit và web origin được lưu trong `chrome.storage.local` của extension. Không chia sẻ token.

## 4. Sử dụng
1. Tự mở trang listing, ví dụ `https://allegro.pl/listing?string=bransoletka%20damska`, chờ trang tải xong (nếu Allegro hỏi xác minh thì bạn tự xử lý như bình thường).
2. Bấm biểu tượng extension → đặt **limit** (mặc định 5) → bấm **Extract & send**.
   - **Chỉ extract**: chỉ đọc trang, không gửi; xem JSON rồi **Copy JSON** nếu cần.
3. Popup hiện số sản phẩm và kết quả `received / added / updated / total`. Dữ liệu được gộp vào `apps\api\data\imports\allegro.json` (trùng `offer_id` thì bản mới hơn thắng).

Muốn lấy thêm sản phẩm ở trang 2? Bạn tự bấm sang trang 2 rồi bấm lại nút — extension không tự phân trang.

## 5. Tìm từ web ToolSpy
**Web origin**: `http://localhost:3000` và `http://127.0.0.1:3000` có sẵn. Nếu mở web ở địa chỉ khác (vd `http://192.168.1.10:3000`): popup → ô **ToolSpy web origin** → **Lưu web origin** → đồng ý cấp quyền. Sau khi cài/cập nhật extension hoặc đổi origin, **tải lại trang ToolSpy** để content script (`bridge.js`) chạy.

Luồng: web gọi `POST /api/eu-market/allegro/search` (provider `bridge`) → nhận job `queued` → `window.postMessage` cho extension → extension mở `https://allegro.pl/listing?string=<keyword>` ở tab nền → chờ tải xong (tối đa 45s) → kiểm tra chặn → đọc bằng `parser.js` → đóng tab → `POST /api/imports/allegro` kèm `job_id`, `keyword`, `status` → web poll `GET /api/eu-market/allegro/jobs/{id}`.

Giao thức `postMessage` (chỉ nhận từ chính cửa sổ đó, cùng origin; trả lời với targetOrigin = `location.origin`):
| Web → extension | Extension → web |
|---|---|
| `{type:"TOOLSPY_ALLEGRO_PING"}` | `{type:"TOOLSPY_ALLEGRO_PONG", version}` — không có trả lời trong ~1.5s = chưa cài/chưa tải lại trang |
| `{type:"TOOLSPY_ALLEGRO_SEARCH", job_id, keyword, limit}` (limit 1–50, mặc định 20) | ngay: `{type:"TOOLSPY_ALLEGRO_ACK", job_id, status:"queued"}`; sau: `{type:"TOOLSPY_ALLEGRO_RESULT", job_id, status, count, error}` |

`status` của RESULT (job trên API tương ứng):
- `done` — có sản phẩm, đã gửi (job `done`). `empty` — trang tải bình thường nhưng 0 sản phẩm (gửi `status:"ok"` + 0 record → job `empty`).
- `captcha` — có iframe/script `captcha-delivery.com` (DataDome); `blocked` — có chữ "You have been blocked". Hai dấu hiệu này **chỉ tính khi trang không có link sản phẩm nào** (tránh báo nhầm khi trang thật có gắn tag DataDome). Khi đó extension **không đọc trang**, **giữ tab mở và đưa lên trước** để bạn tự xử lý, báo API (job `captcha`/`blocked`, đã kết thúc). Xử lý xong thì bấm tìm lại trên web (job mới).
- `error` — token thiếu, hết 45s tải trang, tab bị đóng, API lỗi... (job `failed`; nếu job đã quá 3 phút → API trả 409, dữ liệu vẫn được lưu).

0 sản phẩm mà không có dấu hiệu chặn được coi là tìm kiếm rỗng thật (parser không phân biệt được trang "không có kết quả" với trang lạ) — xem `parser_log.probe` trong `data/imports/allegro.json`/log API nếu nghi ngờ.

Giới hạn:
- Chrome phải đang mở, extension đã bật, đã nhập token; nên dùng Chrome bạn vẫn hay vào Allegro (DataDome tin hơn).
- Hàng đợi tuần tự: **1 job một lúc**, cách nhau **≥5s**; mỗi job chỉ trang 1 của listing. Hàng đợi nằm trong bộ nhớ service worker — nếu Chrome tắt worker giữa chừng thì job còn lại bị mất (API tự chuyển `timeout` sau 3 phút).
- DataDome vẫn có thể chặn → bạn tự giải trong tab, extension không bao giờ giải hộ.

## 6. Lần chạy đầu: kiểm tra log
Parser **chưa được thử trên DOM thật của Allegro** (chỉ thử với file HTML mẫu tự viết ở `test/fixture_listing.html`). Trong JSON preview có mục `log`:
- `strategies`: số sản phẩm lấy từ `article` / `link` / `json`
- `articles`, `offer_links`, `json_blobs`, `json_offers`: những gì tìm thấy trên trang
- `missing.price / image_url / seller`, `unresolved_urls` (link quảng cáo không giải mã được → giữ nguyên và đánh dấu `url_unresolved: true`)
- `warnings`
Log cũng được in ra Console của tab Allegro (F12 → Console, `[ToolSpy Bridge]`). Nếu kết quả sai/thiếu, gửi phần `log` này để sửa parser.

## 7. Test
```powershell
cd D:\ToolSpy\apps\api; .\.venv\Scripts\python -m pytest -q tests\test_bridge_import.py tests\test_bridge_jobs.py
cd D:\ToolSpy\extensions\allegro-bridge\test; npm install --no-save jsdom@16; node parser.test.js
cd D:\ToolSpy\extensions\allegro-bridge; node test\lib.test.js   # helper chặn/captcha, kiểm tra message, origin — không cần trình duyệt
```
