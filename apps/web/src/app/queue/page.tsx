"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowRight, CheckCircle2, Lock, RefreshCw } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { useMe } from "@/components/providers";
import { EscalationChip, ReasonChips } from "@/components/reason-chips";
import { Banner, Button, Kbd, Skeleton, SlaPill } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { cn, dateTime, money } from "@/lib/format";
import type { QueueItem, QueueResponse } from "@/lib/types";

const col = createColumnHelper<QueueItem>();

export default function QueuePage() {
  return (
    <React.Suspense fallback={<QueueSkeleton />}>
      <Queue />
    </React.Suspense>
  );
}

function Queue() {
  const router = useRouter();
  const params = useSearchParams();
  const kind = params.get("kind") === "check" ? "check" : "review";
  const { data: me } = useMe();
  const filters = {
    entity: params.get("entity") ?? "",
    reason: params.get("reason") ?? "",
    sla: params.get("sla") ?? "",
    amount: params.get("amount") ?? "",
    supplier: params.get("supplier") ?? "",
  };
  const qs = new URLSearchParams({ kind, ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) });
  const q = useQuery({
    queryKey: ["queue", qs.toString(), me?.id],
    queryFn: () => api<QueueResponse>(`/queue?${qs}`),
    refetchInterval: 15_000,
    enabled: !!me,
  });
  const [cursor, setCursor] = React.useState(0);
  const [err, setErr] = React.useState<string | null>(null);

  const setFilter = (k: string, v: string) => {
    const p = new URLSearchParams(params.toString());
    if (v) p.set(k, v);
    else p.delete(k);
    router.replace(`/queue?${p}`);
  };

  const next = useMutation({
    mutationFn: () => api<{ invoice_id: string | null }>("/queue/next", { method: "POST", json: { kind } }),
    onSuccess: (r) => {
      if (r.invoice_id) router.push(`/invoices/${r.invoice_id}`);
      else setErr("Nothing left to claim — every open task is claimed or on hold.");
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not claim"),
  });

  const open = React.useCallback(
    async (row: QueueItem) => {
      if (row.assignee && !row.claimed_by_me) {
        router.push(`/invoices/${row.invoice_id}`); // read-only view
        return;
      }
      try {
        await api(`/invoices/${row.invoice_id}/claim`, { method: "POST" });
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 403)) {
          setErr(e instanceof ApiError ? e.message : "Could not claim");
        }
      }
      router.push(`/invoices/${row.invoice_id}`);
    },
    [router],
  );

  const items = React.useMemo(() => q.data?.items ?? [], [q.data]);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, select, textarea")) return;
      if (e.key === "n") next.mutate();
      if (e.key === "j") setCursor((c) => Math.min(items.length - 1, c + 1));
      if (e.key === "k") setCursor((c) => Math.max(0, c - 1));
      if (e.key === "Enter" && items[cursor]) open(items[cursor]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, cursor, next, open]);

  const columns = React.useMemo(
    () => [
      col.accessor("priority", {
        header: "Priority",
        cell: (c) => <PriorityBar value={c.getValue()} />,
      }),
      col.accessor("sla_state", {
        header: "SLA",
        cell: (c) => (
          <span className="flex items-center gap-1.5">
            <SlaPill state={c.getValue()} due={c.row.original.sla_due_at} hold={c.row.original.hold_until} />
            <EscalationChip level={c.row.original.escalation_level} pool={c.row.original.pool} />
          </span>
        ),
      }),
      col.accessor("supplier_name", {
        header: "Supplier",
        cell: (c) => <span className="font-medium">{c.getValue() ?? <em className="text-muted">Unreadable</em>}</span>,
      }),
      col.accessor("invoice_no", {
        header: "Invoice no.",
        cell: (c) => <span className="num font-mono text-[0.93rem]">{c.getValue() ?? "—"}</span>,
      }),
      col.accessor("entity_id", { header: "Entity" }),
      col.accessor("total_minor", {
        header: () => <span className="block text-right">Total</span>,
        cell: (c) => (
          <span className="num block text-right">
            {money(c.getValue(), c.row.original.currency)}{" "}
            <span className="text-muted">{c.row.original.currency ?? ""}</span>
          </span>
        ),
      }),
      col.accessor("reasons", {
        header: "Reasons",
        cell: (c) => <ReasonChips reasons={c.getValue()} />,
      }),
      col.accessor("assignee_name", {
        header: "Assignee",
        cell: (c) =>
          c.row.original.claimed_by_me ? (
            <span className="text-accent">You</span>
          ) : c.getValue() ? (
            <span className="inline-flex items-center gap-1 text-muted">
              <Lock className="size-3.5" aria-hidden /> {c.getValue()}
            </span>
          ) : (
            <span className="text-muted">—</span>
          ),
      }),
    ],
    [],
  );

  const table = useReactTable({ data: items, columns, getCoreRowModel: getCoreRowModel() });
  const facets = q.data?.facets;

  return (
    <div className="mx-auto max-w-[1440px] px-6 py-5">
      <header className="mb-4 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[1.45rem] font-semibold tracking-tight">
            {kind === "check" ? "Checker queue" : "Review queue"}
          </h1>
          <p className="text-muted">
            {kind === "check"
              ? "Maker decisions at or above LKR 1M waiting for a second approver."
              : "Exceptions the pipeline would not post on its own. Riskiest and most urgent first."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => q.refetch()} aria-label="Refresh">
            <RefreshCw className={cn("size-4", q.isFetching && "animate-spin")} />
          </Button>
          <Button variant="primary" size="lg" onClick={() => next.mutate()} disabled={next.isPending}>
            Next invoice <Kbd>N</Kbd>
            <ArrowRight className="size-4" aria-hidden />
          </Button>
        </div>
      </header>

      <div role="search" className="mb-3 flex flex-wrap items-center gap-2">
        <Select label="Entity" value={filters.entity} onChange={(v) => setFilter("entity", v)}
          options={(me?.entities ?? []).map((e) => [e, e])} />
        <Select label="Reason" value={filters.reason} onChange={(v) => setFilter("reason", v)}
          options={(facets?.reasons ?? []).map((r) => [r, r])} />
        <Select label="SLA" value={filters.sla} onChange={(v) => setFilter("sla", v)}
          options={[["on_track", "On track"], ["at_risk", "At risk"], ["breached", "Breached"], ["on_hold", "On hold"]]} />
        <Select label="Amount" value={filters.amount} onChange={(v) => setFilter("amount", v)}
          options={[["lt_250k", "< LKR 250k"], ["250k_1m", "LKR 250k – 1M"], ["gte_1m", "≥ LKR 1M"]]} />
        <input
          aria-label="Supplier"
          placeholder="Supplier…"
          defaultValue={filters.supplier}
          onKeyDown={(e) => e.key === "Enter" && setFilter("supplier", (e.target as HTMLInputElement).value)}
          className="h-8 w-48 rounded-md border border-border-strong bg-surface px-2.5 text-[0.93rem]"
        />
        {Object.values(filters).some(Boolean) ? (
          <Button variant="ghost" size="sm" onClick={() => router.replace(`/queue${kind === "check" ? "?kind=check" : ""}`)}>
            Clear filters
          </Button>
        ) : null}
        <span className="ml-auto text-muted num">{q.data ? `${q.data.total} open` : ""}</span>
      </div>

      {err ? (
        <div className="mb-3">
          <Banner tone="warn" action={<Button size="sm" variant="ghost" onClick={() => setErr(null)}>Dismiss</Button>}>
            {err}
          </Banner>
        </div>
      ) : null}

      {q.isLoading || !me ? (
        <QueueSkeleton />
      ) : q.isError ? (
        <Banner tone="fail">Could not load the queue. Is the API running on :8000?</Banner>
      ) : items.length === 0 ? (
        <EmptyQueue nextRisk={q.data?.next_sla_risk_at ?? null} />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-surface shadow-card">
          <table className="w-full border-collapse text-left">
            <thead className="bg-surface-2 text-[0.86rem] text-muted">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((h) => (
                    <th key={h.id} scope="col" className="px-3 py-2 font-medium">
                      {flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row, i) => (
                <tr
                  key={row.id}
                  tabIndex={0}
                  onClick={() => open(row.original)}
                  onKeyDown={(e) => e.key === "Enter" && open(row.original)}
                  onFocus={() => setCursor(i)}
                  aria-selected={i === cursor}
                  className={cn(
                    "cursor-pointer border-t border-border hover:bg-surface-2",
                    i === cursor && "bg-accent-tint/60",
                    row.original.sla_state === "breached" && "shadow-[inset_3px_0_0_var(--fail)]",
                    row.original.sla_state === "at_risk" && "shadow-[inset_3px_0_0_var(--warn-line)]",
                  )}
                >
                  {row.getVisibleCells().map((c) => (
                    <td key={c.id} className="px-3 py-2.5 align-middle">
                      {flexRender(c.column.columnDef.cell, c.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[0.86rem] text-muted">
        <Kbd>J</Kbd> <Kbd>K</Kbd> move · <Kbd>Enter</Kbd> open · <Kbd>N</Kbd> claim next. Opening an invoice claims it for 15 minutes.
      </p>
    </div>
  );
}

function PriorityBar({ value }: { value: number }) {
  const tone = value >= 60 ? "bg-fail" : value >= 35 ? "bg-warn-line" : "bg-accent";
  return (
    <span className="flex items-center gap-2" title={`Priority ${value.toFixed(0)} / 100`}>
      <span className="num w-7 text-right font-semibold">{value.toFixed(0)}</span>
      <span aria-hidden className="h-1.5 w-14 overflow-hidden rounded-full bg-surface-3">
        <span className={cn("block h-full rounded-full", tone)} style={{ width: `${Math.min(100, value)}%` }} />
      </span>
    </span>
  );
}

function Select({ label, value, onChange, options }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: (readonly [string, string])[] | [string, string][];
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-[0.93rem]">
      <span className="text-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn("h-8 rounded-md border border-border-strong bg-surface px-2", value && "border-accent text-accent")}
      >
        <option value="">All</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>{l}</option>
        ))}
      </select>
    </label>
  );
}

function EmptyQueue({ nextRisk }: { nextRisk: string | null }) {
  return (
    <div className="grid place-items-center rounded-lg border border-dashed border-border-strong bg-surface py-20 text-center">
      <CheckCircle2 className="mb-3 size-9 text-pass" aria-hidden />
      <h2 className="text-[1.2rem] font-semibold">Queue clear</h2>
      <p className="mt-1 text-muted">
        {nextRisk ? `Next SLA risk: ${dateTime(nextRisk)}` : "Nothing is waiting on a person right now."}
      </p>
    </div>
  );
}

function QueueSkeleton() {
  return (
    <div className="space-y-2 rounded-lg border border-border bg-surface p-3" aria-busy="true" aria-label="Loading queue">
      {Array.from({ length: 8 }).map((_, i) => (
        <Skeleton key={i} className="h-9" />
      ))}
    </div>
  );
}
