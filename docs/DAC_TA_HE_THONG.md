# ToolSpy: đặc tả chức năng hệ thống

> Ngày viết: **13/10/2026**. Viết từ code hiện tại (commit `0e1881a` + thay đổi chưa commit về Allegro) và từ dữ liệu thật đọc read-only trong Postgres `toolspy` cùng API đang chạy.
> Người đọc: MKT Mess, MKT Ladi, lead và người vận hành. Tài liệu kỹ thuật sâu hơn (ERD, API, kiến trúc) nằm ở [SYSTEM_ANALYSIS.md](SYSTEM_ANALYSIS.md).
> Quy ước trạng thái trong tài liệu: ✅ dùng được, có dữ liệu thật · ⚠️ chạy được nhưng hạn chế, dễ hiểu nhầm · ❌ chưa dùng được (không có dữ liệu, không có nguồn, hoặc bị ẩn).

---

## Mục lục

1. [Tóm tắt một trang](#1-tóm-tắt-một-trang)
2. [Bản đồ chức năng và cách phân biệt](#2-bản-đồ-chức-năng-và-cách-phân-biệt)
3. [Nguồn dữ liệu và cách kết nối](#3-nguồn-dữ-liệu-và-cách-kết-nối)
4. [Luồng hoạt động](#4-luồng-hoạt-động)
5. [Từ điển thuật ngữ và điểm số](#5-từ-điển-thuật-ngữ-và-điểm-số)
6. [Đọc một thẻ sản phẩm (ví dụ thật)](#6-đọc-một-thẻ-sản-phẩm-ví-dụ-thật)
7. [Bảng icon và ký hiệu](#7-bảng-icon-và-ký-hiệu)
8. [Bấm vào một sản phẩm: trang Chi tiết sản phẩm](#8-bấm-vào-một-sản-phẩm-trang-chi-tiết-sản-phẩm)
9. [Đặc tả từng màn hình](#9-đặc-tả-từng-màn-hình)
10. [EU Market](#10-eu-market)
11. [Tìm bằng ảnh và giá tự động](#11-tìm-bằng-ảnh-và-giá-tự-động)
12. [Công cụ thu thập ngoài web: Chrome extension](#12-công-cụ-thu-thập-ngoài-web-chrome-extension)
13. [Thực trạng dữ liệu 13/10/2026](#13-thực-trạng-dữ-liệu-13102026)
14. [Đánh giá: cái gì hữu ích, cái gì gây hiểu nhầm](#14-đánh-giá-cái-gì-hữu-ích-cái-gì-gây-hiểu-nhầm)

---

## 1. Tóm tắt một trang

**ToolSpy làm gì:** spy quảng cáo đang chạy của đối thủ trên Meta và TikTok, gom các quảng cáo cùng một món hàng thành **một sản phẩm**, đối chiếu giá nhập ở AliExpress / 1688 / Taobao và giá bán của đối thủ, rồi chấm điểm bằng công thức cố định để ra khuyến nghị **TEST_NOW / TEST / WATCH / REVIEW / SKIP**. Thị trường ưu tiên số 1 là **Philippines** (cộng 15 điểm).

**Cho ai:**

| Đội | Bán thế nào | Dùng ToolSpy để |
|---|---|---|
| MKT Mess | Ads CTA "Send Message / WhatsApp", chốt trong inbox | Lọc sản phẩm đối thủ chạy funnel **mess** |
| MKT Ladi | Ads dẫn về landing page có form COD / checkout | Lọc funnel **ladi**, xem giá bán và landing page đối thủ |

**Đã làm được (tóm gọn):**

| Nhóm | Chức năng | Trạng thái |
|---|---|---|
| Tìm sản phẩm | Tìm trong kho, quét trực tiếp Meta + TikTok + AliExpress + 1688 cùng lúc, nhiều từ khoá, loại trừ từ khoá | ✅ |
| Xếp hạng | Opportunity, demand, momentum, quadrant, khuyến nghị kèm lý do bằng số | ✅ (nhưng còn 2 hệ điểm chồng nhau, xem 14) |
| Radar | Product Radar (xu hướng 7/14/30 ngày), Opportunity Radar (scatter), Market Radar theo nước | ✅ |
| Hidden Winners | Sản phẩm ít người bán nhưng ad chạy lâu | ⚠️ 1.148 sản phẩm, quá rộng |
| Thư viện quảng cáo | Xem từng ad như Ad Library, lọc mạng / nước / funnel / đang chạy | ✅ |
| Nguồn hàng TQ | Listing AliExpress / 1688 / Taobao: giá, đã bán, đánh giá | ✅ (1.426 sản phẩm có listing) |
| Giá đối thủ | Tự tìm giá trên landing page / Google, gắn nhãn đã xác minh hay tham khảo | ⚠️ 59 giá xác minh / 258 lượt |
| Tìm bằng ảnh | Ảnh → ảnh trùng, sản phẩm giống, ads, nguồn hàng, giá; cả trong kho và trên Internet (Google Lens) | ✅ trong kho · ⚠️ Internet có hạn mức 250 lượt/tháng |
| Creative | Ảnh bìa ≤480px lưu sẵn, video phát thẳng từ nguồn | ⚠️ link video gốc hết hạn sau vài giờ |
| Vote của team | 🔥 👍 👀 😐 👎 + lý do | ⚠️ chạy được nhưng cả hệ thống mới có **2 vote** |
| Alerts | Đối thủ scale, bão hoà tăng, đổi giá, thị trường mới… | ⚠️ 265 chưa đọc, phần lớn là loại đã tắt |
| AI Agent | Hỏi bằng tiếng Việt, trả bảng số liệu từ DB | ✅ (Gemini đang bật) |
| EU Market | Listing / giá / nguồn sỉ châu Âu | ❌ gần như trống: 2 listing |
| Lifecycle sau test | WIN / FAIL / SCALE dựa trên đơn hàng thật | ❌ không có dữ liệu đơn, chuyển tay |

---

## 2. Bản đồ chức năng và cách phân biệt

### 2.1 Sidebar

| Nhóm | Màn hình | Đường dẫn | Câu hỏi nó trả lời |
|---|---|---|---|
| Khám phá sản phẩm | Tìm sản phẩm | `/search` | "Từ khoá X có sản phẩm nào đang chạy?" |
| | Tìm bằng ảnh | `/visual` | "Tôi có một tấm ảnh, ai đang bán / quảng cáo nó, giá bao nhiêu, nhập ở đâu?" |
| | Product Radar | `/product-radar` | "Ở nước Y, tuần này sản phẩm nào đang tăng ads?" |
| | Product Discovery | `/radar` | "Sản phẩm nào vừa được xác nhận vừa đang tăng? Nhóm theo chủ đề (mới, lạ, có sóng…)" |
| | Hidden Winners | `/hidden-winners` | "Sản phẩm nào ít người bán nhưng ad thắng?" |
| Nguồn dữ liệu | Thư viện quảng cáo | `/ads` | "Cho tôi xem từng quảng cáo cụ thể" |
| | Nguồn hàng & Store | `/marketplace` | "Giá nhập / số đã bán trên AliExpress, 1688, Taobao" |
| | Creative Vault | `/vault` | "Kho video / ảnh quảng cáo" |
| Thị trường | Daily Pulse | `/` | "Hôm nay có gì mới?" |
| | Market Radar | `/markets` | "Nước nào đang nóng, dữ liệu có tươi không?" |
| | Xếp hạng sản phẩm | `/products` | "Bảng toàn bộ sản phẩm, lọc theo điểm" |
| | Competitor Radar | `/competitors` | "Đối thủ nào đang scale?" |
| EU Market | 10 trang con | `/eu-market/*` | Nghiên cứu thị trường bán lẻ châu Âu (không phải ads) |
| Hệ thống | Alerts | `/alerts` | Cảnh báo tự động |
| | AI Agent | `/agent` | Hỏi đáp bằng ngôn ngữ tự nhiên |
| | Data & Connectors | `/data` | Quản trị nguồn, lịch quét, dung lượng |

Ở thanh bên trái có ô **tên người dùng** và **team (Mess / Ladi)**. Chọn team thì Product Radar, Discovery, Thư viện quảng cáo, Creative Vault và Tìm sản phẩm **tự lọc theo funnel của team đó**. Không nhập tên thì **không vote được**.

### 2.2 Các màn hình giống nhau khác nhau ở đâu

Bốn màn hình đều liệt kê "sản phẩm", nhưng mỗi màn hình chọn sản phẩm theo một cách khác:

| | Tìm sản phẩm | Product Radar | Product Discovery | Hidden Winners | Xếp hạng sản phẩm |
|---|---|---|---|---|---|
| Đầu vào | Từ khoá (bắt buộc để quét) | Nước + khung 7/14/30 ngày | Nước + tab chủ đề | Nước + tag | Bộ lọc điểm |
| Dữ liệu tính từ | Kho + quét live | Ads **đang chạy** trong kho, tính trực tiếp | Điểm đã tính sẵn | Ads đang chạy, tính trực tiếp | Điểm đã tính sẵn |
| Hiển thị | Thẻ có ảnh / video | Bảng có ảnh nhỏ | Biểu đồ scatter + thẻ | Thẻ có ảnh | Bảng điểm |
| Điểm chính | Opp / Wave / Novelty / Creative | "Điểm" (= Opp), xu hướng, độ mạnh ad | Demand × Momentum | Hidden Winner score | Win, Rarity, Saturation, Confidence, Opportunity |
| Thu thập dữ liệu mới? | Có (nút ⚡) | Không | Không | Không | Không |
| Dùng khi | Có ý tưởng ngách / từ khoá | Theo dõi một nước hằng ngày | Tìm sản phẩm "đáng test" mà chưa có từ khoá | Tìm ngách ít cạnh tranh | Lọc / so điểm hàng loạt, gộp sản phẩm trùng |

Hai nơi dùng chữ "hidden" nhưng định nghĩa khác nhau:

- **Trang Hidden Winners**: ≤ 5 người bán, ad mạnh nhất có force ≥ 45, Hidden score ≥ 55.
- **Tab "Hidden Products" trong Product Discovery**: ≤ 10 người bán và Wave ≥ 45.

Ba nơi dùng chữ "Price Intelligence / giá" cũng khác nhau:

- **Dòng giá trên mọi thẻ** (PriceTag): giá đối thủ, tự tìm ngầm (mục 11.2).
- **Trang EU Market › Price Intelligence**: lịch sử giá listing châu Âu (đang trống).
- **Tìm bằng ảnh › So sánh giá**: bảng giá của các listing cùng sản phẩm, tìm theo ảnh.

---

## 3. Nguồn dữ liệu và cách kết nối

### 3.1 Nguồn quảng cáo (dữ liệu chính)

| Nguồn | Lấy gì | Cách kết nối | Chi phí | Trạng thái thật 13/10 |
|---|---|---|---|---|
| **Meta Ad Library (public)** | Ads FB / IG / Messenger mọi nước | Gọi GraphQL công khai của Ad Library qua thư viện `meta-ads-collector`, không cần key | Miễn phí | ✅ Bật, healthy, đồng bộ lần cuối 13/10 09:22 (giờ VN). **6.846 ads** |
| **TikTok Creative Center Top Ads (headless)** | Top ads TikTok PH / US / EU / ME | Trình duyệt Edge/Chrome chạy ẩn (Playwright), tự lấy headers từ hồ sơ trình duyệt; chưa đăng nhập thì chỉ lấy được trang 1 | Miễn phí | ✅ Bật, health "slow", đồng bộ 13/10 08:06. **1.839 ads TikTok** (gồm cả Ad Library) |
| TikTok Ad Library | Ads TikTok phân phối ở EU/UK | API công khai | Miễn phí | ✅ Bật, chỉ có ads EU/UK |
| TikTok Creative Center (dán headers / tài khoản) | Như bản headless, đầy đủ hơn | Dán headers hoặc đăng nhập 1 lần bằng `scripts/tiktok_login.py` | Miễn phí | ❌ Tắt, trạng thái `error` |
| Apify (TikTok Top Ads, 1688, Taobao) | Như trên, ổn định hơn | Token Apify | **Trả phí theo lượt** | Bật nhưng **chỉ chạy khi tích "Dùng nguồn trả phí"** |
| Snapchat Ads Library | Ads Snapchat EU theo tên brand | API công khai | Miễn phí | ❌ Tắt |
| Meta Graph API chính thức, TikTok Commercial API, Pipiads API | Ads | Cần token / tài khoản Enterprise | Có | ❌ Chưa cấu hình |
| File export Pipiads / Minea | Ads từ spy tool trả phí | Upload ở Data & Connectors hoặc thả vào `apps/api/data/import/<nguồn>/` | Theo gói spy tool | ✅ Bật (chưa có file mới từ 06/10) |
| Chrome extension Meta | Ads khi người dùng tự duyệt Ad Library | Extension `apps/extension` gửi về `/api/ingest/meta-library` | Miễn phí | ✅ Có sẵn (xem mục 12) |

### 3.2 Nguồn hàng Trung Quốc (giá nhập)

| Nguồn | Lấy gì | Cách kết nối | Trạng thái |
|---|---|---|---|
| AliExpress | Giá, đã bán, đánh giá, ảnh | Đọc JSON trong trang tìm kiếm công khai, miễn phí | ✅ 1.194 listing |
| 1688 | Giá xưởng, số bán 30 ngày, ≤ 20 offer/lần | API chính thức 1688 AI (khoá AK), miễn phí; Apify làm dự phòng | ✅ 563 listing |
| Taobao / Tmall | Giá, đã bán | Chỉ qua Apify (trả phí) | ⚠️ 30 listing |

### 3.3 Nguồn giá và ảnh (dịch vụ ngoài)

| Dịch vụ | Dùng cho | Hạn mức | Đã dùng (tháng 10) |
|---|---|---|---|
| SerpApi Google Lens | Tìm bằng ảnh trên Internet | Chung 250 lượt/tháng, 8 lượt/ngày | 21 lượt Lens |
| SerpApi Google Shopping | Dự phòng tìm giá | Trong pool 250, 4/ngày | 9 lượt |
| Bright Data (search_engine, scrape) | Tìm giá, đọc trang bị chặn 403 | 5.000/tháng, 150/ngày, giữ lại 200 cho thao tác tay | 153 lượt |
| Gemini (dự phòng Claude) | Trích tên sản phẩm, lọc không phải hàng vật lý, AI Agent, giải thích điểm | Theo key | Đang bật (`/api/health` → `llm: true`) |
| Tỷ giá ECB | Quy đổi EUR cho EU Market | Miễn phí | ✅ |

### 3.4 Nguồn EU Market

| Nguồn | Cách kết nối | Trạng thái |
|---|---|---|
| JSON-LD (trang sản phẩm công khai bất kỳ) | Dán URL trang sản phẩm, đọc dữ liệu cấu trúc trong trang | ✅ chạy được, 1 listing |
| Google Lens | Listing phát hiện khi tìm bằng ảnh | 1 listing |
| Allegro (Ba Lan) | Extension `extensions/allegro-bridge` mở trang listing trong Chrome của người dùng, đọc rồi gửi về API | ⚠️ mới viết, **chưa thử trên Allegro thật**, 1 job đã chạy và `failed` |
| Kaufland, bol, BigBuy | API seller (cần tài khoản người bán / gói trả phí) | ❌ chưa có credentials |
| eMAG, OTTO, Cdiscount, ManoMano, Zalando, OnBuy, Faire, Ankorstore, Orderchamp | Chỉ có API cho seller / partner | ❌ `access_restricted` |
| Merkandi, Europages, SoloStocks | Không có API công khai | ❌ `unsupported_capability`, không scrape |
| eBay, Amazon | Đã **gỡ theo quyết định ngày 09/10/2026** | Không dùng |

### 3.5 Ngoài phạm vi (đã gỡ khỏi hệ thống)

Google / Bing Ads, Amazon / Walmart, spy store Shopify, video TikTok organic, dữ liệu sau test (đơn, vận đơn, hoàn). Không dùng cookie lấy trộm, không vượt CAPTCHA / anti-bot.

---

## 4. Luồng hoạt động

### 4.1 Tổng thể

```text
 MKT gõ từ khoá ⚡ ─┐                          ┌─ Scheduler (tiến trình worker, tick 30 giây)
 Extension Chrome ──┼─► Connector từng nguồn ◄─┤   · 49 từ khoá theo dõi (PH 30 phút/lần)
 File export ───────┘   (Meta, TikTok, Ali…)   └─  · TikTok headless 12 giờ/lần
                               │
                               ▼
   Ingest: lưu bản thô → bỏ trùng → phân loại funnel (mess/ladi/form/app/other)
           → đọc giá trong nội dung ad → đặt tên sản phẩm (AI) → gom cụm sản phẩm
                               │
             ┌─────────────────┼──────────────────────┐
             ▼                 ▼                      ▼
   Lưu ảnh bìa ≤480px   Chấm điểm sản phẩm       Index ảnh (hash + CLIP)
   (video không lưu)    → nhãn TEST/WATCH/SKIP   cho Tìm bằng ảnh
             │                 │
             └────────► Postgres ◄────── Giá tự động (khi thẻ hiển thị)
                               │
                               ▼
                 FastAPI :8000  →  Web Next.js :3000  (thông báo realtime qua SSE)
```

### 4.2 Một lượt "⚡ Tìm trực tiếp trên nguồn"

1. MKT nhập một hoặc nhiều từ khoá (Enter sau mỗi từ, khớp **bất kỳ** từ nào), chọn nước, bấm ⚡.
2. API tạo một *search job*, quét **song song mọi nguồn đang bật** (bỏ nguồn trả phí nếu chưa tích), mỗi nguồn × từ khoá × nước.
3. Kết quả về đến đâu được lưu và chấm điểm đến đó. Web cập nhật mỗi 2,5 giây, hiện số ads / sản phẩm theo từng nguồn (badge "x · y mới · z không lưu được").
4. Xong trang 1 → **Trang sau** = quét tiếp từ chỗ dừng (chỉ nguồn quảng cáo; AliExpress / 1688 / Taobao / Apify chỉ quét trang 1).
5. Tích "Theo dõi từ khoá này" → từ khoá vào danh sách theo dõi, tự quét lại mỗi giờ.

### 4.3 Cách gom một sản phẩm

Nhiều ad của nhiều advertiser được coi là **cùng một sản phẩm** khi tên đã chuẩn hoá (bỏ từ thừa, đồng nghĩa đa ngôn ngữ), slug URL, khoảng giá, danh mục và brand giống nhau đủ ngưỡng (0,62). Chưa so ảnh / video khi gom. Hệ quả: **3.986 / 4.869 sản phẩm (82%) chỉ có đúng 1 ad**. Tên sản phẩm lấy từ AI, tiêu đề link hoặc slug landing; nếu không đoán được thì là "`<advertiser> (chưa đặt tên)`" (491 sản phẩm).

### 4.4 Lịch chạy tự động (worker)

| Việc | Chu kỳ |
|---|---|
| Từ khoá theo dõi (56 từ khoá, 49 đang bật) | Theo từng từ khoá; PH 30 phút |
| TikTok Creative Center headless | 12 giờ |
| Lấy ảnh bìa creative | 2 phút |
| Kiểm tra ad còn chạy (2 lần liên tiếp không thấy mới đánh dấu "đã dừng") | 20 phút |
| Quét giá landing page + review đối thủ | 10 phút, 40 URL/lượt |
| Giá tự động (PriceTag) | 30 giây, 6 việc/lượt |
| Index ảnh cho Tìm bằng ảnh | 1 phút |
| Alerts | 15 phút |
| Chấm lại toàn bộ + snapshot ngày | 6 giờ |
| Dọn kho (ảnh 30 ngày, dữ liệu thô 90 ngày) | 6 giờ + sau 0h giờ VN |

---

## 5. Từ điển thuật ngữ và điểm số

Mọi điểm đều **0–100** và đều do **công thức** tính từ dữ liệu trong DB. AI không chấm điểm, chỉ trích tên sản phẩm và viết lời giải thích.

### 5.1 Đơn vị dữ liệu

| Thuật ngữ | Nghĩa |
|---|---|
| **Ad** | Một quảng cáo thật lấy từ thư viện quảng cáo (Meta / TikTok). Listing nguồn hàng (AliExpress…) cũng lưu chung bảng nhưng **không** được đếm là ad |
| **Sản phẩm (cụm)** | Nhóm các ad bán cùng một món hàng, mã `PRD_0000123`. Là đơn vị mọi điểm số tính trên |
| **Advertiser / người bán / seller** | Page Facebook hoặc tài khoản TikTok chạy ad |
| **Creative** | Một file ảnh / video trong ad |
| **Creative family** | Các creative trông giống nhau (so dHash) |
| **Listing** | Một tin bán trên sàn nguồn hàng (AliExpress, 1688, Taobao) hoặc sàn EU |
| **Đang chạy / Đã dừng** | Ad còn xuất hiện ở nguồn khi kiểm tra lại (20 phút/lần, cần 2 lần không thấy mới coi là dừng) |
| **Biến thể / phiên bản (variants)** | Số phiên bản của cùng một ad mà Ad Library báo ("Quảng cáo này có 6 phiên bản") |
| **Biến thể scale (×N)** | Số ad cùng page + cùng nội dung chạy song song = dấu hiệu đang nhân ngân sách |
| **Thị trường mục tiêu** | PH, Trung Đông (SA, AE, KW, QA, OM, BH, JO, EG, IQ), US, EU + UK, AU / NZ. Ads nước khác vẫn lưu nhưng không cộng vào số "tất cả" |
| **ALL / 🌍 / Toàn cầu** | Ad lấy được khi quét không giới hạn nước (Meta trả "ALL") |

### 5.2 Funnel

Suy từ nút CTA trước, rồi đến tên miền landing page.

| Funnel | Nhận biết | Team |
|---|---|---|
| 💬 **mess** | CTA Send Message / WhatsApp / chat, link `m.me`, `wa.me`, `zalo.me`, hoặc nội dung có "inbox / nhắn tin" | MKT Mess |
| 🧾 **ladi** | Dẫn ra landing page ngoài mạng xã hội, CTA Shop now / Order now / Mua ngay | MKT Ladi |
| 📝 **form** | CTA dạng form (Sign up, Get quote) mà không có landing ngoài | Thu lead |
| 📱 **app** | Link về App Store / Google Play | Không phải bán hàng, bị loại |
| **other / Khác** | Dẫn về sàn (Amazon, Noon, Temu, AliExpress…) hoặc không xác định | |

Chip **"Ladi 100%"** trên thẻ = tỷ lệ funnel trong các ad của sản phẩm. Một sản phẩm được tính là "hợp MKT Mess / Ladi" khi ≥ 30% ad dùng funnel đó.

### 5.3 Khuyến nghị (Action)

Quyết định đi theo thứ tự, dừng ở điều kiện đầu tiên khớp:

| Nhãn | Icon | Điều kiện | Nên làm |
|---|---|---|---|
| **SKIP** | ⏭ đỏ | Không còn ad nào đang chạy · hoặc ≥ 50% ad là app / form · hoặc > 15 người bán / saturation ≥ 70 · hoặc tín hiệu yếu | Bỏ qua |
| **REVIEW** | ⚠ cam | Compliance risk ≥ 60 (hàng nhái, claim y tế, hàng cấm) | Kiểm tra chính sách trước khi test |
| **WATCH** | ◉ xám | Tên sản phẩm chưa xác định nhưng demand ≥ 45 · hoặc demand ≥ 45 · hoặc momentum ≥ 20 | Theo dõi thêm |
| **TEST** | ◆ xanh | Quadrant Breakout / Experimental · hoặc demand ≥ 55 và (≥ 2 người bán hoặc ≥ 2 biến thể) | Test ngân sách nhỏ |
| **TEST_NOW** | 🔥 xanh lá | Breakout + ad lâu nhất ≥ 14 ngày + market fit ≥ 50 | Ưu tiên test ngay |

Dưới mỗi nhãn, trang chi tiết ghi lý do bằng số, ví dụ: *"Đã được xác nhận (demand 87): nhiều người bán hoặc đang nhân bản creative · 44 ads đang chạy · 2 người bán · ad lâu nhất 337 ngày"*.

### 5.4 Bốn ô điểm trên thẻ sản phẩm

| Ô | Tên đầy đủ | Ý nghĩa cho MKT | Cách tính (rút gọn) | Độ tin cậy |
|---|---|---|---|---|
| **Opp.** | Opportunity | **Điểm tổng để xếp hạng.** Mọi danh sách sắp theo điểm này | 28% demand + 14% momentum + 12% market fit + 10% MKT appeal + 8% novelty + 8% wave + 8% creative + 12% economics, trừ điểm khi cạnh tranh > 30 và compliance > 20, nhân hệ số confidence, **cộng 15 nếu có chạy ở PH**. Trần: SKIP / REVIEW ≤ 30, WATCH ≤ 55 | Cao nhất trong 4 ô, vì phần lớn dựa trên dữ liệu ads thật |
| **Wave** | Wave potential | Sản phẩm có "sóng" viral không: nội dung gây tò mò, có demo, có before/after, có cảm xúc, đang nhân bản | Trung bình 7 phần: tò mò, demo, biến đổi, chia sẻ, tốc độ creative 7 ngày, số biến thể scale, cảm xúc. 5 / 7 phần là **dò từ khoá trong chữ của ad** (regex) | **Thấp**: không xem video, không hiểu tiếng Tagalog / Ả Rập ngoài bộ từ có sẵn |
| **Novelty** | Độ mới lạ | Sản phẩm còn mới, ít người làm, ít store, ít nước | 30% ít advertiser (2 → 15), 20% ít store, 20% mới phát hiện (< 60 ngày), 15% ít thị trường | Trung bình. Cao ≠ tốt: chỉ có 1 người bán cũng ra novelty cao |
| **Creative** | Creative potential | Có đủ chất liệu để làm creative không: nhiều concept, nhiều hook / angle, có video, có demo, UGC dễ làm | Trung bình 6 phần: số creative family, số angle, số hook, tỷ lệ video, demo / before-after, UGC | Thấp–trung bình (hook / angle cũng phân loại bằng từ khoá) |

### 5.5 Các điểm khác của Product Potential Vector (trang chi tiết)

| Điểm | Ý nghĩa | Cách tính |
|---|---|---|
| **Market demand** | Thị trường đã xác nhận sản phẩm bán được chưa | 35% ad mạnh nhất chạy bao lâu (7 → 120 ngày) + 30% mức nhân bản (số ads, biến thể, số ad mạnh) + 20% số người bán (1 = 0 điểm, 2 = 60, 3–10 = 100, 11–15 = 70, > 15 = 40) + 15% số thị trường |
| **Momentum (7 ngày)** | Tuần này có đang tăng không | 60% số ads mới 7 ngày (0 → 5) + 40% số người bán mới 7 ngày (0 → 3) |
| **Market fit** | Hợp từng nước không | So danh mục và angle của sản phẩm với những gì đang chạy nhiều ở nước đó (Market DNA) và danh mục team đã LOVE / TEST. Ít dữ liệu nước đó thì giữ quanh 50 |
| **MKT appeal** | Gu của team | Trung bình vote (LOVE 100, TEST 80, WATCH 55, NORMAL 35, SKIP 0). Chưa có vote thì dùng Taste Model (cần ≥ 3 vote mỗi đặc điểm), không có thì 50 |
| **Competition** | Mức cạnh tranh | = Saturation |
| **Economics** | Biên lợi nhuận tiềm năng | Từ giá bán đối thủ và giá nhập. Không có giá nhập thì mặc định 55 |
| **Compliance risk** | Rủi ro chính sách | Dò từ: replica / 1:1 (+45), brand xa xỉ (+25), "chữa khỏi / cures" (+35), giảm cân cấp tốc (+30), người lớn (+35), vũ khí / vape / CBD (+40), before/after (+10) |
| **Confidence** | Dữ liệu đủ để tin chưa | 25% số ads (1 → 80) + 20% số advertiser + 20% số ngày có dữ liệu + 10% số thị trường + 10% số nguồn. Nhãn: High ≥ 70, Medium ≥ 45, Low |

### 5.6 Quadrant (phân nhóm Demand × Momentum)

| Nhãn trên thẻ | Điều kiện | Ý nghĩa | Số sản phẩm 13/10 |
|---|---|---|---|
| 🚀 **Breakout** | Momentum ≥ 40 và demand ≥ 50 | Đã được xác nhận **và** đang tăng tuần này | 5 |
| 💎 **Experimental** | Momentum ≥ 40, demand < 50 | Đang tăng nhưng chưa xác nhận | 27 |
| 🏛 **Stable winner** | Demand ≥ 50, momentum < 40 | Đã xác nhận, tuần này không tăng | 357 |
| · **Low signal** | Còn lại | Ít tín hiệu | 5.880 |

### 5.7 Độ mạnh một quảng cáo (force) và tier

```
force = (ngày chạy / 180, tối đa 1) × 60 + (số biến thể − 1) / 9 × 25 + (số placement / 4) × 15,  × 0,7 nếu ad đã dừng
```

| Tier | Ngưỡng | Hiển thị |
|---|---|---|
| Huyền thoại (legendary) | ≥ 70 | 🏆 chấm xanh lá |
| Mạnh (strong) | ≥ 45 | 🏆 chấm xanh |
| Thường (regular) | ≥ 20 | chấm xám |
| Đang test (testing) | < 20 | chấm nhạt |

### 5.8 Các điểm "thế hệ 1" vẫn còn hiển thị

Các điểm này có từ trước bản xếp hạng v2 (09/10). Chúng vẫn được tính và vẫn hiện ở trang chi tiết, Xếp hạng sản phẩm và Daily Pulse, nhưng **không** quyết định nhãn khuyến nghị:

| Điểm | Ý nghĩa | Ghi chú |
|---|---|---|
| **Win Score / External Win** | Tín hiệu thắng từ ads: chạy lâu, tăng creative, thêm advertiser, mở nước mới, tương tác | Nhãn: Likely ≥ 75, Potential ≥ 60, Mixed ≥ 45, Weak. **Thường mâu thuẫn với Opp** (xem 6) |
| **Internal Win** | Kết quả test nội bộ | ❌ luôn "—": không còn dữ liệu đơn hàng |
| **Saturation** | Độ bão hoà: mật độ advertiser, trùng creative, số store, đà giảm | Low < 25 · Growing < 45 · Competitive < 68 · Highly Saturated · Declining (ads mới giảm > 50%) |
| **Rarity** | Độ hiếm: ít advertiser, ít ads, ít store | |
| **Rare Winner** | Trung bình nhân của win, rarity, growth, market fit, margin | Một yếu tố thấp kéo cả điểm xuống |
| **Customer rejection** | Tỷ lệ khách từ chối nhận hàng | ❌ luôn "—" |

### 5.9 Hidden Winner score

Chỉ xét sản phẩm còn ad chạy, **≤ 5 người bán**, ad mạnh nhất force ≥ 45:

```
score = 40% force ad mạnh nhất + 25% độ hiếm (1 người bán = 100) + 20% ads mới 7 ngày (đến 5) + 15% số biến thể (đến 10)
giữ nếu score ≥ 55
```

### 5.10 Lifecycle (vòng đời sản phẩm)

`DISCOVERED → WATCHLIST → CANDIDATE → TESTING → WIN / HOLD / FAIL → SCALE → SATURATED → RETIRE`

- Tự động: DISCOVERED → WATCHLIST khi Opp ≥ 50; → CANDIDATE khi nhãn TEST / TEST_NOW; WIN / SCALE → SATURATED khi bão hoà.
- Từ TESTING trở đi: **chuyển tay** trên trang chi tiết (không có dữ liệu đơn để tự đánh giá).
- Hiện tại: DISCOVERED 5.927 · CANDIDATE 275 · WATCHLIST 66 · RETIRE 1. Chưa sản phẩm nào ở TESTING.

### 5.11 Nhãn giá

| Nhãn | Nghĩa |
|---|---|
| ✓ **Shop đối thủ (domain)** | `VERIFIED_COMPETITOR`: giá đọc trực tiếp từ trang của chính advertiser |
| ✓ **Marketplace (tên)** | `VERIFIED_MARKETPLACE`: giá đọc từ trang shop / sàn khác |
| **Tham khảo · Google…** | `MARKET_REFERENCE`: khoảng giá thấy trong kết quả tìm kiếm, **chưa chắc cùng sản phẩm** |
| **· giá trong quảng cáo** | Giá đọc từ chữ trong ad, chưa có kết quả tìm giá |
| ⟳ **đang tìm giá…** / **giá: đang chờ** | Đang trong hàng đợi |
| 🔍 **chưa tìm được giá** | Không nguồn nào nêu giá (thường là ad funnel mess, không có trang bán) |

---

## 6. Đọc một thẻ sản phẩm (ví dụ thật)

Thẻ trong ảnh chụp là sản phẩm **PRD_0000001**. Số liệu bên dưới lấy từ API lúc viết tài liệu.

| Vị trí trên thẻ | Hiển thị | Nghĩa | Ghi chú |
|---|---|---|---|
| Ảnh lớn | Ảnh bìa creative | Ảnh bìa ≤ 480px lưu sẵn. Nếu là video: bấm ▶ để phát thẳng từ nguồn | Video gốc hết hạn sau vài giờ → "Link video gốc đã hết hạn"; riêng TikTok có nút **Lấy link mới** |
| Tiêu đề | *Ems Thermal Neck Lifting And Tighten Massager Electric* | Tên sản phẩm (cụm). Bấm → trang chi tiết | |
| Góc phải | ◆ **TEST** | Khuyến nghị (5.3) | Lý do: demand 87, 44 ads, 2 người bán, ad lâu nhất 337 ngày |
| Dòng xám | `PRD_0000001 · health · ALL, PH, SA` | Mã · danh mục · các thị trường ad đang chạy | |
| Chip | 🏛 **Stable winner** | Quadrant: demand 87 ≥ 50, momentum 0 < 40 | Đã xác nhận nhưng tuần này không tăng |
| Chip | **Ladi 100%** | 100% ad dẫn về landing page | Hợp MKT Ladi, không hợp Mess |
| **70** Opp. | Opportunity 70,3 | Điểm xếp hạng | = 55,3 điểm gốc **+ 15 vì có chạy ở PH**. Landing page lại bán bằng £ (UK), nên phần cộng PH ở đây có thể làm điểm cao hơn thực tế |
| **44** Wave | 44,3 | Sóng viral | Tò mò 100, nhân bản 100, nhưng demo 9, tốc độ 0, cảm xúc 17 |
| **69** Novelty | 69,4 | Mới lạ | Chỉ 2 người bán, ít store |
| **71** Creative | 70,7 | Chất liệu creative | Nhiều concept, nhiều hook, 100% video; ít đa dạng angle (25) |
| 🔗 weareluna.store ↗ | Landing page đối thủ | Bấm mở tab mới | |
| ✓ **£29 — Shop đối thủ (weareluna.store)** ↗ | Giá đã xác minh trên trang của advertiser | Rê chuột xem thời điểm và phương pháp đọc giá | |
| ● **Meta** | Mạng có ad của sản phẩm | Mỗi chấm màu = một mạng (Meta, TikTok, AliExpress, 1688…) | |
| Dòng cuối | `2 advertiser · 44 ads active · +0 ads 7d · 44 ads khớp` | Số người bán · số ad đang chạy · ads mới 7 ngày · số ad khớp từ khoá / bộ lọc đang tìm | |
| 🔥 👍 👀 😐 👎 | Vote nhanh | LOVE / TEST / WATCH / NORMAL / SKIP | Phải nhập tên ở thanh bên trái |
| ⧉ **Copy URL** | Copy link file media gốc | | ⚠️ Là link CDN có hạn; nút **Video gốc** chỉ hiện khi link còn sống. Copy link đã chết thì vô dụng |

Ở trang chi tiết, cùng sản phẩm này hiện **Win Score 24 "Weak Signals"** nhưng **Opportunity 70 + TEST**. Win Score là điểm thế hệ 1 (mục 5.8). MKT nên đọc **Opp + nhãn khuyến nghị + lý do**, bỏ qua Win Score.

---

## 7. Bảng icon và ký hiệu

| Icon / ký hiệu | Ở đâu | Nghĩa | Dùng được chưa |
|---|---|---|---|
| 🔥 👍 👀 😐 👎 | Thẻ sản phẩm, trang chi tiết | Vote LOVE / TEST / WATCH / NORMAL / SKIP | ✅ chạy (cần nhập tên). Vote LOVE / TEST / WATCH giữ media khỏi bị dọn. Cả hệ thống mới có 2 vote |
| 🔥 TEST NOW · ◆ TEST · ◉ WATCH · ⚠ REVIEW · ⏭ SKIP | Mọi danh sách | Khuyến nghị | ✅ (0 sản phẩm TEST_NOW lúc này) |
| 🚀 Breakout · 💎 Experimental · 🏛 Stable winner · · Low signal | Thẻ, Discovery | Quadrant | ✅ |
| 💬 MKT Mess · 🧾 MKT Ladi · 📝 Form · 📱 App | Bộ lọc, thẻ ad | Funnel | ✅ |
| Chip "Ladi 100%", "Mess 40%" | Thẻ sản phẩm | Tỷ lệ funnel của các ad | ✅ |
| ● + tên mạng (Meta, TikTok…) | Thẻ, bộ lọc | Mạng có dữ liệu; màu do API cấp | ✅ |
| 🔗 domain ↗ | Thẻ | Landing page đối thủ | ✅ |
| ✓ xanh trước giá | Dòng giá | Giá đã xác minh từ trang bán | ✅ |
| ⟳ đang tìm giá… / 🔍 chưa tìm được giá | Dòng giá | Trạng thái tìm giá | ✅ |
| ▶ + "12s" | Ảnh bìa video | Bấm để phát từ nguồn, kèm độ dài | ⚠️ link hết hạn sau vài giờ |
| ⟳ **Lấy link mới** | Video TikTok hết hạn | Hỏi lại TikTok theo ID ad để lấy link mới (~10 giây) | ✅ chỉ TikTok |
| ⧉ Copy URL · ↗ Video gốc | Thẻ, Vault | Copy / mở link media gốc | ⚠️ thường đã chết |
| ⬇ Tải video / ảnh · MB | Thẻ, Vault | Tải file đã lưu trong kho | ❌ không bao giờ hiện: kho không lưu file (0 creative `stored`) |
| 📌 Ghim | Vault | Giữ file khỏi bị dọn | ❌ chỉ hiện với file đã lưu → không bao giờ hiện |
| ⧉ Family · N | Vault | Lọc các creative giống nhau | ❌ cần file đã lưu |
| 🏆 Huyền thoại 82 / Mạnh 51 | Thẻ ad | Tier độ mạnh của ad (5.7) | ✅ |
| ✓ xác minh 3 giờ trước | Thẻ ad | Lần cuối kiểm tra ad còn chạy | ✅ |
| ↺ chạy lại | Thẻ ad | Ad đã dừng rồi chạy lại | ✅ |
| ● Đang chạy / ● Đã dừng | Thẻ ad | Trạng thái ad | ✅ |
| ★ N đánh giá · 🛒 N đã bán · 💲 nhập ~$x | Thẻ sản phẩm có listing nguồn hàng | Số đánh giá, đã bán, giá nhập thấp nhất AliExpress | ✅ khi có listing |
| ↑↑↑ ↑↑ ↑ → ↓ ↓↓ / NEW / – | Product Radar | Xu hướng ads mới so với kỳ trước: ≥ +100%, ≥ +40%, ≥ +10%, đi ngang, ≤ −10%, ≤ −50%; NEW = kỳ trước 0; – = không có ads mới | ✅ |
| ×N (Biến thể scale) | Product Radar | Số ad cùng page cùng nội dung chạy song song | ✅ |
| 🌍 | Product Radar | Thị trường "ALL" | ✅ |
| 🔥 Đang tăng nhanh · ⏳ Chạy lâu ≥ 90 ngày · 📈 Đang nhân bản · 💎 Chỉ 1 người bán · 🌍 Thị trường mới · 🗺 Nhiều thị trường | Hidden Winners | Tag | ⚠️ 1.134 / 1.148 có "Chạy lâu", 1.127 có "1 người bán", chỉ 1 có "Đang tăng nhanh" |
| Chấm xanh lá / xanh / vàng / đỏ "Độ tươi" | Market Radar | Dữ liệu nước đó xác minh < 1h / < 6h / < 24h / > 24h | ✅ |
| Chấm đỏ / cam / xanh | Alerts | Mức độ critical / warn / info | ✅ |
| 📷 Tìm bằng ảnh · € So sánh giá · 🚚 Tìm nguồn hàng | Trang chi tiết, header, ad | Mở `/visual` với ảnh của sản phẩm | ✅ |
| ★ sau "Philippines" | Competitor Radar | Thị trường ưu tiên | ✅ |

---

## 8. Bấm vào một sản phẩm: trang Chi tiết sản phẩm

Đường dẫn `/products/<id>`. Có 5 phần, từ trên xuống.

### 8.1 Đầu trang

- Tên, mã, danh mục, thị trường, giá trung bình trong ad.
- Pill **Lifecycle** và nhãn **Action**.
- 3 nút: **📷 Tìm bằng ảnh**, **€ So sánh giá**, **🚚 Tìm nguồn hàng**. Cả ba mở `/visual` với ảnh của sản phẩm, mỗi nút một chế độ. ✅

### 8.2 Bảy ô điểm

| Ô | Hiển thị | Hữu ích? |
|---|---|---|
| Win Score | Win thế hệ 1 + nhãn | ⚠️ gây nhầm, mâu thuẫn với Opp |
| External / Internal | External / "—" | ❌ Internal luôn trống |
| Rare Winner | + Rarity | ⚠️ ít dùng |
| Saturation | + trạng thái (Growing…) | ✅ |
| Opportunity | + "Raw × confidence adj." | ✅ điểm chính |
| Confidence | % + High / Medium / Low | ✅ |
| Customer rejection | "—" | ❌ không có dữ liệu |

### 8.3 Khung "Thông tin cho MKT — giá, tương tác, bằng chứng bán" ✅ hữu ích nhất

| Ô | Nội dung |
|---|---|
| Giá đối thủ đang bán | Giá USD trung vị + "x/y ads có giá (z%)" + khoảng giá |
| Giá nhập (AliExpress) | Giá thấp nhất; "landed ≈ ×1.6" |
| Biên gộp ước tính | `1 − giá nhập × 1,6 / giá bán`. Xanh > 50%, đỏ < 30%. Chưa gồm ads, COD, hoàn |
| Giá sàn | Giá thấp nhất trên sàn |
| Khách nói gì | % review khen / chê |
| Like / Comment (ads) | Tổng tương tác (Meta thường không công khai, hay là 0) |
| Đã bán (sàn) | Số đã bán, số listing, số đánh giá |
| Ads đang chạy | Đang chạy / tổng, số advertiser, ad lâu nhất bao nhiêu ngày |

Bên dưới có 4 cột: **Giá từng đối thủ** (theo advertiser, ghi nguồn giá) · **Giá sàn theo từng sàn** · **Khách khen / chê** (top phàn nàn, ví dụ review) · **Landing page đối thủ dùng** + funnel + 3 ad nhiều tương tác nhất.

Nút **Quét giá & review đối thủ** (~30 giây): đọc landing page lấy giá và lấy review AliExpress / landing. ✅

### 8.4 Năm tab

| Tab | Nội dung | Hữu ích? |
|---|---|---|
| **Video & Discovery** (mặc định) | Tối đa 24 video / ảnh của sản phẩm (advertiser, nước, số ngày chạy, funnel, nút "Ad gốc"); khung **Product Potential Vector** (11 thanh điểm, mục 5.5); **Theo từng thị trường** (market fit + quyết định riêng mỗi nước); funnel; nguồn phát hiện; thành phần Wave; khung **MKT vote** đầy đủ (chọn lý do: Visual mạnh, Hook dễ, Demo được, Before/After, UGC dễ làm, Hợp Saudi/GCC, Hợp VN, Giá tốt, Độc lạ, Quá phổ biến, Không có angle mới, Khó ship, Rủi ro policy, Margin thấp + ghi chú) | ✅ quan trọng nhất sau khung MKT |
| **Overview** | Decision engine (lý do + nút **AI giải thích vì sao ra điểm này**); breakdown External Win, Internal Win (trống), Saturation, Rarity & Confidence; **Lifecycle** (nút chuyển trạng thái + lịch sử); các tên khác của sản phẩm (alias) | ✅ lý do + lifecycle · ⚠️ phần breakdown chủ yếu cho người vận hành |
| **Market & Ads** | Đa nền tảng (listing nguồn hàng, biên gộp); biểu đồ active ads và điểm theo ngày; ads theo nước; bảng Signals; danh sách advertiser; Stores & traffic; 50 ad gần nhất | ✅ phần lớn · ❌ Traffic, Traffic growth, Search trend, Keyword competition luôn 0 / "—" (không có nguồn) |
| **Creative Library** | Biểu đồ hook / angle / offer; bảng creative gom theo fingerprint (số bản copy, đang chạy, chạy lâu nhất, tương tác, nước) | ✅ để học angle của đối thủ (hook / angle phân loại bằng từ khoá) |
| **Comments** | Phân tích review: tích cực / trung tính / tiêu cực, top khen, top chê, theo tuần | ⚠️ chỉ có khi đã quét review. Sản phẩm ví dụ: 0 review |

**Có hữu ích không:** có. Trang này gom đủ để quyết định test: giá đối thủ, giá nhập, biên, số người bán, ad chạy bao lâu, creative mẫu, landing page. Phần gây nhiễu là các ô thế hệ 1 và các ô không có nguồn (8.2, tab Market & Ads), nên gỡ hoặc ẩn (mục 14).

---

## 9. Đặc tả từng màn hình

### 9.1 Tìm sản phẩm (`/search`) ✅

**Mục đích:** tìm sản phẩm theo từ khoá trong kho, hoặc quét trực tiếp mọi nguồn.

**Thao tác:**
- Ô từ khoá dạng tag: nhiều từ = OR. Ô **Loại trừ**: bỏ sản phẩm chứa từ đó.
- 📷 tìm bằng ảnh (mang theo từ khoá và nước).
- **Tìm trong kho**: chỉ lọc dữ liệu đã có. **⚡ Tìm trực tiếp trên nguồn** (Ctrl+Enter): quét live.
- Chọn thị trường (chip vùng / nước). Funnel: Tất cả / 💬 Mess / 🧾 Ladi / Form (mặc định theo team).
- Sắp xếp: Opportunity · Wave · Độ mới lạ · Creative · Market demand · Tăng trưởng 7d · Mới phát hiện · Số ads.
- Lọc: chạy ≥ N ngày, ≤ N advertiser, chỉ có video, có trên ≥ 2 nền tảng.
- **Tuỳ chọn quét**: số ad / thị trường mỗi trang (20 / 30 / 50 / 100), loại media, "Theo dõi từ khoá này", "Dùng nguồn trả phí (Apify)".
- Hàng **Nguồn sẽ quét**: bấm chip để bỏ một nguồn khỏi lượt quét (đồng thời lọc kết quả theo mạng).

**Hiển thị:** tiến trình theo nguồn, phân trang crawl (Trang trước / Trang sau), dòng tổng "6.065 sản phẩm · ads theo funnel: other … app … mess … ladi …", lưới thẻ sản phẩm (mục 6).

**Lưu ý:** dòng "ads theo funnel" đếm **ad**, không phải sản phẩm. Kết quả sau khi quét chỉ tính ad **đang chạy** khớp bộ lọc, nên có thể ít hơn số sản phẩm vừa quét.

### 9.2 Product Radar (`/product-radar`) ✅

**Mục đích:** bảng sản phẩm đang chạy theo nước và khung 7 / 14 / 30 ngày, tính trực tiếp từ ngày bắt đầu chạy của ad.

**Cột:** ảnh · Sản phẩm · **Xu hướng** (mũi tên + %, "+N mới / M trước") · Ads đang chạy · Sellers · Tuổi (ngày từ ad đầu tiên) · **Điểm** (= Opp) · **Ad mạnh nhất** (tier + force, số ad mạnh) · **Biến thể scale** ×N · Mess / Ladi (số ad) · Thị trường · Hành động.

**Sắp xếp:** Điểm · Xu hướng · Ads mới · Số ads · Sellers · Ads mạnh nhất · Mới xuất hiện.

**Hữu ích:** cao cho việc theo dõi hằng ngày một nước (PH). Tự làm mới khi có đồng bộ mới.

### 9.3 Product Discovery (`/radar`) ✅

**Opportunity Radar:** biểu đồ phân tán, trục X = demand, trục Y = momentum, kích thước chấm = Opp, màu = quadrant. Bấm chấm → trang chi tiết. Hiện 4.715 điểm: Low signal 4.335 · Stable 348 · Experimental 27 · Breakout 5.

**13 tab** (mỗi tab tối đa 60 thẻ sản phẩm):

| Tab | Điều kiện | Sắp theo |
|---|---|---|
| Breakout Products | Quadrant BREAKOUT | Opp |
| Experimental Opportunities | Quadrant EXPERIMENTAL | Opp |
| New Discoveries | Phát hiện trong 7 ngày | Mới nhất |
| Hidden Products | ≤ 10 advertiser và Wave ≥ 45 | Opp |
| High Wave | Wave ≥ 55 | Wave |
| High Novelty | Novelty ≥ 65 | Novelty |
| Creative Goldmine | Creative ≥ 55 | Creative |
| Hợp MKT Mess | ≥ 30% ad là mess | Opp |
| Hợp MKT Ladi | ≥ 30% ad là ladi | Opp |
| MKT Favorites | Có vote LOVE / TEST | MKT appeal |
| Recently Scaling | ≥ 5 ads mới 7 ngày và creative +40% | Ads mới |
| Saved By Team | Có ad lưu bằng extension | Mới thấy |
| High Risk | Compliance ≥ 40 | Compliance |

**Lưu ý nhỏ:** đường tham chiếu ngang trên biểu đồ vẽ ở 55, còn ngưỡng quadrant thật là momentum 40.

### 9.4 Hidden Winners (`/hidden-winners`) ⚠️

**Hiển thị:** thẻ có Hidden Winner score, tag (6 loại), dòng "vì sao" (`N người bán · ad mạnh nhất X/100 (tier) · chạy N ngày · +N ads / 7 ngày · N biến thể`), landing page, vote.

**Vấn đề:** 1.148 sản phẩm lọt vào, gần như tất cả là "1 người bán + ad chạy ≥ 90 ngày". Danh sách quá rộng để dùng. Nên lọc thêm tag 🔥 Đang tăng nhanh / 📈 Đang nhân bản, hoặc siết ngưỡng.

### 9.5 Thư viện quảng cáo (`/ads`) ✅

**Mục đích:** xem từng quảng cáo như Meta Ad Library.

**Hai kênh:** **Quảng cáo** (Meta, TikTok, Snapchat) · **Nguồn hàng & store** (AliExpress, 1688, Taobao).

**Lọc:** từ khoá (nội dung / advertiser / landing), **⚡ Quét nguồn** (live, 50 ad/nước), chip mạng kèm số lượng, thị trường, funnel, loại media (video / ảnh / carousel / chữ), đang chạy / đã dừng, "Chỉ ads đúng sản phẩm" (từ khoá phải có trong tiêu đề / tên sản phẩm / landing, không chỉ trong câu chuyện).

**Sắp xếp quảng cáo:** Mới chạy nhất · Chạy lâu nhất · Nhiều phiên bản nhất · Mới thu thập.

**Thẻ quảng cáo:** mạng · đang chạy / đã dừng · ID thư viện · tier độ mạnh 🏆 · "✓ xác minh X trước" · "↺ chạy lại" · hiển thị / chi tiêu / reach EU (nếu nguồn có) · ngày bắt đầu + số ngày chạy · placement (Facebook, Instagram, Messenger…) · nước · số phiên bản · **Xem chi tiết quảng cáo** (link Ad Library) · advertiser · nội dung ad (Xem thêm) · media (một ảnh / video, hoặc carousel vuốt ngang) · thanh link (domain, tiêu đề, giá, nút CTA) · funnel · hook · angle · **Sản phẩm →** · **⤢ Chi tiết** (bảng trượt đầy đủ, có nút tìm bằng ảnh / so sánh giá / tìm nguồn hàng).

### 9.6 Nguồn hàng & Store (`/marketplace`) ✅

Lưới listing AliExpress / 1688 / Taobao: giá, giá gốc, đã bán, đánh giá, seller. Sắp xếp: Bán nhiều nhất · Nhiều đánh giá · Giá thấp nhất · Mới thu thập. Chọn chip **1688** → hiện nút **Tìm mới trên 1688** (API chính thức, dùng lại kết quả trong 72 giờ; tích thêm Apify nếu thiếu, có phí). Nút **Tìm nguồn hàng bằng ảnh**.

### 9.7 Creative Vault (`/vault`) ❌ gần như không dùng được

**Thiết kế:** kho video / ảnh đã lưu, lọc loại / funnel / trạng thái / nước / từ khoá, gom family, ghim, tải về.

**Thực tế:** theo quyết định ngày 12/10/2026, **video không lưu vào kho**, chỉ lưu ảnh bìa. DB có **0** creative trạng thái `stored`, nên:
- Bộ lọc mặc định "Đã lưu" luôn ra **trang trống**.
- Nút Tải, 📌 Ghim, Family không bao giờ hiện.
- Đổi bộ lọc sang "Đang tải" / "Hết hạn" / "Tất cả" thì thấy ảnh bìa và thông tin ad, nhưng việc đó Thư viện quảng cáo làm tốt hơn.

Phân bố trạng thái hiện tại: expired 45.047 · skipped 21.038 · pending 18.916 · failed 2.009 · archived 256.

### 9.8 Daily Pulse (`/`) ✅

**Hiển thị:** 7 ô KPI (sản phẩm đang theo dõi 4.715 · sản phẩm mới 24h 116 · ads mới 24h 243 · advertiser mới 24h 67 · Potential winners 1 · Hidden winners 1.148 · sản phẩm bão hoà 2); số sản phẩm theo khuyến nghị (bấm để lọc); 8 alert gần nhất; hidden winner nổi bật; biểu đồ active ads và ads mới / ngày 30 ngày; bảng Top opportunities. Nút **Chạy pipeline hôm nay** chấm lại toàn bộ.

**Lưu ý:** "Potential winners" dùng Win Score thế hệ 1 (≥ 65) nên chỉ ra 1, mâu thuẫn với 179 sản phẩm TEST.

### 9.9 Market Radar (`/markets`) ✅

Bảng theo nước (32 nước mục tiêu + 53 nước ngoài mục tiêu): Nước · Vùng · Ads đang chạy · Sellers · Sản phẩm · Ads mới (kỳ này) · Xu hướng · Ads mạnh · Mess / Ladi · Category nổi bật · **Độ tươi** (thời điểm xác minh gần nhất). Khung 7 / 14 / 30 ngày, lọc theo team.

### 9.10 Xếp hạng sản phẩm / Product Explorer (`/products`) ✅

Bảng toàn bộ sản phẩm, lọc: tên / alias / mã, Action, Lifecycle, Saturation, Win ≥, Advertisers ≤, Confidence ≥. Sắp xếp: Opportunity · Win · Rare winner · Rarity · Saturation · Confidence · Growth 7d · Advertisers · Active ads. Cột: Product, Market, Advertisers, Active ads, Growth 7d, Win, Rarity, Saturation, Confidence, Opportunity, Action, Lifecycle.

Cuối trang: **gộp thủ công 2 sản phẩm trùng** (nhập id nguồn và id đích). Đây là chỗ sửa lỗi gom cụm bằng tay.

### 9.11 Competitor Radar (`/competitors`) ✅

KPI: số advertiser theo dõi (2.545), số đang scale nhanh (ads 7 ngày ≥ 2 lần tuần trước; hiện 8), số đang theo dõi. Bảng: ⭐ theo dõi · Advertiser · Country · Active ads · New 7d · Prev 7d · Growth · Products · Markets · Top products. Bấm một advertiser → timeline ad của họ.

### 9.12 Alerts (`/alerts`) ⚠️

Loại đang bật: `new_entrant`, `new_market`, `creative_velocity_spike`, `price_change`, `saturation_increasing`, `competitor_scaling`, `negative_comments_increasing`. Loại đã **tắt** vì quá nhiều rác: `hidden_winner`, `milestone`, nhưng **1.204 alert cũ của hai loại này vẫn còn** và chiếm phần lớn 265 alert chưa đọc. Nên xoá hoặc đánh dấu đã đọc hàng loạt.

### 9.13 AI Agent (`/agent`) ✅

Hỏi bằng tiếng Việt, ví dụ có sẵn: *"Tìm 20 sản phẩm beauty tại Saudi đang tăng nhanh nhưng dưới 10 advertiser"*, *"Đối thủ nào đang scale nhanh?"*, *"Giải thích vì sao PRD_0000001 có điểm này"*. AI dịch câu hỏi thành truy vấn có cấu trúc, **số liệu lấy từ DB**, trả bảng. Engine hiện tại: Gemini (không có key thì dùng bộ phân tích theo luật).

### 9.14 Data & Connectors (`/data`) ✅ (dành cho người vận hành)

4 tab:
- **Nguồn & connector:** danh sách connector (bật / tắt, cấu hình, test, đồng bộ ngay, health), bảng nguồn theo nền tảng.
- **Dung lượng:** video hôm nay / quota, ảnh, dữ liệu thô, đã dọn, đang ghim, ngân sách theo ngăn (Meta 45%, TikTok 30%, ads khác 10%, nguồn hàng 15%).
- **Theo dõi & Upload:** thêm từ khoá / page đối thủ theo dõi, upload file export Pipiads / Minea / BigSpy, bảng tracked queries, feed realtime.
- **Ingest API:** hướng dẫn đẩy dữ liệu vào bằng `X-Ingest-Token`.

---

## 10. EU Market

**Mục đích:** nghiên cứu thị trường **bán lẻ** châu Âu (listing trên sàn, giá, nguồn sỉ B2B), không phải quảng cáo. Nguyên tắc: chỉ dữ liệu từ API được cấp quyền hoặc trang công khai, không đoán số bán.

| Trang | Hiển thị | Trạng thái |
|---|---|---|
| Overview | Số sản phẩm đã gom, listing bán lẻ, offer B2B, snapshot giá, ad match | ❌ 2 sản phẩm |
| Product Explorer | Tìm nhiều từ khoá đa ngôn ngữ (mỗi dòng một từ), lọc nước / giá / nguồn / bán lẻ hay B2B; **dán URL trang sản phẩm công khai** để thêm listing; khung **tìm trên Allegro** qua extension | ⚠️ thêm bằng URL chạy được; Allegro chưa thử thật |
| Chi tiết sản phẩm EU | Offers (dữ liệu thật kèm thời điểm), lịch sử giá, so sánh giá giữa nguồn (EUR), ad match, nguồn B2B, tín hiệu thô | ⚠️ trống |
| Trending | Estimated Trend Signal 0–100 (không phải số bán) | ❌ gần như trống; còn sót code tín hiệu Etsy / idealo đã bỏ |
| Price Intelligence | Biến động giá listing từ snapshot thật; nút **So sánh giá bằng ảnh** | ❌ trống |
| Rare Finds | Hàng ít tương đương **trong dữ liệu đã thu** | ❌ trống |
| Wholesale & Suppliers | Offer B2B, nhà cung cấp, ghép sản phẩm (EAN tự xác nhận, theo tiêu đề cần review) | ❌ cần BigBuy |
| Competitor Shops | Seller trên listing (buybox Kaufland / bol) | ❌ cần Kaufland / bol |
| Ad Matches | Nối sản phẩm EU với ads trong kho | ❌ trống |
| Watchlists & Alerts | Theo dõi từ khoá (đồng bộ 6 giờ / lần), **theo dõi bằng ảnh** | ⚠️ chạy được nhưng chưa có nguồn để đồng bộ |
| Data Sources | Trạng thái từng nguồn (3.4), lịch sử đồng bộ, khung import Allegro từ file JSON | ✅ phản ánh đúng trạng thái |

**Kết luận:** EU Market đã có khung đầy đủ nhưng **chưa có dữ liệu để MKT dùng**. Muốn chạy được cần ít nhất một nguồn: tài khoản seller Kaufland / bol, BigBuy, hoặc Allegro bridge chạy ổn định.

---

## 11. Tìm bằng ảnh và giá tự động

### 11.1 Tìm bằng ảnh (`/visual`) ✅

**Đầu vào:** upload / dán (Ctrl+V) ảnh, URL ảnh, URL trang sản phẩm, hoặc ảnh của một sản phẩm / ad / creative đã có trong kho. Có thể **cắt vùng** ảnh (crop).

**Phạm vi tìm:**
- **Hybrid** (mặc định): tìm trong kho ngay, rồi tìm trên Internet qua Google Lens.
- **Chỉ trong kho**: không tốn lượt.
- **Chỉ Internet**: 1 lượt Lens mỗi ảnh mới (ảnh đã tìm thì dùng cache). Chọn nước để Lens trả giá shop.

**Chế độ:** visual · exact (ảnh trùng) · same_product · similar_product · ads_match (quảng cáo dùng ảnh này) · find_suppliers (nguồn hàng) · compare_prices (so sánh giá).

**Mức khớp** (không bao giờ kết luận "cùng sản phẩm" chỉ vì trông giống):

| Mức | Điều kiện |
|---|---|
| EXACT | Cùng file hoặc hash rất gần (khoảng cách ≤ 6) |
| LIKELY_SAME_PRODUCT | Ảnh giống **và** thông tin khớp (cùng cụm sản phẩm, hoặc CLIP ≥ 0,95 + hash gần) |
| VISUALLY_SIMILAR | Hash ≤ 14 hoặc CLIP ≥ 0,80 |

Chỉ EXACT / LIKELY_SAME vào bảng so sánh giá; còn lại vào "Sản phẩm tương tự". Có nút **Theo dõi** (báo khi xuất hiện ảnh khớp mới) và xác nhận / bác bỏ từng kết quả.

**Độ phủ:** 78.806 / 78.811 ảnh bìa đã được index (hash + CLIP, tìm vector bằng pgvector). Tìm trong kho < 1 giây.

**Hạn mức Internet:** chung 250 lượt SerpApi / tháng (8 Lens / ngày). Còn 236 lượt trong tháng 10.

### 11.2 Giá tự động trên mọi thẻ (PriceTag) ⚠️

Không có trang riêng, không có nút "Tìm giá". Khi thẻ hiện lên, web gửi một yêu cầu cho cả trang, worker tìm giá theo thứ tự:

1. Giá đã có trên ad (chữ trong ad / landing đã đọc).
2. Đọc trực tiếp landing page (JSON-LD → meta → DOM → Shopify `.json`); bị 403 thì nhờ Bright Data đọc hộ.
3. Giá tham khảo đã có của cùng sản phẩm.
4. Bright Data tìm Google (giá trong đoạn trích + đọc link shop đầu tiên).
5. SerpApi Google Shopping, chỉ khi Bright Data lỗi.
6. Không thấy → NOT_FOUND (3 ngày sau mới tìm lại).

Bộ lọc giá giả: tiền tệ phải khớp thị trường, giá 0,5–5.000 USD, kết quả phải trùng ≥ 2 từ với tên. Hạn dùng: giá xác minh 7 ngày, tham khảo 14 ngày.

**Kết quả thật:** 258 lượt tìm → 57 giá xác minh của đối thủ, 2 giá marketplace, 14 tham khảo, **185 không tìm được (72%)**. Nguyên nhân chính: nhiều sản phẩm có tên là câu hook của ad (vd. "Don T Miss Out") chứ không phải tên hàng, và funnel mess không có trang bán.

---

## 12. Công cụ thu thập ngoài web: Chrome extension

| Extension | Thư mục | Làm gì | Trạng thái |
|---|---|---|---|
| **Meta Ad Library** | `apps/extension` | Khi người dùng duyệt `facebook.com/ads/library`, bắt dữ liệu trang đã tải và gửi về kho; có nút "Lưu trang này vào kho" cho trang sản phẩm bất kỳ. Ad lưu bằng extension không bao giờ bị tự xoá và hiện ở tab "Saved By Team" | ✅ |
| **Allegro Bridge** | `extensions/allegro-bridge` | Thủ công: đọc trang listing Allegro người dùng tự mở. Từ web: web gửi từ khoá → extension mở 1 tab nền, đọc trang 1, đóng tab, gửi về API. Gặp CAPTCHA / DataDome thì dừng, đưa tab lên cho người dùng tự xử lý | ⚠️ parser **chưa thử trên Allegro thật**; mới có 1 job, kết quả `failed` |

Cài: `chrome://extensions` → bật Developer mode → Load unpacked → chọn thư mục → điền địa chỉ API và token (`INGEST_TOKEN` cho Meta, `TOOLSPY_BRIDGE_TOKEN` cho Allegro).

---

## 13. Thực trạng dữ liệu 13/10/2026

| Chỉ số | Giá trị |
|---|---|
| Sản phẩm | 6.269 (4.715 có ad đang chạy ở thị trường mục tiêu) |
| Khuyến nghị | SKIP 5.370 · WATCH 719 · **TEST 179** · REVIEW 1 · **TEST_NOW 0** |
| Quadrant | Low signal 5.880 · Stable winner 357 · Experimental 27 · Breakout 5 |
| Ads | Meta 6.846 (6.771 đang chạy) · TikTok 1.839 (1.404 đang chạy) |
| Listing nguồn hàng | AliExpress 1.194 · 1688 563 · Taobao 30 → 1.426 sản phẩm có giá nhập |
| Ads theo funnel | ladi 4.238 · other 2.213 · mess 1.671 · app 464 · form 99 |
| Ads theo nước (top) | không ghi nước 3.401 · PH 1.602 · SA 733 · US 581 · VN 253 · AE 233 · GB 182 · KW 162 |
| Advertiser | 3.263 |
| Sản phẩm chỉ có 1 ad | 82% |
| Sản phẩm "(chưa đặt tên)" | 491 |
| Vote | **2** (cả hai là WATCH) |
| Review / comment | 1.832 (AliExpress 678, landing page 1.154) |
| Alert | 1.467, 265 chưa đọc |
| Connector đang bật | 10 / 22 |
| Từ khoá theo dõi | 56 (49 bật), chạy lần cuối 13/10 09:22 |
| Ad mới nhất vào kho | 13/10/2026 08:06 (giờ VN) |
| Ảnh đã index cho tìm bằng ảnh | 78.806 |
| EU Market | 2 listing, 2 sản phẩm |

---

## 14. Đánh giá: cái gì hữu ích, cái gì gây hiểu nhầm

### 14.1 Hữu ích thật cho MKT hằng ngày

1. **Tìm sản phẩm + ⚡ quét trực tiếp**: một lần bấm quét cả Meta, TikTok, AliExpress và 1688, gom theo sản phẩm, lọc đúng funnel Mess / Ladi. Đây là chức năng mạnh nhất.
2. **Product Radar theo PH**: thấy sản phẩm nào đang tăng ads trong tuần, ai đang scale (×N).
3. **Trang chi tiết, khung "Thông tin cho MKT"**: giá đối thủ, giá nhập, biên ước tính, số người bán, ad chạy bao lâu, landing page, creative mẫu.
4. **Khuyến nghị kèm lý do bằng số**: đọc được vì sao TEST / SKIP, không phải hộp đen.
5. **Tìm bằng ảnh**: từ một ảnh ra ads, nguồn hàng, giá; trong kho không tốn tiền.
6. **Thư viện quảng cáo**: xem ad như Ad Library, có tier độ mạnh, số phiên bản, trạng thái đã xác minh.

### 14.2 Gây hiểu nhầm hoặc chưa dùng được

| # | Vấn đề | Ảnh hưởng | Đề xuất |
|---|---|---|---|
| 1 | **Hai hệ điểm cùng hiển thị.** Win Score / Rare Winner / Potential winners (thế hệ 1) mâu thuẫn với Opp / nhãn khuyến nghị (v2). Ví dụ PRD_0000001: Win 24 "Weak" nhưng Opp 70 "TEST" | MKT không biết tin số nào | Ẩn Win, Rare Winner, Internal, Customer rejection; đổi KPI "Potential winners" sang đếm TEST + TEST_NOW |
| 2 | **Cộng 15 điểm PH** cho mọi sản phẩm có ad ở PH, kể cả khi landing bán bằng £ | Opp bị đẩy cao cho sản phẩm thực chất của thị trường khác | Chỉ cộng khi PH là thị trường chính của sản phẩm |
| 3 | **Wave / Creative dò từ khoá trong chữ ad**, không xem video | Điểm có thể sai với ad tiếng Tagalog / Ả Rập hoặc copy ngắn | Đọc như tham khảo; cân nhắc transcript / OCR cho video top |
| 4 | **Creative Vault mặc định trống**; nút Tải, Ghim, Family không bao giờ hiện vì không lưu video | Người dùng tưởng lỗi | Đổi mặc định sang "Tất cả", hoặc gộp Vault vào Thư viện quảng cáo |
| 5 | **Copy URL / Video gốc** trỏ link CDN có hạn | Link copy thường đã chết | Copy link trang Ad Library (`snapshot_url`) thay vì link file |
| 6 | **Hidden Winners quá rộng** (1.148, hầu hết chỉ là "1 người bán + chạy lâu") | Không lọc ra được gì | Bắt buộc thêm momentum hoặc nhân bản |
| 7 | **Chỉ 2 vote** | MKT appeal, Taste Model, Market DNA phần "team thích" không có tác dụng | Quy định vote hằng ngày; ghi tên thật |
| 8 | **Ô không có nguồn**: Traffic, Search trend, Keyword competition, Customer rejection, Internal Win | Hiện 0 hoặc "—", trông như dữ liệu | Ẩn đến khi có nguồn |
| 9 | **Alert rác cũ** (1.204 hidden_winner + milestone đã tắt) | Lấn át alert có giá trị | Đánh dấu đã đọc / xoá hàng loạt |
| 10 | **82% sản phẩm chỉ có 1 ad**, 491 sản phẩm chưa đặt tên | Demand / số người bán bị thấp, nhiều sản phẩm trùng nhau | Gom cụm thêm theo page + landing + ảnh (đã có hash ảnh); dùng nút gộp ở Xếp hạng sản phẩm |
| 11 | **Giá: 72% không tìm được** | Biên ước tính thường trống | Cải thiện tên sản phẩm trước; dùng Lens theo ảnh cho sản phẩm TEST |
| 12 | **EU Market trống** | 10 trang chưa có dữ liệu | Quyết định nguồn (Kaufland / bol / BigBuy / Allegro), hoặc ẩn khỏi sidebar đến khi có |
| 13 | **TikTok phụ thuộc trình duyệt headless**, chưa đăng nhập chỉ lấy trang 1 | Phủ TikTok PH / ME mỏng | Đăng nhập một lần bằng `scripts/tiktok_login.py` |
| 14 | **Lifecycle sau test là chuyển tay**, không có dữ liệu đơn | Không kiểm chứng được TEST có đúng không | Cho nhập CSV kết quả test để hiệu chỉnh ngưỡng |

### 14.3 Gợi ý quy trình dùng hằng ngày

1. **Sáng:** Daily Pulse → Product Radar (chọn PH, 7 ngày, sắp theo Xu hướng).
2. **Theo ngách:** Tìm sản phẩm → nhập 3–5 từ khoá → ⚡ → sắp theo Opportunity → lọc team.
3. **Với mỗi ứng viên TEST:** mở chi tiết → đọc lý do, giá đối thủ, biên, số người bán, ad lâu nhất → xem creative → **vote kèm lý do**.
4. **Chưa có giá nhập:** bấm 🚚 Tìm nguồn hàng (ảnh) hoặc tìm trên 1688 ở Nguồn hàng & Store.
5. **Bỏ qua:** Win Score, Creative Vault, EU Market (cho đến khi có dữ liệu).
