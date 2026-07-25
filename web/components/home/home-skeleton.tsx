import { Skeleton } from "@/components/ui/skeleton";

/**
 * The home page's loading shape. Mirrors the real 12-column composition (lead + rail) so the
 * layout doesn't reflow when data lands — the perceived-performance win is in the *shape* being
 * right, not in showing more boxes.
 */
export function HomeSkeleton() {
  return (
    <div className="grid grid-cols-12 gap-6 lg:gap-8" aria-hidden>
      <div className="col-span-12 space-y-6 lg:col-span-8">
        <Skeleton className="h-28 w-full rounded-lg" />
        <Skeleton className="aspect-[16/9] w-full rounded-lg" />
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex gap-4">
              <Skeleton className="h-16 flex-1 rounded-md" />
              <Skeleton className="h-16 w-24 rounded-md" />
            </div>
          ))}
        </div>
      </div>
      <div className="col-span-12 space-y-6 lg:col-span-4">
        <Skeleton className="h-64 w-full rounded-lg" />
        <Skeleton className="h-80 w-full rounded-lg" />
      </div>
    </div>
  );
}
