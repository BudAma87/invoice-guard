"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, Check, Clock, Info, MinusCircle, PauseCircle, X } from "lucide-react";
import * as React from "react";

import { cn, relTime } from "@/lib/format";
import type { RuleStatus, SlaState } from "@/lib/types";

/* ---------------------------------------------------------------- Button */

const button = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium " +
    "transition-colors disabled:pointer-events-none disabled:opacity-45 select-none",
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-fg hover:bg-accent-hover shadow-card",
        secondary: "bg-surface text-fg border border-border-strong hover:bg-surface-2",
        ghost: "text-fg-2 hover:bg-surface-2 hover:text-fg",
        danger: "bg-surface text-fail border border-border-strong hover:bg-fail-tint",
        dashed: "border border-dashed border-border-strong text-muted bg-transparent",
      },
      size: { sm: "h-7 px-2.5 text-[0.93rem]", md: "h-9 px-3.5", lg: "h-10 px-4" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(button({ variant, size }), className)} {...props} />
  ),
);
Button.displayName = "Button";

/* ---------------------------------------------------------------- Status
   Never colour alone: colour + icon + word (WCAG 1.4.1).                 */

const STATUS: Record<RuleStatus, { label: string; cls: string; Icon: typeof Check }> = {
  PASS: { label: "Pass", cls: "text-pass bg-pass-tint", Icon: Check },
  WARN: { label: "Warn", cls: "text-warn bg-warn-tint", Icon: AlertTriangle },
  FAIL: { label: "Fail", cls: "text-fail bg-fail-tint", Icon: X },
  SKIPPED: { label: "Not checked", cls: "text-skip bg-skip-tint", Icon: MinusCircle },
};

export function StatusBadge({
  status,
  blocking,
  compact,
  className,
}: {
  status: RuleStatus;
  blocking?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const s = STATUS[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[0.86rem] font-semibold leading-none",
        s.cls,
        className,
      )}
    >
      <s.Icon aria-hidden className="size-3.5 shrink-0" strokeWidth={2.6} />
      {compact ? <span className="sr-only">{s.label}</span> : blocking ? "Blocked" : s.label}
    </span>
  );
}

export function StatusIcon({ status }: { status: RuleStatus }) {
  const s = STATUS[status];
  return (
    <span className={cn("inline-grid size-5 shrink-0 place-items-center rounded-full", s.cls)}>
      <s.Icon aria-label={s.label} className="size-3.5" strokeWidth={2.8} />
    </span>
  );
}

/* ---------------------------------------------------------------- SLA pill */

const SLA: Record<SlaState, { cls: string; label: string; Icon: typeof Clock }> = {
  on_track: { cls: "text-fg-2 bg-surface-2 border-border", label: "On track", Icon: Clock },
  at_risk: { cls: "text-warn bg-warn-tint border-warn-line/50", label: "At risk", Icon: AlertTriangle },
  breached: { cls: "text-fail bg-fail-tint border-fail/40", label: "Breached", Icon: X },
  on_hold: { cls: "text-skip bg-skip-tint border-border", label: "On hold", Icon: PauseCircle },
};

export function SlaPill({ state, due, hold }: { state: SlaState; due: string; hold?: string | null }) {
  const s = SLA[state];
  const text = state === "on_hold" && hold ? `Hold until ${new Date(hold).toLocaleDateString()}` : relTime(due);
  return (
    <span
      className={cn(
        "num inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[0.86rem] font-medium",
        s.cls,
      )}
      title={`SLA ${s.label} · due ${new Date(due).toLocaleString()}`}
    >
      <s.Icon aria-hidden className="size-3.5" />
      <span className="sr-only">SLA {s.label}:</span>
      {text}
    </span>
  );
}

/* ---------------------------------------------------------------- misc */

export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("rounded-lg border border-border bg-surface shadow-card", className)} {...p} />
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-border-strong bg-surface-2 px-1.5 py-px font-mono text-[0.8rem] text-fg-2">
      {children}
    </kbd>
  );
}

export function Chip({ children, tone = "neutral", title }: {
  children: React.ReactNode;
  tone?: "neutral" | "fail" | "warn" | "accent";
  title?: string;
}) {
  const t = {
    neutral: "bg-surface-2 text-fg-2 border-border",
    fail: "bg-fail-tint text-fail border-transparent",
    warn: "bg-warn-tint text-warn border-transparent",
    accent: "bg-accent-tint text-accent border-transparent",
  }[tone];
  return (
    <span title={title} className={cn("inline-flex items-center rounded border px-1.5 py-px font-mono text-[0.8rem]", t)}>
      {children}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton", className)} />;
}

export function Banner({
  tone,
  children,
  action,
}: {
  tone: "warn" | "fail" | "info";
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  const t = {
    warn: "bg-warn-tint text-warn border-warn-line/40",
    fail: "bg-fail-tint text-fail border-fail/30",
    info: "bg-accent-tint text-accent border-accent/30",
  }[tone];
  return (
    <div role="status" className={cn("flex items-center gap-3 rounded-md border px-3 py-2 text-[0.93rem]", t)}>
      {tone === "info" ? (
        <Info aria-hidden className="size-4 shrink-0" />
      ) : (
        <AlertTriangle aria-hidden className="size-4 shrink-0" />
      )}
      <div className="flex-1">{children}</div>
      {action}
    </div>
  );
}

/* ---------------------------------------------------------------- Dialog */

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <DialogPrimitive.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-auto",
            "rounded-xl border border-border bg-surface p-5 shadow-pop",
            wide ? "max-w-[960px]" : "max-w-[560px]",
          )}
        >
          <DialogPrimitive.Title className="text-[1.15rem] font-semibold">{title}</DialogPrimitive.Title>
          {description ? (
            <DialogPrimitive.Description className="mt-1 text-muted">{description}</DialogPrimitive.Description>
          ) : (
            <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
          )}
          <div className="mt-4">{children}</div>
          {footer ? <div className="mt-5 flex justify-end gap-2">{footer}</div> : null}
          <DialogPrimitive.Close
            aria-label="Close"
            className="absolute right-3 top-3 rounded p-1 text-muted hover:bg-surface-2"
          >
            <X className="size-4" />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[0.86rem] font-medium text-fg-2">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-[0.86rem] text-muted">{hint}</span> : null}
    </label>
  );
}

export const inputCls =
  "h-9 w-full rounded-md border border-border-strong bg-surface px-2.5 text-fg placeholder:text-muted " +
  "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25";
