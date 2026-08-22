import type { LucideIcon } from "lucide-react";
import { Activity, BarChart3, Bookmark, Bot, Compass, History, LayoutDashboard, Newspaper, ScanSearch, Settings, Sparkles, User } from "lucide-react";

/**
 * The lucide icon for each navigation destination, keyed by `href`.
 *
 * Split out of the nav table so that table could move to @ih/core. Everything else about a nav item
 * — its route, its English fallback label, its i18n key, its hint, its section — is the same product
 * decision on every platform, and a mobile tab bar that pointed somewhere else would be a bug. The
 * icon is the only part that is genuinely per-platform.
 *
 * Keyed by `href` rather than an invented `iconKey`, because `href` is already the item's identity
 * and already unique. One vocabulary, not two.
 */
export const NAV_ICONS: Record<string, LucideIcon> = {
  "/": LayoutDashboard,
  "/recommendations": Sparkles,
  "/coach": Bot,
  "/discover": Compass,
  "/stories": Newspaper,
  "/analyze": ScanSearch,
  "/saved": Bookmark,
  "/report": Activity,
  "/analytics": BarChart3,
  "/history": History,
  "/profile": User,
  "/settings": Settings,
};
