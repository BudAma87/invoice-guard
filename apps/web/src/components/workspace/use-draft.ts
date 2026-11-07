"use client";

import * as React from "react";

import { api } from "@/lib/api";
import { RULE_LABEL } from "@/lib/format";
import type {
  Corrections,
  Header,
  InvoiceDetail,
  InvoiceLine,
  Overrides,
  RuleResult,
  ValidateResponse,
} from "@/lib/types";

export type DraftLine = Omit<InvoiceLine, "confidence" | "evidence"> & { _id: string };

const LINE_KEYS = ["line_no", "description", "item_code", "uom", "qty", "unit_price_minor", "amount_minor", "po_line_id"] as const;

let seq = 0;
const lid = () => `l${++seq}`;

function toDraftLines(lines: InvoiceLine[]): DraftLine[] {
  return lines.map((l) => ({
    _id: lid(),
    line_no: l.line_no,
    description: l.description,
    item_code: l.item_code,
    uom: l.uom,
    qty: l.qty,
    unit_price_minor: l.unit_price_minor,
    amount_minor: l.amount_minor,
    po_line_id: l.po_line_id,
  }));
}

export type CheckState = "idle" | "checking" | "ok" | "failed";

/**
 * Reviewer draft: corrections are never saved by /validate. Every edit re-runs the
 * exact rules after a 300 ms pause; the result drives the Approve gate.
 */
export function useDraft(detail: InvoiceDetail | undefined) {
  const [header, setHeader] = React.useState<Header | null>(null);
  const [lines, setLines] = React.useState<DraftLine[]>([]);
  const [overrides, setOverrides] = React.useState<Overrides>({});
  // Header fields the reviewer explicitly confirmed without changing (e.g. an ambiguous date)
  const [confirmed, setConfirmed] = React.useState<Set<keyof Header>>(new Set());
  const [check, setCheck] = React.useState<ValidateResponse | null>(null);
  const [checkState, setCheckState] = React.useState<CheckState>("idle");
  const [changedKeys, setChangedKeys] = React.useState<Set<string>>(new Set());
  const [announce, setAnnounce] = React.useState("");
  const base = React.useRef<{ header: Header; lines: DraftLine[] } | null>(null);
  const loadedFor = React.useRef<string | null>(null);

  // (Re)initialise only when a different invoice/version arrives and the draft is clean.
  React.useEffect(() => {
    if (!detail) return;
    const key = `${detail.invoice_id}@${detail.version}`;
    if (loadedFor.current === key) return;
    const dirty = base.current && header && corrections(base.current, header, lines, confirmed) !== undefined;
    if (dirty && loadedFor.current?.startsWith(detail.invoice_id)) {
      loadedFor.current = key; // keep the local draft for comparison (stale banner shows)
      return;
    }
    const dl = toDraftLines(detail.lines);
    base.current = { header: { ...detail.header }, lines: dl.map((l) => ({ ...l })) };
    setHeader({ ...detail.header });
    setLines(dl);
    setOverrides({});
    setConfirmed(new Set());
    loadedFor.current = key;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail]);

  const corr = React.useMemo(
    () => (base.current && header ? corrections(base.current, header, lines, confirmed) : undefined),
    [header, lines, confirmed],
  );
  const dirty = corr !== undefined;

  // Debounced live re-validation
  const reqId = React.useRef(0);
  React.useEffect(() => {
    if (!detail || !header) return;
    const id = ++reqId.current;
    setCheckState("checking");
    const t = window.setTimeout(async () => {
      try {
        const r = await api<ValidateResponse>(`/invoices/${detail.invoice_id}/validate`, {
          method: "POST",
          json: { corrections: corr ?? null, overrides },
        });
        if (id !== reqId.current) return;
        setCheck((prev) => {
          const before = new Map((prev?.results ?? detail.results).map((x) => [x.key, x.status]));
          const changed = new Set<string>();
          const msgs: string[] = [];
          for (const x of r.results) {
            const was = before.get(x.key);
            if (was && was !== x.status) {
              changed.add(x.key);
              const label = RULE_LABEL[x.code] ?? x.code;
              msgs.push(
                x.status === "PASS" ? `${label} now passes` : x.status === "FAIL" ? `${label} now fails` : `${label}: ${x.status.toLowerCase()}`,
              );
            }
          }
          setChangedKeys(changed);
          if (msgs.length) setAnnounce(msgs.slice(0, 3).join(". "));
          return r;
        });
        setCheckState("ok");
      } catch {
        if (id === reqId.current) setCheckState("failed");
      }
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [corr === undefined ? "" : JSON.stringify(corr), JSON.stringify(overrides), detail?.invoice_id, detail?.version]);

  const results: RuleResult[] = check?.results ?? detail?.results ?? [];

  return {
    header,
    lines,
    overrides,
    results,
    check,
    checkState,
    changedKeys,
    announce,
    dirty,
    corrections: corr,
    setHeaderField: <K extends keyof Header>(k: K, v: Header[K]) =>
      setHeader((h) => (h ? { ...h, [k]: v } : h)),
    setLine: (id: string, patch: Partial<DraftLine>) =>
      setLines((ls) => ls.map((l) => (l._id === id ? { ...l, ...patch } : l))),
    addLine: () =>
      setLines((ls) => [
        ...ls,
        { _id: lid(), line_no: Math.max(0, ...ls.map((l) => l.line_no)) + 1, description: "", item_code: null, uom: null, qty: null, unit_price_minor: null, amount_minor: null, po_line_id: null },
      ]),
    splitLine: (id: string) =>
      setLines((ls) => {
        const i = ls.findIndex((l) => l._id === id);
        if (i < 0) return ls;
        const l = ls[i];
        const q = Number(l.qty ?? 0);
        const a = Math.floor(q / 2);
        const b = q - a;
        const price = l.unit_price_minor ?? 0;
        const first = { ...l, qty: String(a), amount_minor: a * price };
        const second: DraftLine = { ...l, _id: lid(), qty: String(b), amount_minor: b * price, po_line_id: null, line_no: 0 };
        const out = [...ls.slice(0, i), first, second, ...ls.slice(i + 1)];
        return renumber(out);
      }),
    deleteLine: (id: string) => setLines((ls) => renumber(ls.filter((l) => l._id !== id))),
    setOverride: (key: string, reason_code: string | null, comment = "") =>
      setOverrides((o) => {
        const n = { ...o };
        if (reason_code) n[key] = { reason_code, comment };
        else delete n[key];
        return n;
      }),
    reset: () => {
      if (!base.current) return;
      setHeader({ ...base.current.header });
      setLines(base.current.lines.map((l) => ({ ...l })));
      setOverrides({});
      setConfirmed(new Set());
    },
    confirmed,
    confirmField: (k: keyof Header) => setConfirmed((c) => new Set(c).add(k)),
  };
}

function renumber(ls: DraftLine[]): DraftLine[] {
  return ls.map((l, i) => ({ ...l, line_no: i + 1 }));
}

function corrections(
  base: { header: Header; lines: DraftLine[] },
  header: Header,
  lines: DraftLine[],
  confirmed: Set<keyof Header> = new Set(),
): Corrections | undefined {
  const h: Record<string, unknown> = {};
  (Object.keys(header) as (keyof Header)[]).forEach((k) => {
    if (header[k] !== base.header[k] || confirmed.has(k)) h[k] = header[k];
  });
  const strip = (l: DraftLine) => Object.fromEntries(LINE_KEYS.map((k) => [k, l[k]]));
  const linesChanged =
    lines.length !== base.lines.length ||
    lines.some((l, i) => JSON.stringify(strip(l)) !== JSON.stringify(strip(base.lines[i])));
  if (!Object.keys(h).length && !linesChanged) return undefined;
  const out: Corrections = {};
  if (Object.keys(h).length) out.header = h;
  if (linesChanged) out.lines = lines.map((l) => strip(l) as Corrections["lines"] extends (infer T)[] | undefined ? T : never);
  return out;
}

export type Draft = ReturnType<typeof useDraft>;
