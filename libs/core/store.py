"""Content-addressed, write-once object store (local FS in dev; S3/Azure Blob in prod).

Key = sha256/{hash}.pdf. Re-uploading the same file is a no-op. PDFs reach the browser
only through short-lived HMAC-signed URLs.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path

from libs.config import settings

# Dedicated key, never derived from another credential. Unset = random per process, so a
# link only verifies in the process that signed it and no public default key exists.
_SECRET = settings.signing_secret.encode() if settings.signing_secret else secrets.token_bytes(32)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXT = re.compile(r"[a-z0-9]{1,8}")


class BadKey(ValueError):
    """Object key is not a lowercase sha256 hex digest (or the extension is malformed)."""


def valid_sha(sha: str) -> bool:
    return bool(_SHA256.fullmatch(sha))


class ObjectStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or settings.object_store_dir)

    def _path(self, sha: str, ext: str = "pdf") -> Path:
        if not valid_sha(sha) or not _EXT.fullmatch(ext):
            raise BadKey(sha)
        base = (self.root / "sha256").resolve()
        p = (base / f"{sha}.{ext}").resolve()
        if p.parent != base:  # defence in depth: never leave the store
            raise BadKey(sha)
        return p

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


def sign(sha: str, ttl_s: int | None = None, now: float | None = None) -> tuple[int, str]:
    exp = int((now or time.time()) + (settings.pdf_link_ttl_s if ttl_s is None else ttl_s))
    sig = hmac.new(_SECRET, f"{sha}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return exp, sig


def verify(sha: str, exp: int, sig: str, now: float | None = None) -> bool:
    t = now or time.time()
    if not valid_sha(sha) or exp < t or exp > t + settings.pdf_link_ttl_s:
        return False
    good = hmac.new(_SECRET, f"{sha}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, sig)
