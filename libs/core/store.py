"""Content-addressed, write-once object store (local FS in dev; S3/Azure Blob in prod).

Key = sha256/{hash}.pdf. Re-uploading the same file is a no-op. PDFs reach the browser
only through short-lived HMAC-signed URLs.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

from libs.config import settings

_SECRET = (settings.anthropic_api_key or "dev-signing-secret").encode()  # vault in prod


class ObjectStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or settings.object_store_dir)

    def _path(self, sha: str, ext: str = "pdf") -> Path:
        return self.root / "sha256" / f"{sha}.{ext}"

    def put(self, data: bytes, ext: str = "pdf") -> str:
        sha = hashlib.sha256(data).hexdigest()
        p = self._path(sha, ext)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return sha

    def get(self, sha: str, ext: str = "pdf") -> bytes:
        return self._path(sha, ext).read_bytes()

    def exists(self, sha: str, ext: str = "pdf") -> bool:
        return self._path(sha, ext).exists()


def sign(sha: str, ttl_s: int = 300, now: float | None = None) -> tuple[int, str]:
    exp = int((now or time.time()) + ttl_s)
    sig = hmac.new(_SECRET, f"{sha}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return exp, sig


def verify(sha: str, exp: int, sig: str, now: float | None = None) -> bool:
    if exp < (now or time.time()):
        return False
    good = hmac.new(_SECRET, f"{sha}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, sig)
