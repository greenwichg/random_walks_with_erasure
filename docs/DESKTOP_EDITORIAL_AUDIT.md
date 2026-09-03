# Desktop editorial audit — what makes Ground News feel finished, and what Hidden View took from it

**Scope.** Desktop browser only. The question was: what makes Ground News's product experience read
as polished, premium and editorial, and where does Hidden View fall short of the same *principles*
— not its layout, its palette, or its features. Hidden View keeps its own identity (the fixed rail,
the indigo mark, the reader's Information Health beside the day's coverage, the coverage plate,
blind spots, story intelligence) and borrows discipline, not decoration.

**How Ground News was studied.** ground.news, its help centre and every mirror/proxy of them are
blocked from the environment this audit ran in, so the analysis rests on the published design
material for the product and on prior knowledge of it, not on a fresh browsing session:

- Ralston Studio's case study of the 2023 rebrand — https://www.ralston.studio/project/groundnews
- Ground News's own account of the rebrand — https://ground.news/ground-rebrand
- The bias-bar explainer — https://ground.news/bias-bar and https://help.ground.news/en/articles/245249
- Product overview — https://en.wikipedia.org/wiki/Ground_News

Hidden View was screenshotted at 1440×900 in both themes before and after (home, stories, a story,
discover, a publisher, search).

## 1 · What makes Ground News feel polished, premium and editorial

1. **One typographic system, set at a real headline scale.** The rebrand set the whole product in
   a single grotesk family (Universal Sans) in three weights, with a defined desktop type scale.
   Headlines are large, bold, tight, and *ink-coloured*; everything else is small and quiet. The
   contrast between the two is the hierarchy. Nothing about it is a serif or a "newspaper"
   pastiche — the editorial feel comes from discipline, not from a typeface with a history.
2. **Colour is data.** The palette is drawn from paper and ink; the only saturated colour on a
   page is the bias bar, and it means the same thing everywhere. The brand colour barely appears
   on content surfaces. A reader learns that colour on the page is always information.
3. **One card grammar repeated everywhere.** Every story, on every surface, carries the same
   strip: headline, sources count, bias bar with the three percentages, factuality. Home, feeds,
   search and the story page differ in layout but never in vocabulary, so nothing has to be
   re-learned.
4. **Reading order.** A story page opens with the headline, then the summary, then the bias
   distribution and coverage details, then the article list. Statistics support the story; they
   never precede it.
5. **Density with rhythm, not decoration.** Modules are separated by hairlines and whitespace,
   cards are flat with thin borders, and the page reads as an edition rather than a dashboard of
   widgets. The rebrand describes the philosophy as "schematics and blueprints" — utilitarian,
   drawn to be used.
6. **Plain sentences beside every chart.** "X% of the sources lean Left", "Blindspot for the
   Right" — the picture is always accompanied by the claim it makes, in words.

## 2 · Where Hidden View stood, and what changed

| Principle | Hidden View before | Change |
|---|---|---|
| One typographic system | No webfont at all. The product rendered in whatever the OS offered (Segoe on Windows, DejaVu on Linux, SF on a Mac) — three different products, none of them editorial. Headlines were the same face as the chrome, one weight step apart. | A two-face house system (`docs/WEB_DESIGN_SYSTEM.md` §3a): **Schibsted Grotesk** — a grotesk drawn for a newspaper group's headlines — on every `h1`–`h3` by default, and **Instrument Sans** for text, labels, controls and numbers. Self-hosted through Fontsource, latin + latin-ext subsets, no build-time network. |
| Headline scale | Lead and story headline at 30px semibold, a single step above card titles. | 34px bold, `leading-[1.12]`, tight tracking, on the home lead and the story `h1`; card and section titles unchanged, so the gap *is* the hierarchy. |
| Colour is data | Already the rule (`globals.css`): neutral surfaces, indigo reserved for interaction, the diverging L/C/R scale. One leak: the Stories grid painted every topic chip indigo, so 24 cards read as 24 buttons. | Topic chips are the neutral `bg-accent` chip on every card, matching the home lead. |
| Reading order | An imageless story page opened with the coverage plate — a 48px publisher count — *above* the headline. The first thing a reader met was a statistic about the story. | Kicker → headline → standfirst → dateline, then the coverage plate as a closing strip (with its labelled band and, on a gap story, the thin-side statement). The block's own spectrum bar and thin-side pill render only with an image, since the plate carries both. |
| Kickers as one voice | Kickers were `<p>`s in most places but `<h2>`/`<h3>` in a few (story intelligence, footer columns, the attached-coverage group). | The display-face default on headings would have split them; those opt out with `font-sans`, so every kicker is the same voice. |
| Card grammar | Already one vocabulary (topic · freshness · spectrum · blind-spot) across hero, feature card, list row and story card. | No change needed. |
| Plain sentences beside charts | Already there: "5 of 9 stories are covered mainly from one side", "40% of the sources are Center", "no left coverage yet", the framing comparison. | No change needed. |
| Tabular numbers | `tabular-nums` was applied throughout but had no effect with the system stack on Linux and only sometimes elsewhere. | Instrument Sans has real tabular figures; the stats columns now align. |

## 3 · What was deliberately not taken

- **Not the top-bar navigation.** Hidden View's fixed rail groups Explore / Insights / Account
  around the reader's own health, which Ground News has no equivalent of. The rail stays.
- **Not paper-tinted surfaces.** The cool charcoal neutrals are the product's own; only the
  saturation discipline transfers.
- **Not the solid "Read article" control.** Recording a read is the product's central action and
  the button is the pipeline's one entry point on cards; it stays a primary control. (Dense lists
  already use the `soft` variant.)
- **Not the Discover/Search card layout.** It was iterated to its current shape deliberately and
  is out of scope here beyond the type system it now inherits.
- **Not a serif.** The editorial feel Ground News achieves is typographic discipline in a sans;
  a serif would have been costume.

## 4 · Verification

- `tsc --noEmit`, `next lint`, `check:i18n` (993 keys × 5 languages, no unused keys) and the web
  unit suite (469 tests) pass; `next build` completes with the Fontsource imports.
- Before/after screenshots at 1440×900, light and dark, for home, stories, story, discover,
  publisher and search were compared by eye; no layout regressions, both themes hold contrast.
- Font payload: two variable woff2 files per face for latin (~47 KB + ~30 KB) fetched on first
  paint, latin-ext (~21 KB + ~11 KB) only when such characters render; all served from
  `/_next/static/media` with immutable hashes.

## 5 · Follow-ups from part 1

- Mobile: the type system applies everywhere automatically, but the mobile headline scale was
  not re-tuned; check the hero at 390px before shipping a mobile-specific pass.
- The home lead could lose its card frame and sit on the page ground with a hairline rule below —
  a stronger editorial "front page" move than a boxed hero. Left for a decision with screenshots.

---

# Part 2 · Structural rework — layout, navigation, hierarchy, cards, filters, responsive

**Scope.** Desktop only (`lg` ≥ 1024px and up). The mobile and tablet chrome is byte-for-byte
what it was: the drawer, the page label, the icon buttons, the account menu. Every route, query,
control and data field is preserved; what changed is where things sit and how they read.

**Method.** All fifteen `(app)` routes were screenshotted at 1024, 1280 and 1440 (light) before
and after — home, recommendations, guide, discover, stories, story, analyze, saved, report,
analytics, history, profile, settings, publisher, search — and compared against the desktop
patterns of Ground News (see the sourcing caveat at the top of this document).

## 6 · Gaps found, and what closed them

| Area | Before | Ground News-grade pattern | After |
|---|---|---|---|
| **Layout / shell** | Fixed 256px sidebar + sticky header. The sidebar took a fifth of a 1280px screen and a quarter of 1024px; content was ~1120px at 1440 and 704px at 1024. | Full-width masthead; one centred content column (~1280px); the page gets the whole width. | Sidebar retired on desktop. Header, utility strip, page and footer share one `max-w-7xl` column with the same gutters. Content is 1280px at 1440 and 960px at 1024. |
| **Navigation** | 12-item grouped rail (a directory), the current page named three times (rail row + header label + h1), a "Reading streak" card in the rail duplicating the Home rail's stat. | Six-or-so section links in the top bar, current section underlined; overflow under a menu; account under the avatar. | `DesktopNav`: Home · Stories · Discover · Recommendations · Health Report · Guide + "More" (Saved, Reading History, Analytics, Analyze). Active section = a rule on the header border. Page label hidden at lg+; the h1 is the one title. Order lives in `@ih/core/logic/nav`. |
| **Search** | A small "Search ⌘K" pill. | A real search field in the masthead. | From xl a 320px field with the real placeholder; at lg an icon (the masthead budget); both open the same ⌘K overlay. |
| **Information hierarchy** | Story page's rail at 1024px was 230px: stat boxes wrapped, timeline rows broke mid-word. | Two-column story page with a rail wide enough for its own modules. | Rail is ~310px at 1024 and ~410px at 1440; nothing wraps. Headline-first order from part 1 kept. |
| **Spacing** | Header gutters (`px-4 lg:px-8`, no max width) did not align with page gutters (`max-w-7xl`); the install prompt used a third set. | Every horizontal edge on one grid. | One gutter definition; the install prompt sits on the content column. |
| **Cards** | Stories cards lifted on hover (`-translate-y-0.5`); topic chips indigo (part 1). | Flat cards, hover as tone/shadow, colour reserved for data. | Hover is a shadow change only; chips neutral. Coverage plate, spectrum bars, freshness badges unchanged — they are the product's own data marks. |
| **Filters** | Four pages, four hand-rolled filter rows; only two showed a result count; Discover showed none. | One filter row, count always in the same place. | `FilterBar`: pills left, count right (`N stories`, `N articles`, `N results`), one margin. Stories, Discover, Search, Reading History. |
| **Typography** | Part 1. | — | Unchanged from part 1. |
| **Interactions** | Sidebar spring-animated active pill. | Section underline; menus that never lock the page. | `ActiveRule`; "More" and the account menu are non-modal (no scroll lock, no `aria-hidden` on the document). ⌘K unchanged. |
| **Responsive** | Grids went to three columns only at xl (1280); at 1024 two columns of 330px cards. Story rail broken at 1024 (above). | Three columns from ~1000px; masthead that fits at 1024. | Every article/story grid is three columns from lg. Masthead measured at 1024: nav `px-2` (xl `px-3`), search icon-only, action cluster `shrink-0`. |

## 7 · Preserved, deliberately

- Every route is reachable from the masthead (inline, "More", or the account menu) and the
  footer; the ⌘K palette is unchanged.
- The mobile drawer still renders the full grouped `NAV`, sections and all.
- All page content, queries, filters, sorts, pagination, feedback strips, save/read pipelines,
  settings and the floating save bar (now centred without the sidebar offset) are unchanged.
- Hidden View's own identity: the indigo mark, the charcoal neutrals, the coverage plate, blind
  spots, story intelligence, the reader's health rail beside the day's coverage.

## 8 · Verification (part 2)

- `tsc --noEmit`, `next lint`, `check:i18n` (parity across 5 languages; the two retired sidebar
  keys removed, `nav.more` and `header.primaryNav` added), web unit suite, `next build`.
- Screenshots at 1024 / 1280 / 1440 after the change: no horizontal scroll on any route, masthead
  fits at 1024 in English, three-column grids from lg, story rail intact at 1024.
- One e2e spec updated for the new overflow: `read-invalidates-recommendations.spec.ts` reaches
  Reading History through the "More" menu — still a soft navigation, which is what it tests.
