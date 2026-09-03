
export interface NavItem {
  /** English label — a fallback; the UI renders `labelKey` through the translation catalog. */
  label: string;
  /** i18n catalog key for the label (Commit 20). */
  labelKey: string;
  href: string;
  // No `icon`. Icons are per-platform and live in web/lib/nav-icons.ts, keyed by this `href`.
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
      { label: "Home", labelKey: "nav.dashboard", href: "/", hint: "Today's coverage", hintKey: "nav.hint.dashboard" },
      { label: "Recommendations", labelKey: "nav.recommendations", href: "/recommendations", hint: "Reads picked to balance your diet", hintKey: "nav.hint.recommendations" },
      // User-facing name is "Guide"; the route/service/query-key stay `coach` by design.
      { label: "Guide", labelKey: "nav.coach", href: "/coach", hint: "Ask about your reading", hintKey: "nav.hint.coach" },
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
      { label: "Discover", labelKey: "nav.discover", href: "/discover", hint: "Trending stories & clusters", hintKey: "nav.hint.discover" },
      { label: "Stories", labelKey: "nav.stories", href: "/stories", hint: "One event, every viewpoint", hintKey: "nav.hint.stories" },
      { label: "Analyze an article", labelKey: "home.footer.analyze", href: "/analyze" },
      { label: "Saved", labelKey: "nav.saved", href: "/saved", hint: "Articles you saved to read later", hintKey: "nav.hint.saved" },
    ],
  },
  // Insights: the reader's own record and measurements.
  {
    title: "Insights",
    titleKey: "nav.section.insights",
    items: [
      { label: "Health Report", labelKey: "nav.report", href: "/report", hint: "The full reading-diet analysis", hintKey: "nav.hint.report" },
      { label: "Analytics", labelKey: "nav.analytics", href: "/analytics", hint: "Trends over time", hintKey: "nav.hint.analytics" },
      { label: "Reading History", labelKey: "nav.history", href: "/history", hint: "Everything you've read", hintKey: "nav.hint.history" },
    ],
  },
  {
    title: "Account",
    titleKey: "nav.section.account",
    items: [
      { label: "Profile", labelKey: "nav.profile", href: "/profile" },
      { label: "Settings", labelKey: "nav.settings", href: "/settings" },
    ],
  },
];

/** Flat list for the command palette / breadcrumbs. */
export const NAV_FLAT: NavItem[] = NAV.flatMap((s) => s.items);

/**
 * The DESKTOP MASTHEAD (web, lg+): six destinations inline in the top bar, in reading order —
 * the day (Home), the events (Stories), the stream (Discover), the reader's own feed, their
 * health, their guide. A horizontal bar has room for an order, not a directory, so the records
 * and tools (`NAV_DESKTOP_MENU`) live under the account menu instead. The grouped `NAV` above is
 * untouched: the mobile drawer and the command palette still render every section.
 */
const DESKTOP_PRIMARY_HREFS = ["/", "/stories", "/discover", "/recommendations", "/report", "/coach"];
const DESKTOP_MENU_HREFS = ["/saved", "/history", "/analytics", "/analyze"];

const byHref = (hrefs: string[]): NavItem[] =>
  hrefs.map((href) => {
    const item = NAV_FLAT.find((i) => i.href === href);
    if (!item) throw new Error(`desktop nav names an href that is not in NAV: ${href}`);
    return item;
  });

export const NAV_DESKTOP_PRIMARY: NavItem[] = byHref(DESKTOP_PRIMARY_HREFS);
/** Records and tools — the account menu's middle group. Profile and Settings stay the menu's
 *  own account group, as they were. */
export const NAV_DESKTOP_MENU: NavItem[] = byHref(DESKTOP_MENU_HREFS);
