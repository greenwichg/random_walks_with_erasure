# MB1 — Mobile Browser Compatibility

**Scope:** UI/UX only. No change to the recommendation engine, backend APIs, report
calculations, ranking, lifecycle, evaluation, observability, authentication, or the database
schema. Every change in this workstream is presentational — CSS, layout classes, viewport
config, and touch-target sizing — and preserves existing functionality.

**Objective:** make the whole application feel production-quality on modern mobile browsers —
treated as a responsive redesign of the presentation layer, not a scatter of one-off CSS patches.

---

## Phase 1 — Mobile UX Audit (read-only)

### Method

The app was exercised at mobile widths in a real headless Chromium (device-emulated: `isMobile`,
`hasTouch`, DSR 2) against the live UI (mock-data fallback, so every surface is fully populated —
dashboard, report, recommendations, history, analytics, settings, saved, search, coach, profile,
plus the public sign-in / onboarding funnel). Every surface was screenshotted at **360px** and
**390px**, and each was probed for horizontal overflow (`documentElement.scrollWidth` vs
`clientWidth`). The remaining widths in the brief (320 / 375 / 414 / 768) were reasoned about from
the same layouts. Code was read for every user-facing page and shared component; **no code was
changed during Phase 1.**

### The headline finding: charts drag the whole page wide (measured)

The single most damaging mobile defect is a **hard horizontal-scroll violation on every
chart-bearing page.** Measured document width at a 360px viewport (0 = no horizontal scroll):

| Surface | scrollWidth @360 | Overflow | Cause |
|---|---:|---:|---|
| **Dashboard** | 634px | **+274px** | Health-trend `TrendChart` |
| **Analytics** | 666px | **+306px** | `TrendChart` + `StackedBar` + `MultiLineChart` |
| **Profile** | 666px | **+306px** | trend + distribution charts |
| **Report** | 386px | **+26px** | `MetricRadar` sizing |
| **Recommendations** | 409px | **+49px** | card inner content (no global overflow net) |
| History, Settings, Saved, Search, Coach, Sign-in, Onboarding | 360px | 0 | clean |

**Root cause (charts).** `hooks/use-measure.ts` seeds the chart width at a fixed **600px** so
Recharts paints on the first frame (Recharts' own `ResponsiveContainer` collapses to 0 inside
flex/grid parents and in headless capture — a deliberate, correct earlier decision). The measured
SVG is then rendered at `width={600}` until a `ResizeObserver` corrects it. The problem is purely
CSS containment: the chart wrapper (`<div className="w-full">`) and its grid-item ancestors have
the CSS default `min-width: auto`, so the 600px SVG establishes a **min-content floor** the parent
grid column cannot shrink below. The ResizeObserver then measures that inflated width and the chart
*stays* at ~600px — a self-reinforcing overflow. Result: on a 360px phone the entire page is
~634–666px wide, content is crammed into the left ~57%, and the reader can scroll into dead space.
This is the exact opposite of the Phase 2 requirement ("zero horizontal scrolling") and it is
**Critical** because it affects the three most-visited authenticated pages.

**Root cause (report radar & recommendations).** `MetricRadar` computes `dim = min(width, size+40)`
and centers an SVG of that size; at 360px the `+40` pushes it just past the content box (+26px).
The recommendations page has no chart but still overflows +49/+19px from card inner content, which
nothing catches because there is **no global horizontal-overflow safety net** on the shell.

### Classified issue register

Severity = mobile impact. **Critical** breaks the experience; **High** is a clear
production-quality gap the brief names explicitly; **Medium** is a noticeable rough edge; **Low** is
polish.

#### Critical

- **C1 — Charts force full-page horizontal scroll** (dashboard, analytics, profile, report).
  Measured above. Files: `hooks/use-measure.ts`, `components/shared/{trend-chart,stacked-bar,multi-line-chart,metric-radar}.tsx`,
  and the chart-bearing grid cells. *Phase 2 + Phase 4.*

#### High

- **H1 — No global zero-horizontal-scroll guarantee.** Nothing on `html`/`body`/shell prevents a
  stray wide element from scrolling the page sideways (recommendations +49px is the live example).
  The app needs a belt-and-suspenders `overflow-x` containment on the shell that still preserves the
  sticky header (`overflow-x: clip`, not `hidden`). File: `app/globals.css`. *Phase 2.*
- **H2 — No safe-area-inset support; viewport not `viewport-fit=cover`.** On notched / home-indicator
  devices the sticky header (`h-16 top-0`), the mobile nav drawer (logo at `top-0`), and the settings
  floating save bar (`bottom-4`) render *under* the status bar / gesture area. `env(safe-area-inset-*)`
  is used nowhere and the viewport meta doesn't opt into the inset model. Files: `app/layout.tsx`
  (viewport), `app/globals.css`, `components/layout/header.tsx`, `components/ui/sheet.tsx`,
  `app/(app)/settings/page.tsx`. *Phase 3.*
- **H3 — Touch targets below the 44px comfortable minimum.** Header icon buttons are `h-9 w-9`
  (36px); recommendation-card action buttons are `h-8 w-8` (32px) and the dismiss is `h-7 w-7`
  (28px); settings theme/language pills and the history view-toggle are ~30–32px tall. These pass
  the WCAG 2.5.8 AA floor (24px) but miss the iOS HIG / Material 44–48px comfortable target the brief
  asks for. Files: `components/ui/button.tsx`, `components/recommendations/recommendation-card.tsx`,
  `app/(app)/settings/page.tsx`, `app/(app)/history/page.tsx`. *Phase 3 + Phase 6.*

#### Medium

- **M1 — Mobile drawer & search sheet don't scroll and ignore the top inset.** `SheetContent` has no
  `overflow-y-auto`; in landscape or on a short screen the nav links can be clipped with no way to
  reach them, and the drawer's logo sits at `top-0` under the notch. Files: `components/ui/sheet.tsx`,
  `components/layout/{header,search-command}.tsx`. *Phase 3.*
- **M2 — First-paint chart flash.** Even after the containment fix, the 600px seed means the very
  first frame can briefly overflow before the observer corrects. Lowering the seed and clipping the
  measured wrapper removes the flash. File: `hooks/use-measure.ts` + chart wrappers. *Phase 4.*
- **M3 — Tap highlight / double-tap zoom.** No global `-webkit-tap-highlight-color` reset or
  `touch-action` tuning, so Android shows a grey flash on every tap and interactive controls are
  double-tap-zoomable. File: `app/globals.css`. *Phase 3.*
- **M4 — Overscroll chaining.** No `overscroll-behavior` on the shell, so a scroll fling at the top
  of a page pulls the whole browser (rubber-band / pull-to-refresh) rather than settling. *Phase 3.*

#### Low

- **L1 — Small secondary text.** A few `text-[10px]` labels (why-drawer section headers, receipt
  captions) are legible but tight on a phone. Acceptable; noted.
- **L2 — Calendar heatmap density.** `CalendarView` (`grid-flow-col grid-rows-7`) is a GitHub-style
  heatmap; it shrinks to fit but the cells get small at 320px. Acceptable.
- **L3 — Scrollbar rule width.** `*::-webkit-scrollbar{width:10px}` is desktop-oriented; harmless on
  mobile (overlay scrollbars) but worth leaving mobile-native.

### What is already correct (and must be preserved)

The app is, structurally, already well built for responsive — the fixes below are targeted, not a
rewrite:

- **Grids stack correctly.** Every multi-column grid is base-single-column with `sm:`/`md:`/`lg:`/
  `xl:` opt-ins (`grid gap-6 lg:grid-cols-3`, `grid gap-5 md:grid-cols-2 xl:grid-cols-3`). No fixed
  multi-column grid on mobile.
- **Shell is mobile-aware.** Sidebar is `lg+` only; below `lg` navigation is a `Sheet` drawer;
  `lg:pl-64` reserves the rail only on desktop; the header collapses search to an icon (`sm:hidden`)
  and hides the page-title label (`hidden sm:block`).
- **No HTML tables.** Tabular data is div/grid based, so there is no un-wrapped `<table>` overflow.
- **Page rhythm is responsive.** `PageContainer` uses `px-4 py-6 sm:px-6 lg:px-8`; headers are
  `flex-col … sm:flex-row`; the dashboard hero is `flex-col … sm:flex-row`.
- **Cards already guard truncation.** The recommendation card uses `min-w-0`, `truncate`,
  `flex-wrap`, and an always-visible (non-hover) dismiss on touch.

---

## Phases 2–6 — the fixes

Everything below is presentational and additive. No business logic, API, report math, ranking,
lifecycle, evaluation, observability, auth, or DB schema was touched.

### Phase 2 — Responsive layout & zero horizontal scrolling

1. **Global overflow safety net** (`app/globals.css`) — `overflow-x: clip` on `html` **and** `body`.
   `clip` (not `hidden`) contains stray overflow *without* becoming a scroll container, so the
   sticky header and every fixed/portaled overlay (dialogs, dropdowns, tooltips, sheets) keep
   working, and vertical scrolling is untouched.
2. **The chart root-cause fix** (Critical C1) — the four measured charts
   (`trend-chart`, `stacked-bar`, `multi-line-chart`, `metric-radar`) now wrap their SVG in
   `min-w-0 overflow-hidden`, and `use-measure.ts` seeds width at **320px** (mobile-first) instead
   of 600px. `min-w-0` lets the wrapper shrink below the seeded SVG inside a grid/flex parent
   (defeating the CSS `min-width:auto` floor), and `overflow-hidden` clips the pre-measure frame so
   a chart can never inflate its ancestors. `MetricRadar` now caps at `min(width, size)` (was
   `size + 40`, which pushed the radar past a narrow card).
3. **Explicit single-column grids** — every responsive grid that was implicitly single-column on
   mobile (`grid gap-… lg:grid-cols-N`) now carries an explicit `grid-cols-1` base. The implicit
   single column is an `auto` track, which sizes to the widest card's **max-content** and can
   overflow the viewport (this is what made a card 370px wide at 360, and 338px at 320, dragging the
   page sideways). `grid-cols-1` = `minmax(0,1fr)` — container-constrained and shrinkable. Applied
   to dashboard, report, analytics, profile, settings, saved, search, discover, recommendations,
   the two skeletons, `loading.tsx`, and the history/story component grids
   (`insight-strip`, `reflection-insights`, `daily-summary`, `reading-pattern`, `story-browser`).
4. **Scrollable filter strip** — the recommendations strategy `TabsList` (4 tabs + icons, wider than
   a phone and longer still in some locales) now scrolls horizontally (`max-w-full justify-start
   overflow-x-auto`) with non-shrinking triggers, instead of overflowing the page.

### Phase 3 — Mobile interactions

5. **Safe-area insets** — `viewport-fit=cover` (`app/layout.tsx`) plus `.safe-top` on the sticky
   header (now `min-h-[4rem]`, so it grows under a notch) and the nav drawer; the `PageContainer`
   pads its sides and bottom with `max(<base>, env(safe-area-inset-*))`; the settings floating save
   bar sits at `bottom-[max(1rem,env(safe-area-inset-bottom))]`. On a rectangular screen every inset
   resolves to 0, so these are no-ops there.
6. **Scrollable drawers** — `SheetContent` gained `overflow-y-auto` so a tall drawer (nav links in
   landscape / on a short screen) scrolls instead of clipping.
7. **Touch behavior** — `-webkit-tap-highlight-color: transparent` and `touch-action: manipulation`
   on interactive elements (no grey tap-flash, no 300ms double-tap-zoom delay).

### Phase 4 — Charts

Covered by the containment fix in Phase 2 (charts now fill their card and reflow correctly at every
width). Additionally, `MultiLineChart` (the Emotional-tone card) **thins its x-axis to ~6 labels on
narrow widths** so daily points no longer collide into an unreadable smear — the same-day
de-duplication is preserved. Tooltips/touch interaction are unchanged.

### Phase 5 — Recommendation cards

The card already handled evidence, impact, lifecycle, and actions well on mobile (`min-w-0`,
`truncate`, `flex-wrap`, always-visible dismiss on touch). MB1 adds `min-w-0` to the card root (it
was letting the recommendations grid column overflow ~49px) and brings the five feedback controls +
dismiss to a comfortable ≥44px tap target on touch. No information was moved or hidden.

### Phase 6 — Accessibility (touch targets)

A `.touch-target` utility (a `pointer: coarse` media query enforcing `min-height/width: 2.75rem`)
gives every compact control a ≥44px comfortable tap area **on touch devices only**, leaving the
precise-pointer desktop density unchanged. Applied at the source (`Button`'s base variant covers
every button incl. the header icon buttons, theme toggle, notifications) and to the raw pill/toggle
controls (recommendation actions + dismiss, settings theme/language pills, history view toggle,
avatar menu). Existing focus-visible rings, ARIA labels, the single-`<h1>`-per-page landmark, and
the colour-blind-safe palette are all retained.

### Phase 7 — Performance

No new dependencies; changes are CSS classes plus two tiny presentational calcs (chart tick interval,
radar dim). The production build is unchanged in weight: **shared First-Load JS 87.5 kB**, **/report
376 kB** — identical to the pre-MB1 (OBS1) baseline.

## Before / after — measured

Zero-horizontal-scroll was verified in a device-emulated headless Chromium (`isMobile`, `hasTouch`)
by probing `documentElement.scrollWidth` vs `clientWidth` on every surface, and by walking the DOM
for any element whose right edge exceeds the viewport. Reproducible via `web/scripts/mobile-shots.mjs`
(screenshots + overflow report) and `web/scripts/mb1-overflow-diag.mjs` (per-element offenders).

| Surface | Before (scrollWidth @360) | After @360 | After @320 |
|---|---|---|---|
| Dashboard | **634px (+274)** | 360 ✓ | clean ✓ |
| Analytics | **666px (+306)** | 360 ✓ | clean ✓ |
| Profile | **666px (+306)** | 360 ✓ | clean ✓ |
| Report | **386px (+26)** | 360 ✓ | clean ✓ |
| Recommendations | **409px (+49)** | 360 ✓ | clean ✓ (tab strip scrolls in-place) |
| History, Settings, Saved, Search, Coach, Sign-in, Onboarding | 360 (already clean) | 360 ✓ | clean ✓ |

Every one of the 12 audited surfaces now reports `scrollWidth == clientWidth` at **320, 360, and
390px**. The only element that still extends past the viewport is a recommendations filter tab —
by design, inside its own horizontally-scrollable strip, so the page itself never scrolls sideways.

## Validation summary

| Check | Result |
|---|---|
| `tsc --noEmit` | **clean** |
| `node --test` (unit) | **96 passed** |
| `check:i18n` | **658 keys × 5 languages** (no keys added/removed) |
| `next build` | **succeeds**; shared JS **87.5 kB**, `/report` **376 kB** — unchanged |
| Playwright **desktop** e2e (real engine + web) | **11/11 passed** (auth, report, history, saved, feedback, settings, error-handling) |
| Playwright **mobile** overflow probe (320/360/390) | **0 horizontal-scroll** on all 12 surfaces |

## Remaining limitations

- **Chart bundle weight (out of scope).** `/report` and `/analytics` remain ≈376 kB First-Load JS
  because Recharts is eagerly imported. MB1 did not *increase* this (Phase 7 constraint) but did not
  reduce it either; lazy-loading the chart bundle is a separate, larger change (CTO-review item G)
  and was deliberately not bundled into this UI-only workstream.
- **Recommendation prose is backend-English.** The evidence/impact/lifecycle sentences are localized
  only where a catalog key exists; the engine's generated prose is unchanged (localizing it is a
  backend concern, explicitly outside MB1's UI-only scope).
- **Safe-area insets are only observable on real notched hardware / standalone PWA.** They resolve to
  0 in a normal desktop browser and in headless emulation, so the header/drawer/save-bar inset
  handling is verified by construction (correct `env()` usage) rather than by screenshot.
- **Calendar heatmap (`CalendarView`)** stays a compact 7×5 grid; its cells get small at 320px. It
  fits without overflow and remains keyboard-accessible, so it was left as-is (Low-severity).
- **The `.mb1-shots/` screenshots and `web/scripts/mb1-*.mjs` harness are dev tooling** — the images
  are git-ignored; the scripts are committed so the mobile audit is reproducible.

---

*MB1 improves the mobile browser experience only — presentational, additive, behavior-preserving.
No recommendation engine, API, report calculation, ranking, lifecycle, evaluation, observability,
authentication, or database-schema change.*
