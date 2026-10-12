# ToolSpy Browser Bridge (Allegro)

Extension Chrome (Manifest V3) **do người dùng tự bấm**: chỉ đọc trang listing Allegro mà **bạn tự mở** trong Chrome của bạn, rồi gửi dữ liệu về API ToolSpy chạy trên máy (`127.0.0.1`).

Extension **không**: tự mở trang, tự chuyển trang/phân trang, mở tab nền, giải CAPTCHA, xuất cookie, giả fingerprint hay tạo dữ liệu giả. Không vào trang chi tiết sản phẩm → `seller` chỉ có khi thẻ sản phẩm trên listing hiển thị người bán, nếu không thì `null`.

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
Token nằm trong `D:\ToolSpy\.env`, dòng `TOOLSPY_BRIDGE_TOKEN=...` (đã được tạo ngẫu nhiên). Mở popup extension → dán giá trị đó vào ô **Token**. Token, API URL và limit được lưu trong `chrome.storage.local` của extension. Không chia sẻ token.

## 4. Sử dụng
1. Tự mở trang listing, ví dụ `https://allegro.pl/listing?string=bransoletka%20damska`, chờ trang tải xong (nếu Allegro hỏi xác minh thì bạn tự xử lý như bình thường).
2. Bấm biểu tượng extension → đặt **limit** (mặc định 5) → bấm **Extract & send**.
   - **Chỉ extract**: chỉ đọc trang, không gửi; xem JSON rồi **Copy JSON** nếu cần.
3. Popup hiện số sản phẩm và kết quả `received / added / updated / total`. Dữ liệu được gộp vào `apps\api\data\imports\allegro.json` (trùng `offer_id` thì bản mới hơn thắng).

Muốn lấy thêm sản phẩm ở trang 2? Bạn tự bấm sang trang 2 rồi bấm lại nút — extension không tự phân trang.

## 5. Lần chạy đầu: kiểm tra log
Parser **chưa được thử trên DOM thật của Allegro** (chỉ thử với file HTML mẫu tự viết ở `test/fixture_listing.html`). Trong JSON preview có mục `log`:
- `strategies`: số sản phẩm lấy từ `article` / `link` / `json`
- `articles`, `offer_links`, `json_blobs`, `json_offers`: những gì tìm thấy trên trang
- `missing.price / image_url / seller`, `unresolved_urls` (link quảng cáo không giải mã được → giữ nguyên và đánh dấu `url_unresolved: true`)
- `warnings`
Log cũng được in ra Console của tab Allegro (F12 → Console, `[ToolSpy Bridge]`). Nếu kết quả sai/thiếu, gửi phần `log` này để sửa parser.

## 6. Test
```powershell
cd D:\ToolSpy\apps\api; .\.venv\Scripts\python -m pytest -q tests\test_bridge_import.py
cd D:\ToolSpy\extensions\allegro-bridge\test; npm install --no-save jsdom@16; node parser.test.js
```
