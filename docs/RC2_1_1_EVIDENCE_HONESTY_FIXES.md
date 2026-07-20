# RC2.1.1 — Evidence Honesty Fixes

Resolves the findings of the RC2.1 Evidence Audit (`RC2_1_EVIDENCE_AUDIT.md`, F1–F5) while leaving the
recommendation **engine, selection, ordering, impact values, and API contract** untouched. All changes
are in the wording produced by the evidence binder (`_improvement_evidence`, `examples/api_server.py`)
plus regression tests; the numbers and `evidenceBasis` are byte-for-byte the same as before.

## Findings resolution

| # | Finding | Resolution | Status |
|---|---|---|---|
| **F1** | Estimate-mode wording implied measured reading ("you've read", "your reading") | The binder is now **mode-aware**: measured describes reading; estimate speaks to *"the outlets you picked"* (mirroring the estimate's own blind-spot note). Applied to topicDiversity, viewpointBalance, echoChamber, emotionalBalance, and reportingRatio. | **Resolved** |
| **F2** | `left == right` tie produced contradictory "leans right" + "add right-leaning" | Exact-tie branch: neutral evidence (*"…evenly split"*) and a side-agnostic action (*"Adding cross-cutting reads would strengthen your viewpoint balance"*). | **Resolved** |
| **F3** | reportingRatio estimate evidence presumed reading | reportingRatio (and openMindedness-style) fallbacks take a mode-specific observation; estimate says *"…the outlets you picked…"*. | **Resolved** |
| **F4** | Trigger % (`round(sum)`) could differ from evidence %s (`round` each) by 1 | Trigger totals are now the **sum of the displayed rounded parts** (sourceDiversity, emotionalBalance), so trigger and parts always agree on screen. | **Resolved** |
| **F5** | Benefit asserted a guaranteed effect on a percentile metric | Benefits now use non-guaranteeing wording: *"Can broaden your Source Diversity"*, *"Can improve your …"*. | **Resolved** |
| O1 | blindSpots basis stores the (undisplayed) `gap` value | **Intentionally deferred** — the displayed evidence (topic names) reconstructs from the basis label; the gap is harmless traceability metadata. No behaviour or honesty impact. Documented, not changed. | **Deferred (justified)** |

## What did NOT change (hard constraints)

Metric calculations, weakest-metric **selection**, recommendation **ordering**, the static `impact`
values, and the API shape (`ImprovementModel` — the same optional fields). The `evidenceBasis` entries
are identical (same `field`/`label`/`value`); only the surrounding prose changed. No ledger, lifecycle,
feedback ranking, dynamic impact, or evaluation framework was introduced.

## How mode-awareness works

The `measured` flag was already passed into the binder (RC2.1); it now drives the *subject* of each
sentence while the numbers and basis stay put:

```
measured → "You've read 80% Topic 3 and 10% Topic 0."
estimate → "Based on the outlets you picked, about 80% of the available content is Topic 3 and 10% is Topic 0."
```

A grep-style guard test (`test_estimate_wording_never_implies_reading`) fails if any estimate trigger or
evidence contains "you've read" / "your reading" / "your political reading" / "of your reading".

## Before / after (live engine output)

**F1 — estimate mode (6 outlets, 0 reads)**
| Rule | Before (RC2.1 — false) | After (RC2.1.1) |
|---|---|---|
| viewpointBalance | "Your political reading is 17% left, 33% center, 50% right." | **"The outlets you picked lean 17% left, 33% center, 50% right."** |
| echoChamber | "About 50% of your political reading sits on one side." | **"The outlets you picked sit mostly on one side (about 50%)."** |
| topicDiversity | "You've read 80% Topic A…" | **"Based on the outlets you picked, about 80% of the available content is Topic A…"** |
| emotionalBalance | "41% of your reading leans on fear and outrage." | **"About 41% of the content in the outlets you picked leans on fear and outrage."** |

**Measured mode is unchanged** and still describes reading: *"40% of your reading came from Outlet 13
and Outlet 19."*, *"You've read 80% Topic 3 and 10% Topic 0."*

**F2 — left == right tie** (`viewpoint = {left:.4, center:.2, right:.4}`)
- *Before:* evidence "Your reading leans right" + action "Adding a couple of right-leaning reads…"
  (contradiction).
- *After:* evidence **"Your left and right reading are evenly split."** + action **"Adding
  cross-cutting reads would strengthen your viewpoint balance."**

**F4 — rounding** (two sources at 0.474 each)
- *Before:* trigger "95% …" while evidence showed "A (47%) and B (47%)" (95 ≠ 47+47).
- *After:* trigger **"94% …"** = **47 + 47**, matching the evidence exactly.

**F5 — benefit** (all rules)
- *Before:* "Broadens your Source Diversity." / "Improves your Viewpoint Balance." / "Raises…"
- *After:* **"Can broaden your Source Diversity."** / **"Can improve your Viewpoint Balance."**

## Validation

| Check | Result |
|---|---|
| `pytest tests/test_api_server.py` | **55 passed** (7 new RC2.1.1 tests) |
| `pytest api_server · api_fastapi · personalize · enrich` | **164 passed** |
| `pytest demo_determinism · demo_account` | **12 passed** |
| Web `tsc --noEmit` | **clean** |
| Web `node --test` | **96 passed** |
| `check:i18n` | **658 keys × 5 languages**, no unused keys |
| `next build` | **succeeds**; `/report` **376 kB** (unchanged) |
| Playwright `health-report.spec` (live engine + web) | **1/1 passed** |

**New regression tests (requirement 6):**
- `test_estimate_wording_never_implies_reading` — zero-read estimate, no reading-language in any card.
- `test_measured_wording_may_describe_reading_and_never_uses_outlet_framing` — measured describes
  reading and never borrows the estimate's "outlets you picked" framing.
- `test_reporting_ratio_estimate_wording` — reportingRatio estimate evidence references outlets, not
  reading.
- `test_viewpoint_left_right_tie_is_neutral` — tie wording is neutral and never names one side.
- `test_trigger_percentage_equals_sum_of_displayed_parts` — trigger total = sum of shown parts.
- `test_benefit_wording_is_non_guaranteeing` — every benefit starts with "Can …" (measured + estimate).
- `test_estimate_evidence_basis_still_traceable` — basis values still equal the exact report-field
  numbers after the wording changes.

## Findings status (summary)

**F1, F2, F3, F4, F5 — resolved and covered by tests.** **O1 — intentionally deferred** (traceability
metadata that is never displayed; no honesty or behaviour impact). Selection, ordering, impact, and API
compatibility are unchanged.

---

*RC2.1.1 is an honesty/wording fix only — no engine, selection, or contract change.*
