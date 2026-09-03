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
  lead,
  rail,
  className,
}: {
  children: React.ReactNode;
  /**
   * Optional: the lead column's FIRST block, split out so the rail can sit between it and the
   * rest of the column when the grid collapses.
   *
   * Below `lg` a rail is simply appended after everything in the lead column, which is right for
   * a rail of afterthoughts and wrong for one carrying the analysis of the thing above it — on a
   * story that put the breakdown below the hero, the framing comparison, forty coverage rows and
   * the related list, which is to say nowhere. Passing the hero as `lead` puts the rail directly
   * under it on a phone; the desktop grid is unchanged (explicit row/column placement pins the
   * rail to row 1 of column 9, exactly where auto-placement had it).
   *
   * Pages that don't pass it keep the two-child behaviour they had.
   */
  lead?: React.ReactNode;
  rail?: React.ReactNode;
  className?: string;
}) {
  if (rail == null) {
    return (
      <div className={cn("grid grid-cols-12 gap-x-8 gap-y-8", className)}>
        <div className="col-span-12 space-y-8">
          {lead}
          {children}
        </div>
      </div>
    );
  }
  if (lead == null) {
    return (
      <div className={cn("grid grid-cols-12 gap-x-8 gap-y-8", className)}>
        <div className="col-span-12 space-y-8 lg:col-span-8">{children}</div>
        <aside className="col-span-12 space-y-8 lg:col-span-4">{rail}</aside>
      </div>
    );
  }
  return (
    <div className={cn("grid grid-cols-12 items-start gap-x-8 gap-y-8", className)}>
      <div className="col-span-12 lg:col-span-8 lg:row-start-1">{lead}</div>
      <aside className="col-span-12 space-y-8 lg:col-span-4 lg:col-start-9 lg:row-span-2 lg:row-start-1">
        {rail}
      </aside>
      <div className="col-span-12 space-y-8 lg:col-span-8 lg:col-start-1 lg:row-start-2">
        {children}
      </div>
    </div>
  );
}
