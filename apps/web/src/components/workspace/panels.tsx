"use client";

import dynamic from "next/dynamic";
import * as React from "react";

import { Button, Card, Dialog, StatusBadge, StatusIcon } from "@/components/ui/primitives";
import { REASON_LABEL, RULE_LABEL, cn, dateTime, money } from "@/lib/format";
import type { AuditEvent, Header, InvoiceDetail, RuleResult } from "@/lib/types";

const PdfViewer = dynamic(() => import("@/components/workspace/pdf-viewer"), { ssr: false });

/* ----------------------------------------------------------- duplicate compare */

const HDR: [keyof Header, string, boolean][] = [
  ["supplier_name", "Supplier", false],
  ["invoice_no", "Invoice no.", false],
  ["invoice_date", "Date", false],
  ["po_number", "PO", false],
  ["currency", "Currency", false],
  ["subtotal_minor", "Subtotal", true],
  ["tax_minor", "Tax", true],
  ["total_minor", "Total", true],
];

export function DuplicateCompare({ d, open, onOpenChange, onConfirm, onNotDuplicate, readOnly }: {
  d: InvoiceDetail;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onConfirm: () => void;
  onNotDuplicate: () => void;
  readOnly: boolean;
}) {
  const other = d.duplicate;
  if (!other) return null;
  const fmt = (h: Header, k: keyof Header, m: boolean) => (m ? money(h[k] as number | null, h.currency) : String(h[k] ?? "—"));
  return (
    <Dialog open={open} onOpenChange={onOpenChange} wide title="Duplicate compare"
      description={`This invoice vs ${other.state.toLowerCase()} invoice ${other.header.invoice_no ?? ""}${other.erp_doc_no ? ` (ERP ${other.erp_doc_no})` : ""}.`}
      footer={!readOnly ? <>
        <Button onClick={onNotDuplicate}>Not a duplicate…</Button>
        <Button variant="primary" onClick={onConfirm}>Confirm duplicate</Button>
      </> : undefined}>
      <div className="grid grid-cols-2 gap-3">
        <div className="h-[420px] overflow-hidden rounded-md border border-border">
          <PdfViewer url={d.pdf?.url ?? null} overlays={[]} selected={null} onSelect={() => {}} title="This invoice" />
        </div>
        <div className="h-[420px] overflow-hidden rounded-md border border-border">
          <PdfViewer url={other.pdf?.url ?? null} overlays={[]} selected={null} onSelect={() => {}} title={`Existing · ${other.state}`} />
        </div>
      </div>
      <table className="mt-3 w-full text-[0.93rem]">
        <thead className="text-left text-[0.8rem] text-muted">
          <tr><th className="py-1">Field</th><th>This invoice</th><th>Existing</th><th /></tr>
        </thead>
        <tbody>
          {HDR.map(([k, label, m]) => {
            const a = fmt(d.header, k, m);
            const b = fmt(other.header, k, m);
            const same = a === b;
            return (
              <tr key={k} className="border-t border-border">
                <td className="py-1 text-muted">{label}</td>
                <td className={cn("num font-mono", !same && "font-semibold text-warn")}>{a}</td>
                <td className="num font-mono">{b}</td>
                <td className="text-[0.86rem] text-muted">{same ? "same" : "differs"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Dialog>
  );
}

/* ----------------------------------------------------------- checker view */

export function CheckerDiff({ d }: { d: InvoiceDetail }) {
  const t = d.task!;
  const makerEvent = [...d.events].reverse().find((e) => e.action === "MAKER_APPROVE");
  const changed = (makerEvent?.payload?.changed ?? {}) as Record<string, [unknown, unknown]>;
  const before = new Map((t.results_before ?? []).map((r) => [r.key, r]));
  const after = t.results_after ?? [];
  const moved = after.filter((r) => before.get(r.key)?.status !== r.status || t.overrides?.[r.key]);
  const ccy = d.header.currency;
  const show = (k: string, v: unknown) => (k.endsWith("_minor") ? money(v as number, ccy) : String(v ?? "—"));
  return (
    <div className="space-y-3">
      <Card className="p-3">
        <h2 className="mb-1 font-semibold">Maker decision</h2>
        <p className="text-fg-2">
          <b>{t.maker_name ?? t.maker_id}</b> chose <span className="font-mono">{t.decision}</span>
          {t.reason_code ? <> with <span className="font-mono">{t.reason_code}</span></> : null}
          {makerEvent ? <span className="text-muted"> · {dateTime(makerEvent.at)}</span> : null}
        </p>
        {t.comment ? <blockquote className="mt-2 border-l-2 border-border-strong pl-3 text-fg-2">{t.comment}</blockquote> : null}
        <p className="mt-2 text-[0.86rem] text-muted">You can approve or send back; you cannot edit values (separation of duties).</p>
      </Card>
      <Card className="p-3">
        <h3 className="mb-2 font-semibold">Changes ({Object.keys(changed).length})</h3>
        {Object.keys(changed).length === 0 ? <p className="text-muted">No field values changed — overrides only.</p> : (
          <table className="w-full text-[0.93rem]">
            <tbody>
              {Object.entries(changed).map(([k, [o, n]]) =>
                k === "lines" ? (
                  <tr key={k} className="border-t border-border">
                    <td className="py-1.5 text-muted">Line items</td>
                    <td colSpan={2} className="text-fg-2">
                      {(o as unknown[]).length} → {(n as unknown[]).length} lines (edited)
                    </td>
                  </tr>
                ) : (
                  <tr key={k} className="border-t border-border">
                    <td className="py-1.5 text-muted">{k.replace(/_minor$/, "")}</td>
                    <td className="num font-mono text-fail line-through decoration-2">{show(k, o)}</td>
                    <td className="num font-mono font-semibold text-pass">{show(k, n)}</td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        )}
      </Card>
      <Card className="p-3">
        <h3 className="mb-2 font-semibold">Rule results before → after</h3>
        <ul className="space-y-1">
          {moved.length === 0 ? <li className="text-muted">No changes.</li> : null}
          {moved.map((r) => {
            const b = before.get(r.key);
            const o = t.overrides?.[r.key];
            return (
              <li key={r.key} className="text-[0.93rem]">
                <div className="flex items-center gap-2">
                  {b ? <StatusBadge status={b.status} /> : <span className="w-14" />}
                  <span aria-hidden>→</span>
                  <StatusBadge status={r.status} />
                  <span className="font-medium">{RULE_LABEL[r.code] ?? r.code}{r.line ? ` · L${r.line}` : ""}</span>
                </div>
                {o ? (
                  <p className="mt-0.5 pl-1 text-[0.86rem] text-accent">
                    Override <span className="font-mono">{o.reason_code}</span> — {REASON_LABEL[o.reason_code] ?? ""}
                    {o.comment ? ` (“${o.comment}”)` : ""}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </Card>
    </div>
  );
}

/* ----------------------------------------------------------- audit */

export function AuditTrail({ events }: { events: AuditEvent[] }) {
  return (
    <ol className="relative space-y-2 border-l border-border pl-4">
      {[...events].reverse().map((e) => (
        <li key={e.id} className="relative">
          <span aria-hidden className={cn("absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-surface",
            e.actor_name === "System" ? "bg-border-strong" : "bg-accent")} />
          <div className="text-[0.93rem]">
            <span className="font-medium">{e.action.replaceAll("_", " ").toLowerCase()}</span>
            {e.to ? <span className="text-muted"> · {e.from ?? "∅"} → {e.to}</span> : null}
          </div>
          <div className="text-[0.8rem] text-muted">
            {dateTime(e.at)} · {e.actor_name}{e.role ? ` (${e.role})` : ""}
            {typeof e.payload?.reason_code === "string" ? ` · ${e.payload.reason_code}` : ""}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function ResultsSummary({ results }: { results: RuleResult[] }) {
  const c = { PASS: 0, FAIL: 0, WARN: 0, SKIPPED: 0 };
  results.forEach((r) => (c[r.status] += 1));
  return (
    <span className="flex items-center gap-2 text-[0.86rem]">
      {(["FAIL", "SKIPPED", "WARN", "PASS"] as const).filter((k) => c[k]).map((k) => (
        <span key={k} className="inline-flex items-center gap-1"><StatusIcon status={k} /> <span className="num">{c[k]}</span></span>
      ))}
    </span>
  );
}
