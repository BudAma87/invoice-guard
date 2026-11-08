"""Security regressions from the security test report (cases A2, A3, D3, E4, E5, F2, F3)."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from libs.config import settings
from libs.core import store as store_mod
from libs.core.store import BadKey, ObjectStore
from tests.conftest import Env


def H(user: str, version: int | None = None) -> dict:
    h = {"X-User": user}
    if version is not None:
        h["If-Match"] = str(version)
    return h


# ------------------------------------------------------------------ identity (F2)


def test_dev_auth_is_off_by_default():
    assert type(settings).model_fields["dev_auth"].default is False


def test_missing_user_header_is_401(api):
    assert api.get("/me").status_code == 401


def test_dev_auth_off_fails_closed(api, monkeypatch):
    monkeypatch.setattr(settings, "dev_auth", False)
    assert api.get("/me", headers=H("a.wijesinghe")).status_code == 501
    assert api.get("/users").status_code == 404


# ------------------------------------------------------------------ signed PDF links (F1)


async def _pdf_env(env: Env, api, monkeypatch):
    inv = await env.run("wireframe")
    monkeypatch.setattr(settings, "object_store_dir", str(env.store.root))
    return inv


def test_no_public_default_signing_secret():
    assert store_mod._SECRET != b"dev-signing-secret"
    assert len(store_mod._SECRET) >= 32


async def test_valid_link_still_works(env: Env, api, monkeypatch):
    inv = await _pdf_env(env, api, monkeypatch)
    link = api.get(f"/invoices/{inv.invoice_id}/pdf", headers=H("n.silva")).json()
    assert api.get(link["url"]).status_code == 200


async def test_link_forged_with_old_default_secret_is_rejected(env: Env, api, monkeypatch):
    inv = await _pdf_env(env, api, monkeypatch)
    exp = int(time.time()) + 200
    sha = inv.pdf_sha256
    forged = hmac.new(b"dev-signing-secret", f"{sha}:{exp}".encode(), hashlib.sha256).hexdigest()
    assert api.get(f"/files/{sha}.pdf?exp={exp}&sig={forged[:32]}").status_code == 403


async def test_expiry_beyond_ttl_is_rejected(env: Env, api, monkeypatch):
    inv = await _pdf_env(env, api, monkeypatch)
    sha = inv.pdf_sha256
    exp, sig = store_mod.sign(sha, ttl_s=10 * 365 * 86400)  # correctly signed, but too long
    assert api.get(f"/files/{sha}.pdf?exp={exp}&sig={sig}").status_code == 403


@pytest.mark.parametrize("name", ["..%5C..%5Coutside", "..%2F..%2Foutside", "ABC", "x" * 64])
async def test_non_hash_keys_never_reach_the_filesystem(env: Env, api, monkeypatch, name):
    await _pdf_env(env, api, monkeypatch)
    (env.store.root.parent / "outside.pdf").write_bytes(b"%PDF-1.4 secret")
    exp = int(time.time()) + 200
    r = api.get(f"/files/{name}.pdf?exp={exp}&sig={'0' * 32}")
    assert r.status_code == 404
    assert b"secret" not in r.content


@pytest.mark.parametrize("sha", ["..\\..\\outside", "../outside", "A" * 64, "", "0" * 63])
def test_store_rejects_bad_keys(tmp_path, sha):
    s = ObjectStore(tmp_path / "blobs")
    with pytest.raises(BadKey):
        s.get(sha)
    with pytest.raises(BadKey):
        s.exists(sha)


def test_store_rejects_bad_extension(tmp_path):
    with pytest.raises(BadKey):
        ObjectStore(tmp_path).exists("0" * 64, ext="pdf/../../x")


# ------------------------------------------------------------------ input limits (F5)


async def test_checker_comment_is_bounded(env: Env, api):
    inv = await env.run("wireframe")
    r = api.post(
        f"/invoices/{inv.invoice_id}/check",
        headers=H("k.fernando", inv.version),
        json={"action": "send_back", "comment": "z" * 2001},
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ HTTP hardening (F4, F6)


def test_security_headers_on_api_responses(api):
    r = api.get("/me", headers=H("n.silva"))
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "max-age" in r.headers["strict-transport-security"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


async def test_pdf_keeps_its_own_cache_and_csp(env: Env, api, monkeypatch):
    inv = await _pdf_env(env, api, monkeypatch)
    link = api.get(f"/invoices/{inv.invoice_id}/pdf", headers=H("n.silva")).json()
    r = api.get(link["url"])
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.headers["content-security-policy"] == "default-src 'none'; script-src 'none'"


def test_rate_limit_returns_429(api, monkeypatch):
    from services.api import main

    monkeypatch.setattr(main.limiter, "per_min", 5)
    codes = [api.get("/me", headers=H("n.silva")).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    # rotating to an unknown X-User value does not mint a fresh bucket
    rotated = [api.get("/me", headers=H(f"nobody-{i}")).status_code for i in range(7)]
    assert rotated[:5] == [401] * 5 and rotated[5:] == [429, 429]
    assert api.get("/health").status_code == 200  # health checks are never limited
