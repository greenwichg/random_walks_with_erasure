import { Skeleton } from "@/components/ui/skeleton";
import { PageGrid } from "@/components/layout/page-grid";

/**
 * The home page's loading shape. Mirrors the real composition tier by tier — briefing card, hero,
 * feature pair, ranked rows, and the rail's module stack — on the SAME PageGrid the loaded page
 * uses, so nothing reflows when data lands. The perceived-performance win is in the *shape* being
 * right, not in showing more boxes.
 */
export function HomeSkeleton() {
  return (
    <div aria-hidden>
      <PageGrid
        rail={
          <>
            <Skeleton className="h-72 w-full rounded-lg" />
            <Skeleton className="h-96 w-full rounded-lg" />
            <Skeleton className="h-36 w-full rounded-lg" />
          </>
        }
      >
        <Skeleton className="h-36 w-full rounded-lg" />
        <Skeleton className="aspect-[16/9] w-full rounded-lg" />
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-64 rounded-lg" />
          <Skeleton className="h-64 rounded-lg" />
        </div>
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex gap-4">
              <Skeleton className="h-20 flex-1 rounded-md" />
              <Skeleton className="h-20 w-32 rounded-md" />
            </div>
          ))}
        </div>
      </PageGrid>
    </div>
  );
}
