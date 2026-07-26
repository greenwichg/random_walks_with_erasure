import { cn } from "@/lib/utils";

/**
 * The canonical editorial grid (design system, Phase 6): a 12-column grid with an 8-column lead
 * and a 4-column companion rail, collapsing to a single column below `lg`. Extracted from the
 * hand-rolled copies on the Home and Story Details pages so every future page (Publishers, Topics,
 * Blindspots, Insights, …) inherits the same layout by composition:
 *
 *   <PageGrid rail={<>…rail modules…</>}>
 *     …lead sections…
 *   </PageGrid>
 *
 * Both columns carry the house vertical rhythm (space-y-8). For a full-width page, simply don't
 * use PageGrid — PageContainer alone is the full-width layout.
 */
export function PageGrid({
  children,
  rail,
  className,
}: {
  children: React.ReactNode;
  rail?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("grid grid-cols-12 gap-x-8 gap-y-8", className)}>
      <div className={cn("col-span-12 space-y-8", rail != null && "lg:col-span-8")}>{children}</div>
      {rail != null && <aside className="col-span-12 space-y-8 lg:col-span-4">{rail}</aside>}
    </div>
  );
}
