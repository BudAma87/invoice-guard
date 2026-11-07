"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Clock, Copy, Inbox, PauseCircle, Send, ServerCrash, Timer, Zap } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useMe } from "@/components/providers";
import { Banner, Card, Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { OpsMetrics } from "@/lib/types";

// Validated with dataviz/scripts/validate_palette.js (slots 1-2, light + dark): all checks pass.
const S1 = "var(--series-1)";
const S2 = "var(--series-2)";
const AXIS = { stroke: "var(--muted)", fontSize: 12, tickLine: false, axisLine: false } as const;

export default function OpsPage() {
  const { data: me } = useMe();
  const [entity, setEntity] = React.useState("");
  const allowed = me && (me.roles.includes("supervisor") || me.roles.includes("auditor"));
  const q = useQuery({
    queryKey: ["ops", entity, me?.id],
    queryFn: () => api<OpsMetrics>(`/ops/metrics${entity ? `?entity=${entity}` : ""}`),
    enabled: !!allowed,
    refetchInterval: 20_000,
  });

  if (me && !allowed) {
    return (
      <div className="mx-auto max-w-xl px-6 py-20 text-center">
        <h1 className="text-[1.3rem] font-semibold">Supervisors only</h1>
        <p className="mt-2 text-muted">Switch to a supervisor (e.g. Anushka Wijesinghe) in the sidebar.</p>
      </div>
    );
  }

  const m = q.data;
  return (
    <div className="mx-auto max-w-[1440px] px-6 py-5">
      <header className="mb-4 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[1.45rem] font-semibold tracking-tight">Ops dashboard</h1>
          <p className="text-muted">
            Today, {m ? new Date(m.as_of).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "…"} · refreshes every 20 s
            {m?.month_end_window ? <span className="ml-2 rounded bg-warn-tint px-1.5 text-warn">Month-end window · SLA 4 h</span> : null}
          </p>
        </div>
        <label className="inline-flex items-center gap-1.5">
          <span className="text-muted">Entity</span>
          <select value={entity} onChange={(e) => setEntity(e.target.value)} className="h-8 rounded-md border border-border-strong bg-surface px-2">
            <option value="">All my entities</option>
            {(me?.entities ?? []).map((e) => <option key={e}>{e}</option>)}
          </select>
        </label>
      </header>

      {q.isError ? <Banner tone="fail">Could not load metrics.</Banner> : null}
      {!m ? (
        <div className="grid grid-cols-3 gap-3 lg:grid-cols-5">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : (
        <>
          <section aria-label="Key figures" className="grid grid-cols-3 gap-3 xl:grid-cols-5">
            <Tile label="Received today" value={m.tiles.received_today} Icon={Inbox} />
            <Tile label="Auto-pass rate" value={m.tiles.auto_pass_rate === null ? "—" : `${(m.tiles.auto_pass_rate * 100).toFixed(0)}%`}
              sub="target 90% after tuning" Icon={Zap}
              tone={m.tiles.auto_pass_rate !== null && m.tiles.auto_pass_rate < 0.8 ? "warn" : undefined} />
            <Tile label="Queue depth" value={m.tiles.queue_depth} href="/queue" Icon={Clock} />
            <Tile label="SLA at risk" value={m.tiles.sla_at_risk} href="/queue?sla=at_risk" Icon={AlertTriangle}
              tone={m.tiles.sla_at_risk ? "warn" : undefined} />
            <Tile label="SLA breached" value={m.tiles.sla_breached} href="/queue?sla=breached" Icon={Timer}
              tone={m.tiles.sla_breached ? "fail" : undefined} />
            <Tile label="Posted today" value={m.tiles.posted_today} Icon={Send} />
            <Tile label="Duplicates blocked" value={m.tiles.duplicates_blocked} href="/queue?reason=DUP_EXACT" Icon={Copy} />
            <Tile label="Provider error rate" value={`${(m.tiles.provider_error_rate * 100).toFixed(1)}%`} Icon={ServerCrash}
              tone={m.tiles.provider_error_rate > 0.05 ? "fail" : undefined} />
            <Tile label="Oldest not final" Icon={PauseCircle}
              value={m.tiles.oldest_open ? fmtAge(m.tiles.oldest_open.age_minutes) : "—"}
              sub={m.tiles.oldest_open?.state} href={m.tiles.oldest_open ? `/invoices/${m.tiles.oldest_open.invoice_id}` : undefined}
              tone={m.tiles.oldest_open && m.tiles.oldest_open.age_minutes > 240 ? "fail" : undefined} />
            <Tile label="On hold" value={m.tiles.on_hold} href="/queue?sla=on_hold" Icon={PauseCircle} />
          </section>

          <section className="mt-4 grid grid-cols-2 gap-3">
            <ArrivalsChart m={m} />
            <ReasonChart m={m} />
            <ThroughputChart m={m} />
            <BacklogChart m={m} />
          </section>
        </>
      )}
    </div>
  );
}

function fmtAge(min: number) {
  return min >= 60 ? `${Math.floor(min / 60)}h ${min % 60}m` : `${min}m`;
}

function Tile({ label, value, sub, href, Icon, tone }: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  href?: string;
  Icon: typeof Inbox;
  tone?: "warn" | "fail";
}) {
  const body = (
    <Card className={cn("h-full p-3 transition-colors", href && "hover:border-accent")}>
      <div className="flex items-center gap-1.5 text-[0.86rem] text-muted">
        <Icon className="size-4" aria-hidden /> {label}
      </div>
      <div className="num mt-1 flex items-center gap-1.5 text-[1.75rem] font-semibold leading-tight">
        {value}
        {tone ? (
          <span className={cn("rounded px-1.5 py-0.5 text-[0.8rem] font-semibold", tone === "fail" ? "bg-fail-tint text-fail" : "bg-warn-tint text-warn")}>
            {tone === "fail" ? "✕ attention" : "▲ watch"}
          </span>
        ) : null}
      </div>
      {sub ? <div className="text-[0.8rem] text-muted">{sub}</div> : null}
    </Card>
  );
  return href ? <Link href={href} className="block rounded-lg">{body}</Link> : body;
}

/* ----------------------------------------------------------- chart frame + table view */

function ChartCard({ title, sub, legend, table, children }: {
  title: string;
  sub?: string;
  legend?: { label: string; color: string; dashed?: boolean }[];
  table: { head: string[]; rows: (string | number)[][] };
  children: React.ReactNode;
}) {
  const [asTable, setAsTable] = React.useState(false);
  return (
    <Card className="p-3">
      <div className="mb-2 flex items-start gap-3">
        <div>
          <h2 className="font-semibold">{title}</h2>
          {sub ? <p className="text-[0.86rem] text-muted">{sub}</p> : null}
        </div>
        {legend ? (
          <ul className="ml-auto flex gap-3 text-[0.86rem] text-fg-2" aria-label="Legend">
            {legend.map((l) => (
              <li key={l.label} className="inline-flex items-center gap-1.5">
                <span aria-hidden className="inline-block h-0.5 w-4" style={{ background: l.dashed ? "none" : l.color, borderTop: l.dashed ? `2px dashed ${l.color}` : undefined }} />
                {l.label}
              </li>
            ))}
          </ul>
        ) : null}
        <button onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}
          className={cn("rounded border border-border px-2 py-0.5 text-[0.8rem] text-muted hover:bg-surface-2", !legend && "ml-auto")}>
          {asTable ? "Chart" : "Table"}
        </button>
      </div>
      {asTable ? (
        <div className="h-[220px] overflow-auto">
          <table className="num w-full text-[0.86rem]">
            <thead className="text-left text-muted"><tr>{table.head.map((h) => <th key={h} className="py-1 pr-3 font-medium">{h}</th>)}</tr></thead>
            <tbody>{table.rows.map((r, i) => <tr key={i} className="border-t border-border">{r.map((c, j) => <td key={j} className="py-1 pr-3">{c}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div className="h-[220px]">{children}</div>
      )}
    </Card>
  );
}

interface TipProps {
  active?: boolean;
  label?: string | number;
  payload?: { dataKey?: string | number; name?: string; value?: number; color?: string }[];
  unit?: string;
}

function Tip({ active, payload, label, unit = "" }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-[0.86rem] shadow-pop">
      <div className="mb-0.5 font-medium">{label}</div>
      {payload.map((p) => (
        <div key={String(p.dataKey)} className="num flex items-center gap-2 text-fg-2">
          <span aria-hidden className="size-2 rounded-full" style={{ background: p.color }} />
          {p.name}: <b className="text-fg">{p.value}{unit}</b>
        </div>
      ))}
    </div>
  );
}

const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`;

function ArrivalsChart({ m }: { m: OpsMetrics }) {
  const data = m.series.hours.map((h, i) => ({ h: hourLabel(h), arrivals: m.series.arrivals[i], processed: m.series.processed[i] }));
  return (
    <ChartCard title="Arrivals vs processed per hour" sub="Processed = reached a final state · the gap is backlog building up"
      legend={[{ label: "Arrivals", color: S1 }, { label: "Processed", color: S2 }]}
      table={{ head: ["Hour", "Arrivals", "Processed"], rows: data.map((d) => [d.h, d.arrivals, d.processed]) }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="h" {...AXIS} interval={2} />
          <YAxis allowDecimals={false} {...AXIS} />
          <Tooltip content={<Tip />} cursor={{ stroke: "var(--border-strong)" }} />
          <Line name="Arrivals" dataKey="arrivals" stroke={S1} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
          <Line name="Processed" dataKey="processed" stroke={S2} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function ReasonChart({ m }: { m: OpsMetrics }) {
  const router = useRouter();
  const data = m.queue_by_reason.slice(0, 10);
  return (
    <ChartCard title="Queue depth by reason" sub="Open tasks carrying each reason · click a bar to open the filtered queue"
      table={{ head: ["Reason", "Open tasks"], rows: data.map((d) => [d.reason, d.count]) }}>
      {data.length === 0 ? <div className="grid h-full place-items-center text-muted">Queue clear</div> : (
        <ResponsiveContainer>
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 24, bottom: 0, left: 40 }} barCategoryGap={2}>
            <CartesianGrid horizontal={false} stroke="var(--grid)" />
            <XAxis type="number" allowDecimals={false} {...AXIS} />
            <YAxis type="category" dataKey="reason" width={130} interval={0} {...AXIS} tick={{ fill: "var(--text-2)", fontSize: 12, fontFamily: "var(--font-mono)" }} />
            <Tooltip content={<Tip />} cursor={{ fill: "var(--surface-2)" }} />
            <Bar name="Open tasks" dataKey="count" fill={S1} radius={[0, 4, 4, 0]} maxBarSize={18} isAnimationActive={false}
              className="cursor-pointer" onClick={(d) => router.push(`/queue?reason=${encodeURIComponent((d as unknown as { reason: string }).reason)}`)}
              label={{ position: "right", fill: "var(--text-2)", fontSize: 12 }} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

function ThroughputChart({ m }: { m: OpsMetrics }) {
  const data = m.series.hours.map((h, i) => ({ h: hourLabel(h), decisions: m.series.reviewer_throughput[i] }));
  return (
    <ChartCard title="Reviewer throughput per hour" sub="Approve, reject, duplicate and checker decisions"
      table={{ head: ["Hour", "Decisions"], rows: data.map((d) => [d.h, d.decisions]) }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="h" {...AXIS} interval={2} />
          <YAxis allowDecimals={false} {...AXIS} />
          <Tooltip content={<Tip />} cursor={{ fill: "var(--surface-2)" }} />
          <Bar name="Decisions" dataKey="decisions" fill={S1} radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function BacklogChart({ m }: { m: OpsMetrics }) {
  const data = m.series.hours.map((h, i) => ({ h: hourLabel(h), backlog: m.series.backlog[i] }));
  const cap = m.series.capacity_per_hour;
  return (
    <ChartCard title="Backlog burn-down vs capacity" sub={`Open, not-final invoices · reviewer capacity ≈ ${cap}/h at 2.5 min each`}
      legend={[{ label: "Backlog", color: S1 }, { label: "Capacity", color: "var(--muted)", dashed: true }]}
      table={{ head: ["Hour", "Backlog", "Capacity/h"], rows: data.map((d) => [d.h, d.backlog, cap]) }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="h" {...AXIS} interval={2} />
          <YAxis allowDecimals={false} {...AXIS} domain={[0, (max: number) => Math.max(max, cap) + 4]} />
          <Tooltip content={<Tip />} cursor={{ stroke: "var(--border-strong)" }} />
          <ReferenceLine y={cap} stroke="var(--muted)" strokeDasharray="4 4" strokeWidth={1.5}
            label={{ value: `capacity ${cap}/h`, position: "insideTopRight", fill: "var(--muted)", fontSize: 12 }} />
          <Line name="Backlog" dataKey="backlog" stroke={S1} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
