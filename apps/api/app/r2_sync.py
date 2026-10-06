"""Cloudflare R2 helper.

  python -m app.r2_sync --check     kiểm tra token, R2 đã bật chưa, bucket có chưa, ghi/đọc thử 1 file
  python -m app.r2_sync --setup     như --check, và tạo bucket R2_BUCKET nếu chưa có
  python -m app.r2_sync --upload    đẩy toàn bộ creatives/ thumbs/ raw/ ở máy lên R2 (bỏ qua file đã có, chạy lại được)
  python -m app.r2_sync --upload --only thumbs    chỉ đẩy một thư mục

Đọc cấu hình từ .env (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, CLOUDFLARE_API_TOKEN).
Sau khi upload xong: đặt STORAGE_BACKEND=r2 trong .env và khởi động lại app.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import time
from pathlib import Path

import httpx

from .storage import DATA_DIR

ACC = os.getenv("R2_ACCOUNT_ID", "")
BUCKET = os.getenv("R2_BUCKET", "spyads")
TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
ENDPOINT = os.getenv("R2_ENDPOINT") or f"https://{ACC}.r2.cloudflarestorage.com"


def s3():
    import boto3
    from botocore.config import Config

    return boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto",
                        config=Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=20, read_timeout=120))


def cf(method: str, path: str, **kw):
    return httpx.request(method, f"https://api.cloudflare.com/client/v4/accounts/{ACC}{path}",
                         headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30, **kw).json()


def check(create: bool) -> bool:
    ok = True
    if not ACC or not os.getenv("R2_ACCESS_KEY_ID"):
        print("✗ Thiếu R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY trong .env")
        return False
    if TOKEN:
        v = cf("GET", "/tokens/verify")
        print("✓ Cloudflare API token hợp lệ" if v.get("success") else f"✗ Token: {v.get('errors')}")
        b = cf("GET", "/r2/buckets")
        if not b.get("success"):
            msg = "; ".join(e.get("message", "") for e in b.get("errors", []))
            print(f"✗ R2 API: {msg}")
            if "enable R2" in msg:
                print("  → Vào https://dash.cloudflare.com → R2 Object Storage → bật R2 (Purchase / Enable), rồi chạy lại lệnh này.")
            return False
        names = [x["name"] for x in (b.get("result") or {}).get("buckets", [])]
        print(f"✓ R2 đã bật · bucket hiện có: {names or '(chưa có)'}")
        if BUCKET not in names:
            if create:
                r = cf("POST", "/r2/buckets", json={"name": BUCKET})
                print(f"✓ Đã tạo bucket '{BUCKET}'" if r.get("success") else f"✗ Tạo bucket lỗi: {r.get('errors')}")
                ok = ok and bool(r.get("success"))
            else:
                print(f"✗ Chưa có bucket '{BUCKET}' — chạy với --setup để tạo")
                return False
    try:
        c = s3()
        key = f"_healthcheck/{int(time.time())}.txt"
        c.put_object(Bucket=BUCKET, Key=key, Body=b"ok", ContentType="text/plain")
        body = c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        c.delete_object(Bucket=BUCKET, Key=key)
        print(f"✓ S3 API ghi/đọc/xoá thử thành công trên bucket '{BUCKET}'" if body == b"ok" else "✗ Đọc lại không khớp")
    except Exception as e:
        print(f"✗ S3 API lỗi: {type(e).__name__}: {e}")
        if "HANDSHAKE" in str(e).upper():
            print("  → Máy chủ R2 từ chối kết nối: thường do tài khoản chưa bật R2.")
        ok = False
    return ok


def upload(only: str | None):
    c = s3()
    roots = [only] if only else ["creatives", "thumbs", "raw"]
    files = [p for r in roots if (DATA_DIR / r).exists() for p in (DATA_DIR / r).rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"{len(files)} file · {total / 1073741824:.2f} GB cần kiểm tra")
    existing: set[str] = set()
    for r in roots:
        for page in c.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=f"{r}/"):
            existing |= {o["Key"] for o in page.get("Contents", [])}
    done = up = 0
    t0 = time.time()
    for i, p in enumerate(files, 1):
        key = p.relative_to(DATA_DIR).as_posix()
        if key not in existing:
            ctype = mimetypes.guess_type(p.name)[0] or ("application/x-ndjson" if p.suffix == ".jsonl" else "application/octet-stream")
            c.upload_file(str(p), BUCKET, key, ExtraArgs={"ContentType": ctype})
            up += p.stat().st_size
        done += p.stat().st_size
        if i % 25 == 0 or i == len(files):
            rate = up / max(1, time.time() - t0) / 1048576
            print(f"  {i}/{len(files)} · {done / total * 100:.0f}% · đã đẩy {up / 1048576:.0f} MB · {rate:.1f} MB/s", flush=True)
    print("Xong. Đặt STORAGE_BACKEND=r2 trong .env rồi khởi động lại app.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--only", choices=["creatives", "thumbs", "raw"])
    a = ap.parse_args()
    if a.upload:
        if not check(create=False):
            sys.exit(1)
        upload(a.only)
    else:
        sys.exit(0 if check(create=a.setup) else 1)


if __name__ == "__main__":
    main()
