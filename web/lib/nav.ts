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
  Settings,
  User,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Optional short description for tooltips / command palette. */
  hint?: string;
}

export interface NavSection {
  title?: string;
  items: NavItem[];
}

/**
 * Primary navigation. Grouped so new sections (e.g. an Enterprise or Publisher
 * dashboard) can be added later without touching the sidebar component.
 */
export const NAV: NavSection[] = [
  {
    items: [
      { label: "Dashboard", href: "/", icon: LayoutDashboard, hint: "Your health at a glance" },
      { label: "Health Report", href: "/report", icon: Activity, hint: "The full reading-diet analysis" },
      { label: "Recommendations", href: "/recommendations", icon: Sparkles, hint: "Reads picked to balance your diet" },
      { label: "AI Coach", href: "/coach", icon: Bot, hint: "Ask about your reading" },
    ],
  },
  {
    title: "Explore",
    items: [
      { label: "Discover", href: "/discover", icon: Compass, hint: "Trending stories & clusters" },
      { label: "Stories", href: "/stories", icon: Newspaper, hint: "One event, every viewpoint" },
      { label: "Saved", href: "/saved", icon: Bookmark, hint: "Articles you saved to read later" },
      { label: "Reading History", href: "/history", icon: History, hint: "Everything you've read" },
      { label: "Analytics", href: "/analytics", icon: BarChart3, hint: "Trends over time" },
    ],
  },
  {
    title: "Account",
    items: [
      { label: "Profile", href: "/profile", icon: User },
      { label: "Settings", href: "/settings", icon: Settings },
    ],
  },
];

/** Flat list for the command palette / breadcrumbs. */
export const NAV_FLAT: NavItem[] = NAV.flatMap((s) => s.items);
