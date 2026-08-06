# Story Continuation — technical design

**What it is:** when a reader returns to Hidden View after opening an article on a publisher's site,
the card they read from expands in place with one offer — another outlet's account of the *same
event*, from the opposite side of the rated spectrum.

**What it is not:** an expansion of the Same-Story recommendation quota. The feed slot stays at
**one card per story, unchanged**, and serves as the fallback for readers who return later or in a
different session.

**Status:** design only. Nothing implemented.
**Date:** 2026-08-03.
**Depends on:** story clustering (`story_service`), `evidence_resolver.story_index`, the outlet lean
registry, `publisher_identity`, and the shared `ReadArticleButton`.

---

## 0. Where I'd challenge the brief

The requirements are sound and I'd build to them. Seven places where I think the design should
differ from the obvious reading, in descending order of importance.

**0.1 — A Phase 0 probe must gate implementation.** The eligibility rules are strict by design:
trusted cluster, unread sibling, *both* outlets rated, genuinely opposing, non-template. Nobody
knows what share of production reads satisfy all five. If it is 2%, this is a feature with no
audience, and the answer is to improve registry lean coverage first. This project has been here
before — the L1–L3 Coverage Comparison roadmap was built and then retired because its readiness
measurement was deferred. **Measure the eligible rate against real reads before writing UI code**
(§9.0).

**0.2 — `visibilitychange` alone is the wrong trigger.** It fires when the reader alt-tabs to Slack
for four seconds. That is not a read, and a comparison prompt after it is noise. **Require a minimum
hidden duration** (~20 s) before the strip is eligible. Cheap, and it converts a tab-focus event into
a weak-but-real "they probably read something" signal.

**0.3 — Prefetch at click, don't fetch on return.** My earlier sketch had the client resolve
eligibility on return. That puts a network round-trip at the latency-sensitive moment. Resolve at
**Read-click time** instead and cache the answer, so the strip renders instantly.
And *not* by extending `/api/me/reads`: that is a **batch** endpoint (`reads: list[ReadInput]`)
returning counts, shared with the browser extension where no return moment exists. It needs its own
small read-only endpoint (§10.2).

**0.4 — The freshness window should start at 4 hours, not 90 minutes.** The memory-decay argument
justifies why a continuation beats a feed card; it does not justify a specific cutoff. A reader
returning after lunch still remembers the gist, and the cost of showing slightly stale is lower than
the cost of never showing. The window's real job is preventing a strip about something read on
Tuesday from a tab left open all week. Instrument it and let the decay curve set it.

**0.5 — Showing lean labels *is* a bias statement, and the copy has to earn it.** The brief says the
UI must not imply one outlet is biased, but "Left of centre / Right of centre" is exactly a claim
about outlets. It is defensible only as a *sourced, descriptive* fact — where each outlet sits on a
published spectrum — never as an evaluation. §1.3 sets the copy rules that keep it on the right side
of that line.

**0.6 — Cap continuation chains.** A reader who takes a continuation has now read both sides; that
was the goal. Offering a third account on return from the second turns a moment into a treadmill.
**At most one continuation per story per session.**

**0.7 — Deep-link the secondary action, pre-filtered.** "View all outlets" should land on the story
page *already filtered to the opposing side*, not on an unfiltered list the reader must re-filter.
`CoverageList` holds its lean filter in `useState`, so this needs a small URL-param addition — a
v1.1 item with real payoff.

---

## 1. UX flow

### 1.1 The path

```
  Feed / Discover / Search / Stories / Saved
        │
        │  reader clicks [ Read article ]
        ▼
  read recorded → continuation prefetched → publisher tab opens (_blank)
        │
        │  … reader reads on the publisher's site …
        │
        ▼  returns to the Hidden View tab
  visibilitychange → visible,  hidden ≥ 20 s,  candidate cached,  card still mounted
        ▼
  strip animates in beneath the source card
        │
        ├─ [ Read another perspective ] → records a read, opens the sibling
        ├─ [ View all outlets → ]       → story page, pre-filtered to the opposing side
        └─ [ × ]                        → collapse; suppressed for this story permanently
```

### 1.2 Mockup — desktop

```
┌────────────────────────────────────────────────────────────────────────┐
│  The Guardian · Left of centre                       Politics · 2h ago │
│  Hamas agrees to complete disarmament under 'historic' Gaza agreement  │
│  Officials said the framework would be signed within the week…         │
│                                                          ✓ Read        │
├────────────────────────────────────────────────────────────────────────┤
│ ⇄  Compare this story                                               ×  │
│    20 outlets covered this event. The Wall Street Journal is rated     │
│    right of centre — you read a left-of-centre account.                │
│                                                                        │
│    [ Read another perspective ]          View all 20 outlets →         │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Copy rules — how the framing stays descriptive

The brief's "must not imply one outlet is correct or biased" is the hardest requirement to satisfy
while showing lean ratings at all. Four rules:

1. **Foreground the event, not the outlets.** The heading is *"Compare this story"* — not "See the
   other side," which frames the reader's own read as a side to be corrected.
2. **State ratings as sourced placement, never as judgement.** *"The Wall Street Journal is rated
   right of centre"* — the passive "is rated" points at the registry. Never "is biased," "leans," or
   "skews."
3. **Name the reader's own article symmetrically.** *"you read a left-of-centre account"* puts both
   outlets on the same axis. Rating only the sibling would imply the anchor is neutral.
4. **No corrective verbs.** Never "balance," "correct," "counter," "fix," or "the full picture." The
   offer is a comparison, and comparison is the reader's to make.

The disclosure affordance on the lean rating links to the existing registry methodology, so the
claim is checkable rather than asserted.

### 1.4 States

| state | render |
|---|---|
| Not eligible | nothing. No placeholder, no "no comparison available." |
| Eligible, not yet returned | nothing. The strip is a response to return, not to the click. |
| Eligible, returned, card mounted | strip animates in (`prefers-reduced-motion` respected) |
| Dismissed | plain read-state card, permanently for that story |
| Sibling read in the meantime | strip disappears — derived from live read state, not a snapshot |
| Prefetch failed | nothing. Never a spinner or an error; the feed slot covers it. |

---

## 2. Trigger and lifecycle

### 2.1 Trigger conditions

All must hold:

1. `document.visibilitychange` → `visible`
2. **hidden duration ≥ `MIN_HIDDEN_MS` (20 s)** — the dwell gate from §0.2
3. a cached continuation candidate exists for a read made in this session
4. the source card is currently **mounted** (not necessarily in the viewport)
5. read age ≤ `FRESHNESS_WINDOW` (4 h)
6. story not dismissed, and shown < `MAX_IMPRESSIONS` (2) this session

### 2.2 Lifecycle

```
  idle ──[Read click]──▶ prefetching ──▶ armed ──[return, gates pass]──▶ shown
                              │                                            │
                              └──[no candidate]──▶ inert                    ├──[×]──▶ dismissed (persisted)
                                                                           ├──[open]──▶ consumed
                                                                           └──[read elsewhere in cluster]──▶ retired
```

- **No auto-dismiss timer.** The strip is inline content, covers nothing, and does not need to
  expire on its own. A timed disappearance would also punish a reader who is still reading.
- **Superseded, not stacked**: reading another member of the same cluster moves the strip to the
  newest card rather than adding a second.

---

## 3. Eligibility rules

Evaluated **server-side** at prefetch. Any failure returns `null` and the client renders nothing.

| # | gate | rule | rationale |
|---|---|---|---|
| 1 | Cluster membership | the read article resolves in `evidence_resolver.story_index` | Same P1 licensing gate the feed slot uses — one definition of "same story". |
| 2 | Cluster trust | `story.clusterTrust == "ok"` | A welded cluster offers an account of a *different* event. Measured failure mode — `docs/STORY_CLUSTER_MERGES.md`. |
| 3 | Genre | cluster does not match `coverage_comparison._TEMPLATE_PATTERNS` | "Compare coverage" on a lottery-results or box-office tracker is noise by construction. |
| 4 | Sibling exists | ≥1 cluster member that is unread, from a different publisher identity, with a usable absolute URL | `publisher_identity` collapse, so a syndicated reprint is not "another outlet". |
| 5 | Both rated | anchor **and** sibling carry a registry lean | Never infer opposition. Unrated licenses no claim (L2.2). |
| 6 | Genuinely opposing | opposite sides by the catalog's ±0.5 buckets (`_opposing_leans`) | Centre opposes nothing; −1.5 vs −0.8 is the same side. |
| 7 | Freshness | read age ≤ 4 h | §0.4. |
| 8 | Not dismissed | story not in the dismissal set | §6. |
| 9 | Chain cap | no continuation already consumed for this story this session | §0.6. |

**Deliberately excluded from v1:** same-side siblings. A pattern that sometimes means *another
perspective* and sometimes means *another article* stops being trusted. Same-side is a v2 with
different copy (§9), never a v1 fallback.

---

## 4. Ranking

Among candidates passing §3, choose deterministically:

```
sort key = ( slider_distance_preference(|lean_sib − lean_anchor|),
             publisher_novelty_rank,        # never > rarely > familiar
             −publishedAt,                  # newest first
             canonical_url )                # deterministic tiebreak
```

### 4.1 The slider's only role here

**Political Openness continues to control only the RWE-B bridge-slot budget in the feed
(`blend_plan_for`). It does not gate this feature.** A reader at slider 0 still sees continuations,
because the strip responds to their own reading act rather than injecting into their feed.

Its sole influence is *which* opposing candidate wins when several qualify:

| slider | preference | rationale |
|---|---|---|
| 0–37 | **nearest** opposing outlet | Genuine opposition, gentlest available. A reader who asked for less cross-perspective content is not force-fed the sharpest contrast. |
| 38–62 | no distance preference; novelty ranks first | The default. |
| 63–100 | **furthest** rated opposing outlet | A reader who asked for more contrast gets the sharpest available. |

Implemented as a `_piecewise`-style preference over `|lean_sib − lean_anchor|`, matching the existing
slider-mapping idiom.

**Trade-off:** at low openness the nearest opposing outlet may be only just across the ±0.5
threshold, making the comparison subtle. That is the honest reading of the reader's stated
preference, and the alternative — ignoring the slider — would be worse.

---

## 5. Desktop and mobile

**Desktop.** `_blank` keeps the app tab alive. Return is a genuine `visibilitychange` on a live SPA
instance with the card still mounted at the same scroll position. Best case; the design is built for
it.

**Mobile — the harder case, in two ways:**

1. **Tab eviction.** iOS Safari and Android Chrome routinely discard background tabs. Return is then
   a **full page load**, and the in-memory candidate is gone. `sessionStorage` survives reload in the
   same tab, so the armed candidate rehydrates from there (§6.2). If the tab itself was replaced,
   nothing shows — correct, and what the feed slot exists for.
2. **PWA / standalone.** With the service worker installed, `window.open` may hand off to the system
   browser and return is via the app switcher. Handled identically to eviction.

**Mobile layout** — the strip stacks in a narrow column:

```
┌──────────────────────────────────┐
│  The Guardian · Left of centre   │
│  Hamas agrees to complete…       │
│                       ✓ Read     │
├──────────────────────────────────┤
│ ⇄ Compare this story          ×  │
│   20 outlets covered this.       │
│   The Wall Street Journal is     │
│   rated right of centre.         │
│                                  │
│ ┌──────────────────────────────┐ │
│ │  Read another perspective    │ │
│ └──────────────────────────────┘ │
│   View all 20 outlets →          │
└──────────────────────────────────┘
```

- Full-width primary button; × as a **44×44** touch target.
- No horizontal scroll; publisher names wrap rather than truncate.
- **Never scroll-jack.** If the card is off-screen on return, the moment has passed.

---

## 6. Dismissal and persistence

### 6.1 Dismissal

- **× collapses the strip**, no confirmation.
- **Per story, not per card** — dismissing on one member suppresses every other member of the
  cluster.
- **Permanent for that story.** A reader who has declined once should not be asked again;
  session-scoped dismissal is nagging by another name.

### 6.2 Storage — two tiers, deliberately

| what | where | why |
|---|---|---|
| Armed candidate (pending strip) | `sessionStorage` | Survives reload in the same tab — the mobile eviction path. Dies with the tab, which is correct: a new session is the feed slot's job. |
| Dismissals + impression counts | `localStorage` (`hv.continue`) | Must outlive the session. Follows the existing `lib/onboarding.ts` pattern. |

`hv.continue` shape: `{ [storyId]: { d?: 1, n: <impressions>, t: <epoch ms> } }`. Prune entries older
than 30 days on every read so the key cannot grow unbounded.

**No cross-session continuation, by design.** The brief assigns that case to the feed slot, and
honouring that keeps the two surfaces cleanly separated.

### 6.3 Impression cap

Re-render after reload only if not dismissed **and** `n < 2`. After two impressions without
engagement, treat it as declined and stop. Without this the strip would return on every page view
for four hours — which on mobile, where reload *is* the return path, would be the common case.

---

## 7. Analytics and success metrics

Following `lib/analytics.ts`'s `track(event, props)` convention and existing naming
(`article_read`, `recommendation_opened`).

| event | props | question |
|---|---|---|
| `continuation_eligible` | `storyId`, `anchorLean`, `siblingLean`, `distance`, `candidateCount` | **How often can this fire at all?** The number that sizes the audience — and §9.0's gate. |
| `continuation_armed` | `storyId`, `openedFrom` | Prefetch succeeded; the difference from `eligible` is client-side loss. |
| `continuation_shown` | + `hiddenMs`, `minutesSinceRead`, `impressionIndex`, `surface` | Armed→shown ratio; catches dwell-gate and mount losses. |
| `continuation_opened` | + `minutesSinceRead`, `sliderBucket`, `distance` | Click-through, and the **decay curve** that should replace the 4 h guess. |
| `continuation_dismissed` | + `impressionIndex` | Irritation. High dismissal at first impression means the *offer* is wrong, not the timing. |
| `continuation_all_outlets` | `storyId` | Do readers prefer the overview to a single article? If this beats `opened`, swap the CTAs. |

### 7.1 Success is not click-through

The product goal is exposure to alternative perspectives, so the primary measure is whether readers
who take a continuation subsequently read **more opposite-lean articles organically** than a matched
cohort who did not. Click-through measures the card; this measures the goal.

**Guardrail:** reads per session must not fall. If continuations only redirect attention that would
have gone to the feed, the feature moves reading around rather than adding perspective.

**Segment everything by `user_side == 0`.** Model-bridging is structurally inert for balanced readers
(`_cross_of` requires `user_side != 0`). If continuations perform well for that segment specifically,
this surface is doing work the feed provably cannot — the strongest possible justification for it.

---

## 8. Edge cases and failure modes

| case | behaviour |
|---|---|
| Reader never leaves (middle-click, copy link) | No `visibilitychange`, no strip. Correct — no return, no return moment. |
| Alt-tab for 4 s | Blocked by the dwell gate (§0.2). |
| Reader returns after 6 h | Outside the freshness window; nothing. Feed slot covers it. |
| Sibling read in another tab meanwhile | Strip retires — derived from live read state. |
| Sibling URL dead / 404 | Not preventable at prefetch. Mitigation: prefer siblings from the current recommendable corpus, which inherits the freshness gates. |
| Cluster re-clusters between read and return | Candidate is resolved at prefetch and cached; a re-cluster does not retroactively invalidate it. Acceptable — the offer was true when made. |
| Reader takes the continuation, returns again | Chain cap: no second continuation for this story this session (§0.6). |
| Anchor is itself a continuation target | Same cap applies. |
| Both outlets rated, but the *same* outlet under two names | `publisher_identity` collapse prevents it. |
| Extension-captured read (outside the app) | Never prefetched, never armed. Correct — there is no return moment to attach to. |
| Anonymous / signed-out reader | No read history, no reliable "unread" gate. **Signed-in only in v1.** |
| Prefetch fails (network, 5xx) | Silent no-op. Never a spinner, never an error state. |
| `story_index` cold at prefetch | Returns empty → no candidate. Never trigger an inline ~24 s clustering on a click path. |

---

## 9. Rollout

### 9.0 Phase 0 — the gate (before any UI)

A read-only probe over production reads, in the style of `audit_story_coverage.py`:

- share of reads in a **trusted** cluster
- of those, share with an **unread different-publisher** sibling
- of those, share where **both leans are rated**
- of those, share **genuinely opposing**
- the resulting **end-to-end eligible rate**, and its distribution by topic

**Gate: ≥10% of reads eligible.** Below that, stop and improve registry lean coverage — the
bottleneck is data, not UX, and shipping a prompt that almost never fires wastes the surface.

### 9.1 V1

Everything in §§1–8, behind `RWE_STORY_CONTINUATION` (default off), signed-in readers only.
Ship with the Phase 0 probe re-runnable so the eligible rate can be tracked as registry coverage
improves — that is `examples/audit_continuation.py`, offline by default and `--serve` against the
running engine.

**Measured on production, 2026-08-03** (59.2k articles, 1,757 stories, one reader, 99 stored reads):

| measure | value |
|---|---|
| offers from real reads, through the live endpoint | **9 of 99 (9.1%)**, 0 errors |
| structural ceiling over cluster members (`--ceiling`, n=800) | 25.6% eligible |
| dominant loss, realized | `not_clustered` — only ~21% of the clustering window is in any cluster |
| dominant loss, structural | `anchor_unrated` 35.8%, `no_opposing_sibling` 30.1% |
| index cost on the serving path | one build 64.7 ms; 100 hits averaging 6.6 ms |

Two conclusions the numbers settled. **Registry lean coverage is not the lever** it was assumed to
be in §0.1: `audit_registry_coverage` shows 4,318 untracked outlets of which only 35 sit in a
one-short story, worth 40 claims between them, and the high-volume unrated outlets are deliberately
unrated (aggregator / wire / research / forum). The backlog is a flat tail, not a head. **Cluster
membership is the binding constraint**, and loosening clustering admission re-opens the merge
defects in `docs/STORY_CLUSTER_MERGES.md` — not something to trade for this feature.

### 9.1.1 Surface priority — decided 2026-08-05

**Recommendations is the primary surface. The story page is secondary.** The design shipped with
five equal mounts and no stated ranking; this settles it.

The story page has the higher *eligibility* — every row there is already a cluster member, so gate 1
passes by construction where it rejects ~4 in 5 feed cards (67.8% of 118 real reads were
`not_clustered`). That is the argument for it, and it loses.

It loses because eligibility is not value. The story page **already** exposes multiple viewpoints
through the coverage list and its lean filters; the strip's marginal contribution there is naming
*which* single outlet opposes you, on a page where all of them are visible. It is additive on a
feed and largely redundant next to a coverage list — and the strip already concedes this by
suppressing its own "View all N outlets" link on that surface, because the link would point at the
page the reader is on. Recommendations is also where the decision the strip is trying to influence
actually happens: what to read next.

**This costs nothing structurally.** `RecommendationCard` and `DiscoverCard` already mount
`<ContinuationStrip anchorUrl={…} />` with identical defaults; the story page differs only in
`showAllOutlets={false}` and `surface="story"`. Priority here means where verification, measurement
and copy work go — not a change to what renders.

What it does change:

* **Verification order.** Confirming the strip renders on Recommendations after `be0426d` is now the
  gating check, not a follow-up. The story page render (2026-08-05) proved the mechanism; it did not
  prove the surface that matters.
* **§9.2's topic gate gets more urgent, not less.** A story page is one the reader deliberately
  opened; a feed carries whatever the recommender picked, so the sports-and-entertainment problem is
  strictly worse on the primary surface.
* **The overlap with the feed's own story slot becomes a live question.** `RWE_STORY_SLOT` puts a
  cross-session "You've been following this story / compare with X" card on the same page. Same
  goal, different timing (same-session minutes vs. next session). Whether both may fire for one
  story is unanswered here and should be decided before either is tuned.
* **`continuation_shown.surface` stops being a curiosity.** `card` vs `story` armed→shown is now the
  measurement that would overturn this decision if the yield gap is larger than the redundancy
  argument. Nothing has been measured yet.

### 9.1.2 The feed instance — decided 2026-08-06, supersedes part of §2.1

**The strip is no longer only card-bound. Recommendations carries an unbound instance** that serves
whatever is armed, wherever the read happened.

§2.1 gate 4 required "the source card is currently mounted". That was written assuming the card the
reader opened from is still there when they come back, and on Recommendations it never is:

* the recommender excludes what the reader has read, so the article they just opened is absent from
  the **next** feed by construction — `shouldDeferFeedRefetch` holds one refetch back, and the card
  still goes on the following navigation;
* a read from Discover, Search, Saved or a story page never had a Recommendations card to attach to
  in the first place.

So on the surface §9.1.1 named primary, the gate was unsatisfiable in the ordinary case. Three
production sessions confirmed it: `armed` tracked `eligible` exactly, and `shown` stayed at 1 —
every render was `surface: "story"`, never once a feed.

**The trigger changes with it.** The card-bound instance watches for a visibility return, which it
can only observe if it was mounted when the reader left. The feed instance is not, so it uses
**time since the read** (`armedAt`, same 20 s) instead. That is the fact the dwell gate was
approximating, it survives navigation, and it needs no listener attached at the right moment in a
backgrounded tab — which is a second silent failure mode the card-bound path still carries.

**One event, one offer.** The strip and the engine's `story_match` feed card both say "another
outlet covered this". While the strip is up, the feed withholds its story card for that same story:
the strip is the more specific of the two — it names the opposing outlet and states the reader's own
side — so it wins. The slot itself is untouched for every other story, and remains the cross-session
path for readers who return in a later session with nothing armed.

**It wears the card's chrome, and carries the card's information.** Same radius, border, surface and
padding as `DiscoverCard`, with the accent kept only on the border — distinguishable from the grid
without reading as a banner injected around the page. More importantly it now shows what a card
shows before a reader commits: the sibling's **own headline** (the first version showed the offered
article's headline nowhere at all), its publisher as the same linked chip every card uses rather
than a name buried mid-sentence, its lean badge, and its age. The §1.3 sentence stays underneath —
naming both outlets on one axis is a copy rule, not a decoration.

It stays **out of the ranked grid** deliberately. Placing it in the grid would assert it was ranked
there, and it would inherit the filter tabs — selecting "Bridging" would hide a time-sensitive offer
that has nothing to do with the filter.

Unchanged: the nine gates, the copy rules in §1.3, dismissal and the impression cap (§6.1, §6.3),
and the card-bound instances on Discover / Search / Saved / the story page — which keep the compact
in-card treatment, since there the card above already supplies the context.

### 9.1.3 The impression cap counts read episodes — 2026-08-06

`MAX_IMPRESSIONS` (2) was written against **returns**, which are rare. Once the trigger included a
mount — which mobile requires, since a discarded tab reloads rather than firing `visibilitychange` —
every navigation to a surface carrying the strip became an impression. Recommendations → Discover →
Recommendations would spend the entire budget in seconds, and the story would go permanently quiet
before the reader had engaged with the offer once.

The cap now counts **read episodes**, keyed by the candidate's `armedAt`. Re-rendering the same
offer after a reload or a navigation is free; being offered the story again after a *second read* is
the second impression. That is what §6.3 was always about — being asked repeatedly — and it makes
the two triggers safe to coexist.

State written before this carries no `armedAt` and falls back to counting, so an existing cap is
honoured rather than silently reset.

### 9.1.4 §1.4's live read state, actually implemented — 2026-08-06

§1.4 has always said *"sibling read in the meantime — strip disappears, derived from live read
state, not a snapshot"*. It was never built: the armed candidate **is** a snapshot, resolved at the
anchor's Read-click and then parked in sessionStorage, which now survives reloads and navigation by
design. A reader who opened the offered article from Discover or Search would be shown the same
offer again after a refresh, inviting them to read what they had just read.

`retireIfSiblingRead` hangs off `useRecordRead` — the one mutation every surface shares — so it
covers Discover, Search, Saved, the story page and the strip's own CTA without polling read state or
refetching history. A rendered strip follows: `sync` clears a displayed offer whose candidate is
gone, which also fixes §2.2 on the unbound instance, where reading a second article previously left
the *previous* story's offer on screen because the mount trigger declines to run while one is shown.

**And the snapshot is re-checked against the engine immediately before showing** (2026-08-06), which
closes the rest of it. `retireIfSiblingRead` only sees reads made in this tab; a read from the
browser extension, a phone or a second tab does not touch its mutation. Re-resolving is the general
answer rather than another special case — the endpoint re-runs every gate against current state, so
a sibling read anywhere drops out of the candidate set and the engine names a different one or
declines. It also catches an article that has left the catalog, a rebuilt cluster, and a moved
openness slider.

Three rules make it safe:

* **A failed request is not a decline.** Offline, the reader keeps the snapshot. Trading a rare
  wrong offer for a common missing one is the worse deal, and revalidation must not become a second
  way to see nothing.
* **A replacement re-arms with the ORIGINAL `armedAt`.** That value is both the freshness clock and
  the impression episode key; restarting it would silently extend the 4 h window and re-open the cap.
* **The trigger is serialized.** The mount and visibility triggers can both reach it, and since the
  check is now awaited, an `offer`-based guard alone let both through and counted two impressions
  for one read — intermittently, which is the worst way to find out.

This does not violate §0.3's "no round trip at the return moment": nothing on the page waits for it,
and it gates only the strip's own appearance, which animates in regardless.

### 9.2 V1.1 — small, high-value

- **A topic gate for non-political stories.** *Measured on production, 2026-08-03, and the largest
  quality problem the live probe found.* Of nine offers resolved from 99 real reads, two were about
  a mile race at the Commonwealth Games (`The Independent` → `The Straits Times`) and a film's
  Chinese box office (`Variety` → `The Times of India`). Registry leans are an **outlet-level**
  political rating, so a sports result inherits its publisher's rating and the strip would say
  *"The Straits Times is rated right of centre — you read a left-of-centre account"* about who won a
  mile. The copy rules in §1.3 are technically satisfied and substantively absurd: there is no
  opposing viewpoint on a finishing time, and offering one teaches readers the ratings mean less
  than they do.

  `_TEMPLATE_PATTERNS` does not catch this — it targets betting, lottery and obituary mills, not
  ordinary sports and entertainment reporting. The natural gate is the story's own `topic` (the mode
  across members, already computed by `story_service._mode_topic`), which would need adding to
  `evidence_resolver.story_index`'s entries — the same additive extension `clusterTrust` /
  `publisherCount` / `title` already took. Deliberately NOT in v1: it narrows an eligible rate
  measured at 9.1%, and which topics genuinely carry viewpoint is a product judgement that the
  `continuation_opened` rate per topic can answer with evidence instead of assertion.

- **Deep-link "View all outlets" pre-filtered** to the opposing side (§0.7). Needs a URL param on
  `CoverageList`'s lean filter.
- **Story Intelligence hook**: for a `Breaking` / `Growing` cluster, add one counted line —
  *"6 more outlets have covered this since you read."* The panel already computes momentum; this is
  a stronger reason to compare than a static offer.
- Tune the freshness window from the measured decay curve.

### 9.3 V2 — only if V1 earns it

- **Same-side siblings** with distinct copy ("another account", not "another perspective") — only
  after v1 establishes the pattern is trusted.
- **Coverage Comparison inline**: the L0 card already computes counted differences between an
  article and its cluster. Surfacing one finding in the strip (*"12 of 20 outlets place this in
  Israel; this account does not"*) would make the comparison concrete before the click.
- **Cross-session continuation** — only if `continuation_eligible` shows the feed slot is failing
  to cover returning readers.

---

## 10. Technical architecture

### 10.1 Reuse, not new machinery

| existing | reused for |
|---|---|
| `story_service` clusters + `clusterTrust` | cluster membership and trust gate |
| `evidence_resolver.story_index` (TTL-cached) | canonical-url → story lookup, already warmed by the feed path |
| `personalize._opposing_leans` | the ±0.5 opposition test — **lift to a shared module** so the feed slot and the continuation cannot drift |
| `publisher_identity.groups` | same-outlet collapse |
| `coverage_comparison._TEMPLATE_PATTERNS` | the mill/template gate |
| `store.get_reads` | the unread test |
| `ReadArticleButton` | the trigger point *and* the sibling's open action |
| story page `CoverageList` | the "all outlets" destination — already lean-filterable |
| `lib/analytics.track` | events |

**No new table. No new worker. No model.** This is a lookup over an index the feed path already
builds and caches.

### 10.2 The one new endpoint

```
GET /api/me/continuation?url=<canonical>          (signed-in, read-only)
  → { storyId, outlets, sibling: { url, publisher, lean, leanBucket, publishedAt } } | null
```

**Why not extend `/api/me/reads`:** it is a **batch** endpoint (`reads: list[ReadInput]`) returning
`IngestResultModel` counts, and it is shared with the browser extension where no return moment
exists. A single-candidate payload does not belong on a batch counts contract.

**Why prefetch at click, not fetch on return:** the strip must appear instantly. Calling at click
time overlaps the request with the tab switch — by the time the reader is back, the answer is
cached. Degrades gracefully: if the prefetch failed, the client may retry once on return, and
failing that renders nothing.

**Cost:** one `story_index` lookup (dict access on a TTL-cached index the feed already builds), a
scan of that cluster's members, and registry lean reads. Sub-millisecond warm; the index build cost
is already paid and instrumented (`rec_story_index_hit_ms`).

### 10.3 Client

```
ReadArticleButton.onClick
   ├─ recordRead.mutate(...)                     (unchanged)
   ├─ track("article_read")                      (unchanged)
   ├─ prefetchContinuation(url) ──▶ sessionStorage[hv.continue.armed]
   └─ window.open(href, "_blank")                (unchanged)

<ContinuationStrip storyId anchorUrl />           mounted by each card that has been read
   ├─ useVisibilityReturn({ minHiddenMs: 20_000 })
   ├─ reads armed candidate + hv.continue (localStorage)
   └─ renders, or nothing
```

`ContinuationStrip` is a leaf component with no data dependency of its own beyond the cached
candidate. It touches **no** recommendation infrastructure: no blend-plan slot, no
`DEFAULT_BLEND_PLAN` total, no `blend_plan_for` arithmetic, no `rec_explain` parity, no explanation
ladder. That isolation is the main architectural argument for this surface over expanding the feed.

### 10.4 Testing

- **Engine**: eligibility unit tests per gate (untrusted cluster, template genre, unrated lean,
  same-side, already-read, no sibling), determinism of ranking, slider-distance selection at each
  plateau.
- **Web**: dwell gate (19 s vs 21 s), dismissal persistence across reload, impression cap, "sibling
  read meanwhile" retirement, reduced-motion.
- **E2E**: read → simulated visibility cycle → strip appears → open records a read; dismissal
  survives reload.
- **Shape-contract test** binding the endpoint's response keys to what the strip reads — the defect
  class that has bitten this repo repeatedly.

---

## 11. Risks

| risk | severity | mitigation |
|---|---|---|
| **Eligible rate too low to matter** | High | §9.0 gate before building. Improve lean coverage rather than loosening gates. |
| **Reach limited to in-session returns** | High | Accepted by design; the feed slot covers the rest. Do not grow either to cover the other. |
| **A weak prompt at a high-attention moment** | High | Every §3 gate exists for this asymmetry. A weak feed card is ignorable; a weak interstitial is an irritation. |
| **Lean labels read as accusation** | Medium | §1.3 copy rules; sourced phrasing; symmetric labelling of both outlets; methodology link. |
| **Layout shift on expand** | Medium | Animate; respect reduced-motion; never scroll-jack. |
| **Two surfaces for one idea** | Medium | Justified only while they serve non-overlapping populations. If measurement shows overlap, retire one. |
| **Cluster quality** | Medium | Trust gate + template gate. Comparison quality is capped by clustering quality; story fragmentation also makes "20 outlets" an undercount. |
| **`localStorage` unavailable** (private mode) | Low | Degrade to in-memory; the strip may reappear next session. Acceptable. |

## 12. Open questions

1. **4 h freshness and 2-impression cap are judgement calls.** The `continuation_opened` decay curve
   should replace both within weeks of data.
2. **Primary CTA — article or story page?** This design opens the article. If
   `continuation_all_outlets` outperforms `continuation_opened`, swap them.
3. **Should the strip show when the reader dismisses *quickly and often*?** A per-reader global
   opt-out after N dismissals may be kinder than per-story suppression alone.
4. **Anonymous readers** are excluded for lack of a reliable unread signal. Worth revisiting if the
   signed-out population is large.
