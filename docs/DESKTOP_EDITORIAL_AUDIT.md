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

## 5 · Follow-ups (not done here)

- Mobile: the type system applies everywhere automatically, but the mobile headline scale was
  not re-tuned; check the hero at 390px before shipping a mobile-specific pass.
- The home lead could lose its card frame and sit on the page ground with a hairline rule below —
  a stronger editorial "front page" move than a boxed hero. Left for a decision with screenshots.
