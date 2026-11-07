import { Chip } from "@/components/ui/primitives";
import { reasonChip } from "@/lib/format";

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
