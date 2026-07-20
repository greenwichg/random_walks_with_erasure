# RC2.1 — Personalized Recommendation Evidence Binding

Replaces the Health Report's generic recommendation text with **user-specific evidence bound from data
already in the report**, while leaving the deterministic recommendation *engine* untouched. This is the
first RC2 recommendation phase: **evidence binding only** — no impact estimation, no ledger, no
feedback-aware ranking, no lifecycle, no LLM.

## What changed (and, deliberately, what didn't)

**Unchanged (hard requirement):** metric calculations, weakest-metric selection, and recommendation
ordering. The 3 shown are still the reader's 3 lowest available non-`confidence` metrics by score, in
the same order; `title`, `detail`, and the fixed `impact` still come from the static `_IMPROVEMENTS`
table. A regression test re-derives the selection and impact independently to prove it.

**Added:** each improvement now carries four optional, user-specific fields plus a traceability record,
all bound from the *same* report the request already computed:

| Part | Field | Example (measured) |
|---|---|---|
| Trigger | `trigger` | "72% of your reading came from Outlet 13 and Outlet 19." |
| Evidence | `evidence` | "Outlet 13 (20%) and Outlet 19 (20%) account for most of your reading." |
| Suggested action | `suggestedAction` | "Reading from an outlet beyond Outlet 13 and Outlet 19 would widen your sources." |
| Expected benefit | `expectedBenefit` | "Broadens your Source Diversity." |
| Traceability | `evidenceBasis[]` | `[{field:"sources", label:"Outlet 13", value:0.20}, …]` |

The fields are **additive and optional**: absent on older payloads, and omitted (`exclude_none`)
whenever the report lacked grounded data — so the static `title`/`detail` still stand alone and every
existing consumer keeps working.

### Files
- `examples/api_server.py` — new pure helpers `_improvement_evidence(...)` / `_attach_evidence(...)`,
  wired into **both** improvement builders (measured `_serialize_report`, and the `estimate` path).
- `examples/api_fastapi.py` — `EvidenceBasisModel` + the five optional fields on `ImprovementModel`
  (so `response_model` passes them through instead of stripping them).
- `web/types/domain.ts` — optional fields + `EvidenceBasis` on the `Improvement` type.
- `web/components/report/report-widgets.tsx` — renders trigger/evidence/action when present, **falls
  back to `detail`** otherwise.
- `web/mock/data.ts` — dev mock enriched so local dev shows the new UI.
- `tests/test_api_server.py` — 6 new tests (present · traceable · deterministic · selection-unchanged ·
  honesty · estimate-no-false-concentration).

## Evidence sources — exactly which report field populates each recommendation

Nothing is fabricated: every number traces to a field the same report returns. No alternate outlet the
reader hasn't used is ever named (the catalog isn't in the report).

| Metric (rule) | Trigger / Evidence bound from | Traceability `field` |
|---|---|---|
| **sourceDiversity** | top-2 `sources[].share` + names (measured only) | `sources` |
| **topicDiversity** | top-2 `topics[].share` + `blindSpots[].topic` | `topics`, `blindSpots` |
| **viewpointBalance** | `viewpoint.{left,center,right}` | `viewpoint` |
| **echoChamber** | `viewpoint.{left,center,right}` (one-sidedness) | `viewpoint` |
| **emotionalBalance** | `attention.{fear,outrage,analysis}` | `attention` |
| **reportingRatio** | metric `score` vs `benchmark` (raw ratio isn't on the report) | `metric.score`, `metric.benchmark` |
| **openMindedness** | metric `score` vs `benchmark` (reception isn't on the report) | `metric.score`, `metric.benchmark` |

**Honesty rules baked in:**
- The concentration claim ("X% of your reading came from …") is made **only for a measured report**,
  where source shares are real. An estimate's source shares are equal-weighted, so its sourceDiversity
  evidence speaks to the *range* of picked outlets instead — never dressed up as a reading mix.
- The score-vs-benchmark trigger **never claims "below the typical reader" unless the score truly is
  below the benchmark.** Selection surfaces a reader's *lowest* metrics, which can still sit at/above
  the median — so a metric at 52 vs a benchmark of 50 reads *"above the typical reader's 50 but still
  among your lowest metrics,"* not a false "below." (This was a real bug caught during validation; it
  now has a dedicated regression test.)
- Score-fallback evidence is **definitional** ("This tracks how much of your reading is straight
  reporting rather than opinion."), never a directional assertion that could be false for the reader.

## API additions (backward-compatible)

```python
class EvidenceBasisModel(BaseModel):
    field: str          # e.g. "sources" | "topics" | "viewpoint" | "attention" | "metric.score"
    label: str          # e.g. "Reuters" | "left" | "Reporting Ratio"
    value: float        # the exact number used (0–1 share, score, or count)

class ImprovementModel(BaseModel):
    id: str; title: str; detail: str; metric: str; impact: int   # unchanged
    trigger: Optional[str] = None
    evidence: Optional[str] = None
    suggestedAction: Optional[str] = None
    expectedBenefit: Optional[str] = None
    evidenceBasis: Optional[list[EvidenceBasisModel]] = None
```

No field was renamed or removed; `response_model_exclude_none=True` keeps old-shaped payloads clean.

## Before / after — multiple reader profiles (live engine output)

**Source concentration (measured demo reader)**
- *Before:* "A few outlets dominate your diet. Two new sources meaningfully lift Source Diversity."
- *After:* **"40% of your reading came from Outlet 13 and Outlet 19."** → *"Outlet 13 (20%) and Outlet 19
  (20%) account for most of your reading."* → *"Reading from an outlet beyond Outlet 13 and Outlet 19
  would widen your sources."* → *"Broadens your Source Diversity."*

**Topic narrowness + blind spots (measured demo reader)**
- *Before:* "You circle a few topics. Deliberately reading an unfamiliar subject lifts Topic Diversity."
- *After:* **"You've read 80% Topic 3 and 10% Topic 0."** → *"Topic 5 and Topic 2 are underrepresented in
  your reading."* → *"Reading a Topic 5 piece would broaden your topics."*

**One-sided viewpoint (reader 0)**
- *Before:* "Your reading sits mostly on one side of the centre. Two opposite-but-close reads a week lift
  Viewpoint Balance the most."
- *After:* **"Your political reading is 7% left, 0% center, 93% right."** → *"Your reading leans right;
  the other side is thin."* → *"Adding a couple of left-leaning reads would balance your viewpoints."*

**Echo chamber (reader 0)**
- *Before:* "Your political reading is fairly one-sided. One good-faith opposite-side piece loosens the
  echo chamber."
- *After:* **"Your political reading is 7% left, 0% center, 93% right."** → *"About 93% of your political
  reading sits on one side."* → *"A good-faith opposite-side read loosens the echo chamber."*

**Charged emotional diet (reader 3)**
- *Before:* "A large share of your reading leans on fear and outrage. Swapping one for calm analysis
  raises Emotional Balance."
- *After:* **"41% of your reading leans on fear and outrage."** → *"Fear 20% and outrage 21%; analysis is
  31%."* → *"Swapping one charged read a day for calm analysis raises the balance."*

**Score-grounded, honest comparison (reportingRatio, demo reader — score 52 vs benchmark 50)**
- *Before:* "Opinion outweighs reporting in your diet. Pairing commentary with straight reporting raises
  the Reporting Ratio."
- *After:* **"Your Reporting Ratio is 52, above the typical reader's 50 but still among your lowest
  metrics."** → *"This tracks how much of your reading is straight reporting rather than opinion."* →
  *"Pair commentary with a straight-reporting source."*

**Estimate path (6 outlets — no false concentration)**
- sourceDiversity → **"Your estimate is based on 6 outlets."** → *"Add a couple of outlets outside your
  usual set."* (equal-weighted shares are never presented as a reading mix)
- viewpointBalance → **"Your political reading is 17% left, 33% center, 50% right."** → *"Adding a couple
  of left-leaning reads would balance your viewpoints."*

## Validation results

| Check | Result |
|---|---|
| `pytest tests/test_api_server.py` | **48 passed** (6 new evidence tests) |
| `pytest test_api_fastapi · test_personalize · test_enrich` | **109 passed** |
| Web `tsc --noEmit` | **clean** |
| Web `node --test` | **96 passed** |
| `check:i18n` | **658 keys × 5 languages**, no unused keys |
| `next build` | **succeeds**; `/report` **376 kB** First-Load JS (vs ~373 kB baseline → no regression) |
| Playwright `health-report.spec` (real engine + web) | **1/1 passed** — measured & estimate render live |

**Requirement-specific verification**
- **Deterministic output** — `test_improvement_evidence_is_deterministic`: two calls produce
  byte-identical improvements (evidence included). No randomness, no clock, no LLM.
- **Identical recommendation selection** — `test_improvements_selection_and_impact_unchanged`:
  the metric list/order and static impacts match an independent re-derivation of the current rule.
- **No fabrication** — `test_improvement_evidence_is_traceable_to_report_fields`: every `evidenceBasis`
  value equals the exact number in the report field it names; plus the honesty guard against false
  "below typical" claims.
- **Existing behavior intact** — `test_report_contract` (superset check) still passes; the frontend
  falls back to `detail` when evidence is absent; the E2E journey is green.

## Out of scope (later RC2 phases — deliberately not implemented)

Impact estimation (the `+N` stays the existing fixed constant), recommendation persistence / ledger,
feedback-aware ranking, lifecycle tracking, and the evaluation framework. Backend-side localisation of
the new prose is also deferred — like the pre-existing `title`/`detail`/blind-spot `note`, the bound
evidence is backend English for now (tracked with the broader recommendation-localisation work).

---

*RC2.1 delivers evidence binding only. Selection, ordering, and impact are unchanged.*
