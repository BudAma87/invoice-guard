"""OWASP N1 regressions: the ingest consumer treats every message as untrusted input."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from libs.config import settings
from libs.db import models as m
from services.ingest.consumer import (
    IngestRejected,
    dead_letter,
    parse_message,
    read_spool_file,
    receive,
)
from tests.conftest import Env

PDF = b"%PDF-1.4\n% test invoice\n"


def msg(**body) -> bytes:
    base = {"message_id": "msg-1", "entity_id": "E01"}
    return json.dumps({**base, **body}).encode()


def code_of(fn, *a, **kw) -> str:
    with pytest.raises(IngestRejected) as e:
        fn(*a, **kw)
    return e.value.code


@pytest.fixture
def spool(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "spool"
    root.mkdir()
    (root / "inv-1.pdf").write_bytes(PDF)
    (root / "sub").mkdir()
    (root / "sub" / "inv-2.pdf").write_bytes(PDF)
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4 outside the spool")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-not-real")
    monkeypatch.setattr(settings, "ingest_spool_dir", str(root))
    return root


# ------------------------------------------------------------------ file references


def test_file_reference_refused_when_no_spool_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "ingest_spool_dir", "")
    assert code_of(parse_message, msg(pdf_path="inv-1.pdf")) == "FILE_REF_DISABLED"


def test_file_inside_spool_is_accepted(spool):
    assert parse_message(msg(pdf_path="inv-1.pdf")).pdf == PDF
    assert parse_message(msg(pdf_path="sub/inv-2.pdf")).pdf == PDF


@pytest.mark.parametrize(
    "name",
    ["../secret.pdf", "..\\secret.pdf", "sub/../../secret.pdf", "../.env", "..", "."],
)
def test_traversal_out_of_the_spool_is_refused(spool, name):
    assert code_of(parse_message, msg(pdf_path=name)) == "PATH_OUTSIDE_SPOOL"


def test_absolute_path_is_refused(spool):
    outside = str(spool.parent / "secret.pdf")
    assert code_of(parse_message, msg(pdf_path=outside)) == "PATH_OUTSIDE_SPOOL"


def test_missing_file_and_bad_names(spool):
    assert code_of(parse_message, msg(pdf_path="nope.pdf")) == "FILE_NOT_FOUND"
    assert code_of(parse_message, msg(pdf_path="")) == "BAD_FIELD"
    assert code_of(parse_message, msg(pdf_path=["inv-1.pdf"])) == "BAD_FIELD"
    assert code_of(parse_message, msg(pdf_path="inv\x00.pdf")) == "BAD_FIELD"


def test_symlink_escaping_the_spool_is_refused(spool):
    link = spool / "link.pdf"
    try:
        link.symlink_to(spool.parent / "secret.pdf")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra privileges on this platform")
    assert code_of(parse_message, msg(pdf_path="link.pdf")) == "PATH_OUTSIDE_SPOOL"


def test_spool_file_size_is_checked_before_reading(spool):
    (spool / "big.pdf").write_bytes(PDF + b"x" * 100)
    assert code_of(read_spool_file, "big.pdf", max_bytes=50) == "PDF_TOO_LARGE"


# ------------------------------------------------------------------ content and fields


def test_inline_pdf_is_accepted():
    inc = parse_message(msg(pdf_hex=PDF.hex(), source="email"))
    assert (inc.message_id, inc.entity_id, inc.pdf, inc.source) == ("msg-1", "E01", PDF, "email")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"pdf_hex": b"PK\x03\x04 zip".hex()}, "NOT_A_PDF"),
        ({"pdf_hex": ""}, "EMPTY_PDF"),
        ({"pdf_hex": "zz"}, "BAD_FIELD"),
        ({"pdf_hex": 123}, "BAD_FIELD"),
        ({}, "BAD_FIELD"),  # neither pdf_hex nor pdf_path
        ({"pdf_hex": PDF.hex(), "pdf_path": "inv-1.pdf"}, "BAD_FIELD"),  # both
        ({"pdf_hex": PDF.hex(), "entity_id": "E99"}, "UNKNOWN_ENTITY"),
        ({"pdf_hex": PDF.hex(), "entity_id": None}, "UNKNOWN_ENTITY"),
        ({"pdf_hex": PDF.hex(), "message_id": ""}, "BAD_FIELD"),
        ({"pdf_hex": PDF.hex(), "message_id": "m" * 129}, "BAD_FIELD"),
        ({"pdf_hex": PDF.hex(), "source": "s" * 33}, "BAD_FIELD"),
    ],
)
def test_malformed_messages_are_refused(body, code):
    assert code_of(parse_message, msg(**body)) == code


def test_bad_json_is_refused():
    assert code_of(parse_message, b"not json") == "BAD_JSON"
    assert code_of(parse_message, b"[1, 2]") == "BAD_JSON"
    assert code_of(parse_message, b"\xff\xfe") == "BAD_JSON"


def test_oversized_inline_pdf_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(settings, "ingest_max_pdf_bytes", 16)
    assert code_of(parse_message, msg(pdf_hex=(PDF + b"x" * 20).hex())) == "PDF_TOO_LARGE"


# ------------------------------------------------------------------ inbox and DLQ


def test_receive_refuses_unknown_entity_and_non_pdf(env: Env):
    with env.factory() as s:
        for kw, code in (
            ({"entity_id": "E99", "pdf": PDF}, "UNKNOWN_ENTITY"),
            ({"entity_id": "E01", "pdf": b"<html>"}, "NOT_A_PDF"),
        ):
            assert code_of(receive, s, env.store, message_id="m-x", **kw) == code
        s.rollback()
    assert env.count(m.Inbox) == 0 and env.count(m.Invoice) == 0


def test_dead_letter_carries_reason_and_hash_not_body():
    raw = msg(pdf_path="../.env")
    rec = json.loads(dead_letter(raw, IngestRejected("PATH_OUTSIDE_SPOOL", "x"), offset=7))
    assert rec["reason"] == "PATH_OUTSIDE_SPOOL" and rec["offset"] == 7
    assert len(rec["message_sha256"]) == 64 and "../.env" not in json.dumps(rec)
