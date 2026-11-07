import { Chip } from "@/components/ui/primitives";
import { cn, reasonChip } from "@/lib/format";

export function ReasonChips({ reasons, max = 3 }: { reasons: string[]; max?: number }) {
  const shown = reasons.slice(0, max);
  return (
    <span className="flex flex-wrap gap-1">
      {shown.map((r) => {
        const c = reasonChip(r);
        return (
          <Chip key={r} tone={c.code === "LOW_CONF" ? "warn" : c.code.startsWith("DUP") ? "accent" : "fail"}>
            {r}
          </Chip>
        );
      })}
      {reasons.length > max ? <Chip title={reasons.slice(max).join(", ")}>+{reasons.length - max}</Chip> : null}
    </span>
  );
}

export function EscalationChip({ level, pool }: { level: number; pool: string | null }) {
  if (!level) return null;
  const label = level >= 3 ? "Finance mgr" : pool === "supervisor" ? "Supervisor pool" : "Warned";
  return (
    <span
      title={`SLA escalation level ${level} of 3`}
      className={cn(
        "whitespace-nowrap rounded px-1.5 py-px text-[0.8rem] font-medium",
        level >= 2 ? "bg-fail-tint text-fail" : "bg-warn-tint text-warn",
      )}
    >
      {level >= 2 ? "✕" : "▲"} {label}
    </span>
  );
}
