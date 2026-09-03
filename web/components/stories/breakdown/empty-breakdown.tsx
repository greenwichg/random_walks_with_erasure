/**
 * What a breakdown tab says when the story's data cannot back it.
 *
 * A tab that exists on every story will sometimes have nothing to draw — no outlet on this story
 * is rated, the registry classifies none of them, the deployment doesn't publish the rating at
 * all. The reference has no such state because it assumes complete data; the house rule is that
 * absence is stated (L2.2), so the tab keeps its place in the strip and says which absence it is.
 * That is the whole component: one sentence, in the tab's own body box, never an empty panel and
 * never a zeroed chart.
 */
export function EmptyBreakdown({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-md bg-muted/50 px-3 py-6 text-center text-xs leading-relaxed text-muted-foreground">
      {children}
    </p>
  );
}
