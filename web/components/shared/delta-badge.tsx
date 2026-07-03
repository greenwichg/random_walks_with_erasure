import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A signed change chip. `invert` for metrics where down is good (rarely used —
 * all our metrics are higher-is-healthier, but kept for reuse).
 */
export function DeltaBadge({
  value,
  suffix = "pts",
  invert = false,
  className,
}: {
  value: number;
  suffix?: string;
  invert?: boolean;
  className?: string;
}) {
  const good = invert ? value < 0 : value > 0;
  const neutral = value === 0;
  const Icon = neutral ? Minus : value > 0 ? ArrowUpRight : ArrowDownRight;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-medium tabular-nums",
        neutral
          ? "bg-muted text-muted-foreground"
          : good
            ? "bg-positive/12 text-positive"
            : "bg-negative/12 text-negative",
        className,
      )}
    >
      <Icon className="h-3 w-3" />
      {value > 0 ? "+" : ""}
      {value}
      {suffix ? ` ${suffix}` : ""}
    </span>
  );
}
