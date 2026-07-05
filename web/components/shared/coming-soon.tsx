import * as React from "react";
import { Construction, type LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

/**
 * An honest "coming soon" state for a feature that has no real backend yet. Used where showing
 * fabricated data would mislead — e.g. Stories / Discover, which depend on story clustering over the
 * news corpus that isn't built. It renders no placeholder data; it explains the dependency instead.
 *
 * Body-level (no PageContainer): the page keeps its own header and navigation and drops this into
 * the content area, so wiring the real feature later is a straightforward swap.
 */
export function ComingSoon({
  icon: Icon = Construction,
  eyebrow = "Coming soon",
  title,
  description,
  points,
}: {
  icon?: LucideIcon;
  eyebrow?: string;
  title: string;
  description: string;
  points?: string[];
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-4 px-6 py-14 text-center">
        <span className="grid h-14 w-14 place-items-center rounded-2xl bg-muted text-muted-foreground">
          <Icon className="h-7 w-7" />
        </span>
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wider text-primary">{eyebrow}</p>
          <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        </div>
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
        {points && points.length > 0 && (
          <ul className="mt-1 max-w-md space-y-1.5 text-left text-sm text-muted-foreground">
            {points.map((p) => (
              <li key={p} className="flex gap-2">
                <span className="mt-[0.4rem] h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
                <span>{p}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 max-w-md text-xs text-muted-foreground/80">
          We don&apos;t show placeholder or fabricated content here — this page stays empty until the
          feature is real.
        </p>
      </CardContent>
    </Card>
  );
}
