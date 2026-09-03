# Hidden View — Web Design System (Template-4)

The design language established by the Home Page and Story Details, written down so every future
page (Publishers, Topics, Countries, Blindspots, Insights, …) inherits it by composition instead of
convention. The **token source of truth is `web/app/globals.css`**; the **layout source of truth is
the shell** (`web/app/(app)/layout.tsx`). This document is the map, not a second copy of the values.

## 1 · Shell (every `(app)` page inherits automatically)

```
Sidebar (fixed rail lg+, Sheet drawer below)      components/layout/sidebar.tsx + nav-links.tsx
Header  (sticky: nav trigger · page label ·       components/layout/header.tsx
         search ⌘K · notifications · theme · user menu)
UtilityBarSlot (date · extension)                 components/layout/chrome-slots.tsx
main            (the page renders ONLY its content)
FooterSlot      (site footer)                     components/layout/chrome-slots.tsx
```

- Pages never render their own utility bar or footer.
- **Immersive routes** (pinned-composer layouts like `/coach`) opt out of both slots via
  `IMMERSIVE_ROUTES` in `chrome-slots.tsx`.
- The shell persists across navigation (App Router layout), so chrome never remounts.

## 2 · Layouts

| Need | Use |
|---|---|
| Page padding + max width + enter animation | `PageContainer` (`components/layout/page-container.tsx`) |
| Page heading block | `PageHeader` (same file) |
| Editorial 8/4 two-column | `PageGrid` with a `rail` (`components/layout/page-grid.tsx`) |
| Full width | `PageContainer` alone |
| Section heading (title · eyebrow · View-all) | `SectionHeader` (`components/shared/section-header.tsx`) |

## 3 · Tokens (defined in `globals.css`, consumed via Tailwind)

- **Surfaces are neutral charcoal** (hue 220, saturation 5–8%); never blue-tinted.
- **Purple (`--primary`) is interactive state and identity marks only** — focus rings, links,
  active chips, the cross-cutting badge. Never surfaces, meters, kickers (kickers are
  `text-muted-foreground`, uppercase, tracked) or topic labels (a topic chip is `bg-accent`,
  the neutral elevated surface, on every card).
- **The political spectrum is a diverging scale**: `--left` blue / `--center` neutral grey /
  `--right` red. The centre is never a hue.
- Status colors (`--positive/--caution/--negative`, freshness badge palette) are semantic and never
  reused as accents.
- Radius: `--radius: 0.6rem` (editorial, not app-round). Shadows: `shadow-soft` (resting),
  `shadow-card` (hover/feature).

## 3a · Type system (desktop editorial audit, `docs/DESKTOP_EDITORIAL_AUDIT.md`)

Two faces, one job each, self-hosted via Fontsource (imported in `app/layout.tsx`; the variables
live in `globals.css`):

| Token | Face | Used for |
|---|---|---|
| `--font-display` | Schibsted Grotesk (variable, 400–900) | every `h1`–`h3` by default (base rule in `globals.css`), plus `font-display` on non-heading headline moments: the framing quotes, the coverage plate's big count |
| `--font-sans` | Instrument Sans (variable, 400–700) | running text, labels, kickers, controls, numbers (real tabular figures — keep `tabular-nums` on every stats column) |

- **Headline scale.** Page lead and story headline: `text-[1.75rem] sm:text-[2.125rem] font-bold
  leading-[1.12] tracking-tight` (`HeroStory`, the story page `h1`). Section titles `text-lg`,
  card titles `text-base`/`text-sm`, all `font-semibold tracking-tight`. Do not add a third
  headline size between the lead and the section title — the gap is the hierarchy.
- **Kickers stay in the text face.** A tracked-uppercase label that happens to be an `h2`/`h3`
  (story-intelligence panel, footer columns, the attached-coverage group) opts out with
  `font-sans`, so kickers read as one voice whether they are `<p>` or headings.
- **Headlines are ink.** Never the accent colour at rest; `group-hover:text-primary` is the only
  colour a headline takes, so it reads as content first and a link second.
- **Reading order beats statistics.** A story block goes kicker → headline → standfirst →
  dateline → coverage; the coverage plate closes the block, it never opens it.
- **Subsets.** Latin and Latin-extended are shipped (all five UI languages); scripts neither
  face covers (Hangul, CJK, Vietnamese tone marks) fall through to the system stack.

## 4 · Spacing & elevation rule

- Rail modules: `p-4`, **border-only** cards.
- Lead features (briefing, hero): `p-5`, **elevated** (`shadow-soft`).
- Vertical rhythm between sections: `space-y-8` (carried by `PageGrid`).
- List rows: `py-3` compact / `py-4` summary, hover tint bleeding via `-mx-2 px-2`.

Elevation encodes hierarchy; it is never decoration.

## 5 · Story hierarchy (one signal vocabulary: topic · freshness · spectrum · blind-spot)

1. `HeroStory` — full-bleed lead (Home) / hero article block (Story page)
2. `StoryFeatureCard` — image-forward second tier
3. `StoryListItem variant="summary"` — synopsis + labelled L/C/R split
4. `StoryListItem variant="compact"` — dateline · headline · bar · count

`showTopic={false}` inside any single-topic section (the header already names it).

## 6 · Interactive primitives

`Button`, `Badge`, `FilterChip` (the one filter pill — `components/ui/filter-chip.tsx`),
`Switch`, `Slider`, `Tabs`, `DropdownMenu`, `Sheet`, `Tooltip`, `Skeleton`,
`EmptyState`/`ErrorState`, `ShareButton`, `SpectrumBar`, `ScoreRing`, `TrendChart`.
Micro-interaction budget: one slow `motion-safe` image zoom on hero/feature cards; rows use hover
tint only; `prefers-reduced-motion` respected globally (incl. smooth scrolling).

## 7 · Copy & i18n

All user-visible text goes through the catalog (5 languages) and is gated by `check:i18n`
(key parity · placeholder parity · **no unused keys**) in the build. Counted facts over generated
prose; nothing renders that the data can't back — no dead links, no inert controls.

## 8 · Settings framework

`/settings` is the pattern: `SectionCard` groups + diff-based saves (`lib/settings-diff.ts`)
against the engine's settings contract. New settings = a new `SectionCard` + a field in the
contract; no new framework needed.
