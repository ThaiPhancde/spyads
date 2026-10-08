"""Object storage for raw data + creative media.

STORAGE_BACKEND=local (default, ./data) or r2 (Cloudflare R2 / any S3-compatible).
Container disks are usually ephemeral, so in production use R2 (or a persistent volume mounted at DATA_DIR).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterator

BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError("invalid key")
        return p

    def put(self, key: str, data: bytes, content_type: str | None = None):
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def put_file(self, key: str, src: Path, content_type: str | None = None):
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.move(str(src), str(p))  # works across drives / volumes

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def size(self, key: str) -> int | None:
        p = self.path(key)
        return p.stat().st_size if p.exists() else None

    def get(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def delete(self, key: str) -> int:
        p = self.path(key)
        if p.exists():
            n = p.stat().st_size
            p.unlink()
            return n
        return 0

    def delete_prefix(self, prefix: str) -> int:
        import shutil

        p = self.path(prefix)
        if not p.exists():
            return 0
        n = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        shutil.rmtree(p, ignore_errors=True)
        return n

    def public_url(self, key: str, download_name: str | None = None) -> str | None:
        return None  # served by the API (/api/media/...)

    def list(self, prefix: str) -> Iterator[str]:
        base = self.path(prefix)
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    yield p.relative_to(self.root).as_posix()


class S3Storage:
    """Cloudflare R2: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, optional R2_PUBLIC_URL."""

    def __init__(self):
        import boto3

        account = os.environ["R2_ACCOUNT_ID"]
        self.bucket = os.environ["R2_BUCKET"]
        self.public_base = os.getenv("R2_PUBLIC_URL", "").rstrip("/") or None
        self.s3 = boto3.client(
            "s3",
            endpoint_url=os.getenv("R2_ENDPOINT", f"https://{account}.r2.cloudflarestorage.com"),
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )

    def put(self, key: str, data: bytes, content_type: str | None = None):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, **({"ContentType": content_type} if content_type else {}))

    def put_file(self, key: str, src: Path, content_type: str | None = None):
        self.s3.upload_file(str(src), self.bucket, key, ExtraArgs={"ContentType": content_type} if content_type else None)
        src.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def size(self, key: str) -> int | None:
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except Exception:
            return None

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> int:
        n = self.size(key) or 0
        self.s3.delete_object(Bucket=self.bucket, Key=key)
        return n

    def delete_prefix(self, prefix: str) -> int:
        freed, batch = 0, []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                freed += o["Size"]
                batch.append({"Key": o["Key"]})
        for i in range(0, len(batch), 1000):
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": batch[i:i + 1000]})
        return freed

    def usage_bytes(self) -> int:
        return sum(o["Size"] for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket)
                   for o in page.get("Contents", []))

    def public_url(self, key: str, download_name: str | None = None) -> str:
        if self.public_base and not download_name:
            return f"{self.public_base}/{key}"
        params = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        return self.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=3600)

    def list(self, prefix: str) -> Iterator[str]:
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                yield o["Key"]


_store = None


def store():
    global _store
    if _store is None:
        _store = S3Storage() if BACKEND in ("r2", "s3") else LocalStorage(DATA_DIR)
    return _store


def write_raw_batch(source: str, entity_type: str, records: list[dict]) -> str:
    """Raw data layer: /raw/<source>/<YYYY-MM-DD>/<entity>_<HHMMSSffffff>.jsonl — reprocessable later."""
    now = datetime.utcnow()
    import gzip

    key = f"raw/{source}/{now:%Y-%m-%d}/{entity_type}_{now:%H%M%S%f}.jsonl.gz"  # gzip: raw lake ~10x smaller
    body = gzip.compress("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in records).encode(), 6)
    store().put(key, body, "application/gzip")
    return key
