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
  onAction,
  actionLabel,
  id,
  className,
}: {
  title: string;
  /** Small uppercase kicker above the title (e.g. the topic a module covers). */
  eyebrow?: string;
  /** Optional destination for the trailing action; renders nothing without `actionLabel`. */
  href?: string;
  /**
   * In-place alternative to `href` — the trailing action reveals more of THIS section instead of
   * going somewhere. Same treatment either way, because to a reader "View all" means the same
   * thing whether the rest of the list is on another page or below the fold of this card; what
   * must differ is the element, so a link goes somewhere and a button does something.
   *
   * `href` wins if both are passed: a real destination is the stronger promise.
   */
  onAction?: () => void;
  actionLabel?: string;
  id?: string;
  className?: string;
}) {
  const action =
    "group inline-flex shrink-0 items-center gap-1 rounded text-xs font-medium text-muted-foreground transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2";
  return (
    <div className={cn("mb-4 flex items-end justify-between gap-4 border-b pb-2.5", className)}>
      <div className="min-w-0">
        {/* The kicker is a neutral editorial label, not an accent mark — accent colour is reserved
            for interactive state, so a page of section headers doesn't read as branded chrome. */}
        {eyebrow && (
          <p className="mb-0.5 text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground">
            {eyebrow}
          </p>
        )}
        <h2 id={id} className="truncate text-lg font-semibold tracking-tight">
          {title}
        </h2>
      </div>
      {actionLabel &&
        (href ? (
          <Link href={href} className={action}>
            {actionLabel}
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
          </Link>
        ) : (
          onAction && (
            <button type="button" onClick={onAction} className={action}>
              {actionLabel}
              <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </button>
          )
        ))}
    </div>
  );
}
