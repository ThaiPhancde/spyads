# AI / LLM miễn phí trong ToolSpy

Kiểm tra ngày 03/11/2026 trên nhánh `llm-free-providers` (tách từ `main` @ `98d85c1`). Mọi con số hạn mức dưới đây lấy từ tài liệu chính thức của nhà cung cấp và từ header rate-limit trả về khi gọi thật. Con số nào chỉ có ở nguồn thứ ba thì được ghi rõ là chưa kiểm chứng.

## 1. AI đang làm gì trong ToolSpy

| Chức năng | File chính | Vào → Ra | Chạy khi nào | Fallback khi không có model |
|---|---|---|---|---|
| Đặt tên sản phẩm UNKNOWN (theo lô 10–30) | `naming_ai.py`, `naming_jobs.py` | caption, title, URL, OCR → tên SP + bằng chứng (JSON strict) | worker, mỗi 3 phút (`NAMING_EVERY_MIN`) | rule pass (slug, listing, JSON-LD, OCR), SP giữ UNKNOWN chờ lượt sau |
| Đặt tên lúc ingest (ad mới, không phải batch Meta) | `ingest.py` → `enrichment.extract_products` | caption → tên SP | mỗi lần ingest | `naming.best` (rule) |
| Hiểu từ khoá mọi ngôn ngữ | `services/query_intent.py` | từ khoá → biến thể vi/en/tl/ar/zh… | ô tìm kiếm, một lần/từ khoá rồi lưu vĩnh viễn | từ điển `lexicon.py` |
| AI Agent | `services/agent.py` | câu hỏi → *query spec* (JSON) → **code** truy vấn DB → tóm tắt | trang `/agent` | parser regex + câu trả lời mẫu |
| Giải thích điểm | `agent.explain_product` | điểm do công thức tính → lời giải thích | trang sản phẩm | câu giải thích mẫu |
| Phân tích comment (ABSA) | `enrichment.analyze_comments` | comment → cảm xúc theo khía cạnh | đồng bộ comment | rule vi/en |
| OCR thumbnail | `creative_enrich.py` | ảnh ≤480px → chữ trên ảnh | worker, mỗi 10 phút, chỉ ad TEST/TEST_NOW caption quá mỏng | Tesseract cục bộ |
| Trích lời video | `creative_enrich.py` | clip ≤6 MB, gửi nguyên file → lời nói | như trên, chỉ khi OCR vẫn chưa đủ | bỏ qua, lượt sau thử lại |
| Dịch tên sang tiếng Việt | `naming_jobs.translate` | tên SP → tên tiếng Việt | **chạy tay**: `backfill_names --translate N` | không |

**Không phải LLM nên không bị thay đổi:** CLIP/embedding ảnh (`visual/embed.py`, onnxruntime cục bộ), pHash và fingerprint video, Google Lens qua SerpApi (`visual/lens.py`), Bright Data / SerpApi (giá), mọi connector thu thập quảng cáo.

**AI không bao giờ chấm điểm.** Mọi điểm TEST/TEST_NOW do code tính; LLM chỉ chuyển dữ liệu thô thành dữ liệu có cấu trúc và diễn giải con số có sẵn.

### Điều kiểm tra được trên DB thật trước khi sửa

- **Groq báo lỗi 400 ở 108/792 lần gọi (14%).** Nguyên nhân: chỉ cần một sản phẩm trong lô có `category` nằm ngoài danh sách cho phép (một trường không ai dùng) là cả lô bị từ chối. Mỗi lần như vậy cả 20–30 sản phẩm bị +1 lần thử, và Groq bị khoá 60 giây cho mọi chức năng. Kết quả: **594 ad Meta kẹt vĩnh viễn ở trạng thái "lỗi 3 lần"**.
- **Hiểu từ khoá hầu như chưa từng chạy được** (chỉ có 1 bản ghi, và bản ghi đó thất bại). Schema của nó không hợp lệ ở chế độ strict, nên Groq từ chối ngay (`additionalProperties:false must be set on every object`). Schema của AI Agent cũng mắc lỗi tương tự.
- **Cerebras (gpt-oss-120b, 1M token/ngày) không được gọi lại kể từ ngày 14/10.** Số đếm "còn 0 request trong phút" lưu từ hôm đó không bao giờ được làm mới, vì không có lần gọi nào để nhận header mới.
- **Claude:** chưa cấu hình key, chưa có lần gọi nào, lại là dịch vụ trả phí.
- **Các hạn mức trong ngày được đếm bằng biến trong bộ nhớ** (query_intent, OCR, dịch tên): mỗi lần khởi động lại worker là được cấp lại từ đầu.
- **`.env` có `CLOUDFLARE_AI_MODEL=…llama-3.1-8b` nhưng code đọc `CF_AI_MODEL`**, nên dòng đó không có tác dụng. Thực tế Cloudflare vẫn chạy gpt-oss-20b.

## 2. So sánh các API miễn phí

| Provider · model | Hạn mức miễn phí (nguồn) | JSON schema | Tiếng Việt / batch | Đa phương thức | Thẻ / phí |
|---|---|---|---|---|---|
| **Cerebras · gpt-oss-120b** | 5 req/phút · 30k token/phút · **1M token/ngày** ([docs](https://inference-docs.cerebras.ai/support/rate-limits)); header thật: 2 400 req/ngày | strict ✅ (đã test) | ✅ / lô 20 ad trong 1,5 s | không (văn bản) | không cần thẻ; gói Free Trial |
| **Cerebras · qwen-3.8-27b** | 5 req/phút · 1M token/ngày · 2 ảnh/request, 10 MiB (docs như trên) | ✅ | ✅ | **ảnh** ✅ (OCR đã test) | như trên |
| **Groq · openai/gpt-oss-120b** | 30 req/phút · 1 000 req/ngày · 8k token/phút · **200k token/ngày, tính riêng từng model** ([docs](https://console.groq.com/docs/rate-limits)) | strict ✅ | ✅ / 4 s | không | không cần thẻ |
| **Groq · openai/gpt-oss-20b** | như trên, một hạn mức 200k riêng | strict ✅, đôi khi trả JSON sai schema (đã xử lý) | ✅ / 1,6 s, đặt tên "dễ dãi" hơn | không | không cần thẻ |
| **Groq · qwen/qwen3.8-27b** | 30/phút · 1 000/ngày · 200k token/ngày (docs như trên) | ✅ | ✅ | **ảnh** ✅ (OCR đã test) | không cần thẻ |
| **Groq · whisper-large-v3-turbo** | 20 req/phút · 2 000 req/ngày · **28 800 giây audio/ngày**; file ≤25 MB, nhận mp4/webm/mp3/wav ([docs](https://console.groq.com/docs/speech-to-text)) | — | đa ngôn ngữ | **audio/video** ✅ (đã test) | không cần thẻ |
| Cloudflare Workers AI · @cf/openai/gpt-oss-20b | **10 000 neurons/ngày**, reset 00:00 UTC; gói Free hết hạn mức thì báo lỗi, không tính phí; gói Workers Paid tính 0,011 USD/1 000 neurons ([docs](https://developers.cloudflare.com/workers-ai/platform/pricing/)) | không nhận `response_format`, đưa schema vào prompt | chậm (20 s/lô) | có model vision/whisper (chưa tích hợp) | cùng account R2. **Nếu account là Workers Paid, vượt hạn mức sẽ bị tính phí**; app tự dừng ở 9 500 neurons/ngày |
| Google Gemini · gemini-3.6-flash / 3.5-flash-lite | Google **không còn công bố** hạn mức gói Free; xem tại trang AI Studio ([docs](https://ai.google.dev/gemini-api/docs/rate-limits)). Đo được 20 req/ngày với flash (13/10); một báo cáo trên forum ghi 500/ngày với 3.5-flash-lite (**chưa kiểm chứng**) | ✅ | ✅ | ảnh + video + audio | không cần thẻ; dữ liệu gói Free có thể được dùng để cải thiện sản phẩm |
| OpenRouter `:free` | 20 req/phút; **50 req/ngày** nếu chưa từng nạp ≥10 credit ([docs](https://openrouter.ai/docs/api-reference/limits)) | tuỳ model | — | tuỳ model | quá ít cho ToolSpy, không tích hợp |
| Mistral (gói Experiment) | Mistral không công bố con số; nguồn thứ ba ghi khoảng 1 req/giây và 1 tỷ token/tháng (**chưa kiểm chứng**) | ✅ | ✅ | có | phải xác minh số điện thoại và đồng ý cho dùng dữ liệu để huấn luyện. Chưa tích hợp; ghi lại làm phương án sau |

*Hạn mức token khác với độ dài context:* các con số trên là số token được dùng mỗi phút hoặc mỗi ngày, không phải độ dài một request. Một lô đặt tên 20 ad tốn khoảng 4–6k token.

## 3. Phương án đã chọn và đã tích hợp

Có ba chuỗi; mỗi chuỗi thử lần lượt đến khi một provider trả lời được:

```
Văn bản   LLM_PROVIDERS        cerebras → groq:openai/gpt-oss-120b → groq:openai/gpt-oss-20b → cloudflare → gemini
Ảnh       VISION_PROVIDERS     groq:qwen/qwen3.8-27b → cerebras:qwen-3.8-27b → (Tesseract cục bộ) → gemini
Lời nói   TRANSCRIBE_PROVIDERS groq:whisper-large-v3-turbo → gemini
```

- **Cerebras đứng đầu** vì có hạn mức ngày lớn nhất (1M token) và đặt tên chặt nhất. Trên 20 ad của bộ nhãn tay (2 sản phẩm thật), Cerebras đặt tên 3/20 và bắt đúng cả hai; gpt-oss-20b đặt tên 17/20, kể cả dịch vụ.
- **Groq có hai model văn bản**, mỗi model là một hạn mức 200k token riêng. Cú pháp `provider:model` cho phép dùng cả hai mà không cần thêm code.
- **Tổng hạn mức văn bản miễn phí** khoảng 1,4M token/ngày (1M + 200k + 200k), chưa kể Cloudflare và Gemini. Như vậy đủ khoảng 250–300 lô đặt tên (5 000–6 000 sản phẩm) mỗi ngày.
- **OCR:** model qwen đọc ảnh quảng cáo tốt hơn Tesseract. Trên cùng một thumbnail, qwen đọc ra "…shiny as DR. PHIL'S HEAD", Tesseract đọc ra "DR. iEAD". Tesseract vẫn được giữ làm dự phòng cục bộ, không tốn quota.
- **Trích lời:** Whisper nhận nguyên file mp4/webm, không re-encode, không tải hàng loạt. Gemini vẫn là dự phòng, và đọc thêm được phụ đề in trên hình.

### AI Agent: giữ hay thay bằng nền tảng mới?

| Phương án | Ưu | Nhược | Kết luận |
|---|---|---|---|
| **1. Giữ Agent hiện tại, thay model phía sau** | Model chỉ dịch câu hỏi thành *spec*; **code** mới là bên truy vấn DB, nên không có SQL tự do, không bịa số. Không thêm dịch vụ, chạy trong cùng chuỗi miễn phí (đã test với Cerebras, Groq và Cloudflare) | Chỉ 5 loại câu hỏi đã định nghĩa | **Chọn** |
| Groq Compound (agent có sẵn web search và chạy code) | Không cần tự viết | **Đã ngừng**, tài liệu chính thức ghi rõ request tới `groq/compound` không còn được hỗ trợ ([docs](https://console.groq.com/docs/compound/systems/compound)) | Loại |
| Dify Cloud | Giao diện kéo thả | Gói Sandbox chỉ có 200 lượt cho cả đời tài khoản (nhiều nguồn thứ ba; trang giá chính thức không đọc được đầy đủ). Muốn tự chạy thì phải dựng Docker, Redis, Postgres, và vẫn phải gọi API model | Loại |
| n8n tự chạy (node AI Agent) | Miễn phí cho dùng nội bộ (giấy phép Sustainable Use) | Thêm một server; vẫn cần API model; dữ liệu phải đi qua webhook | Loại |
| Google ADK / LangGraph (thư viện) | Mã nguồn mở, miễn phí | Chỉ là lớp điều phối; hạn mức vẫn do API model quyết định; thêm dependency cho một việc 5 intent đang làm tốt | Không cần lúc này |

**Không nền tảng Agent nào cho thêm hạn mức model miễn phí**, vì hạn mức nằm ở API model. Vì vậy giữ Agent hiện tại là lựa chọn rẻ nhất và an toàn nhất. Nếu sau này cần Agent tự gọi nhiều công cụ liên tiếp, gpt-oss-120b trên Cerebras/Groq có hỗ trợ tool calling, và có thể thêm vào `agent.py` mà không cần nền tảng mới.

## 4. Những gì đã sửa

**Đã gỡ:**
- Toàn bộ đường gọi Claude (`_claude`, `_create`, `_claude_json`, `CLAUDE_MODEL`) và dependency `anthropic` trong `requirements.txt`.
- `llm.media_text`, thay bằng `read_image` và `transcribe`.
- Các bộ đếm trong bộ nhớ (`_spent`) ở `query_intent`, `naming_jobs` và `creative_enrich`.
- Trường `category` không dùng trong schema đặt tên.
- Các nhãn engine `"claude"` gắn sai trong Agent và phân tích comment.

**Đã giữ nguyên:**
- CLIP, tìm kiếm ảnh, Lens, connector, dữ liệu, lịch sử usage.
- Cloudflare: biến `R2_*` dùng chung account, không đổi.
- Gemini, chuyển xuống làm dự phòng cuối.
- Tesseract.
- Mọi fallback bằng quy tắc.

**Đã thêm hoặc sửa:**
- `llm_providers.py`:
  - Mục dạng `provider:model`, loại trùng khi hai mục cùng một model.
  - `strict()` tự chuyển mọi schema sang dạng strict hợp lệ.
  - Gửi ảnh cho model vision; thêm `transcribe()` cho Whisper.
  - Lỗi 400/413/422 không còn khoá provider.
  - Lỗi 401/403/404 khoá 30 phút.
  - Số đếm hạn mức cũ hơn chu kỳ reset của chính nó thì bị bỏ qua.
  - Mỗi lần gọi ghi `kind` (tên chức năng) vào sổ.
  - `spent_today()` đếm theo sổ.
  - Các phiên chỉ đọc không còn chờ khoá ghi của SQLite.
- `llm.py`: ba chuỗi. Gemini được ghi vào sổ usage, cooldown lưu chung trong DB, và có trần `GEMINI_DAILY` cho mọi chức năng.
- `naming_ai.py`:
  - Khi mọi provider đều sập, sản phẩm không bị trừ lượt thử.
  - Bảng trạng thái hiện cả provider OCR và trích lời (cột `roles`).
- `creative_enrich.py`: thứ tự OCR mới; hạn mức `CREATIVE_LLM_DAILY` đếm theo sổ, mặc định 300.
- `app/llm_migrate.py`: nới cột `provider` lên 64 ký tự. Migration này cộng thêm, chạy lại được nhiều lần, tự chạy khi khởi động API và worker.
- `.env.example` (phần AI), trang Agent (dòng gợi ý), `docs/DATA_SOURCES_AND_DEPLOY.md`.

### Kiểm tra

| Kiểm tra | Kết quả |
|---|---|
| Toàn bộ `pytest tests` (SQLite) | **417 passed, 3 skipped** |
| Test LLM trên PostgreSQL (`toolspy_test_meta`) | 69 passed |
| `tests/test_llm_free_providers.py` (mới, 18 test, dùng mock) | strict schema · mục `provider:model` · lỗi 400 schema không khoá provider · key sai khoá 30 phút · số đếm cũ không khoá vĩnh viễn · không key thì dùng rules · lỗi mạng chuyển sang provider sau · Agent từ câu hỏi ra spec rồi ra dữ liệu DB · hạn mức sau khởi động lại · trần Gemini · OCR vision · thứ tự OCR · Whisper, fallback Gemini khi gặp 429 · lô thất bại không trừ lượt thử |
| `tsc --noEmit` (web) | 0 lỗi |
| **Gọi thật bằng key**, mỗi provider một lần | đặt tên theo lô ✅ cả 4 provider văn bản · hiểu từ khoá ✅ Cerebras và cả 2 model Groq (Cloudflare chậm quá timeout 8 s của ô tìm kiếm, tự fallback) · Agent ✅ Cerebras, Groq 120b, Cloudflare (Groq 20b trả JSON sai schema, tự fallback về rules) · OCR ✅ qwen trên Groq và Cerebras · Whisper ✅ |

### Giới hạn còn lại

- **Chưa khởi động lại API/worker thật**, nên hệ thống đang chạy vẫn dùng code cũ cho tới khi khởi động lại.
- **594 ad đang kẹt ở trạng thái "lỗi 3 lần"** từ đợt lỗi 400 trước. Code mới không làm chúng tự chạy lại; cần đặt `naming_attempts` về 0 (chưa làm, vì là thay đổi dữ liệu).
- **Whisper chỉ nghe lời nói, không đọc phụ đề in trên hình**, và có thể "nghe nhầm" vài câu khi video không có tiếng. Gemini vẫn là dự phòng.
- **Chất lượng đặt tên mới đo trên 20 ad.** Bộ lọc `no_product_noun` vẫn chặn nhiều tên đúng; đây là việc riêng, xem `PRODUCT_NAMING.md`.
- **Hạn mức Gemini Free không còn được Google công bố**; `GEMINI_DAILY=20` là giới hạn an toàn.
- **Script cũ `apps/api/test_scoring_rules.py` ở thư mục gốc kiểm tra cấu trúc `llm.status()` cũ.** Nó đã sai từ trước, vì hàm cũ cũng trả thêm khoá `providers`.

## 5. Hướng dẫn cho người mới

### Bước 1 — Đăng ký và lấy key (đều miễn phí, không cần thẻ)

| Key | Ở đâu | Làm gì |
|---|---|---|
| `CEREBRAS_API_KEY` (**chính**) | https://cloud.cerebras.ai | Đăng ký bằng Google hoặc email → mục **API Keys** → **Generate API Key** → copy chuỗi `csk-…` |
| `GROQ_API_KEY` (**chính**: văn bản, OCR, trích lời) | https://console.groq.com/keys | Đăng ký → **Create API Key** → copy chuỗi `gsk_…` (chỉ hiện một lần). Hạn mức thật của tài khoản xem ở https://console.groq.com/settings/limits |
| `CLOUDFLARE_API_TOKEN` (dự phòng) | https://dash.cloudflare.com/profile/api-tokens | **Create Token** → mẫu **Workers AI** → copy token. `CLOUDFLARE_ACCOUNT_ID` lấy ở trang tổng quan account (trùng `R2_ACCOUNT_ID`). Nên dùng gói **Free** để vượt hạn mức chỉ báo lỗi, không tính tiền |
| `GEMINI_API_KEY` (dự phòng cuối) | https://aistudio.google.com/apikey | **Create API key**. Hạn mức xem trong AI Studio |

Không gửi key qua chat và không commit key. Chỉ ghi key vào file `.env` (đã nằm trong `.gitignore`) hoặc vào biến môi trường của server.

### Bước 2 — Điền vào `.env` (Windows, PowerShell)

```powershell
cd D:\ToolSpy
if (-not (Test-Path .env)) { Copy-Item .env.example .env }   # máy mới: tạo từ mẫu
notepad .env
```

Điền hoặc sửa các dòng sau (ví dụ):

```
CEREBRAS_API_KEY=csk-...
GROQ_API_KEY=gsk_...
LLM_PROVIDERS=cerebras,groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b,cloudflare,gemini
```

> Trên máy này, dòng `LLM_PROVIDERS` cũ (`groq,cerebras,cloudflare,gemini,claude`) **đã được đổi** sang dòng trên; bản sao lưu nằm trong thư mục scratchpad của phiên làm việc. Dòng `CLOUDFLARE_AI_MODEL=` không có tác dụng (code đọc `CF_AI_MODEL`), có thể xoá.

### Bước 3 — Kiểm tra kết nối trước khi khởi động lại

```powershell
cd D:\ToolSpy\apps\api
.\.venv\Scripts\python.exe -c "from app.services import llm; import json; print(json.dumps(llm.status(), indent=1, default=str))"
.\.venv\Scripts\python.exe -c "from app.services import llm; print(llm.complete_text('Reply with one word.', 'Say OK', max_tokens=50), llm.last_provider)"
```

Kết quả đúng: `"available": true`, các provider đã có key hiện `"ready": true`, và lệnh thứ hai in ra `OK {'name': 'cerebras'}` (hoặc tên provider đầu tiên còn hạn mức).

### Bước 4 — Khởi động lại API + worker

Migration chạy tự động khi khởi động lại. Cách đơn giản nhất: dừng tiến trình `python` của API/worker trong Task Manager, rồi chạy

```powershell
cd D:\ToolSpy
.\start.ps1
```

Sau đó mở `http://localhost:3000/naming`. Bảng **AI provider** liệt kê cả provider OCR và trích lời, kèm số request và token đã dùng hôm nay, đọc từ sổ usage.

### Trên server Linux (VPS)

```bash
cd /path/to/ToolSpy
nano .env        # thêm CEREBRAS_API_KEY, GROQ_API_KEY, LLM_PROVIDERS như trên
cd apps/api && .venv/bin/python -c "from app.services import llm; print(llm.complete_text('Reply with one word.', 'Say OK', max_tokens=50), llm.last_provider)"
# khởi động lại theo cách server đang chạy, ví dụ: sudo systemctl restart toolspy-api toolspy-worker
```

Nếu chạy Docker hoặc PaaS, khai báo cùng các biến trên ở phần environment của **cả API lẫn worker**, rồi deploy lại. Repo chưa có file systemd/compose nào, nên tên dịch vụ ở trên chỉ là ví dụ.

### Khi có sự cố

- `status()` báo `cooldown until …`: provider đang bị giới hạn tốc độ, sẽ tự quay lại sau mốc giờ đó. Trong lúc chờ, các provider sau trong chuỗi trả lời thay.
- Báo `HTTP 401` trong `last_error`: key sai hoặc đã bị thu hồi. Tạo key mới; provider sẽ bị khoá 30 phút rồi tự thử lại.
- Không còn provider nào: app chạy bằng quy tắc. Đặt tên dừng ở UNKNOWN và chờ lượt sau, nhưng tìm kiếm, chấm điểm và Agent vẫn hoạt động.
