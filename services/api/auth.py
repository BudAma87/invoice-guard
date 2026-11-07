"""Identity. Production: OIDC (Entra ID / Okta) with roles + entity scope from group claims.
Dev (`DEV_AUTH=true`): the `X-User` header picks one of the seeded users below.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from libs.config import settings
from libs.core.review import User

DEV_USERS: dict[str, User] = {
    u.id: u
    for u in [
        User("n.silva", "Nimali Silva", frozenset({"reviewer"}), frozenset({"E01", "E02"})),
        User("j.perera", "Janith Perera", frozenset({"reviewer"}), frozenset({"E01"})),
        User(
            "k.fernando",
            "Kasun Fernando",
            frozenset({"checker", "reviewer"}),
            frozenset({"E01", "E02"}),
        ),
        User(
            "a.wijesinghe",
            "Anushka Wijesinghe",
            frozenset({"supervisor", "reviewer"}),
            frozenset({"E01", "E02"}),
        ),
        User("r.jayasuriya", "Ruwan Jayasuriya", frozenset({"auditor"}), frozenset({"E01", "E02"})),
    ]
}


def current_user(x_user: str | None = Header(default=None)) -> User:
    if not settings.dev_auth:
        raise HTTPException(501, "OIDC not configured")  # wire the IdP here
    if not x_user:
        raise HTTPException(401, "X-User header required")
    user = DEV_USERS.get(x_user)
    if user is None:
        raise HTTPException(401, "Unknown user")
    return user


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
