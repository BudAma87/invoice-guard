from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from libs.core.review import NotFound, User
from libs.db import models as m
from libs.db.session import SessionLocal
from services.api.auth import current_user


def db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def scoped_invoice(session: Session, invoice_id: str, user: User) -> m.Invoice:
    """Repository-layer entity scope. Out of scope is 404 (never reveal existence)."""
    inv = session.get(m.Invoice, invoice_id)
    if inv is None or inv.entity_id not in user.entities:
        raise NotFound("Invoice not found")
    return inv


__all__ = ["db", "scoped_invoice", "current_user", "Depends"]
