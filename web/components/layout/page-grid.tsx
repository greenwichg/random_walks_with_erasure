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
    // ONE COLUMN, NOT TWO ROWS. The lead and the rest of the lead column are a single flow inside
    // one grid cell, beside the rail — because the earlier shape (lead in row 1, children in row 2,
    // rail spanning both) let the RAIL decide how tall row 1 was. A tall rail — Story Intelligence
    // with its timeline expanded is the case that surfaced it — stretched row 1 to fit itself and
    // parked the lead at the top of it, opening a band of empty page between the hero and the
    // section under it that grew with every timeline row revealed. Nothing can fill that band: it
    // is a row's leftover height, not a gap between siblings.
    //
    // `contents` is what keeps the phone's order intact through that change. Below `lg` the wrapper
    // dissolves, so lead, rail and the rest are three grid items again and `order` puts the rail
    // between the first two — the reason `lead` exists as a prop at all. At `lg` the wrapper becomes
    // an ordinary 8-column block and the two halves of the lead column close up.
    <div className={cn("grid grid-cols-12 items-start gap-x-8 gap-y-8", className)}>
      <div className="contents lg:col-span-8 lg:col-start-1 lg:row-start-1 lg:block lg:space-y-8">
        <div className="order-1 col-span-12 lg:order-none">{lead}</div>
        <div className="order-3 col-span-12 space-y-8 lg:order-none">{children}</div>
      </div>
      <aside className="order-2 col-span-12 space-y-8 lg:order-none lg:col-span-4 lg:col-start-9 lg:row-start-1">
        {rail}
      </aside>
    </div>
  );
}
