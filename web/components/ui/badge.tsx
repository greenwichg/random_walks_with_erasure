import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary/10 text-primary",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "text-foreground",
        positive: "border-transparent bg-positive/12 text-positive",
        caution: "border-transparent bg-caution/15 text-caution",
        negative: "border-transparent bg-negative/12 text-negative",
        // Lean pills carry a visible tint AND a colored hairline: at /12 with no border the pill
        // ground vanished on the dark card and "Lean left" read as a bare blue hyperlink — a
        // misleading affordance on a political signal. The border is what keeps the pill legible
        // as a pill on both themes without pushing the fill loud.
        left: "border-left/30 bg-left/15 text-left",
        center: "border-center/30 bg-center/15 text-center",
        right: "border-right/30 bg-right/15 text-right",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
