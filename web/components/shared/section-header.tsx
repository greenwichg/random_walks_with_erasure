import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The editorial section rule — one heading treatment shared by every home module, so the page
 * reads as a single publication rather than a stack of unrelated cards. A hairline underline
 * (not a shadowed card header) does the separating work: content-first, minimal chrome.
 *
 * `id` lets a caller bind the section's `aria-labelledby` to this heading, so each module is a
 * properly-labelled landmark for assistive tech.
 */
export function SectionHeader({
  title,
  eyebrow,
  href,
  actionLabel,
  id,
  className,
}: {
  title: string;
  /** Small uppercase kicker above the title (e.g. the topic a module covers). */
  eyebrow?: string;
  /** Optional destination for the trailing action; renders nothing without `actionLabel`. */
  href?: string;
  actionLabel?: string;
  id?: string;
  className?: string;
}) {
  return (
    <div className={cn("mb-4 flex items-end justify-between gap-4 border-b pb-2.5", className)}>
      <div className="min-w-0">
        {eyebrow && (
          <p className="mb-0.5 text-[0.7rem] font-semibold uppercase tracking-wider text-primary">
            {eyebrow}
          </p>
        )}
        <h2 id={id} className="truncate text-lg font-semibold tracking-tight">
          {title}
        </h2>
      </div>
      {href && actionLabel && (
        <Link
          href={href}
          className="group inline-flex shrink-0 items-center gap-1 rounded text-xs font-medium text-muted-foreground transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          {actionLabel}
          <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
        </Link>
      )}
    </div>
  );
}
