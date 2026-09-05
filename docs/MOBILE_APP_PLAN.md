# The Hidden View mobile app — inspection, mapping and plan

The React Native (Expo) app for Android and iOS, built from ONE codebase, reproducing the existing
**mobile web app** (the web app below `lg`, 1024px) screen for screen. This document is the
inspection that preceded the code, the component map it produced, and the plan the build follows.

The mobile web app is the source of truth. Nothing here redesigns it; where a web component could
not be carried across as-is, the reason is stated below.

## 1. What the mobile web app is made of

Every mobile-web screen renders inside `web/app/(app)/layout.tsx`:

| Chrome (every screen) | Web component | Notes |
|---|---|---|
| Sticky masthead: menu · wordmark · bell · theme · avatar | `layout/header.tsx` (below `lg`) | no search control below `sm` — Search is a tab |
| Topic chip strip (12 catalog topics, follow toggle in each chip) | `shared/topic-strip.tsx` via `chrome-slots.tsx` | chips link to `/stories?topic=` |
| Utility strip: today's date · "Browser extension" | `home/utility-bar.tsx` | |
| Footer: wordmark, tagline, three link columns, © line | `home/site-footer.tsx` | |
| Bottom tab bar: Home · For You · Search · Blind spots · Local | `layout/mobile-tab-bar.tsx` | 56px + home indicator |
| Full-screen directory menu | `layout/mobile-menu.tsx` + `menu-panel.tsx` | rows listed in §3 |
| Notifications panel (bell) | `layout/notifications-menu.tsx` | active first, "show earlier" |
| Account menu (avatar) | inside `header.tsx` | Report · Saved · History · Profile · Settings · Sign out |

Screens, in the order the tab bar and the menu reach them:

| Route | Web page | Composition |
|---|---|---|
| `/` | `home/home-mobile.tsx` | Briefing → lens tabs (Top / Latest / Blind spots) → LeadStory → StoryRows (lg, thumb, action) → More stories → Blind spots (2 SpotCards + button) → Local Pulse → 2 topic sections |
| `/stories/[id]` | `stories/[id]/page.tsx` (mobile branch) | back link + share → hero (image, or CoveragePlate masthead) → six collapsible `StorySection`s: Story Intelligence · Breakdown (Bias/Factuality/Ownership tabs) · How each side frames it · Coverage across publishers · Related Topics · Similar Stories |
| `/stories` (+ query) | `stories/story-browser.tsx` | filters (topic · publisher · covered-by · type · country · gaps · sort), tag chip, country facts, StoryCard grid, pager |
| `/search` | `search/page.tsx` | input → filters → DiscoverCards → pager |
| `/publishers/[name]` | `publishers/[name]/page.tsx` | header (mark, lean, factuality, country, scope, snapshot) → Ownership → About → cards (topics, geography, gaps, co-coverage, tone, recent) |
| `/recommendations` | `recommendations/page.tsx` | title → strategy tabs → consequence strips → cards (feedback vocabulary) with the country-backfill divider |
| `/settings` | `settings/page.tsx` | Appearance · Recommendations · For You country · Feedback effects · Interests · Places · Reports · Notifications · Privacy, floating save bar |
| `/alerts` | `alerts/page.tsx` | notification list, "Manage" |
| `/saved` | `saved/page.tsx` | DiscoverCards over the saved list |
| `/sign-in` | NextAuth `/signin` | Google |

## 2. Component map — mobile web → React Native

One RN file per web file, same name, same props where the platform allows. `web/...` on the left,
`mobile/components/...` on the right.

| Web | RN | Platform difference |
|---|---|---|
| `ui/button.tsx` | `ui/button.tsx` | `Pressable`; variants default · outline · ghost · secondary · destructive |
| `ui/badge.tsx` | `ui/badge.tsx` | same variants incl. lean pills |
| `ui/tabs.tsx` | `ui/tabs.tsx` | segmented control, horizontally scrollable |
| `ui/skeleton.tsx` | `ui/skeleton.tsx` | opacity pulse |
| `ui/filter-chip.tsx` | `ui/filter-chip.tsx` | |
| `ui/sheet.tsx` + Radix dropdowns | `ui/bottom-sheet.tsx` | one native `Modal` bottom sheet stands in for Sheet, DropdownMenu and the searchable popovers |
| `ui/slider.tsx` (Radix) | `ui/slider.tsx` | `PanResponder` track — no extra dependency |
| `ui/switch.tsx` | `ui/switch.tsx` | RN `Switch` |
| `shared/info-tooltip.tsx` | `shared/info-tooltip.tsx` | a tap opens the text in a sheet (no hover) |
| `shared/card-image.tsx` + `story-fallback-art.tsx` | same names | fallback art is the same SVG through `react-native-svg` |
| `shared/bias-strip.tsx`, `spectrum-bar.tsx` | same | Views; the entrance animation is dropped (framer-motion is web-only) |
| `shared/lead-story.tsx`, `story-row.tsx`, `spot-card.tsx` | same | |
| `shared/publisher-logo.tsx`, `outlet-avatar.tsx` | same | `Image` `onError`/`onLoad` drives the same candidate walk (`@ih/core/logic/publisher-logo`) |
| `shared/article-badges.tsx`, `factuality-badge.tsx` | same | |
| `shared/country-badge.tsx` | same | flag SVGs served by the deployment (`/flags/*.svg`) via `SvgUri` |
| `shared/states.tsx`, `section-header.tsx`, `section-card.tsx`, `bar-list.tsx`, `topic-list.tsx`, `article-row.tsx` | same | |
| `shared/read-article-button.tsx` | same | opens the publisher in the in-app browser (`expo-web-browser`) after recording the read |
| `shared/save-button.tsx`, `follow-button.tsx` | same | same mutations |
| `shared/filter-select.tsx`, `country-picker.tsx` | same | bottom sheet with the same search-first list |
| `discover/discover-card.tsx`, `stories/story-card.tsx` | same | |
| `stories/story-section.tsx` | same | `LayoutAnimation` reveal instead of framer-motion |
| `stories/story-intelligence-panel.tsx` | same | |
| `stories/breakdown/*`, `bias-distribution.tsx`, `shared/category-distribution.tsx` | same | the radial is `react-native-svg`; hover → tap |
| `stories/framing-comparison.tsx`, `coverage-list.tsx`, `coverage-plate.tsx`, `story-topics.tsx`, `similar-stories.tsx`, `freshness-badge.tsx` | same | |
| `home/home-mobile.tsx`, `local-pulse.tsx`, `home-skeleton.tsx`, `daily-briefing` (inline) | same | |
| `recommendations/recommendation-card.tsx` | same | the vocabulary menu is a bottom sheet |
| `layout/header.tsx`, `logo.tsx`, `menu-panel.tsx`, `mobile-tab-bar.tsx`, `notifications-menu.tsx`, `topic-strip.tsx`, `utility-bar.tsx`, `site-footer.tsx` | `layout/*` | |

## 3. What is reused unchanged

- **API layer**: `@ih/core/api/services` + `queryKeys` over the shared axios client, configured
  once with the deployment's base URL and the keystore token (`mobile/lib/api.ts`). No endpoint is
  reimplemented; `mobile/lib/hooks.ts` is the React Query layer, mirroring `web/hooks/use-data.ts`
  hook for hook (same keys, same invalidations, same optimistic saves).
- **Business logic**: `@ih/core/logic/*` — home derivations, framing, story timeline, coverage
  groups, Tier-B split, bias/factuality/ownership distributions, interests, country partition,
  settings diff, notification kinds, publisher-logo candidate walk, placeholder art, metrics.
- **i18n**: the same five catalogs and `makeT`; the active language comes from Settings
  (as on the web) and falls back to the device locale before settings load.
- **Design tokens**: the full `globals.css` palette (light + dark, lean, positive/caution/negative,
  ownership and factuality ramps) transcribed to hex and checked against the stylesheet by
  `design/tokens.test.ts`; the same two typefaces (Schibsted Grotesk for headlines, Instrument
  Sans for text) as static TTFs.
- **Auth**: Google ID token → `POST /api/auth/mobile` → Hidden View bearer token in the platform
  keystore (`expo-secure-store`), attached to every request by the shared client. Android and iOS
  each use their own Google OAuth client; nothing confidential ships in the app.

## 4. What cannot be ported as-is, and what was done instead

| Web behaviour | Why not | On the phone |
|---|---|---|
| Story Continuation strip (`continuation-strip.tsx`, `lib/continuation.ts`) | built on `sessionStorage`, `visibilitychange` and tab-return dwell gates — browser return-visit mechanics | not rendered in this pass; the read still records and the feed still refreshes |
| Browser-extension connect + API tokens in Settings | `/api/me/tokens` is `SESSION_ONLY` by design (a bearer token must not mint tokens) | card omitted; manage tokens on the web |
| Per-device push toggle in Settings | Web Push (VAPID `PushSubscription`) — native push needs an APNs/FCM registration endpoint that does not exist yet | omitted; the account-level "breaking on your devices" preference stays |
| Profile name/avatar in the header | `/api/profile` is session-only | the account menu shows the signed-in email (returned by the exchange) |
| "New since your last visit" in Story Intelligence | `/api/stories/[id]/intelligence` is session-only, so a bearer caller is anonymous there | the panel renders; that block is empty |
| Report / Guide / Analytics / Analyze / Profile / History / Discover more / Privacy | outside this build's scope (not mobile-web *story* surfaces) | menu rows keep their place and open the web page in the in-app browser |
| Search overlay (`search-command.tsx`) | unreachable on the mobile web below `sm` — Search is a tab | the Search tab is the `/search` page |
| Framer-motion entrances, hover reveals, `⌘K` | no pointer / keyboard | tap replaces hover; `LayoutAnimation` for the collapsibles |

## 5. Build order

1. Foundations: tokens, fonts, `Txt`/`Icon`, theme, auth, i18n, hooks, transports.
2. Shell: root stack, tab group, header, menu, sheets, chrome, safe areas.
3. Shared cards + Home, then the Story page and its six sections.
4. Stories browser, Search, Publisher, For You, Settings, Alerts, Saved.
5. Tests (`npm test --workspace @ih/mobile`), `tsc`, `expo export` for android and ios, docs.

Running it: see `mobile/README.md`.
