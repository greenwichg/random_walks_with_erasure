import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names, de-duping conflicting Tailwind utilities. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Clamp a number to a range. */
export function clamp(n: number, min = 0, max = 100) {
  return Math.min(max, Math.max(min, n));
}

/** Format a 0–100 score with no decimals. */
export function formatScore(n: number) {
  return Math.round(clamp(n)).toString();
}

// Locale-aware `formatCompact` / `timeAgo` / `formatDate` now live in @ih/core/i18n/core and are
// exposed through the `useTranslation()` hook. The old hardcoded-"en" helpers were removed in
// Commit 20.1 so no UI path formats in a fixed locale.
