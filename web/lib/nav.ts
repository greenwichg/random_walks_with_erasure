import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  Activity,
  Sparkles,
  Bot,
  History,
  Compass,
  Newspaper,
  BarChart3,
  Bookmark,
  ScanSearch,
  Settings,
  User,
} from "lucide-react";

export interface NavItem {
  /** English label — a fallback; the UI renders `labelKey` through the translation catalog. */
  label: string;
  /** i18n catalog key for the label (Commit 20). */
  labelKey: string;
  href: string;
  icon: LucideIcon;
  /** Optional short description for tooltips / command palette (English fallback). */
  hint?: string;
  /** i18n catalog key for the hint. */
  hintKey?: string;
}

export interface NavSection {
  title?: string;
  /** i18n catalog key for the section title. */
  titleKey?: string;
  items: NavItem[];
}

/**
 * Primary navigation. Grouped so new sections (e.g. an Enterprise or Publisher
 * dashboard) can be added later without touching the sidebar component. Labels carry both an
 * English fallback and an i18n key; render sites translate via `useTranslation().t(labelKey)`.
 */
export const NAV: NavSection[] = [
  // Read: the daily surfaces.
  {
    items: [
      { label: "Home", labelKey: "nav.dashboard", href: "/", icon: LayoutDashboard, hint: "Today's coverage", hintKey: "nav.hint.dashboard" },
      { label: "Recommendations", labelKey: "nav.recommendations", href: "/recommendations", icon: Sparkles, hint: "Reads picked to balance your diet", hintKey: "nav.hint.recommendations" },
      // User-facing name is "Guide"; the route/service/query-key stay `coach` by design.
      { label: "Guide", labelKey: "nav.coach", href: "/coach", icon: Bot, hint: "Ask about your reading", hintKey: "nav.hint.coach" },
    ],
  },
  // Explore: finding and keeping coverage. Every href is a real route — aspirational pages
  // (Publishers, Topics, Blindspots) join here when they exist, not before. Consolidation
  // history: "Local" folded into Countries, then Countries folded into Stories as its country
  // filter (/local and /countries redirect there; "Local" stays reserved for the future
  // personalized experience — docs/LOCATION_PLATFORM.md). "Search" is deliberately NOT a nav
  // destination: search is a global action in the header (visible button + ⌘K on every page),
  // and /search remains as that action's full-results landing page.
  {
    title: "Explore",
    titleKey: "nav.section.explore",
    items: [
      { label: "Discover", labelKey: "nav.discover", href: "/discover", icon: Compass, hint: "Trending stories & clusters", hintKey: "nav.hint.discover" },
      { label: "Stories", labelKey: "nav.stories", href: "/stories", icon: Newspaper, hint: "One event, every viewpoint", hintKey: "nav.hint.stories" },
      { label: "Analyze an article", labelKey: "home.footer.analyze", href: "/analyze", icon: ScanSearch },
      { label: "Saved", labelKey: "nav.saved", href: "/saved", icon: Bookmark, hint: "Articles you saved to read later", hintKey: "nav.hint.saved" },
    ],
  },
  // Insights: the reader's own record and measurements.
  {
    title: "Insights",
    titleKey: "nav.section.insights",
    items: [
      { label: "Health Report", labelKey: "nav.report", href: "/report", icon: Activity, hint: "The full reading-diet analysis", hintKey: "nav.hint.report" },
      { label: "Analytics", labelKey: "nav.analytics", href: "/analytics", icon: BarChart3, hint: "Trends over time", hintKey: "nav.hint.analytics" },
      { label: "Reading History", labelKey: "nav.history", href: "/history", icon: History, hint: "Everything you've read", hintKey: "nav.hint.history" },
    ],
  },
  {
    title: "Account",
    titleKey: "nav.section.account",
    items: [
      { label: "Profile", labelKey: "nav.profile", href: "/profile", icon: User },
      { label: "Settings", labelKey: "nav.settings", href: "/settings", icon: Settings },
    ],
  },
];

/** Flat list for the command palette / breadcrumbs. */
export const NAV_FLAT: NavItem[] = NAV.flatMap((s) => s.items);
