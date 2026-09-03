import * as React from "react";
import { cn } from "@/lib/utils";

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      className={cn(
        // A field is a TILE, not the page: `bg-card` keeps it visible whether it sits directly on
        // the (grey, on desktop) page or inside a card. The search controls that are deliberately
        // RECESSED into a bar or popover — the masthead pill, the filter popovers' own inputs —
        // keep `bg-background` and read as a well in the surface above them.
        "flex h-9 w-full rounded-md border border-input bg-card px-3 py-1 text-sm shadow-soft transition-colors",
        "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
