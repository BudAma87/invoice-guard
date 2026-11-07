# Docs
- `TSD_v2.pdf` — Invoice Validation Pipeline technical specification (current, Nov 2026)
- `TSD_v1.docx` — superseded LangGraph design

## Decisions taken in Phase 1 (open questions in TSD v2)
- **DUP_EXACT** is BLOCK: the reviewer can only confirm duplicate or reject; "Not a duplicate" applies to DUP_FUZZY (override) or to a suspected duplicate whose key the reviewer corrects.
- **SKIPPED** rules block Approve (inputs missing or ERP unavailable) — they cannot be overridden.
- **Auto-approve ceiling** (`auto_approve_ceiling_lkr_minor`, e.g. LKR 250k for the pilot) is config; off by default.
- Non-PO invoices and credit notes are still exceptions (TOTAL_POSITIVE / PO_OWNERSHIP fail).
