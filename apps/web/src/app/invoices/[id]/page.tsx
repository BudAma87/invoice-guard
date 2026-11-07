"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight, History, Lock, RotateCcw } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";

import { useMe } from "@/components/providers";
import { Banner, Button, Kbd, Skeleton, SlaPill } from "@/components/ui/primitives";
import { ActionBar, OverrideDialog, ReasonDialog } from "@/components/workspace/actions";
import {
  DateCard,
  ExceptionsList,
  HeaderStrip,
  IdentityCard,
  LinesGrid,
  TotalCard,
  orderedIssues,
  type Sel,
} from "@/components/workspace/cards";
import { AuditTrail, CheckerDiff, DuplicateCompare, ResultsSummary } from "@/components/workspace/panels";
import { useDraft } from "@/components/workspace/use-draft";
import { api, ApiError } from "@/lib/api";
import { cn, cssId, lkrShort } from "@/lib/format";
import type { InvoiceDetail, Overlay, RuleResult } from "@/lib/types";

const PdfViewer = dynamic(() => import("@/components/workspace/pdf-viewer"), {
  ssr: false,
  loading: () => <Skeleton className="m-4 aspect-[1/1.414]" />,
});

const TERMINAL = new Set(["POSTED", "REJECTED", "DUPLICATE"]);
const FIELD_LABEL: Record<string, string> = {
  supplier: "Supplier", invoice_no: "Invoice no.", invoice_date: "Invoice date", currency: "Currency",
  po_number: "PO number", total: "Total", subtotal: "Subtotal", tax: "Tax", charges: "Charges",
};

function useNarrow() {
  const [narrow, set] = React.useState(false);
  React.useEffect(() => {
    const mq = window.matchMedia("(max-width: 1279px)");
    const on = () => set(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

export default function WorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const narrow = useNarrow();
  const q = useQuery({
    queryKey: ["invoice", id, me?.id],
    queryFn: () => api<InvoiceDetail>(`/invoices/${id}`),
    enabled: !!me,
    refetchInterval: 30_000,
  });
  const d = q.data;
  const draft = useDraft(d);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [cursor, setCursor] = React.useState(-1);
  const [tab, setTab] = React.useState<"review" | "audit">("review");
  const [dialog, setDialog] = React.useState<null | "override" | "reject" | "duplicate" | "hold" | "compare" | "notdup" | "sendback">(null);
  const [stale, setStale] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState<string | null>(null);

  const task = d?.task ?? null;
  const isCheck = task?.kind === "CHECK";
  const canReview = !!me && (me.roles.includes("reviewer") || me.roles.includes("supervisor"));
  const readOnly = !d || !task || !task.claimed_by_me || TERMINAL.has(d.state) || isCheck || !canReview || narrow;

  const results: RuleResult[] = draft.results;
  const issues = React.useMemo(() => orderedIssues(results), [results]);
  const fails = results.filter((r) => r.status === "FAIL" && r.severity !== "BLOCK");
  const blocked = results.some((r) => r.status === "FAIL" && r.severity === "BLOCK");
  const lowConf = React.useMemo(
    () => (d ? Object.entries(d.confidence).filter(([, c]) => c < d.min_confidence).map(([k]) => k) : []),
    [d],
  );

  const overlays: Overlay[] = React.useMemo(() => {
    if (!d) return [];
    const out: Overlay[] = Object.entries(d.evidence).map(([f, ev]) => ({
      field: f, label: FIELD_LABEL[f] ?? f, evidence: ev, low: (d.confidence[f] ?? 1) < d.min_confidence,
    }));
    d.lines.forEach((l) =>
      Object.entries(l.evidence ?? {}).forEach(([f, ev]) => {
        const key = `line:${l.line_no}:${f === "unit_price" ? "unit_price" : f}`;
        out.push({ field: key, label: `Line ${l.line_no} ${f}`, evidence: ev, low: (l.confidence?.[f] ?? 1) < d.min_confidence });
      }),
    );
    return out;
  }, [d]);

  const select = React.useCallback((field: string) => {
    setSelected(field);
    const el = document.getElementById(`f-${cssId(field)}`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, []);

  const sel: Sel = { selected, onSelect: select, readOnly, changedKeys: draft.changedKeys };

  const refresh = () => qc.invalidateQueries({ queryKey: ["invoice", id] });

  const claim = useMutation({
    mutationFn: () => api(`/invoices/${id}/claim`, { method: "POST" }),
    onSuccess: refresh,
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not claim"),
  });

  const goNext = async () => {
    try {
      const r = await api<{ invoice_id: string | null }>("/queue/next", { method: "POST", json: { kind: isCheck ? "check" : "review" } });
      if (r.invoice_id && r.invoice_id !== id) router.push(`/invoices/${r.invoice_id}`);
      else router.push(isCheck ? "/queue?kind=check" : "/queue");
    } catch {
      router.push("/queue");
    }
  };

  const decide = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<{ state: string; needs_checker?: boolean }>(`/invoices/${id}/decision`, {
        method: "POST",
        version: d!.version,
        json: { corrections: draft.corrections ?? null, overrides: draft.overrides, results_digest: draft.check?.results_digest, ...body },
      }),
    onSuccess: (r) => {
      setDialog(null);
      setDone(r.state === "PENDING_CHECKER" ? "Sent to a checker" : `Done — ${r.state.replaceAll("_", " ").toLowerCase()}`);
      qc.invalidateQueries({ queryKey: ["queue"] });
      refresh();
    },
    onError: (e) => {
      setDialog(null);
      if (e instanceof ApiError && e.status === 409) {
        setStale(e.message);
        refresh();
      } else setError(e instanceof ApiError ? `${e.message}${Array.isArray(e.body.problems) ? ": " + (e.body.problems as string[]).join("; ") : ""}` : "Decision failed");
    },
  });

  const check = useMutation({
    mutationFn: (body: { action: "approve" | "send_back"; comment?: string }) =>
      api<{ state: string }>(`/invoices/${id}/check`, { method: "POST", version: d!.version, json: body }),
    onSuccess: (r) => {
      setDialog(null);
      setDone(r.state === "APPROVED_MANUAL" ? "Approved — posting to ERP" : "Sent back to the maker");
      qc.invalidateQueries({ queryKey: ["queue"] });
      refresh();
    },
    onError: (e) => {
      setDialog(null);
      if (e instanceof ApiError && e.status === 409) setStale(e.message);
      else setError(e instanceof ApiError ? e.message : "Check failed");
    },
  });

  const approve = () => decide.mutate({ action: Object.keys(draft.overrides).length ? "approve_with_override" : "approve" });

  // Keyboard-first
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (dialog || e.ctrlKey || e.metaKey || e.altKey) return;
      const inField = (e.target as HTMLElement).closest("input, textarea, select");
      if (inField) {
        if (e.key === "Enter" || e.key === "Escape") (e.target as HTMLElement).blur();
        return;
      }
      const k = e.key.toLowerCase();
      if (k === "j" || k === "k") {
        e.preventDefault();
        const n = issues.length;
        if (!n) return;
        const next = k === "j" ? Math.min(n - 1, cursor + 1) : Math.max(0, cursor - 1);
        setCursor(next);
        const r = issues[next];
        const f = r.fields[0] ?? (r.line ? `line:${r.line}:description` : null);
        if (f) select(f);
      } else if (k === "e" && selected) {
        e.preventDefault();
        (document.getElementById(`f-${cssId(selected)}`) as HTMLInputElement | null)?.focus();
      } else if (readOnly) {
        return;
      } else if (k === "a" && draft.check?.approve_allowed && draft.checkState === "ok") approve();
      else if (k === "o" && fails.length) setDialog("override");
      else if (k === "r") setDialog("reject");
      else if (k === "d") setDialog("duplicate");
      else if (k === "h") setDialog("hold");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (q.isError) {
    const status = q.error instanceof ApiError ? q.error.status : 0;
    return (
      <div className="mx-auto max-w-xl px-6 py-20 text-center">
        <h1 className="text-[1.3rem] font-semibold">{status === 404 ? "Invoice not found" : "Could not load invoice"}</h1>
        <p className="mt-2 text-muted">{status === 404 ? "It doesn't exist or isn't in your entities." : "Is the API running?"}</p>
        <Link href="/queue" className="mt-4 inline-block text-accent underline">Back to queue</Link>
      </div>
    );
  }

  if (!d || !draft.header) return <WorkspaceSkeleton />;

  const needsChecker = draft.check?.needs_checker ?? d.needs_checker;
  const erpDown = !d.erp_available || draft.check?.erp_available === false;

  return (
    <div className="relative flex h-screen flex-col overflow-clip">
      {/* breadcrumb strip */}
      <div className="flex items-center gap-2 border-b border-border bg-surface px-4 py-2.5">
        <Link href={isCheck ? "/queue?kind=check" : "/queue"} className="inline-flex items-center gap-1 text-muted hover:text-fg">
          <ArrowLeft className="size-4" aria-hidden /> Queue
        </Link>
        <ChevronRight className="size-4 text-muted" aria-hidden />
        <span className="num font-mono font-semibold">{d.header.invoice_no ?? "No invoice no."}</span>
        <span className="text-muted">· {d.header.supplier_name ?? "Unknown supplier"} · {d.header.po_number ?? "no PO"}</span>
        <span className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.8rem] text-fg-2">{d.state}</span>
        {isCheck ? <span className="rounded bg-accent-tint px-1.5 py-0.5 text-[0.8rem] font-medium text-accent">Checker view</span> : null}
        <span className="ml-auto flex items-center gap-3">
          <ResultsSummary results={results} />
          <span className="num text-[0.86rem] text-muted">{lkrShort(d.amount_lkr_minor)}</span>
          {task ? <SlaPill state={task.sla_state} due={task.sla_due_at} hold={task.hold_until} /> : null}
        </span>
      </div>

      <div className="space-y-2 px-4 pt-2 empty:hidden">
        {done ? (
          <Banner tone="info" action={<Button size="sm" variant="primary" onClick={goNext}>Next invoice <Kbd>N</Kbd></Button>}>
            {done}
          </Banner>
        ) : null}
        {stale ? (
          <Banner tone="warn" action={<Button size="sm" onClick={() => { setStale(null); refresh(); }}><RotateCcw className="size-4" /> Reload</Button>}>
            {stale}. Your edits are kept in a local draft for comparison.
          </Banner>
        ) : null}
        {error ? <Banner tone="fail" action={<Button size="sm" variant="ghost" onClick={() => setError(null)}>Dismiss</Button>}>{error}</Banner> : null}
        {TERMINAL.has(d.state) ? (
          <Banner tone="info">
            Final state: <b>{d.state}</b>{d.erp_doc_no ? <> · ERP document <span className="font-mono">{d.erp_doc_no}</span></> : null}. Read-only.
          </Banner>
        ) : null}
        {d.last_error && d.state === "NEEDS_REVIEW" ? <Banner tone="fail">ERP refused the posting: {d.last_error}</Banner> : null}
        {erpDown ? <Banner tone="warn">ERP unavailable — reference rules show “not checked” and Approve is disabled.</Banner> : null}
        {draft.checkState === "failed" ? <Banner tone="fail">Results not re-checked (validation call failed). Approve is disabled until a check succeeds.</Banner> : null}
        {narrow ? <Banner tone="info">Read-only on narrow screens. Use a screen at least 1280 px wide to review.</Banner> : null}
        {task && !task.claimed_by_me && !TERMINAL.has(d.state) &&
        !(task.pool === "supervisor" && !task.assignee && !me?.roles.includes("supervisor")) ? (
          task.assignee ? (
            <Banner tone="warn"><Lock className="mr-1 inline size-4" aria-hidden />Claimed by {task.assignee_name} — read-only.</Banner>
          ) : (
            <Banner tone="info" action={<Button size="sm" variant="primary" disabled={claim.isPending} onClick={() => claim.mutate()}>Claim for 15 min</Button>}>
              Not claimed. Claim it to {isCheck ? "check" : "edit and decide"}.
            </Banner>
          )
        ) : null}
        {task?.pool === "supervisor" && !TERMINAL.has(d.state) ? (
          <Banner tone="fail">
            SLA breached — escalated to the supervisor pool{task.escalation_level >= 3 ? " and the finance manager" : ""}.
            {me?.roles.includes("supervisor") ? "" : " Only a supervisor can pick it up once the current claim ends."}
          </Banner>
        ) : null}
        {task?.hold_until ? <Banner tone="warn">On hold until {new Date(task.hold_until).toLocaleString()} — “{task.hold_note}”. SLA paused.</Banner> : null}
      </div>

      <div className={cn("grid min-h-0 flex-1", narrow ? "grid-cols-1" : "grid-cols-[55fr_45fr]")}>
        <div className="min-h-0 border-r border-border">
          <PdfViewer url={d.pdf?.url ?? null} overlays={overlays} selected={selected} onSelect={select} title={`Invoice PDF`} />
        </div>
        <div className="flex min-h-0 flex-col">
          <div role="tablist" aria-label="Panels" className="flex gap-1 border-b border-border bg-surface px-3">
            {(["review", "audit"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                className={cn("-mb-px border-b-2 px-3 py-2 text-[0.93rem]", tab === t ? "border-accent font-medium text-fg" : "border-transparent text-muted hover:text-fg")}>
                {t === "review" ? (isCheck ? "Checker view" : "Review") : <span className="inline-flex items-center gap-1"><History className="size-4" aria-hidden />Audit trail ({d.events.length})</span>}
              </button>
            ))}
            {draft.dirty && !readOnly ? (
              <Button size="sm" variant="ghost" className="my-1 ml-auto" onClick={draft.reset}><RotateCcw className="size-3.5" /> Discard edits</Button>
            ) : null}
          </div>
          <div className="relative min-h-0 flex-1 space-y-3 overflow-auto bg-bg p-3">
            {tab === "audit" ? (
              <AuditTrail events={d.events} />
            ) : isCheck ? (
              <CheckerDiff d={d} />
            ) : (
              <>
                <ExceptionsList results={results} lowConf={lowConf} cursor={cursor} overrides={draft.overrides}
                  onPick={(i) => { setCursor(i); const r = issues[i]; const f = r.fields[0] ?? (r.line ? `line:${r.line}:description` : null); if (f) select(f); }} />
                <HeaderStrip d={d} draft={draft} sel={sel} />
                <IdentityCard d={d} results={results} sel={sel} onCompare={() => setDialog("compare")} />
                <DateCard d={d} draft={draft} results={results} sel={sel} />
                <TotalCard d={d} draft={draft} results={results} sel={sel} />
                <LinesGrid d={d} draft={draft} results={results} sel={sel} />
              </>
            )}
          </div>
        </div>
      </div>

      {isCheck ? (
        <div className="sticky bottom-0 flex items-center gap-2 border-t border-border bg-surface px-4 py-2.5">
          <Button variant="primary" disabled={!task?.claimed_by_me || check.isPending} onClick={() => check.mutate({ action: "approve" })}>
            Approve
          </Button>
          <Button disabled={!task?.claimed_by_me || check.isPending} onClick={() => setDialog("sendback")}>Send back</Button>
          <span className="ml-auto text-[0.93rem] text-muted">Maker: {task?.maker_name} · the checker cannot edit values</span>
        </div>
      ) : (
        <ActionBar
          readOnly={readOnly}
          allowed={!!draft.check?.approve_allowed}
          checkState={draft.checkState}
          needsChecker={needsChecker}
          fails={fails}
          blocked={blocked}
          overrides={draft.overrides}
          busy={decide.isPending}
          isDuplicateSuspected={d.state === "DUPLICATE_SUSPECTED"}
          problems={draft.check?.problems ?? []}
          onApprove={approve}
          onOverride={() => setDialog("override")}
          onReject={() => setDialog("reject")}
          onDuplicate={() => (d.duplicate ? setDialog("compare") : setDialog("duplicate"))}
          onHold={() => setDialog("hold")}
        />
      )}

      <div aria-live="polite" className="sr-only">{draft.announce}</div>

      <OverrideDialog open={dialog === "override"} onOpenChange={(o) => setDialog(o ? "override" : null)} fails={fails}
        overrides={draft.overrides} setOverride={draft.setOverride} needsChecker={needsChecker} busy={decide.isPending}
        onConfirm={() => decide.mutate({ action: "approve_with_override" })} />
      <ReasonDialog open={dialog === "reject"} onOpenChange={(o) => setDialog(o ? "reject" : null)} title="Reject invoice"
        description="The invoice ends as REJECTED. A reason code is required." confirm="Reject" busy={decide.isPending}
        onConfirm={(code, comment) => decide.mutate({ action: "reject", reason_code: code, comment })} />
      <ReasonDialog open={dialog === "duplicate"} onOpenChange={(o) => setDialog(o ? "duplicate" : null)} title="Mark as duplicate"
        description="The invoice ends as DUPLICATE and is never posted." confirm="Mark duplicate" defaultCode="DUPLICATE_CONFIRMED"
        busy={decide.isPending} onConfirm={(code, comment) => decide.mutate({ action: "duplicate", reason_code: code, comment })} />
      <ReasonDialog open={dialog === "hold"} onOpenChange={(o) => setDialog(o ? "hold" : null)} title="Hold – ask supplier"
        description="Pauses the SLA for up to 3 business days and records your note." confirm="Hold" noCode requireComment
        busy={decide.isPending} onConfirm={(_, comment) => decide.mutate({ action: "hold", comment })} />
      <ReasonDialog open={dialog === "notdup"} onOpenChange={(o) => setDialog(o ? "notdup" : null)} title="Not a duplicate"
        description="Explain why this is a different invoice. It goes back to normal review." confirm="Not a duplicate"
        busy={decide.isPending}
        onConfirm={(code, comment) => {
          if (d.state === "DUPLICATE_SUSPECTED") decide.mutate({ action: "not_duplicate", reason_code: code, comment });
          else {
            draft.setOverride("DUP_FUZZY", code, comment);
            setDialog(null);
          }
        }} />
      <ReasonDialog open={dialog === "sendback"} onOpenChange={(o) => setDialog(o ? "sendback" : null)} title="Send back to maker"
        description="Returns to NEEDS_REVIEW with the same maker. A comment is required." confirm="Send back" noCode requireComment
        busy={check.isPending} onConfirm={(_, comment) => check.mutate({ action: "send_back", comment })} />
      <DuplicateCompare d={d} open={dialog === "compare"} onOpenChange={(o) => setDialog(o ? "compare" : null)} readOnly={readOnly}
        onConfirm={() => decide.mutate({ action: "duplicate", reason_code: "DUPLICATE_CONFIRMED" })}
        onNotDuplicate={() => setDialog("notdup")} />
    </div>
  );
}

function WorkspaceSkeleton() {
  return (
    <div className="grid h-screen grid-cols-[55fr_45fr] gap-3 p-4" aria-busy="true" aria-label="Loading invoice">
      <Skeleton className="h-full" />
      <div className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-32" />
        <Skeleton className="h-40" />
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}
