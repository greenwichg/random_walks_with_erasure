import { Hash } from "lucide-react";
import { cn } from "@/lib/utils";

/** A pill for a topic, optionally showing its share of reading. */
export function TopicChip({
  topic,
  share,
  active,
  onClick,
  className,
}: {
  topic: string;
  share?: number;
  active?: boolean;
  onClick?: () => void;
  className?: string;
}) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
        active
          ? "border-primary/30 bg-primary/10 text-primary"
          : "bg-card text-foreground/80 hover:bg-accent",
        onClick && "cursor-pointer",
        className,
      )}
    >
      <Hash className="h-3 w-3 text-muted-foreground" />
      {topic}
      {typeof share === "number" && (
        <span className="tabular-nums text-muted-foreground">{Math.round(share * 100)}%</span>
      )}
    </Comp>
  );
}
