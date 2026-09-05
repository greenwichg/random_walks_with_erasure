/**
 * The handful of Tailwind DEFAULT colours the mobile web uses outside the token palette — the
 * freshness badge's and lifecycle pill's semantic tints (`red-500/12 text-red-600 dark:text-red-400`
 * and friends). Copied from Tailwind's own palette, not approximated, so a "Breaking" pill is the
 * same red on both platforms.
 */
export const tw = {
  red400: "#f87171",
  red500: "#ef4444",
  red600: "#dc2626",
  amber400: "#fbbf24",
  amber500: "#f59e0b",
  amber600: "#d97706",
  emerald400: "#34d399",
  emerald500: "#10b981",
  emerald600: "#059669",
  sky400: "#38bdf8",
  sky500: "#0ea5e9",
  sky600: "#0284c7",
  slate400: "#94a3b8",
  slate500: "#64748b",
  white: "#ffffff",
  black: "#000000",
} as const;
