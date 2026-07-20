# Recommendation Evidence Audit (RC2.1)

**Read-only audit** of the personalized recommendation evidence system shipped in RC2.1
(`_improvement_evidence` / `_attach_evidence`, `examples/api_server.py:129–271`). **No code was
modified.** Findings are cited to code and classified by severity; the audit answers each of the six
required checks and ends with recommendations for a follow-up phase.

## Verdict

The **measured** path is sound: every displayed sentence is traceable to a real report field, the
`evidenceBasis` reconstructs the displayed evidence, causation is not over-claimed, and the fallback
wording is honest and definitional.

**One High-severity honesty defect exists in the *estimate* path** (Finding F1): four rules emit
"you've read …" / "your reading" / "your political reading" wording in estimate mode, where the
distributions are computed from the *character of the chosen outlets* with **zero reads** — so the
sentences imply measured reading behaviour that did not happen. This violates requirement 4 and
contradicts the estimate builder's own contract (*"No reading history is fabricated"*,
`api_server.py:1281`). Plus a few Low/cosmetic wording risks.

| Severity | Findings |
|---|---|
| **High** | F1 — estimate-mode wording implies measured reading (topicDiversity, viewpointBalance, echoChamber, emotionalBalance) |
| **Low** | F2 left==right tie contradiction · F3 reportingRatio estimate evidence presumes reading · F4 sum-vs-parts rounding drift · F5 benefit asserts a definite effect on a percentile metric |
| **Info** | O1 — blindSpots basis stores a `gap` value that is never displayed |

---

## Rule-by-rule audit

Each rule is checked against: **(2)** derived from real report fields, **(3)** causation-vs-correlation,
**(4)** estimate never implies measured, **(5)** basis reconstructs the evidence, **(6)** fallback
honesty. `api_server.py` line numbers in brackets.

### 1. sourceDiversity  [165–186]
- **(2) Derived:** ✅ measured branch binds top-2 `sources[].share` + names; count in the estimate
  branch. Every number is a `sources` field.
- **(3) Causation:** ✅ "would widen your sources" — mechanically true (adding an outlet widens the
  source set); not a health-outcome claim.
- **(4) Estimate:** ✅ **the only rule that is estimate-aware.** The `elif sources` branch fires only
  when `measured=False` and says *"Your estimate is based on N outlets"* — no reading implied.
- **(5) Basis:** ✅ trigger % = sum of the two basis shares; evidence %s = each basis share; names =
  basis labels. Fully reconstructable. (See F4 for a ±1% rounding nuance.)
- **(6) Fallback:** ✅ definitional ("This tracks how many different outlets your reading draws on").
- **Verdict: PASS.**

### 2. topicDiversity  [188–208]
- **(2) Derived:** ✅ top-2 `topics[].share` (trigger) + `blindSpots[].topic` (evidence).
- **(3) Causation:** ✅ "underrepresented in your reading" is a fact from blind spots; "would broaden
  your topics" is mechanical.
- **(4) Estimate:** ❌ **F1.** The trigger *"You've read {80% Topic 3 and 10% Topic 0}"* is emitted
  unconditionally. In estimate mode `topics` is the **selected outlets' catalog topic mix**
  (`api_server.py:1300`), not reading — so "You've read …" is false for a 0-read estimate.
- **(5) Basis:** ✅ topics reconstruct the trigger; blind-spot labels reconstruct the evidence names.
  (O1: the stored blind-spot `value` is the gap, which is not displayed.)
- **(6) Fallback:** ✅ definitional.
- **Verdict: PASS (measured) / FAIL (estimate — F1).**

### 3. viewpointBalance  [210–221, 227–231]
- **(2) Derived:** ✅ `viewpoint.{left,center,right}`.
- **(3) Causation:** ✅ "would balance your viewpoints" — mechanical.
- **(4) Estimate:** ❌ **F1.** Trigger *"Your political reading is X% left, …"* — in estimate mode
  `left/center/right` come from the **outlets' house leans** (`api_server.py:1308`), not reading.
- **(5) Basis:** ✅ full left/center/right triple; lean direction derivable.
- **(6) Fallback:** ✅ definitional ("This tracks how balanced your political reading is …").
- **Extra (F2):** evidence lean uses `'left' if l > r else 'right'` while the suggested side uses
  `weak = 'right' if r <= l else 'left'`. On the exact tie `l == r`, evidence says *"leans right"* and
  the action says *"add right-leaning reads"* — mutually contradictory. Measure-zero edge, but latent.
- **Verdict: PASS (measured, non-tie) / FAIL (estimate — F1) + F2 edge.**

### 4. echoChamber  [210–216, 222–231]
- **(2) Derived:** ✅ `viewpoint`; evidence uses `max(left%, right%)`.
- **(3) Causation:** ✅ "loosens the echo chamber" — mechanical (reading the other side reduces
  one-sidedness).
- **(4) Estimate:** ❌ **F1.** *"Your political reading is …" / "About X% of your political reading sits
  on one side"* — same outlet-derived `viewpoint` in estimate mode.
- **(5) Basis:** ✅ the left/center/right triple reconstructs the max-side figure.
- **(6) Fallback:** ✅ definitional.
- **Verdict: PASS (measured) / FAIL (estimate — F1).**

### 5. emotionalBalance  [233–247]
- **(2) Derived:** ✅ `attention.{fear,outrage,analysis}`.
- **(3) Causation:** ✅ "Swapping one charged read … raises the balance" — mechanical. ("a day" is a
  suggested cadence, not a claim about the reader.)
- **(4) Estimate:** ❌ **F1.** Trigger *"X% of your reading leans on fear and outrage"* — in estimate
  mode `attention` is aggregated over the **selected outlets' catalog articles** (`api_server.py:1312+`),
  not reading.
- **(5) Basis:** ✅ fear/outrage/analysis reconstruct trigger (fear+outrage) and evidence (each). (F4
  rounding nuance applies to the summed trigger.)
- **(6) Fallback:** ✅ definitional.
- **Verdict: PASS (measured) / FAIL (estimate — F1).**

### 6. reportingRatio  [249–252]
- **(2) Derived:** ✅ always score-fallback; `metric.score` + `metric.benchmark`.
- **(3) Causation:** ✅ "Pair commentary with a straight-reporting source" — neutral suggestion.
- **(4) Estimate:** ⚠️ **F3 (Low).** Evidence *"This tracks how much of your reading is straight
  reporting rather than opinion"* presumes reading; in estimate mode there is none. Softened by the
  definitional "This tracks …" framing and by the trigger being a *score* statement (not a reading
  claim), so Low, not High.
- **(5) Basis:** ✅ score + benchmark reconstruct the trigger and its comparison.
- **(6) Fallback:** ✅ honest comparison (below/at/above — see the honesty note below), definitional
  evidence.
- **Verdict: PASS (measured) / minor F3 (estimate).**

### 7. openMindedness  [254–256]
- **(2) Derived:** ✅ score-fallback (score + benchmark). **Notably honest:** the reception counts
  (shown/opened cross-cutting) are *not* on the report, and the rule does **not** invent them —
  evidence is definitional ("This measures how often you engage views that challenge your own").
- **(3) Causation:** ✅ "Open the cross-cutting reads we surface" — direct, neutral.
- **(4) Estimate:** ✅ not applicable — openMindedness is `available: false` in estimate and is filtered
  out before improvements are built, so this rule never runs in estimate mode.
- **(5) Basis:** ✅ score + benchmark.
- **(6) Fallback:** ✅ definitional.
- **Verdict: PASS.**

### The shared score-fallback  [145–163]
The honest comparison introduced in RC2.1 holds: it says "below the typical reader" only when
`score < benchmark`, and "above … but still among your lowest metrics" when the reader's *weakest*
metric nonetheless sits above the median. **(6) PASS.** All fallback observations are definitional and
neutral.

### expectedBenefit (all rules)
"Broadens / Improves / Loosens / Raises / Lifts your {Metric}." **(3):** these describe the effect of
the suggested action on the metric that measures exactly that behaviour — mechanical, not a
correlational claim about the reader's beliefs or "health." See **F5** for a subtle over-certainty note.

---

## Requirement-by-requirement summary

1. **Every rule reviewed** — 7 rules + shared fallback, above.
2. **Derived from real report fields** — ✅ in measured mode, every displayed number traces to
   `sources` / `topics` / `viewpoint` / `attention` / `blindSpots` / `metric.{score,benchmark}`. No
   value is invented; no alternate outlet is named. (The *labels* used in estimate mode are real too —
   the defect is the surrounding **verb**, F1, not the numbers.)
3. **Causation vs correlation** — ✅ actions/benefits are mechanical/definitional links between the
   suggested behaviour and the metric that counts it, not correlational health claims. Closest to a
   causal reading is the benefit phrasing (F5, Low).
4. **Estimate never implies measured** — ❌ **F1 (High):** four rules use "you've read" / "your reading"
   in estimate mode. Only sourceDiversity is estimate-aware today.
5. **Basis reconstructs the evidence** — ✅ every displayed figure is reconstructable from the basis
   entries; one basis value (blind-spot `gap`, O1) is traceability-only and never displayed.
6. **Fallback honesty/neutrality** — ✅ definitional framing throughout; the score comparison is
   truthful in all three below/at/above cases.

---

## Remaining wording risks

- **F1 (High)** — estimate-mode measured-behaviour implication. Evidence: my own captured estimate
  output shows `viewpointBalance` and `echoChamber` emitting *"Your political reading is 17% left, 33%
  center, 50% right."* for a 6-outlet, 0-read estimate. Contradicts `api_server.py:1281` and the
  estimate blind-spot note, which already says *"…light in the outlets you picked"* (`:1353`) — the
  correct framing this system should mirror.
- **F2 (Low)** — viewpointBalance `l == r` tie: evidence "leans right" vs action "add right-leaning"
  contradict. Exact float tie, so vanishingly rare, but latent.
- **F3 (Low)** — reportingRatio estimate evidence presumes reading (softened by definitional framing).
- **F4 (Low/cosmetic)** — trigger % is `round(sum(shares)*100)` while evidence shows each
  `round(share*100)`; the two can differ by 1 point (e.g. 47%+35% shown but "83%" in the trigger). Not
  a correctness issue — the basis holds the exact floats — but visibly inconsistent.
- **F5 (Low)** — benefit asserts a definite effect ("Broadens your Source Diversity") on a metric that
  is a **percentile vs the population**; the raw dimension always moves, but the *score* is not strictly
  guaranteed to. Slightly stronger than the UI's existing "Helps {metric}" framing.
- **O1 (Info)** — the blindSpots basis entry stores `gap`, which is never shown; the displayed evidence
  (topic names) reconstructs from the label alone. Harmless, but the numeric value is orphaned.

---

## Recommendations (for a follow-up phase — not applied here)

1. **Fix F1 — make the four rules estimate-aware**, mirroring the estimate's own outlet framing (the
   blind-spot note at `:1353` is the model). In estimate mode, replace the verb, not the numbers:
   - topicDiversity → *"The outlets you picked skew toward Topic 3 (80%) and Topic 0 (10%)."*
   - viewpoint/echo → *"The outlets you picked lean 50% right, 17% left."*
   - emotionalBalance → *"The outlets you picked lean on fear and outrage (41%)."*
   Thread the existing `measured` flag (already passed to the binder) into these branches; keep the
   basis identical. This is the one change that should be prioritized.
2. **Fix F2** — compute the lean/weak side once and derive both the evidence and the action from it,
   with an explicit balanced-case branch when `left == right`.
3. **Reconcile F4** — display the trigger % as the sum of the rounded parts (or state a ±1 tolerance),
   so trigger and evidence always agree on screen.
4. **Consider F5** — soften benefit to "Helps broaden your Source Diversity" to match the UI's existing
   "Helps {metric}" chip and avoid implying a guaranteed score move.
5. **O1** — either display the gap ("Economy is 9% of the catalog…") or drop the value from the basis
   to keep every basis number tied to something shown.

All five are wording/estimate-mode refinements; none touch selection, ordering, impact, or the measured
path, which pass this audit.

---

*Read-only audit — no code was modified.*
