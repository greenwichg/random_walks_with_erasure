import { cn } from "@/lib/utils";

/**
 * The one filter row (design system): wrapping filter pills on the left, the result count — or any
 * trailing status — pinned to the right edge, one bottom margin everywhere.
 *
 * Stories, Discover, Search and Reading History had each grown their own copy of this row with
 * slightly different spacing, and only two of them said how many results the filters produced.
 * A reader who learns the row once should recognise it on every list page.
 */
export function FilterBar({
  children,
  trailing,
  className,
}: {
  children: React.ReactNode;
  /** Right-aligned status, typically the result count. Omit rather than passing an empty string. */
  trailing?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-6 flex flex-wrap items-center gap-2", className)}>
      {children}
      {trailing != null && trailing !== false && (
        <span className="ml-auto text-sm tabular-nums text-muted-foreground">{trailing}</span>
      )}
    </div>
  );
}
