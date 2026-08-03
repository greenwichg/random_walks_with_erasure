# Story Continuation — design

**What it is:** when a reader returns to Hidden View after opening an article, the card they read
from expands in place with one offer — *another outlet's account of the same event, from the
opposite side of the spectrum*.
**Status:** design only. Nothing implemented.
**Relationship to the feed:** the existing Same-Story feed slot stays at **one card, quota
unchanged**. This is a second, complementary surface — not a replacement and not an expansion.
**Date:** 2026-08-03.

---

## 1. Why a continuation rather than more feed cards

The feed and the continuation answer different questions at different moments, and the second
moment is the better one.

| | Same-Story feed card | Story Continuation |
|---|---|---|
| Reader's question | "what should I read next?" | "I just read this — what else is there about it?" |
| Anchor freshness | hours or days old | **seconds** |
| Cost of the offer | zero-sum — evicts a discovery / bridge / publisher card | **additive** — occupies space that holds nothing today |
| Works for balanced readers | yes, but competes for capacity | yes, at no cost |
| Reach | everyone who opens the feed | only readers who return in-session |

**Cognitive flow is the decisive argument.** Comparing two accounts is only cheap while the first
one's claims are still in working memory — you can only notice what is *different* if you remember
what the first article said. That window decays in minutes, and the feed is the surface with the
longest possible delay between anchor and offer.

**The zero-sum problem disappears.** Every attempt to expand the feed slot required a displacement
policy — which explanation tier may be evicted, whether `topic_continuity` is protected, whether
same-story cards may spend the bridge budget. A continuation evicts nothing, so none of those
questions arise.

**The destination already exists.** The story page's `CoverageList` is lean-filterable and the
analyze page carries the Coverage Comparison card. This feature is a **prompt at the right moment
toward machinery already built**, not a new comparison experience.

**But reach is genuinely worse**, which is exactly why the feed slot stays. A reader who closes the
tab, or returns tomorrow, never sees a continuation. The two surfaces cover different populations:

- **Continuation** — hot anchor, in-session, high intent, narrow reach.
- **Feed slot (1 card)** — cold anchor, cross-session, low intent, broad reach.

Neither should be grown to cover the other's gap.

## 2. The trigger

Verified against the code rather than assumed:

`ReadArticleButton` records the read to `/api/me/reads` (tagged with `openedFrom`) **before**
calling `window.open(href, "_blank", "noopener,noreferrer")`. The app is never navigated away from.

So the reader returns to a **live SPA instance**, on the page they left, at the scroll position they
left, with the source card already rendering its `Check` state. The trigger is
`document.visibilitychange → visible`, not a page load — the same hook `rum-listener.tsx` and
`lib/analytics.ts` already use.

## 3. Placement — in the card, not above the feed

The continuation strip attaches **to the card the reader read from**.

```
┌──────────────────────────────────────────────────────────────────┐
│  The Guardian · Left of centre               Politics · 2h ago   │
│  Hamas agrees to complete disarmament under Gaza agreement       │
│  …                                                    ✓ Read     │
├──────────────────────────────────────────────────────────────────┤
│ ⇄  Continue this story                                        ×  │
│    Wall Street Journal · Right of centre · 20 outlets covering   │
│    [ Read this account ]        Compare all coverage →           │
└──────────────────────────────────────────────────────────────────┘
```

Why in-place beats a banner above the feed:

1. **The two accounts sit adjacent.** "Compare these" is legible when the things being compared are
   next to each other; a top-of-page banner separates them.
2. **No layout disruption elsewhere.** A banner on a page the reader has scrolled into either
   shifts everything or renders off-screen.
3. **Every surface gets it for free.** `ReadArticleButton` is shared by Recommendations, Discover,
   Search, Stories and Saved. Attach to the card and there is no per-page banner logic.
4. **Multiplicity resolves itself** (§7).
5. **It fails correctly.** If the reader navigated away the card is gone and the moment has passed.
   A continuation is a moment, not a campaign.

## 4. Eligibility — every gate, and why

The strip renders **only** when all of these hold. Any failure renders nothing, silently.

| gate | rule | why |
|---|---|---|
| **Read recency** | read within the last **90 minutes** | The comparison's value decays with memory of the first article. Past that the reader is starting fresh and the prompt is noise. |
| **Cluster membership** | the read article is in a validated multi-publisher story cluster | The existing P1 licensing gate; identical to `evidence_resolver`'s. |
| **Cluster trust** | `clusterTrust == "ok"` | A welded cluster produces a "same story" offer that isn't the same story. This project measured that failure (`docs/STORY_CLUSTER_MERGES.md`). |
| **Genre** | cluster does not match `_TEMPLATE_PATTERNS` | "Compare coverage" on a lottery-numbers or box-office piece is noise. |
| **Both leans rated** | anchor and sibling both carry a registry lean | Never infer opposition. Unrated licenses no claim (L2.2). |
| **Genuinely opposing** | opposite sides by the catalog's ±0.5 buckets | Centre opposes nothing; −1.5 vs −0.8 is the same side. |
| **Unread** | sibling not in the reader's read history | Offering something already read is a broken promise. |
| **Different publisher** | sibling's publisher ≠ anchor's publisher | Syndicated reprints are not another account. |
| **Live article** | sibling resolves in the current catalog with a usable URL | Never offer a dead link. |
| **Not dismissed** | this story not dismissed by this reader | §6. |
| **Impression cap** | shown < 2 times for this story | §6. |

**v1 shows opposite-lean siblings only.** A same-side sibling is legitimate "more coverage," but a
pattern that sometimes means *another perspective* and sometimes means *another article* stops being
trusted. Keep the promise narrow. Same-side is a v2 with different copy, never a fallback.

**Expected consequence, stated up front:** these gates are strict, and the strip will fire on a
minority of reads. That is the correct trade for a prompt that always means what it says — but §9's
first measurement is precisely how small that minority is.

## 5. Ranking — and the slider's only role here

When several siblings qualify, choose in this order:

1. **Opposing and rated** — the eligibility gate; everything below applies within that set.
2. **Ideological distance, modulated by the Political Openness slider** (below).
3. **Publisher novelty** — `never` / `rarely` familiarity bands first, so the offer doubles as
   source diversification.
4. **Recency** — newest `publishedAt`, ties broken by canonical URL so the choice is deterministic.

### The slider's role — narrow and deliberate

**Political Openness continues to control only the RWE-B bridge-slot budget in the feed
(`blend_plan_for`). Nothing here changes that.**

Its only influence on this feature is **which** opposing candidate is chosen when more than one
qualifies — never *whether* the strip appears:

| slider | candidate preference | rationale |
|---|---|---|
| low (0–37) | the **nearest** opposing outlet | Opposition, but the gentlest available; a reader who asked for less cross-perspective content still gets a genuine comparison, not the most distant one. |
| mid (38–62) | balanced — rank by publisher novelty first | The default; no distance preference. |
| high (63–100) | the **furthest** opposing outlet | A reader who asked for more cross-perspective content gets the sharpest available contrast. |

This uses `_piecewise`-style anchors over `|lean_sibling − lean_anchor|`. It respects the slider's
meaning — *how much ideological distance you want* — without letting it gate a feature whose whole
value is being shown at the right moment. **A reader at slider 0 still sees continuations**, because
the strip is a response to their own reading act, not an injection into their feed.

## 6. Dismissal and persistence

- **× on the strip.** Dismissing collapses it back to the plain read-state card. No confirmation.
- **Dismissal is per STORY, not per card.** Dismissing on one member suppresses the strip for every
  other member of that cluster.
- **Dismissal is permanent for that story.** A reader who has declined to compare this story once
  should not be asked again. Session-scoped dismissal would be nagging by another name.
- **Storage:** `localStorage`, following `lib/onboarding.ts`'s existing pattern —
  `hv.continue` → `{ [storyId]: { dismissed?: true, shown: number, ts: number } }`. Device-local is
  right: this is a UI preference, not user data worth a table, and losing it on a new device costs
  one dismissible strip. Prune entries older than the freshness window on every read of the key so
  it cannot grow unbounded.

### Surviving refresh

**Yes, but bounded — and the obvious answer is wrong.** Read state comes from the server, so after a
refresh the card can re-derive "read + opposing sibling exists." Letting it re-render freely would
mean the strip returns on every page view for 90 minutes.

- Re-renders after refresh **only if** not dismissed **and** `shown < 2`.
- After two impressions without engagement, treat it as declined and stop permanently.

This matters more on mobile than desktop (§8), where tab eviction makes refresh the *normal* return
path rather than the exception.

## 7. Multiple recently read stories

**The placement dissolves the problem.** Each strip is anchored to its own card, so three reads
produce three strips on three different cards — no queue, no stacking, no priority ordering, no
"which one wins."

Two bounds:

- **One strip per story cluster.** Reading three members of one story yields one strip, on the most
  recently read card.
- **At most three strips visible at once.** A reader who opens eight tabs should not return to a
  feed peppered with them; keep the three most recent reads and let the rest fall to the feed slot.

## 8. Desktop and mobile

The return path differs materially, and mobile is the harder case.

**Desktop.** `_blank` opens a background/foreground tab; the app tab survives. Return is a genuine
`visibilitychange` on a live instance. The strip animates in below the source card
(`prefers-reduced-motion` respected). This is the design's best case.

**Mobile.** Two complications:

1. **Tab eviction.** iOS Safari and Android Chrome routinely discard background tabs under memory
   pressure. Return is then a **full page load**, not a visibility event — which is exactly why §6's
   refresh-survival path exists. On mobile it is the primary path, not a fallback. The card must be
   able to derive its strip from server read-state alone.
2. **PWA / standalone.** With the service worker installed, `window.open` may hand off to the system
   browser, and return is via the app switcher. Same handling as eviction: derive from read state.

**Mobile layout.** The single-column card is narrow, so the strip stacks: publisher + lean on one
line, actions on the next, full-width primary button, × as a 44×44 touch target in the corner. No
horizontal scrolling, no truncated publisher names.

**Mobile scroll position.** Browsers restore scroll inconsistently after eviction. Never scroll-jack
to reveal the strip — if the card is off-screen on return, the moment has passed.

## 9. Interaction with existing recommendation cards

- **The feed's Same-Story slot is unchanged** — one card, existing displacement rule, no quota
  increase. It serves cold anchors; the continuation serves hot ones.
- **Deduplication:** a sibling offered as a continuation should not also appear as the feed's
  Same-Story card in the same session. The continuation is the stronger offer; the feed slot should
  pick its next candidate or yield.
- **No effect on the blend plan.** The strip is not a feed card, occupies no slot, and never touches
  `DEFAULT_BLEND_PLAN`'s fixed total, `blend_plan_for`'s arithmetic, or `rec_explain`'s parity
  guarantee. This isolation is the main architectural argument for the surface.
- **No effect on the explanation ladder.** Nothing is displaced, so no explanation is lost.
- **Reception signals:** opening a continuation records a read exactly as any other
  `ReadArticleButton` does — the Health/History/Analytics pipeline is fed identically, so a
  continuation read counts toward viewpoint balance like any other.

## 10. Analytics

Following `lib/analytics.ts`'s existing `track(event, props)` convention and naming style
(`article_read`, `recommendation_opened`):

| event | props | question it answers |
|---|---|---|
| `continuation_eligible` | `storyId`, `anchorLean`, `siblingLean`, `distance`, `candidateCount`, `minutesSinceRead` | **How often can this fire at all?** The single most important number — it sizes the audience before anything else matters. |
| `continuation_shown` | + `openedFrom`, `surface`, `impressionIndex` | Eligible-to-shown ratio; catches gating or viewport losses. |
| `continuation_opened` | + `minutesSinceRead`, `sliderBucket` | Click-through, and the **decay curve** that should set the 90-minute window empirically. |
| `continuation_dismissed` | + `impressionIndex` | Irritation signal. A high dismissal rate at first impression means the offer is wrong, not the timing. |
| `continuation_compare_opened` | `storyId` | Secondary CTA — do readers want the full coverage list rather than one article? |

**Success is not click-through.** The product goal is exposure to alternative perspectives, so the
measure that matters is whether readers who take a continuation subsequently read **more
opposite-lean articles organically** than a matched cohort who did not. Click-through measures the
card; this measures the goal.

**Guardrail metric:** total reads per session must not fall. If continuations merely redirect
attention that would have gone to the feed, the feature is moving reading around rather than adding
perspective.

**Segment everything by whether the reader's `user_side` is zero.** Model-bridging is structurally
inert for balanced readers (`_cross_of` requires `user_side != 0`), so if continuations perform well
for that segment specifically, this surface is doing work the feed provably cannot.

## 11. Trade-offs

**Reach is the real cost.** In-session return only. This is why the feed slot stays, and why neither
surface should be grown to cover the other.

**The strict gates may make it rare.** Both leans rated, genuinely opposite, trusted cluster,
non-template genre. If `continuation_eligible` fires on only a few percent of reads, the honest
response is to improve registry lean coverage — not to loosen the bar. A weakened gate at a
high-attention moment is worse than no feature.

**Higher salience means a higher cost of error.** A weak card in a 14-card feed is ignorable; a weak
prompt at the moment of return is an irritation. Every gate in §4 exists because of this asymmetry.

**Layout shift.** Expanding under a card pushes content below it down. Animation makes it read as a
response rather than a glitch, but it is a genuine cost of in-place over a fixed banner.

**Two surfaces to maintain** for one product idea, with different triggers, different storage and
different failure modes. Justified only because they serve non-overlapping populations — if
measurement shows the continuation reaches nearly everyone the feed slot does, one of them should
be retired.

## 12. Open questions

1. **The 90-minute window and the 2-impression cap are judgement calls, not derived numbers.** The
   `continuation_opened` decay curve should replace both within a few weeks of data.
2. **Should the primary CTA open the article or the story page?** This design opens the article —
   the goal is reading the other account. If `continuation_compare_opened` outperforms
   `continuation_opened`, readers prefer the overview and the CTAs should swap.
3. **Same-side siblings as a v2** — legitimate content, different promise, different copy. Only
   after v1 establishes that the pattern is trusted.
4. **Does dismissal deserve a server-side home?** `localStorage` loses on a new device. Acceptable
   for v1; revisit only if cross-device nagging shows up in `continuation_dismissed`.
