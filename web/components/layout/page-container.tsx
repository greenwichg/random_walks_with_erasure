"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

/** Consistent page padding, max width, and a gentle enter animation. */
export function PageContainer({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
      // MB1 H2: horizontal padding and the bottom edge honor the device safe areas (landscape
      // notch on the sides, home indicator at the bottom) via max(base, env(...)); `sm:px-6`/`lg`
      // take over at wider breakpoints where insets are irrelevant.
      className={cn(
        // max-w-6xl: the reference desktop layout runs ~1100px of content; the wider 7xl column
        // stretched its three-column front page into a dashboard.
        "mx-auto w-full max-w-6xl pt-6 pb-[max(1.5rem,env(safe-area-inset-bottom))] pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] sm:px-6 lg:px-8 lg:pb-8 lg:pt-8",
        className,
      )}
    >
      {children}
    </motion.div>
  );
}

/** Standard page heading block. */
export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {action && <div className="flex items-center gap-2">{action}</div>}
    </div>
  );
}
