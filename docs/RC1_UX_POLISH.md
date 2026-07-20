# RC1 UX Polish

The final RC1 polish pass — resolving the remaining presentation-layer findings from the End-to-End
UX Review. **Presentation only:** no scoring, analytics, backend calculation, recommendation
algorithm, or API contract was changed. No new features.

## What changed

### 1. Sidebar streak — truthful data (was CC2)
The sidebar promo hardcoded *"12 days strong…"* for every reader. It now reads the reader's **real**
`streakDays` from the dashboard summary (shared react-query cache — no extra fetch when the dashboard
is already loaded):
- `streakDays > 0` → "{n}-day streak. Keep it going — read today."
- `0` / absent → an honest empty state: "No active streak yet. Read an article today to start one."

The old `sidebar.streakBlurb` catalog key was removed.

### 2. Accessibility — single `<h1>` per page (was CC1)
The header rendered the current page name as an `<h1>`, duplicating each page's own `<h1>` on desktop
(two landmarks per page). The header label is now a plain `<span>`; every page keeps exactly one
primary heading. (Verified: all 14 app pages render their own `<h1>` / `PageHeader`.)

### 3. Mobile / touch — no hover-only interactions (was RE1, D1, A2)
- **Recommendation dismiss ✕** — was `opacity-0 → group-hover` (invisible on touch). Now
  `opacity-100 sm:opacity-0 sm:group-hover:opacity-100`: always visible on phones, hover-reveal on
  desktop.
- **Metric & chart explanations** (`InfoTooltip`, used on every metric card and section card) — the
  Radix tooltip never opened on touch. It's now a **controlled** tooltip: hover/focus opens it on
  desktop *and* a tap toggles it on mobile. The tap `stopPropagation`s so tapping the "i" inside a
  card that is itself a link never navigates the card; tapping outside closes it.

### 4. Empty states — forward guidance (was CC4 / RE4)
The **Saved** and **Recommendations** empty states were dead ends. Both now carry a primary CTA to
Discover ("Browse articles" / "Browse Discover").

### 5. Internationalization — no hardcoded English on the Health Report (was CC3 / R2)
Localized the Health Report's data-driven captions and the metric-card labels, which had bypassed the
i18n system (so es/fr/de/pt readers saw English):
- `report.viewpointCaption` ("You read X% left, Y% center, and Z% right"), `report.tilt.*` (balanced /
  both-sides / leans-heavily), `report.diet.*` (healthy / fair / needs-work).
- `metric.typicalReader` ("Typical reader:") and `metric.yourValue` ("Your value:") — on the metric
  card and the report accordion.

All new strings are in **all five languages**; `check:i18n` passes with no unused keys.

## Files
`components/layout/sidebar.tsx`, `components/layout/header.tsx`,
`components/shared/info-tooltip.tsx`, `components/recommendations/recommendation-card.tsx`,
`app/(app)/saved/page.tsx`, `app/(app)/recommendations/page.tsx`, `app/(app)/report/page.tsx`,
`components/shared/metric-card.tsx`, `components/report/metric-accordion.tsx`, and the message
catalogs (`messages/`).

## Before / after (real stack)

| Fix | Before | After |
|---|---|---|
| **Sidebar streak** | "12 days strong. Read one cross-cutting article today…" (identical for everyone). | "1-day streak. Keep it going — read today." (real count); "No active streak yet…" when there's none. |
| **Saved / Recommendations empty** | Icon + text, no next step. | Adds a "Browse articles" / "Browse Discover" CTA. |
| **Mobile rec dismiss** | ✕ invisible on touch (hover-only). | ✕ always visible on phones. |
| **Metric/chart info** | Tooltip never opened on touch. | Tap toggles the tooltip; hover still works on desktop. |
| **Header title** | Duplicate `<h1>` on desktop. | Single `<h1>` per page (header label is a `<span>`). |
| **Report captions / metric labels** | Hardcoded English under every locale. | Localized in en/es/fr/de/pt. |

## Validation

- **Desktop**: tsc clean · `next build` succeeds · `node --test` 96 passed.
- **i18n**: `check:i18n` — 658 keys × 5 languages, no unused keys.
- **Mobile / touch**: captured a 390 px viewport — the empty-state CTA and header (no duplicate title)
  render correctly; the dismiss/tooltip fixes are CSS/behavioral and exercised by the suite below.
- **Dark mode**: unchanged token-based theming; the new CTAs/labels use existing tokens.
- **Accessibility**: one `<h1>` per page; `InfoTooltip` keeps its `aria-label` and is now
  keyboard/tap reachable; the streak empty state is truthful text.
- **Playwright regression suite**: **11/11 passed** (auth, reading history, recommendation feedback,
  health report, settings, saved, error handling) — no functional regression.
