# Chạy ToolSpy online trên 1 VPS (AWS) + tên miền id.vn

Kiến trúc: **1 VPS chạy tất cả** bằng Docker Compose (Postgres + API + Worker + Web + Caddy lấy HTTPS tự động).
Không cần Vercel: web dùng SSE và proxy `/api/*` sang API nên để cùng máy là đơn giản và ít lỗi nhất.
File cấu hình: [deploy/docker-compose.yml](../deploy/docker-compose.yml), [deploy/Caddyfile](../deploy/Caddyfile).

## 0. Cần chuẩn bị (kiểm tra trước)

| Thứ | Yêu cầu |
|---|---|
| VPS | Ubuntu 22.04/24.04, **RAM ≥ 4 GB**, ổ đĩa ≥ 30 GB (data ~3 GB + Postgres ~1,5 GB + image Docker ~3 GB) |
| Địa chỉ IP | Nên gắn **Elastic IP** (IP cố định) để DNS không đổi khi restart |
| Security Group (AWS) | Mở **22** (chỉ IP của bạn), **80**, **443** (mọi nơi). **Không** mở 5432, 8000, 3000 |
| Tên miền | Quyền sửa DNS của miền id.vn (ví dụ tạo `toolspy.tenban.id.vn`) |
| File `.pem` | Đúng key của VPS này (xem mục 1) |

## 1. SSH vào VPS từ Windows

PowerShell (thay `IP` và `ubuntu` cho đúng; Amazon Linux thì user là `ec2-user`):

```powershell
# Windows từ chối key nếu file mở quyền cho nhiều người dùng. Chỉ để lại quyền cho bạn:
$k = "D:\ai-operations-server-ver-2.pem"
icacls $k /inheritance:r
icacls $k /grant:r "$($env:USERNAME):(R)"
ssh -i $k ubuntu@IP
```

## 2. Cài Docker trên VPS

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
docker compose version      # phải in ra phiên bản
```

Nếu RAM dưới 4 GB, thêm swap để build không bị kill:
```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

## 3. Trỏ tên miền

Tại nơi quản lý DNS của miền id.vn, tạo bản ghi:

| Loại | Tên | Giá trị |
|---|---|---|
| A | `toolspy` | IP công khai (Elastic IP) của VPS |

Kiểm tra: `nslookup toolspy.tenban.id.vn` phải ra đúng IP. Caddy chỉ xin được chứng chỉ HTTPS khi DNS đã đúng và cổng 80/443 đã mở.

## 4. Đưa code lên VPS

Cách đơn giản (không cần GitHub key trên VPS), chạy **trên máy Windows**:

```powershell
cd D:\ToolSpy
ssh -i $k ubuntu@IP "mkdir -p ~/toolspy"
# không gửi .venv, node_modules, data, .git
tar --exclude=.venv --exclude=node_modules --exclude=.next --exclude=apps/api/data --exclude=.git --exclude=reference -czf $env:TEMP\toolspy.tgz .
scp -i $k $env:TEMP\toolspy.tgz ubuntu@IP:~/toolspy.tgz
ssh -i $k ubuntu@IP "cd ~/toolspy && tar xzf ../toolspy.tgz"
```

`.env` đã nằm trong gói (nó có key thật), nên **chỉ gửi qua SSH như trên, đừng đưa lên GitHub công khai**.

## 5. Sửa `.env` trên VPS

```bash
cd ~/toolspy && nano .env
```

Thêm hoặc sửa các dòng (các key API cũ như R2, Cerebras, Groq… giữ nguyên):

```
DOMAIN=toolspy.tenban.id.vn
POSTGRES_PASSWORD=<mật khẩu dài ngẫu nhiên, chạy: openssl rand -hex 24>
APP_URL=https://toolspy.tenban.id.vn
WEB_ORIGIN=https://toolspy.tenban.id.vn
AUTH_COOKIE_SECURE=1
ADMIN_TOKEN=<giá trị mới: openssl rand -hex 32>
INGEST_TOKEN=<giá trị mới: openssl rand -hex 32>
STORAGE_BACKEND=r2
```

Dòng `DATABASE_URL` cũ trong `.env` có thể để nguyên: compose tự ghi đè bằng Postgres trong Docker.
Nếu đổi `ADMIN_TOKEN` / `INGEST_TOKEN` thì extension phải dùng `INGEST_TOKEN` mới.
Nếu `.env` có `PUBLIC_API_BASE_URL=` trỏ tunnel cũ thì đổi thành `https://toolspy.tenban.id.vn`.

## 6. Khởi động database và nạp dữ liệu

Tạo bản dump trên máy Windows (Postgres local, mất khoảng 1 phút), rồi gửi lên:

```powershell
cd D:\ToolSpy\apps\api\data\backups   # hoặc dùng bản dump mới nhất đã có ở đây
scp -i $k pre_repair_cta_2026-11-04.dump ubuntu@IP:~/toolspy/
```

Hoặc tạo bản mới: `pg_dump -Fc -U postgres -h localhost toolspy -f D:\toolspy.dump`.

Trên VPS:

```bash
cd ~/toolspy
docker compose -f deploy/docker-compose.yml --env-file .env up -d db
# nạp dump (bỏ qua cảnh báo về owner/role):
docker compose -f deploy/docker-compose.yml --env-file .env exec -T db \
  pg_restore -U toolspy -d toolspy --no-owner --no-privileges --clean --if-exists < pre_repair_cta_2026-11-04.dump
```

## 7. Chép ảnh và model

Thumbnail và model CLIP (khoảng 2,2 GB) nằm trong `apps/api/data`, không nằm trong Postgres.
Chép vào volume `appdata`:

```powershell
cd D:\ToolSpy\apps\api\data
tar -czf $env:TEMP\data.tgz thumbs models
scp -i $k $env:TEMP\data.tgz ubuntu@IP:~/data.tgz
```
```bash
# trên VPS: tạo volume, chép vào
docker compose -f deploy/docker-compose.yml --env-file .env create api
docker run --rm -v toolspy_appdata:/data -v ~/data.tgz:/data.tgz alpine tar xzf /data.tgz -C /data
```

(Tên volume là `<tên thư mục>_appdata`; xem bằng `docker volume ls`.)
Video đã ở Cloudflare R2 nên không phải chép.

## 8. Chạy toàn bộ

```bash
docker compose -f deploy/docker-compose.yml --env-file .env up -d --build
docker compose -f deploy/docker-compose.yml ps          # 5 dịch vụ phải "running"
docker compose -f deploy/docker-compose.yml logs -f api # Ctrl+C để thoát
```

Lần build đầu mất 5–10 phút. Mở `https://toolspy.tenban.id.vn/login`.

## 9. Tạo tài khoản cho mọi người

App chỉ có tài khoản MKT (không có ADMIN). Người dùng tự đăng ký tại `/login` (API `/api/auth/register` là công khai),
nên **ai biết link cũng tạo được tài khoản và xem dữ liệu**. Nếu chỉ muốn người được mời vào, đặt thêm
`BASIC_AUTH_USER` / `BASIC_AUTH_PASS` cho dịch vụ `web` (mục 11) và chia sẻ mật khẩu đó.

Tạo tài khoản bằng tay:

```bash
docker compose -f deploy/docker-compose.yml exec api python -m app.workspace.cli create-user ten@email.com
# khóa / mở khóa / đặt lại mật khẩu: lock | unlock | reset-password EMAIL
```

## 10. Vận hành hằng ngày

| Việc | Lệnh |
|---|---|
| Xem log | `docker compose -f deploy/docker-compose.yml logs -f --tail=100 api worker` |
| Cập nhật code | gửi lại gói như mục 4, rồi `up -d --build` |
| Khởi động lại | `docker compose -f deploy/docker-compose.yml restart` |
| Backup DB hằng đêm | `crontab -e` thêm: `0 3 * * * cd ~/toolspy && docker compose -f deploy/docker-compose.yml exec -T db pg_dump -Fc -U toolspy toolspy > ~/backup-$(date +\%F).dump` |
| Dọn backup cũ | `find ~ -name 'backup-*.dump' -mtime +7 -delete` |

## 11. Giới hạn cần biết

- **Chỉ 1 bản API và 1 bản Worker.** Event bus nằm trong bộ nhớ nên không mở rộng ngang được.
- **Collector TikTok** (`tiktok_top_ads_headless`, không cần đăng nhập) dùng Chrome headless; Dockerfile đã cài Chrome và đặt `BROWSER_CHANNEL=chrome`. Rủi ro: TikTok có thể chặn IP datacenter AWS (app báo "nguồn từ chối trang"). Khi đó chạy collector từ máy Windows của bạn trỏ về API online, hoặc dùng proxy dân cư. Lấy đủ 500 ads/quốc gia cần đăng nhập một lần: chạy `scripts/tiktok_login.py` trên máy Windows rồi chép thư mục `data/browser/tiktok` vào volume `appdata`.
- Khóa thêm toàn site: trong `deploy/docker-compose.yml`, dịch vụ `web`, mục `environment` thêm `BASIC_AUTH_USER: ten` và `BASIC_AUTH_PASS: matkhau`, rồi `up -d`.
