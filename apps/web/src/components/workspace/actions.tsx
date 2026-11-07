"use client";

import { useQuery } from "@tanstack/react-query";
import { Ban, Check, Copy, PauseCircle, ShieldAlert, UserCheck } from "lucide-react";
import * as React from "react";

import { Button, Dialog, Field, Kbd, StatusIcon, inputCls } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { REASON_LABEL, RULE_LABEL, cn } from "@/lib/format";
import type { Overrides, RuleResult } from "@/lib/types";

export type Action = "approve" | "approve_with_override" | "reject" | "duplicate" | "not_duplicate" | "hold";

export function useReasonCodes() {
  return useQuery({ queryKey: ["reason-codes"], queryFn: () => api<string[]>("/reason-codes"), staleTime: Infinity });
}

export function ReasonSelect({ value, onChange, id, exclude = [] }: {
  value: string;
  onChange: (v: string) => void;
  id?: string;
  exclude?: string[];
}) {
  const { data } = useReasonCodes();
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={inputCls}>
      <option value="">Choose a reason code…</option>
      {(data ?? []).filter((c) => !exclude.includes(c)).map((c) => (
        <option key={c} value={c}>{c} — {REASON_LABEL[c] ?? c}</option>
      ))}
    </select>
  );
}

export const reasonOk = (code: string, comment: string) => !!code && (code !== "OTHER" || comment.trim().length >= 20);

/* ----------------------------------------------------------- action bar */

export function ActionBar({
  readOnly,
  allowed,
  checkState,
  needsChecker,
  fails,
  blocked,
  overrides,
  busy,
  isDuplicateSuspected,
  onApprove,
  onOverride,
  onReject,
  onDuplicate,
  onHold,
  problems,
}: {
  readOnly: boolean;
  allowed: boolean;
  checkState: string;
  needsChecker: boolean;
  fails: RuleResult[];
  blocked: boolean;
  overrides: Overrides;
  busy: boolean;
  isDuplicateSuspected: boolean;
  onApprove: () => void;
  onOverride: () => void;
  onReject: () => void;
  onDuplicate: () => void;
  onHold: () => void;
  problems: string[];
}) {
  const notRechecked = checkState === "failed";
  const checking = checkState === "checking";
  const unOverridden = fails.filter((f) => !overrides[f.key]);
  const canApprove = !readOnly && allowed && !notRechecked && !checking && !blocked;
  const canOverride = !readOnly && !blocked && fails.length > 0 && !notRechecked;
  const approveLabel = needsChecker ? "Send to checker" : "Approve";
  const reason = blocked
    ? "A blocking rule failed — this invoice cannot be approved"
    : notRechecked
      ? "Not re-checked — approve stays disabled until a check succeeds"
      : checking
        ? "Re-checking…"
        : unOverridden.length
          ? `${unOverridden.length} failing rule${unOverridden.length > 1 ? "s" : ""} to fix or override`
          : problems[0] ?? "";
  return (
    <div className="sticky bottom-0 z-10 border-t border-border bg-surface/95 px-4 py-2.5 backdrop-blur">
      <div className="flex items-center gap-2">
        <Button
          variant={canApprove ? "primary" : "dashed"}
          disabled={!canApprove || busy}
          onClick={onApprove}
          aria-describedby="approve-why"
        >
          {needsChecker ? <UserCheck className="size-4" aria-hidden /> : <Check className="size-4" aria-hidden />}
          {approveLabel} <Kbd>A</Kbd>
        </Button>
        <Button variant={canOverride && !canApprove ? "primary" : "secondary"} disabled={!canOverride || busy} onClick={onOverride}>
          <ShieldAlert className="size-4" aria-hidden /> {needsChecker ? "Override & send to checker" : "Approve with override"} <Kbd>O</Kbd>
        </Button>
        <Button variant="danger" disabled={readOnly || busy} onClick={onReject}>
          <Ban className="size-4" aria-hidden /> Reject <Kbd>R</Kbd>
        </Button>
        <span className="mx-1 h-6 w-px bg-border" aria-hidden />
        <Button disabled={readOnly || busy} onClick={onDuplicate}>
          <Copy className="size-4" aria-hidden /> {isDuplicateSuspected ? "Confirm duplicate" : "Mark duplicate"} <Kbd>D</Kbd>
        </Button>
        <Button variant="ghost" disabled={readOnly || busy} onClick={onHold}>
          <PauseCircle className="size-4" aria-hidden /> Hold – ask supplier <Kbd>H</Kbd>
        </Button>
        <span id="approve-why" className={cn("ml-auto max-w-[420px] truncate text-right text-[0.93rem]", notRechecked || blocked ? "text-fail" : "text-muted")}>
          {readOnly ? "Read-only" : reason}
        </span>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- dialogs */

export function OverrideDialog({ open, onOpenChange, fails, overrides, setOverride, onConfirm, needsChecker, busy }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  fails: RuleResult[];
  overrides: Overrides;
  setOverride: (key: string, code: string | null, comment?: string) => void;
  onConfirm: () => void;
  needsChecker: boolean;
  busy: boolean;
}) {
  const ok = fails.every((f) => overrides[f.key] && reasonOk(overrides[f.key].reason_code, overrides[f.key].comment ?? ""));
  return (
    <Dialog open={open} onOpenChange={onOpenChange} wide title="Approve with override"
      description="Every remaining failed rule needs a reason code. The server re-runs the rules before it accepts."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" disabled={!ok || busy} onClick={onConfirm}>
          {needsChecker ? "Send to checker" : "Approve with override"}
        </Button>
      </>}>
      <ul className="space-y-3">
        {fails.map((f) => {
          const o = overrides[f.key] ?? { reason_code: "", comment: "" };
          return (
            <li key={f.key} className="rounded-md border border-border p-3">
              <div className="mb-2 flex items-start gap-2">
                <StatusIcon status="FAIL" />
                <div>
                  <div className="font-medium">{RULE_LABEL[f.code] ?? f.code}{f.line ? ` · line ${f.line}` : ""}</div>
                  <div className="text-[0.93rem] text-muted">{f.message}</div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Reason code">
                  <ReasonSelect value={o.reason_code} onChange={(v) => setOverride(f.key, v || null, o.comment)} exclude={["DUPLICATE_CONFIRMED", "NOT_OUR_INVOICE"]} />
                </Field>
                <Field label="Comment" hint={o.reason_code === "OTHER" ? `${(o.comment ?? "").trim().length}/20 characters minimum` : "Optional"}>
                  <input className={inputCls} value={o.comment ?? ""} disabled={!o.reason_code}
                    onChange={(e) => setOverride(f.key, o.reason_code || null, e.target.value)} />
                </Field>
              </div>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}

export function ReasonDialog({ open, onOpenChange, title, description, confirm, onConfirm, defaultCode = "", busy, requireComment, noCode }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description: string;
  confirm: string;
  onConfirm: (code: string, comment: string) => void;
  defaultCode?: string;
  busy: boolean;
  requireComment?: boolean;
  noCode?: boolean;
}) {
  const [code, setCode] = React.useState(defaultCode);
  const [comment, setComment] = React.useState("");
  React.useEffect(() => {
    if (open) {
      setCode(defaultCode);
      setComment("");
    }
  }, [open, defaultCode]);
  const ok = (noCode || reasonOk(code, comment)) && (!requireComment || comment.trim().length >= 5);
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title} description={description}
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" disabled={!ok || busy} onClick={() => onConfirm(code, comment)}>{confirm}</Button>
      </>}>
      <div className="space-y-3">
        {!noCode ? (
          <Field label="Reason code"><ReasonSelect value={code} onChange={setCode} /></Field>
        ) : null}
        <Field label={requireComment ? "Note (required)" : "Comment"} hint={code === "OTHER" ? `${comment.trim().length}/20 characters minimum` : undefined}>
          <textarea className={cn(inputCls, "h-24 py-2")} value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}


