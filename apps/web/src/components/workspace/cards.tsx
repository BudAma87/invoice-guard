"use client";

import { CalendarClock, Copy, Link2, Plus, Scissors, Trash2 } from "lucide-react";
import * as React from "react";

import { Button, Card, Chip, Dialog, StatusBadge, StatusIcon, inputCls } from "@/components/ui/primitives";
import type { Draft, DraftLine } from "@/components/workspace/use-draft";
import { GROUP_ORDER, RULE_LABEL, cn, cssId, money, parseMoney } from "@/lib/format";
import type { Group, InvoiceDetail, POCandidate, RuleResult } from "@/lib/types";

export interface Sel {
  selected: string | null;
  onSelect: (field: string) => void;
  readOnly: boolean;
  changedKeys: Set<string>;
}

const GROUP_LABEL: Record<Group, string> = {
  identity: "Supplier & duplicates",
  date: "Invoice date",
  total: "Total",
  lines: "Line items",
};

export function isIssue(r: RuleResult) {
  return r.status === "FAIL" || r.status === "SKIPPED" || r.status === "WARN";
}

/* ----------------------------------------------------------- exceptions */

export function ExceptionsList({
  results,
  lowConf,
  cursor,
  onPick,
  overrides,
}: {
  results: RuleResult[];
  lowConf: string[];
  cursor: number;
  onPick: (i: number) => void;
  overrides: Record<string, unknown>;
}) {
  const issues = orderedIssues(results);
  const total = issues.length + lowConf.length;
  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-semibold">
          {total ? `${total} thing${total > 1 ? "s" : ""} to check` : "Everything checks out"}
        </h2>
        <span className="text-[0.86rem] text-muted">J / K to step through</span>
      </div>
      {total === 0 ? (
        <p className="text-pass">All rules pass and every field is above the confidence threshold.</p>
      ) : (
        <ol className="space-y-1">
          {issues.map((r, i) => (
            <li key={r.key}>
              <button
                onClick={() => onPick(i)}
                aria-current={cursor === i ? "true" : undefined}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left hover:bg-surface-2",
                  cursor === i && "bg-accent-tint",
                )}
              >
                <StatusIcon status={r.status} />
                <span className="min-w-0 flex-1">
                  <span className="font-medium">
                    {RULE_LABEL[r.code] ?? r.code}
                    {r.line ? <span className="text-muted"> · L{r.line}</span> : null}
                  </span>
                  <span className="block truncate text-[0.93rem] text-fg-2">{r.message}</span>
                </span>
                {r.severity === "BLOCK" && r.status === "FAIL" ? <Chip tone="fail">BLOCK</Chip> : null}
                {overrides[r.key] ? <Chip tone="accent">override</Chip> : null}
              </button>
            </li>
          ))}
          {lowConf.map((f) => (
            <li key={f} className="flex items-center gap-2 px-2 py-1 text-[0.93rem]">
              <StatusIcon status="WARN" />
              <span>
                Low confidence: <span className="font-mono">{f}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export function orderedIssues(results: RuleResult[]) {
  return results
    .filter(isIssue)
    .sort(
      (a, b) =>
        GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group) ||
        (a.line ?? 0) - (b.line ?? 0) ||
        (a.status === "FAIL" ? -1 : 1) - (b.status === "FAIL" ? -1 : 1),
    );
}

function RuleRow({ r, changed }: { r: RuleResult; changed: boolean }) {
  return (
    <li className={cn("flex items-start gap-2 py-1", changed && "changed-once rounded")}>
      <StatusIcon status={r.status} />
      <span className="min-w-0 flex-1">
        <span className={cn(r.status === "FAIL" && "text-fail", r.status === "PASS" && "text-fg-2")}>
          {RULE_LABEL[r.code] ?? r.code}
        </span>
        <span className="block text-[0.86rem] text-muted">{r.message}</span>
      </span>
    </li>
  );
}

function CardTitle({ group, results, right }: { group: Group; results: RuleResult[]; right?: React.ReactNode }) {
  const worst = results.some((r) => r.status === "FAIL")
    ? "FAIL"
    : results.some((r) => r.status === "SKIPPED")
      ? "SKIPPED"
      : results.some((r) => r.status === "WARN")
        ? "WARN"
        : "PASS";
  return (
    <div className="mb-2 flex items-center gap-2">
      <h3 className="font-semibold">{GROUP_LABEL[group]}</h3>
      <StatusBadge status={worst} blocking={results.some((r) => r.severity === "BLOCK" && r.status === "FAIL")} />
      <div className="ml-auto">{right}</div>
    </div>
  );
}

/* ----------------------------------------------------------- inputs */

function FieldInput({
  field,
  label,
  value,
  onChange,
  sel,
  conf,
  minConf,
  mono,
  type = "text",
  className,
}: {
  field: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  sel: Sel;
  conf?: number;
  minConf: number;
  mono?: boolean;
  type?: string;
  className?: string;
}) {
  const low = conf !== undefined && conf < minConf;
  return (
    <label className={cn("block", className)}>
      <span className="mb-1 flex items-center gap-1.5 text-[0.86rem] text-muted">
        {label}
        {conf !== undefined ? (
          <span className={cn("num rounded px-1 text-[0.8rem]", low ? "bg-warn-tint font-semibold text-warn" : "text-muted")}>
            {low ? "▲ " : ""}
            {(conf * 100).toFixed(0)}%
          </span>
        ) : null}
      </span>
      <input
        id={`f-${cssId(field)}`}
        type={type}
        value={value}
        readOnly={sel.readOnly}
        onFocus={() => sel.onSelect(field)}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          inputCls,
          mono && "num font-mono",
          low && "border-warn-line ring-1 ring-warn-line/40",
          sel.selected === field && "border-accent ring-2 ring-accent/30",
        )}
      />
    </label>
  );
}

function MoneyField(props: {
  field: string;
  label: string;
  minor: number | null;
  ccy: string | null;
  onChange: (m: number | null) => void;
  sel: Sel;
  conf?: number;
  minConf: number;
}) {
  const [text, setText] = React.useState(money(props.minor, props.ccy).replace("—", ""));
  const [bad, setBad] = React.useState(false);
  React.useEffect(() => {
    setText(props.minor === null ? "" : money(props.minor, props.ccy).replace("−", "-"));
  }, [props.minor, props.ccy]);
  return (
    <div>
      <FieldInput
        {...props}
        value={text}
        mono
        onChange={(v) => {
          setText(v);
          const m = v.trim() === "" ? null : parseMoney(v, props.ccy);
          setBad(m === null && v.trim() !== "");
          if (m !== null || v.trim() === "") props.onChange(m);
        }}
      />
      {bad ? <span className="text-[0.8rem] text-fail">Not an amount</span> : null}
    </div>
  );
}

/* ----------------------------------------------------------- header strip */

export function HeaderStrip({ d, draft, sel }: { d: InvoiceDetail; draft: Draft; sel: Sel }) {
  const h = draft.header!;
  const c = d.confidence;
  const overall = React.useMemo(() => {
    const vals = Object.values(c);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }, [c]);
  return (
    <Card className="p-3">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[1.1rem] font-semibold">{h.supplier_name || "Unknown supplier"}</h2>
        <span className="text-muted">· {d.entity.name} ({d.entity.id})</span>
        <span className="ml-auto text-[0.86rem] text-muted">
          Extraction confidence {overall !== null ? `${(overall * 100).toFixed(0)}% overall` : "—"}
        </span>
      </div>
      <div className="grid grid-cols-4 gap-3">
        <FieldInput className="col-span-2" field="supplier" label="Supplier" value={h.supplier_name ?? ""} sel={sel}
          conf={c.supplier} minConf={d.min_confidence} onChange={(v) => draft.setHeaderField("supplier_name", v)} />
        <FieldInput field="invoice_no" label="Invoice no." value={h.invoice_no ?? ""} sel={sel} mono
          conf={c.invoice_no} minConf={d.min_confidence} onChange={(v) => draft.setHeaderField("invoice_no", v)} />
        <FieldInput field="po_number" label="PO" value={h.po_number ?? ""} sel={sel} mono
          conf={c.po_number} minConf={d.min_confidence} onChange={(v) => draft.setHeaderField("po_number", v.toUpperCase())} />
        <FieldInput field="supplier_tax_id" label="Tax ID" value={h.supplier_tax_id ?? ""} sel={sel} mono
          minConf={d.min_confidence} onChange={(v) => draft.setHeaderField("supplier_tax_id", v)} />
        <FieldInput field="currency" label="Currency" value={h.currency ?? ""} sel={sel} mono
          conf={c.currency} minConf={d.min_confidence} onChange={(v) => draft.setHeaderField("currency", v.toUpperCase().slice(0, 3))} />
        <div className="col-span-2 self-end text-[0.86rem] text-muted">
          {d.amount_lkr_minor !== null ? (
            <>≈ LKR {money(d.amount_lkr_minor, "LKR")} at today&apos;s ERP rate{d.needs_checker ? " · needs a checker" : ""}</>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

/* ----------------------------------------------------------- identity */

export function IdentityCard({ d, results, sel, onCompare }: {
  d: InvoiceDetail;
  results: RuleResult[];
  sel: Sel;
  onCompare: () => void;
}) {
  const rs = results.filter((r) => r.group === "identity");
  const dup = rs.find((r) => (r.code === "DUP_EXACT" || r.code === "DUP_FUZZY") && r.status === "FAIL");
  return (
    <Card className="p-3">
      <CardTitle group="identity" results={rs} right={
        dup && d.duplicate ? (
          <Button size="sm" variant="secondary" onClick={onCompare}>
            <Copy className="size-4" aria-hidden /> Compare side by side
          </Button>
        ) : null
      } />
      <ul>
        {rs.map((r) => (
          <RuleRow key={r.key} r={r} changed={sel.changedKeys.has(r.key)} />
        ))}
      </ul>
    </Card>
  );
}

/* ----------------------------------------------------------- date */

export function DateCard({ d, draft, results, sel }: {
  d: InvoiceDetail;
  draft: Draft;
  results: RuleResult[];
  sel: Sel;
}) {
  const rs = results.filter((r) => r.group === "date");
  const period = rs.find((r) => r.code === "DATE_PERIOD_OPEN");
  const firstOpen = period?.status === "FAIL" ? (period.expected as string | null) : null;
  const ov = draft.overrides["DATE_PERIOD_OPEN"];
  return (
    <Card className="p-3">
      <CardTitle group="date" results={rs} />
      <div className="grid grid-cols-[180px_1fr] gap-4">
        <div>
          <FieldInput field="invoice_date" label="Invoice date (YYYY-MM-DD)" value={draft.header?.invoice_date ?? ""}
            sel={sel} conf={d.confidence.invoice_date} minConf={d.min_confidence} mono
            onChange={(v) => draft.setHeaderField("invoice_date", v.trim() || null)} />
          <p className="mt-1 text-[0.86rem] text-fg-2">{longDate(draft.header?.invoice_date)}</p>
          {d.date_ambiguous && draft.header?.invoice_date === d.header.invoice_date && !draft.confirmed.has("invoice_date") ? (
            <div className="mt-1 space-y-1.5">
              <p className="text-[0.86rem] text-warn">
                ▲ Printed as <span className="font-mono">{d.evidence.invoice_date?.text ?? "?"}</span> — day/month order is
                unclear. Read as {longDate(d.header.invoice_date)}.
              </p>
              {!sel.readOnly ? (
                <Button size="sm" variant="secondary" onClick={() => draft.confirmField("invoice_date")}>
                  Confirm {longDate(d.header.invoice_date)}
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
        <ul>
          {rs.map((r) => (
            <RuleRow key={r.key} r={r} changed={sel.changedKeys.has(r.key)} />
          ))}
        </ul>
      </div>
      {firstOpen && !sel.readOnly ? (
        <div className="mt-2 flex items-center gap-2 rounded-md bg-surface-2 p-2">
          <CalendarClock className="size-4 text-muted" aria-hidden />
          <span className="flex-1 text-[0.93rem]">
            {ov ? <>Will post in <b>{firstOpen}</b> with reason <span className="font-mono">PERIOD_CLOSED</span>.</> :
              <>Period {String(period?.actual)} is closed for AP.</>}
          </span>
          <Button size="sm" variant={ov ? "ghost" : "secondary"}
            onClick={() => draft.setOverride("DATE_PERIOD_OPEN", ov ? null : "PERIOD_CLOSED")}>
            {ov ? "Undo" : `Post in first open period (${firstOpen})`}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

function longDate(iso: string | null | undefined) {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return "Enter as YYYY-MM-DD";
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return "Not a valid date";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

/* ----------------------------------------------------------- total */

export function TotalCard({ d, draft, results, sel }: {
  d: InvoiceDetail;
  draft: Draft;
  results: RuleResult[];
  sel: Sel;
}) {
  const rs = results.filter((r) => r.group === "total");
  const h = draft.header!;
  const ccy = h.currency;
  const lineSum = draft.lines.reduce((a, l) => a + (l.amount_minor ?? 0), 0);
  const base = h.subtotal_minor ?? lineSum;
  const computed = base + (h.tax_minor ?? 0) + (h.charges_minor ?? 0) - (h.discount_minor ?? 0);
  const diff = h.total_minor === null ? null : h.total_minor - computed;
  const recon = rs.find((r) => r.code === "TOTAL_RECONCILE");
  const term = (field: string, label: string, v: number | null) => (
    <button
      onClick={() => sel.onSelect(field)}
      className={cn("rounded px-1 hover:bg-accent-tint", sel.selected === field && "bg-accent-tint text-accent")}
    >
      <span className="text-muted">{label}</span> <span className="num font-mono">{money(v, ccy)}</span>
    </button>
  );
  return (
    <Card className="p-3">
      <CardTitle group="total" results={rs} />
      <div
        className={cn(
          "num mb-3 flex flex-wrap items-center gap-x-1 gap-y-1 rounded-md border px-2 py-2 font-mono text-[0.93rem]",
          recon?.status === "FAIL" ? "border-fail/40 bg-fail-tint/40" : "border-border bg-surface-2",
        )}
        aria-label="Reconciliation"
      >
        {term("subtotal", h.subtotal_minor !== null ? "Subtotal" : "Lines", base)}
        <span className="text-muted">+</span>
        {term("tax", "Tax", h.tax_minor)}
        <span className="text-muted">+</span>
        {term("charges", "Charges", h.charges_minor)}
        {h.discount_minor ? (<><span className="text-muted">−</span>{term("discount", "Disc.", h.discount_minor)}</>) : null}
        <span className="text-muted">=</span>
        <b>{money(computed, ccy)}</b>
        <span className="mx-1 text-muted">·</span>
        {term("total", "Invoice", h.total_minor)}
        <span className="mx-1 text-muted">·</span>
        <span className={cn("font-semibold", diff ? "text-fail" : "text-pass")}>
          Diff {diff === null ? "—" : money(diff, ccy, true)} {diff ? "✕" : "✓"}
        </span>
      </div>
      <div className="grid grid-cols-4 gap-3">
        <MoneyField field="subtotal" label="Subtotal" minor={h.subtotal_minor} ccy={ccy} sel={sel}
          conf={d.confidence.subtotal} minConf={d.min_confidence} onChange={(m) => draft.setHeaderField("subtotal_minor", m)} />
        <MoneyField field="tax" label="Tax" minor={h.tax_minor} ccy={ccy} sel={sel}
          conf={d.confidence.tax} minConf={d.min_confidence} onChange={(m) => draft.setHeaderField("tax_minor", m ?? 0)} />
        <MoneyField field="charges" label="Charges" minor={h.charges_minor} ccy={ccy} sel={sel}
          minConf={d.min_confidence} onChange={(m) => draft.setHeaderField("charges_minor", m ?? 0)} />
        <MoneyField field="total" label="Invoice total" minor={h.total_minor} ccy={ccy} sel={sel}
          conf={d.confidence.total} minConf={d.min_confidence} onChange={(m) => draft.setHeaderField("total_minor", m)} />
      </div>
      <ul className="mt-2">
        {rs.filter((r) => r.code !== "TOTAL_RECONCILE").map((r) => (
          <RuleRow key={r.key} r={r} changed={sel.changedKeys.has(r.key)} />
        ))}
      </ul>
      {d.po ? (
        <p className="num mt-1 text-[0.86rem] text-muted">
          Remaining PO {d.po.po_number} balance {money(d.po.remaining_minor, d.po.currency)} {d.po.currency} · tolerance ±1
          minor unit on the total, +2% on the PO balance
        </p>
      ) : null}
    </Card>
  );
}

/* ----------------------------------------------------------- lines */

export function LinesGrid({ d, draft, results, sel }: {
  d: InvoiceDetail;
  draft: Draft;
  results: RuleResult[];
  sel: Sel;
}) {
  const rs = results.filter((r) => r.group === "lines");
  const [picker, setPicker] = React.useState<DraftLine | null>(null);
  const ccy = draft.header?.currency ?? null;
  const byLine = (n: number) => rs.filter((r) => r.line === n);
  const status = (n: number) => {
    const x = byLine(n);
    return x.some((r) => r.status === "FAIL") ? "FAIL" : x.some((r) => r.status === "SKIPPED") ? "SKIPPED" : x.some((r) => r.status === "WARN") ? "WARN" : "PASS";
  };
  const orig = (n: number) => d.lines.find((l) => l.line_no === n);
  const cands = (n: number) => d.po_line_candidates.find((c) => c.line_no === n)?.candidates ?? d.po_line_candidates[0]?.candidates ?? [];
  const lineConf = (n: number, f: string) => orig(n)?.confidence?.[f];

  return (
    <Card className="p-3">
      <CardTitle group="lines" results={rs.filter((r) => r.line === null || true)} right={
        !sel.readOnly ? (
          <Button size="sm" variant="ghost" onClick={draft.addLine}>
            <Plus className="size-4" aria-hidden /> Add row
          </Button>
        ) : null
      } />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-[0.93rem]">
          <thead className="text-left text-[0.8rem] text-muted">
            <tr>
              <th className="w-8 py-1 font-medium">#</th>
              <th className="py-1 font-medium">Description · matched item</th>
              <th className="w-28 py-1 text-right font-medium">Qty / open</th>
              <th className="w-36 py-1 text-right font-medium">Unit price / PO</th>
              <th className="w-28 py-1 text-right font-medium">Amount</th>
              <th className="w-24 py-1 font-medium">Status</th>
              <th className="w-16" />
            </tr>
          </thead>
          <tbody>
            {draft.lines.map((l) => {
              const n = l.line_no;
              const lr = byLine(n);
              const match = lr.find((r) => r.code === "LINE_PO_MATCH");
              const price = lr.find((r) => r.code === "LINE_PRICE");
              const qty = lr.find((r) => r.code === "LINE_QTY");
              const st = status(n);
              const cand = cands(n).find((c) => c.po_line_id === (l.po_line_id ?? (match?.expected as string)));
              return (
                <React.Fragment key={l._id}>
                  <tr className={cn("border-t border-border align-top", lr.some((r) => sel.changedKeys.has(r.key)) && "changed-once")}>
                    <td className="num py-1.5 text-muted">{n}</td>
                    <td className="py-1.5 pr-2">
                      <CellInput field={`line:${n}:description`} value={l.description} sel={sel}
                        onChange={(v) => draft.setLine(l._id, { description: v })} />
                      <div className="mt-0.5 flex items-center gap-1.5 text-[0.8rem] text-muted">
                        <CellInput field={`line:${n}:item_code`} value={l.item_code ?? ""} sel={sel} small mono placeholder="item code"
                          onChange={(v) => draft.setLine(l._id, { item_code: v || null })} />
                        {cand ? <span>→ PO line {cand.line_no} · {cand.item_code}</span> : null}
                        {match?.status === "FAIL" && !sel.readOnly ? (
                          <Button size="sm" variant="secondary" className="h-6" onClick={() => setPicker(l)}>
                            <Link2 className="size-3.5" aria-hidden /> Pick a PO line
                          </Button>
                        ) : null}
                      </div>
                    </td>
                    <td className="py-1.5 text-right">
                      <CellInput field={`line:${n}:qty`} value={l.qty ?? ""} sel={sel} right mono
                        low={(lineConf(n, "qty") ?? 1) < d.min_confidence}
                        onChange={(v) => draft.setLine(l._id, { qty: v || null })} />
                      <div className={cn("num text-[0.8rem]", qty?.status === "FAIL" ? "text-fail" : "text-muted")}>
                        / {cand ? cand.open_qty : "—"} {l.uom && cand && l.uom !== cand.uom ? `(${l.uom}→${cand.uom})` : ""}
                      </div>
                    </td>
                    <td className="py-1.5 text-right">
                      <CellMoney field={`line:${n}:unit_price`} minor={l.unit_price_minor} ccy={ccy} sel={sel}
                        low={(lineConf(n, "unit_price") ?? 1) < d.min_confidence}
                        onChange={(m) => draft.setLine(l._id, { unit_price_minor: m })} />
                      <div className={cn("num text-[0.8rem]", price?.status === "FAIL" ? "text-fail" : "text-muted")}>
                        / {cand ? money(cand.price_minor, ccy) : "—"} {price?.status !== "SKIPPED" ? variance(price?.message) : ""}
                      </div>
                    </td>
                    <td className="py-1.5 text-right">
                      <CellMoney field={`line:${n}:amount`} minor={l.amount_minor} ccy={ccy} sel={sel}
                        low={(lineConf(n, "amount") ?? 1) < d.min_confidence}
                        onChange={(m) => draft.setLine(l._id, { amount_minor: m })} />
                    </td>
                    <td className="py-1.5 pl-2">
                      <StatusBadge status={st} />
                    </td>
                    <td className="py-1.5">
                      {!sel.readOnly ? (
                        <span className="flex gap-0.5">
                          <button aria-label={`Split line ${n}`} title="Split" onClick={() => draft.splitLine(l._id)}
                            className="rounded p-1 text-muted hover:bg-surface-2"><Scissors className="size-3.5" /></button>
                          <button aria-label={`Delete line ${n}`} title="Delete" onClick={() => draft.deleteLine(l._id)}
                            className="rounded p-1 text-muted hover:bg-fail-tint hover:text-fail"><Trash2 className="size-3.5" /></button>
                        </span>
                      ) : null}
                    </td>
                  </tr>
                  {lr.filter((r) => r.status !== "PASS").length ? (
                    <tr>
                      <td />
                      <td colSpan={6} className="pb-1.5">
                        <ul className="space-y-0.5 text-[0.86rem]">
                          {lr.filter((r) => r.status !== "PASS").map((r) => (
                            <li key={r.key} className="flex items-center gap-1.5">
                              <StatusBadge status={r.status} compact />
                              <span className="text-fg-2">{RULE_LABEL[r.code] ?? r.code}:</span>
                              <span className="text-muted">{r.message}</span>
                            </li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {rs.filter((r) => r.line === null || r.code === "LINE_REPEAT" || r.code === "LINES_PRESENT").filter((r) => r.status !== "PASS").map((r) => (
        <p key={r.key} className="mt-1 text-[0.86rem]"><StatusBadge status={r.status} compact /> {r.message}</p>
      ))}
      <p className="mt-2 text-[0.8rem] text-muted">Edits re-validate after 300 ms. Totals card recalculates live.</p>
      <POLinePicker line={picker} candidates={picker ? cands(picker.line_no) : []} ccy={ccy}
        onClose={() => setPicker(null)}
        onPick={(c) => {
          if (picker) draft.setLine(picker._id, { po_line_id: c.po_line_id, item_code: picker.item_code || c.item_code });
          setPicker(null);
        }} />
    </Card>
  );
}

function variance(msg?: string) {
  const m = msg?.match(/\(([+-]?[\d.]+%)\)\s*$/);
  return m ? `(${m[1]})` : "";
}

function CellInput({ field, value, onChange, sel, right, mono, small, placeholder, low }: {
  field: string;
  value: string;
  onChange: (v: string) => void;
  sel: Sel;
  right?: boolean;
  mono?: boolean;
  small?: boolean;
  placeholder?: string;
  low?: boolean;
}) {
  return (
    <input
      id={`f-${cssId(field)}`}
      aria-label={field}
      value={value}
      placeholder={placeholder}
      readOnly={sel.readOnly}
      onFocus={() => sel.onSelect(field)}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "w-full rounded border border-transparent bg-transparent px-1 py-0.5 hover:border-border-strong focus:border-accent focus:bg-surface focus:outline-none",
        right && "text-right",
        mono && "num font-mono",
        small && "w-28 text-[0.8rem]",
        low && "border-warn-line bg-warn-tint/50",
        sel.selected === field && "border-accent bg-accent-tint/50",
      )}
    />
  );
}

function CellMoney({ field, minor, ccy, onChange, sel, low }: {
  field: string;
  minor: number | null;
  ccy: string | null;
  onChange: (m: number | null) => void;
  sel: Sel;
  low?: boolean;
}) {
  const [text, setText] = React.useState("");
  React.useEffect(() => setText(minor === null ? "" : money(minor, ccy)), [minor, ccy]);
  return (
    <CellInput field={field} value={text} sel={sel} right mono low={low}
      onChange={(v) => {
        setText(v);
        const m = v.trim() === "" ? null : parseMoney(v, ccy);
        if (m !== null || v.trim() === "") onChange(m);
      }} />
  );
}

function POLinePicker({ line, candidates, ccy, onClose, onPick }: {
  line: DraftLine | null;
  candidates: POCandidate[];
  ccy: string | null;
  onClose: () => void;
  onPick: (c: POCandidate) => void;
}) {
  return (
    <Dialog open={!!line} onOpenChange={(o) => !o && onClose()} title={`Pick a PO line for line ${line?.line_no ?? ""}`}
      description={line ? `“${line.description}” — open PO lines ranked by similarity.` : undefined}>
      <ul className="divide-y divide-border rounded-md border border-border">
        {candidates.length === 0 ? <li className="p-3 text-muted">No open PO lines.</li> : null}
        {candidates.map((c) => (
          <li key={c.po_line_id}>
            <button onClick={() => onPick(c)} className="flex w-full items-center gap-3 p-2.5 text-left hover:bg-surface-2">
              <span className="num w-12 text-right font-semibold">{Math.round(c.score * 100)}%</span>
              <span className="flex-1">
                <span className="font-medium">{c.description}</span>
                <span className="block text-[0.86rem] text-muted">
                  Line {c.line_no} · <span className="font-mono">{c.item_code}</span> · open {c.open_qty} {c.uom}
                </span>
              </span>
              <span className="num font-mono">{money(c.price_minor, ccy)}</span>
            </button>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}
