# Pre-Production Recommendation Roadmap — W1 · W2 · W3 · W8

> **Status:** canonical implementation plan for the four remaining major recommendation-engine
> improvements before production. **This is a design document — it prescribes no code and changes
> no behaviour.**
> **Scope:** W1 (openness control), W2 (adaptive exposure), W3 (article-level ideology),
> W8 (collaborative warm-start). W4, W5 Tier 0, W6 Tier A, W7 remain as implemented today.
> **Reads with:** `docs/RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md` (the W1–W8 / I1–I11 review),
> `docs/RECOMMENDATION_ENGINE_STATUS.md` (shipped status), `docs/W1_OPENNESS_SLIDER_AUDIT.md`
> (the ε-inertness deep-dive), `docs/QBIAS_VALIDATION.md` (real-corpus activation).
> **Invariants honoured throughout:** REPORT CONTRACT v1 (schema/byte-identity — golden *values*
> shift where feeds change, the *schema* never does), determinism, explain↔served parity, and the
> product philosophy: **transparent, explainable, viewpoint-aware, user-steerable** recommendations.

## Revision history

- **R1 (2026-07-15) — W3 pulled from the pre-production critical path.** Review surfaced that the
  text-lean classifier is **not accurate enough at the article level** to serve as a confidence-gated
  adjustment. The repo already documented this: two BERT bias models agree at **Cohen's κ = 0.14 exact
  (L/C/R)**, 0.575 side-only (`lean_agreement.py`; `docs/HEALTH_REPORT_PLAN.md:158`); text-lean is
  **~0.27 vs human** (`docs/PRODUCT_SIMULATION.md:79`) — a "**weak, model-sensitive proxy**"
  (`docs/TODO.md:139`) the repo had **already chosen to replace with an outlet-first axis**
  (`docs/TODO.md:204`). Crucially, `classify_lean` is **not in the production path** (production lean is
  the outlet registry only — `examples/ingest.py:6,410,429`), so **no current behaviour is affected** —
  only the proposed W3 and W8's classifier-lean *fidelity bonus*. **Effect:** W3 is **deferred /
  redesigned** (outlet-first — see the R1 banner in §W3); the recommended order becomes **W1 → W2 →
  W8**; W8 proceeds using **outlet lean** for its lean component (its behavioural `fit_ideology` core
  never depended on the classifier). The original W3 proposal is retained below, superseded and
  annotated, for decision-history integrity.

- **R2 (2026-07-16) — W1 and W2 are shipped; both leave this roadmap.** W1 re-mapped openness onto
  the RWE-B **bridge-slot budget** (I1; commit `8be5e55`) and W2 wired the measured, κ-shrunk
  cross-cutting reception into `AdaptiveRWEB` (I8 wiring; commit `3f29c7f`). The remaining W2 scope —
  the dosing-policy *retune* — is traffic-gated, as §W2's analysis argued. **Effect:** of the
  recommended order **W1 → W2 → W8**, only **W8** remains, unchanged in status (paused pending real
  traffic — `docs/W8_FINAL_ARCHITECTURE_DECISION.md`). §W1/§W2 below are retained as the design
  record (see the R2 banners).

## Reading guide & conventions

- **Run from the repo root.** All algorithms live in `rwe/`; orchestration/serving in
  `examples/api_server.py`; the evaluation engine in `examples/rec_sandbox.py`.
- **"Preserves contracts"** means: REPORT CONTRACT v1 schema unchanged, determinism held, and the
  explain observer (`rec_explain`) stays byte-parity with the served feed. Golden *values* may shift
  wherever a feed legitimately changes; those goldens are re-pinned as part of the change.
- **Algo vs orchestration.** We flag whether each change touches an RWE *algorithm* (`rwe/*.py`) or
  only *orchestration / preprocessing* (`examples/*.py`). We do **not** swap in a generic
  neural/LLM ranker: where we add learning it is the repo's own QBias-validated classifier
  (`classify_lean.py`) or ideal-point model (`fit_ideology`), keeping every input interpretable.

## The four at a glance

| W | Weakness | Class | Preferred fix | Touches algo? | Complexity | Blocked by |
|---|---|---|---|:--:|:--:|:--:|
| **W1** | Openness (ε) control is inert on the served feed | Defect (dead control) | Re-map openness to **bridge-slot budget + `max_distance`** (I1) | orchestration + 1 algo input | Low–Med | — |
| **W2** | Adaptive dosing uses a constant `exposure=0.5` | Incomplete feature | **Prior + online per-user exposure**, wired into `AdaptiveRWEB` (I8) | input wiring | Med | **W1** |
| **W3** ⚠️ | Ideology is outlet-level & coarse | Design limitation | **DEFERRED (R1)** — classifier too weak at article level (κ 0.14); stay **outlet-first**, revisit behind a validated signal | preprocessing (positions) | — (deferred) | validated lean signal |
| **W8** | Collaborative base is synthetic | Stage-of-product | **Transfer warm-start** from real public behaviour (MIND) + content/KG edges, decaying under real reads (I11) | data + positions | High | — (uses outlet lean) |

---

# W1 — Openness control is inert on the served feed

> **R2 (2026-07-16): SHIPPED** as proposed (openness → bridge-slot budget, commit `8be5e55`). This
> section is retained as the design record — see the revision history.

## 1. Current implementation

**How it works today.** The onboarding/settings "political openness" slider (0–100, default 50) is
mapped to the RWE-B non-bridge erasure value ε:

```
examples/api_server.py:350   _OPENNESS_EPSILON = (0.70, 0.90, 0.97)   # slider 0 / 50 / 100 → ε
examples/api_server.py:373   params["epsilon"] = _piecewise(s["politicalOpenness"], *_OPENNESS_EPSILON)
examples/api_server.py:1213  if strategy == "rwe-b" and "epsilon" in params: RWEB(..., epsilon=...)
```

ε is consumed **only** by RWE-B. In `rwe/random_walk.py:285` (`RWEB._compute`), the erasure matrix is
`Q = ε` for every item, overwritten with `Q[bridge] = sim(u,i)` for bridge items
(`is_bridge`, line 276: opposite side of `center`, within `max_distance` which is **`None`/unbounded**
in production). The score (`BaseRecommender._score_batch`, line 60) is
`retained / (1 − c)`, where `retained = p·(1 − Q)` and `(1 − c)` is a **per-user scalar**.

**Why it exists.** ε is the paper's knob for "how strongly to suppress same-side content," so mapping
"openness" → ε was the intuitive wiring. Slider 50 maps to ε = 0.90, the stack's historical default,
so the default feed is unchanged.

## 2. Root cause

**Why it is inert.** Two independent mechanisms cancel ε on the *visible* feed (proven in
`docs/W1_OPENNESS_SLIDER_AUDIT.md`):

1. **Math (centered readers).** A reader near `center` has *no* opposite-side items → no bridges →
   `Q = ε` uniformly → `retained = p·(1 − ε)` is a **uniform scale** of `p`; the per-user denominator
   cancels it under `argsort`. ε cannot reorder anything.
2. **Slice logic (sided readers).** For a sided reader, `_slice_select` (`api_server.py:1237`) takes
   **cross-cutting items first** (`(cross + same)[:k]`, line 1253). The visible bridge slice is chosen
   by the *crossCutting fact*, not by score, so ε's within-slice reordering never surfaces.

**Evidence.** The ε 0.2 vs 0.9 sweep produced `identical=True` served feeds for every reader profile
(W1 audit + `RECOMMENDATION_ENGINE_STATUS.md:173`). The design review lists this as W1
(`RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md:55`).

## 3. Production impact

- **Recommendation quality:** none directly — the feed is what it is; the *control* over it is dead.
- **Information Health:** the product's central promise (user agency over exposure) is unfulfilled; a
  user who wants more cross-cutting content cannot get it.
- **User trust & explainability:** **this is the sharpest cost.** A visible control that does nothing
  is a false affordance — precisely the kind of dishonesty this product exists to oppose.
- **Correctness / UX / maintainability:** UX/trust defect. Not a crash; not a maintainability issue.

## 4. Proposed solution — re-map openness to a *visible* lever (I1)

Map "openness" to two quantities the served feed actually reflects:

- **(a) Bridge-slot budget** in the blend plan. Today `DEFAULT_BLEND_PLAN = (("rwe-b",6),("rwe-d",4),
  ("adaptive",4))` (`api_server.py:154`). Openness shifts slots **to/from `rwe-b`** (e.g. high → 8/3/3,
  low → 4/5/5). More bridge slots = more cross-cutting items on screen.
- **(b) RWE-B `max_distance`** (`rwe/random_walk.py:246`, currently `None`). Openness widens/narrows how
  ideologically far a bridge may be: high openness → larger `max_distance` (reach further across),
  low → tighter (gentler steps).

```
 slider "openness"  ─┬─►  bridge-slot budget  ──►  blend plan  ─► how MANY cross items appear
                     └─►  RWE-B max_distance   ──►  is_bridge()  ─► how FAR across they reach
        (replaces the inert ε mapping)
```

**Data flow.** `politicalOpenness` → `_openness_plan(slider)` returning `(blend_plan, max_distance)` →
`_build_recommenders` builds RWE-B with that `max_distance`; the serve site consumes `blend_plan`
instead of `DEFAULT_BLEND_PLAN` (`api_server.py:1372`). No RWE math changes — `max_distance` is an
existing RWEB input; slot budget is pure orchestration.

**Why this over alternatives.** It changes *quantities the feed exposes* (count and reach of bridges),
which are (i) visible, (ii) monotone in openness, and (iii) trivially explainable.

## 5. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Keep ε mapping, fix the slice** (make ε reorder within the cross-first slice) | Even reordered, ε can't add bridges a centered reader doesn't have; fragile; still confusing to explain. |
| **I2 — Honest slider UI** ("this control can't move *your* feed right now") | Zero-risk and worth shipping as a *fallback message* when no bridges exist, but it abandons agency rather than delivering it. Adopt as a **companion**, not the fix. |
| **Openness → RWE-B `beta`/strength** | Conflates diversity-reach with popularity erasure (RWE-D's job); muddies two orthogonal levers. |

## 6. Risks

- **Engineering:** re-opens the W5 blend-plan surface and its parity goldens (`test_explain_matches_
  the_served_feed`) — mechanical re-pin. Low.
- **Recommendation-quality:** a very wide `max_distance` could surface *too-far* bridges (jarring
  jumps). Mitigate: bound the high end; keep slider 50 at today's behaviour.
- **Explainability:** *positive* — the control becomes honest and narratable.
- **Performance:** negligible (`max_distance` is a mask; slot budget is allocation).
- **Production:** default (50) must reproduce today's feed exactly (regression guard).

## 7. Validation strategy

- **rec_sandbox scenarios:** sweep openness ∈ {0, 50, 100} for a **centered** reader, a **left**
  reader, and a **right** reader; assert the served feed's cross-cutting count is **monotone
  non-decreasing** in openness and that slider 50 == today (`--compare` diff empty at 50).
- **Regression:** full recommendation regression suite (contract-v1 invariants) green; explain↔served
  parity holds at every slider value; determinism (same input → same feed).
- **Manual:** in the app, move the slider 0→100 for a sided demo reader; confirm the visible number of
  cross-cutting cards rises.
- **Before/after metrics:** cross-cutting card count and mean bridge |lean gap| vs openness; the W1
  audit's `identical=True` must become `identical=False` off the default.

## 8. Rollout strategy

Independent of W3/W8. **Do first** — it is low-risk *and* **unblocks W2**. Ship the honest-fallback
message (I2) in the same change for the no-bridges case.

## 9. Future work

Openness could later modulate RWE-D discovery too (breadth of topics), but that is a policy extension,
not part of closing W1.

---

# W2 — Adaptive dosing uses a constant (`exposure = 0.5`)

> **R2 (2026-07-16): WIRING SHIPPED** (measured shrunk reception → `AdaptiveRWEB`, commit `3f29c7f`);
> the dosing-policy retune remains traffic-gated. Retained as the design record — see the revision
> history.

## 1. Current implementation

`AdaptiveRWEB(fg, theta, item_pos, exposure)` is built at `api_server.py:635`. `exposure` is a
**per-user array** that becomes RWE-B's **per-user ε** (RWEB accepts a per-user ε vector,
`rwe/random_walk.py:238`, `264`), i.e. per-user cross-cutting dosing. But in production it is a
constant:

```
examples/api_server.py:624  exposure = np.full(len(rec_dataset.user_ids), 0.5)   # neutral default
examples/api_server.py:625  if probe_csv and os.path.exists(probe_csv):          # only in sim/eval
examples/api_server.py:628      exposure, _ = asf.measured_exposure(...)          # measured reception
```

A production reader has no probe CSV → everyone gets `0.5`. The serializer already tells the truth
("measured exposure isn't wired into AdaptiveRWEB yet", `api_server.py:1296`).

## 2. Root cause

`AdaptiveRWEB` is designed to *dose* cross-cutting content by a **measured per-user cross-cutting
reception** signal (how well the user receives challenging pieces). That signal is produced by the
simulator's satisfaction probe (`adaptive_satisfaction.py`) but is **not wired to any real signal**.
So "adaptive" is inert (`RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md:56`, `api_server.py:613`).

**Coupling to W1:** even a correct `exposure` is an RWE-B ε, so it is subject to the **same cross-first
slice** that neutralises ε (W1). Until W1's slice/lever is fixed, correct dosing moves the served feed
less than expected (`RECOMMENDATION_ENGINE_STATUS.md:201`). **W2 is therefore gated behind W1.**

## 3. Production impact

- **Recommendation quality:** every user is dosed identically; no personalization of challenge level.
- **Information Health:** Open-Mindedness dosing — the health loop's point — is not actually adaptive.
- **Trust & explainability:** the serializer is honest today, so no *dishonesty*, but a promised
  capability is absent.
- **Correctness/UX/maintainability:** incomplete feature; no defect.

## 4. Proposed solution — principled prior + online per-user exposure (I8)

`exposure(u) = shrink( prior(u), measured(u), n_u )`, blending a **prior** with the user's **own**
accumulating reception, weighted by how much we've observed (`n_u` = the user's cross-cutting reads):

```
 pre-production PRIOR                      online SIGNAL (from read #1)
   ├─ onboarding calibration               dwell / save / share on the
   └─ MIND population reception   ──┐        user's OWN cross-cutting reads
      (from W8's fit_ideology)      │              │
                                    ▼              ▼
                         exposure(u) = (n_u·measured(u) + κ·prior(u)) / (n_u + κ)
                                    │
                                    ▼
                    AdaptiveRWEB per-user ε   (api_server.py:635)
```

**Data flow.** A new `exposure_provider(user)` replaces the `np.full(..., 0.5)` default: it reads the
user's cross-cutting reception from the store (reusing the same save/share/dwell signals the health
report already consumes) and shrinks toward the prior. `measured_exposure` (`adaptive_satisfaction.py`)
is reused verbatim for the measured term. No RWE math changes — only the array feeding `AdaptiveRWEB`.

**Why this approach.** It degrades gracefully (a decent prior beats `0.5` immediately), self-corrects
per user with no cold "empty" state, and needs no aggregate production traffic — only the user's own
reads plus a prior we can source offline (crucially, from **W8's MIND fit** — a real-behaviour
reception distribution, not a guess).

## 5. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Leave `0.5`; ship after launch** | The prior + online estimator are fully buildable now; waiting forgoes real value and couples the launch to a data-collection lag. |
| **Global measured exposure only (no prior)** | Cold until a user has many cross reads; the prior removes the cold window. |
| **Ask the user directly each session** | High-friction; contradicts "let behaviour speak." Keep onboarding as a *seed* to the prior only. |

## 6. Risks

- **Engineering:** store read-path for per-user reception; shrinkage tuning (`κ`). Med.
- **Recommendation-quality:** a mis-calibrated prior over/under-doses early; bound `exposure` to a safe
  band and let `n_u` wash the prior out.
- **Explainability:** must state provenance — "default dose until we learn you," then "based on how you
  received N challenging pieces." Preserves philosophy if labelled.
- **Performance:** one store aggregate per user per feed build; cache with the augmented model.
- **Production:** **do after W1** or the dosing is partly masked by the slice (measure the W1/W2
  interaction explicitly).

## 7. Validation strategy

- **rec_sandbox:** synthesize users with high vs low cross-cutting reception; assert `exposure`
  diverges and the served cross-cutting proportion tracks it (monotone). Confirm a zero-history user
  gets the prior, not a crash.
- **Regression:** contract-v1 suite green; determinism (same reads → same exposure); explain↔served
  parity with the per-user ε in play.
- **Manual:** two demo readers with opposite reception histories → visibly different dosing.
- **Before/after:** per-user cross-cutting proportion vs measured reception (should correlate after,
  be flat at 0.5 before).

## 8. Rollout strategy

**After W1.** Independent of W3. **Consumes W8's MIND prior** if W8's offline fit is available (source
the population reception term there) — so sequence W8's *offline prototype* early even though W8 lands
last.

## 9. Future work

Deferred: multi-signal reception (reply/▲ as in the probe), and decaying exposure toward neutrality
during long inactivity. Not needed to close W2.

---

# W3 — Ideology is outlet-level and coarse

> ### ⚠️ REVISED (R1, 2026-07-15) — W3 is DEFERRED, not scheduled before production
>
> **Canonical W3 doc:** `docs/W3_ROADMAP_REVISION.md` supersedes this section (W3-Core deferred +
> W3-Lite tracks); `docs/REGISTER_VALIDATION_EXPERIMENT.md` is the gate for register-gated extremity.
>
> **What changed.** The preferred fix below (confidence-gated article-level lean via `classify_lean`)
> assumed the classifier is accurate enough at the article level. **It is not**, and the repo already
> documented this:
> - two BERT bias models agree at **Cohen's κ = 0.14 exact (L/C/R)**, 0.575 side-only
>   (`lean_agreement.py`; `docs/HEALTH_REPORT_PLAN.md:158`) — near-chance on the full label, and the
>   disagreement is **centre-vs-lean** (`docs/TODO.md:108`), *exactly* the NYT-news-vs-NYT-op-ed
>   distinction W3 needed;
> - **~0.27 vs human** (`docs/PRODUCT_SIMULATION.md:79`); "**weak, model-sensitive proxy**"
>   (`docs/TODO.md:139`); the repo had **already chosen outlet-first** (`docs/TODO.md:204`).
>
> **Why the design fails.** (a) The classifier is weakest precisely where W3 needed it (centre-vs-lean).
> (b) `confidence` = top-2 softmax margin (`classify_lean.py:62`) measures **self-certainty, not
> accuracy** — a confidently-wrong article gets the *largest* adjustment, injecting error where you trust
> it most. That confidence signal is validated only for **aggregate** down-weighting in the health report
> (`docs/HEALTH_REPORT_PLAN.md:154–158`), never for per-article point decisions like bridge status.
>
> **Scope of impact.** `classify_lean` is **not in the production path** — production lean is the outlet
> registry only (`examples/ingest.py:6,410,429`), so **no current behaviour is affected.** Only this
> proposal and W8's classifier-lean *fidelity bonus* are.
>
> **Revised direction — outlet-first (do no harm):**
> 1. **Keep outlet-registry lean as the sole production lean signal.** Trusted, AllSides-validated,
>    explainable; do not degrade the anchor with an unreliable classifier.
> 2. **If article-level nuance is wanted, gate on *register* (news vs opinion), not lean magnitude —
>    and only after `classify_register` clears its own agreement/human validation** (the finding concerns
>    *lean*; register accuracy is currently **unproven**). Register can raise extremity within an outlet
>    ("an opinion piece reads more strongly than the outlet's news piece") without needing an accurate
>    per-article lean *position*, and stays explainable.
> 3. **Treat a trustworthy article-level lean as a research prerequisite** (ensemble via
>    `ensemble_lean.py`, LLM labelling via `llm_label.py` with agreement thresholds, a human-labelled
>    calibration set), **not a pre-production deliverable**.
> 4. **Confine the existing classifier to its validated use** — aggregate, confidence-down-weighted
>    health-report inputs — never per-article bridge decisions.
>
> **Order impact:** W3 leaves the critical path; the sequence is now **W1 → W2 → W8** (§Conclusion). W8
> is **unaffected at its core** (its `fit_ideology` warm-start is behavioural, not text-lean) and uses
> **outlet lean** for its lean component.
>
> _The original proposal is preserved below (superseded) for decision-history integrity._

## 1. Current implementation

Item positions used by RWE-B/Adaptive come from **outlet house lean**: `_build_recommenders` calls
`mind.recommender_inputs()` (`api_server.py:617`), which returns `item_positions` = the per-outlet lean
(`rwe/mind.py:366`). Every article from an outlet inherits that one lean. User `theta` is the
read-weighted mean of item positions (`user_positions_from_clicks`, `rwe/mind.py:286`) or a learned fit.

**Why it exists.** Outlet lean is a trusted, low-variance anchor (AllSides/registry, ~55 outlets),
cheap and explainable. It was the right v1 signal.

## 2. Root cause

One lean per outlet is coarse: "a NYT sports piece and a NYT op-ed both `−1`"
(`RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md:57`). This mis-places non-political and opinion content on
the ideology axis, which (a) mis-labels bridges, (b) skews the health report's viewpoint mix, and
(c) makes explanations wrong for those articles. `QBIAS_VALIDATION.md:79` records the coarseness
("corpus article leans are coarse … vs the reads' registry leans").

**Key finding that reclassifies this:** ~~the hard artifact **already exists and is validated.**~~
**[CORRECTED — R1]** `classify_lean.py` exists, but this claim was wrong: only its *confidence* signal is
validated (for **aggregate** down-weighting), while its **article-level positions are not accurate**
(κ 0.14 exact; ~0.27 vs human — see the R1 banner). So W3 is **not** the low-risk integration claimed
here; it is deferred behind a validated article-level signal.

## 3. Production impact

- **Recommendation quality:** better bridge selection (real cross-cutting, not outlet-inherited) and
  fewer non-political items mis-treated as ideological.
- **Information Health:** more accurate Viewpoint Balance / Echo Chamber (the metrics read item lean).
- **Trust & explainability:** explanations become *truer* — "this **opinion** piece reads strongly
  left (conf 0.82)" instead of "NYT ⇒ left."
- **Correctness:** a correctness improvement in the ideology signal; large golden **value** shift.

## 4. Proposed solution — confidence-gated article-level lean around the outlet prior (I10)

Hierarchical shrinkage: keep the trusted outlet lean as the **anchor**, let a **confident** per-article
classifier move the estimate; low-confidence articles stay at the outlet prior.

```
 article  ─►  classify_lean  ─►  (classifier_lean, confidence)
 outlet   ─►  registry lean  ─►  outlet_prior
                                   │
      article_lean = outlet_prior + confidence · (classifier_lean − outlet_prior)
      (+ register: opinion widens the adjustment, news narrows it)
                                   │
                                   ▼
             item_positions  ─►  recommender_inputs  ─►  RWE-B / Adaptive / health report
```

**Data flow.** A batch `positions-csv` (already the `ingest_mind.py --positions-csv` contract) supplies
`(position, confidence)` per article; a new position-assembly step blends it with the registry lean
before `recommender_inputs`. **No RWE algorithm changes** — item positions are an *input*; this is
preprocessing.

**Why this approach.** It directly answers the I4 objection ("don't inject model error into the trusted
signal") by making the classifier an **adjustment**, never a replacement — the outlet prior dominates
exactly where the classifier is unsure. It also **shrinks W4**: unknown-outlet articles that a text
classifier *can* place get a finite position instead of being dropped at
`recommender_inputs` (`rwe/mind.py:381`).

## 5. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **I4 — classifier *replaces* outlet lean for unknown outlets** | Rejected in the design review: replaces a trusted anchor with model output. Our shrinkage keeps the anchor. |
| **Pure article-level (drop outlet prior)** | Discards a low-variance, trusted signal; raises variance on short/ambiguous text. |
| **Multi-dimensional article embedding as the position** | Powerful but breaks the interpretable 1-D lean the whole product explains on; deferred to future work (see §W8/Future). |

## 6. Risks

- **Engineering:** offline classification pass (GPU/Colab per `classify_lean.py`); position-assembly +
  confidence gating; **large golden re-pin** wherever leans move. Med.
- **Recommendation-quality:** high-confidence-but-wrong articles mis-bridge; bound the max adjustment
  and require a confidence floor.
- **Explainability:** must surface provenance (anchor + adjustment + confidence) or a moved lean looks
  arbitrary. Net *positive* if surfaced (`rec_explain` already carries a confidence field).
- **Performance:** classification is offline/batch; serving cost unchanged.
- **Production:** re-classify on ingest; stale positions if the classifier version changes (version the
  positions CSV).

## 7. Validation strategy

- **rec_sandbox:** an outlet with a mixed diet (news + op-ed) — assert the op-ed's lean moves and the
  sports piece's does not; a bridge that was outlet-inherited flips correctly.
- **Regression:** contract-v1 suite green (schema); re-pin golden values; determinism (fixed positions
  CSV → fixed feed); parity holds.
- **Manual:** health report viewpoint mix before/after on a known reader; explanation text shows the
  article-level lean + confidence.
- **Before/after:** distribution of |article lean − outlet lean|; count of items rescued from the NaN
  drop (W4 interaction); bridge-set churn.

## 8. Rollout strategy

**[SUPERSEDED — R1: W3 is deferred; it no longer precedes W8, which uses outlet lean.]** ~~Independent of
W1/W2; parallelizable with W1. Precedes W8 (article-level lean improves the content mapping).~~ When
revisited behind a validated signal, ship behind a flag and validate the golden shift before default-on.

## 9. Future work

Multi-dimensional viewpoint (topic-framing, epistemic, geographic) is the real ceiling on the diversity
mission — deferred as its own research track; it must stay interpretable to preserve explainability.

---

# W8 — Collaborative base is synthetic

## 1. Current implementation

The collaborative graph is built from **simulated users** (`simulate_users.py`): synthetic agents read
over a **real** article catalog, events are collapsed to a binary click matrix, and
`FeedbackGraph(rec_dataset.matrix)` (`api_server.py:618`) builds the bipartite walk substrate
(`rwe/graph.py`, `binarize=True`, line 47). The walk `p = item_distribution(user_ids, k=3)`
(`graph.py:118`) is **ideology-free**; ideology enters only at the erasure step (RWE-B/RWE-D `Q`).

The repo **already** has the real-behaviour machinery, pointed at evaluation rather than production:
`fit_ideology` (`rwe/mind.py:322`) learns latent 1-D user/item positions from **real click logs** via
an ideal-point model (`ingest_mind.py:98`, tested in `test_mind.py:118`); `QBIAS_VALIDATION.md:83`
explicitly names MIND as "a real click-log population."

## 2. Root cause

Synthetic collaboration is not real collaboration: the co-read edges reflect the simulator's utility
model, not real readers (`RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md:62`,
`RECOMMENDATION_ENGINE_STATUS.md:220`). **But** "real collaboration" was conflated with "*your* users on
*your* catalog." It separates into (i) *how real humans co-read news* — available now from public
datasets (MIND) — and (ii) *your community's tastes* — genuinely launch-gated.

**Not the rejected idea.** This is **not** simulating trajectories from our own "open-minded reader"
policy (circular — you rediscover your prior). It is **transfer of real human behaviour** from an
external population — standard transfer learning, no circularity.

## 3. Production impact

- **Recommendation quality:** today's collaborative relevance is only as good as the simulator; a
  real-behaviour warm-start grounds RWE-B/RWE-D in genuine co-reading.
- **Information Health:** real cross-cutting co-reads make the bridges the product surfaces *earned*,
  not manufactured.
- **Trust & explainability:** the walk substrate becomes an *opaque* transfer prior unless labelled —
  the one place philosophy is at risk (see Risks).
- **Correctness/UX:** correctness of the collaborative signal; also the lever that most reduces the
  **item cold-start** blindness (a fresh, zero-read article has no walk mass today).

## 4. Proposed solution — real-behaviour warm-start that decays under real reads (I11)

Three layers, each grounded in real signal or interpretable content, feeding the *existing* RWE walk:

```
 (a) MIND real clicks ─► fit_ideology ─► real-behaviour item/user positions
                                          + real co-read structure
                          │  (content map: OUTLET lean, topic, entities, register)  [R1: outlet, not classifier]
                          ▼
 (b) content edges  ── lean-proximity + shared-topic  ──►┐
 (c) event KG       ── same real event, diff outlet   ──►│  seed FeedbackGraph
                                                          ▼   edges (a PRIOR)
                                    RWE-B / RWE-D walk (unchanged, explainable ranker)
                                                          ▲
              real reads accumulate ──► prior DECAYS ─────┘  (Bayesian overwrite)
```

- **(a)** Run `fit_ideology` on MIND's real clicks → real-behaviour positions; map onto the catalog by
  content (**outlet** lean [R1: article-level lean deferred — W3], topic, entities). Because
  `fit_ideology(orient_by_lean=True)` yields an **interpretable 1-D** position (`rwe/mind.py:355`) and is
  learned from **click behaviour, not text**, the transferred signal is independent of the classifier and
  stays on the lean axis, not a black box.
- **(b)** Content item–item edges from attributes we already compute — a graph with **no users**.
- **(c)** A news-event knowledge graph linking same-event articles across outlets — the most
  **defensible, explainable** cross-cutting bridge.
- **Decay:** the transferred edges are a prior that real reads progressively overwrite after launch.

**Optional companion — two-stage retrieval for cold-start.** The same content/transfer embeddings can
index *every* article (including zero-read fresh ones) for a retrieval stage, with the RWE walk as the
**explainable ranker** over retrieved candidates. This fixes fresh-item blindness *before* launch while
keeping the transparent RWE core. Scoped as a follow-on, not required to close W8.

**Why this approach.** It supplies the real, non-circular co-read structure the walk needs before a
single user exists, reuses machinery we've already built and tested (`fit_ideology`, MIND ingest, the
outlet registry), and converges to your population as reads arrive. **(R1: it does not depend on
`classify_lean` — the behavioural `fit_ideology` axis is the validated primary, `lean_corr 0.57±0.19`.)**

## 5. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Simulated reading trajectories from our own policy** | Circular (distills then rediscovers our prior); manufactured diversity; see the W8 trajectory audit. |
| **Wait for real traffic (status quo)** | Forgoes a real-behaviour bootstrap available now; couples launch quality to a cold graph. |
| **Content-only graph (no MIND)** | Viable and interpretable, but misses real co-read structure MIND provides; keep as layer (b), not the whole answer. |
| **Generic neural news encoder as the ranker** | Breaks explainability; we keep RWE as the ranker and use encoders (if any) only for retrieval. |

## 6. Risks

- **Engineering:** transfer + content-mapping pipeline; decay schedule; optional retrieval index. High.
- **Recommendation-quality:** **domain shift** (MIND ≈ 2019 US MSN news) — transfer at robust levels
  (lean/category transition priors) over brittle article-level edges; let live reads correct it.
- **Explainability:** the transfer prior is the risk. Mitigate by (i) keeping the RWE walk as the
  explainable ranker, (ii) using **interpretable** edges (shared event, lean proximity) for the
  human-facing "why," (iii) **labelling provenance** ("bootstrapped from public real-behaviour data
  until your community's reads take over"), (iv) exploiting the lean-oriented `fit_ideology` axis.
- **Performance:** `fit_ideology` is dense `O(users×items)` — filter first (`political_subset`,
  min-clicks) as the code already enforces (`rwe/mind.py:340`). Offline, one-time.
- **Production:** **licensing** — MIND is a *research*-licensed dataset; using it to *learn/validate a
  transferable prior* offline is one thing, shipping it in a commercial product may be restricted.
  **Clear the licence before any production use;** if research-only, use MIND to prove/pre-train and
  source a permissively-licensed or licensed population for production bootstrap.

## 7. Validation strategy

- **rec_sandbox / offline:** compare three warm-start graphs on the **same** catalog — (A) current sim
  baseline, (B) content-only, (C) MIND-transfer — on catalog coverage, popularity concentration (Gini),
  cross-cutting edge rate, and RWE output diversity.
- **The decisive test — homogenization loop:** iterate *graph → recommend → feed recs back as next
  reads → rebuild*; plot diversity vs iteration for A/B/C. Transfer must **not** collapse faster than
  baseline (a live version of the W8 collapse test).
- **Regression:** contract-v1 suite green; determinism (fixed seed/inputs → fixed graph); explain↔served
  parity with provenance labels present.
- **Manual:** trace one recommendation to its seed edge and confirm the provenance label is truthful.
- **Before/after:** time-to-first-recommendation for a fresh article (cold-start), and warm-user
  neighbourhood realism vs the sim.

## 8. Rollout strategy

**Largest; land last, but prototype the offline fit EARLY** — it is the long pole, it de-risks the
biggest claim, and it **produces W2's population prior**. ~~Benefits from W3~~ **(R1: W3 deferred — W8
uses outlet lean; its `fit_ideology` core never depended on the classifier, so nothing here is blocked).**
Independent of W1. Keep it entirely offline until the homogenization test passes; never touch serving or
goldens during the spike.

## 9. Future work

Deferred: full two-stage retrieval productionization; multi-dimensional transferred positions; periodic
re-fit as the catalog drifts. Intentionally out of scope: replacing RWE with a learned ranker (would
break the explainability philosophy).

---

# Decision History

### W1 — Openness control
- **Originally believed:** openness → ε was the natural wiring; the control worked.
- **Discovered:** ε is inert on the *served* feed for **every** reader profile — not just "bridge-rich"
  ones as first hypothesized.
- **Experiments proved:** ε 0.2 vs 0.9 → `identical=True` served feeds (W1 audit); traced to uniform-scale
  cancellation (centered) + cross-first slice (sided).
- **Final direction:** re-map openness to bridge-slot budget + `max_distance` (I1), with an honest
  fallback message (I2) when no bridges exist. **Deferred once for prioritization — it is pure
  engineering and should now be built first.**

### W2 — Adaptive exposure
- **Originally believed:** "traffic-gated" — needs real production reception data.
- **Discovered:** conflated "needs a per-user signal" with "needs *your* traffic." Onboarding, an online
  per-user estimator (from read #1), and a MIND population prior are all available pre-launch.
- **Experiments proved:** the serializer already exposes the neutral-exposure truth; W1's inertness
  proof showed W2 is *partly blocked by W1*.
- **Final direction:** prior + online per-user exposure (I8), built after W1, sourcing its prior from
  W8's MIND fit.

### W3 — Article-level ideology
- **Originally believed:** "major modeling research," high effort/risk (I10).
- **Then believed (roadmap v0 — since corrected):** the validated artifact already exists
  (`classify_lean.py` + `classify_register.py`), so W3 is integration, not research.
- **Discovered (R1):** that was wrong. `classify_lean`'s article-level positions are **weak** — κ 0.14
  exact / 0.575 side-only (`lean_agreement.py`), ~0.27 vs human (`PRODUCT_SIMULATION.md:79`); its
  `confidence` measures **self-certainty, not accuracy**, and is validated only for aggregate
  down-weighting. The repo had **already chosen outlet-first** (`TODO.md:204`), and `classify_lean` is
  **not in production** (`ingest.py:429`).
- **Experiments proved:** the two BERT bias models disagree at chance-corrected κ 0.14 on the exact
  L/C/R label, worst precisely at the centre-vs-lean boundary W3 targeted (`TODO.md:108`).
- **Final direction:** **deferred.** Keep outlet-first; revisit article-level nuance only behind a
  validated signal (register — pending its own validation — or an ensemble/LLM/human-calibrated lean).
  W3 leaves the pre-production critical path.

### W8 — Collaborative base
- **Originally believed:** "fundamentally requires real production traffic."
- **Discovered:** too strong — it conflated *any real reading behaviour* (MIND, already ingested and
  `fit_ideology`-able) with *your users on your catalog*. Only the latter is launch-gated. Distinct from
  the (rejected) simulated-trajectory idea, which is circular.
- **Experiments proved:** the earlier W8 trajectory audit established the circularity/manufactured-
  diversity failure of self-authored trajectories; `fit_ideology` on real click logs is tested and works
  (`test_mind.py`).
- **Final direction:** real-behaviour transfer warm-start (MIND) + content/KG edges, decaying under real
  reads, with provenance labelling to preserve explainability; a two-stage retrieval companion to close
  cold-start.

---

# Conclusion

## Recommended implementation order

```
   ┌─────────────┐
   │  W1 (first) │   openness — pure orchestration, unblocks W2
   └──────┬──────┘
          │ unblocks
          ▼
   ┌─────────────┐         ┌───────────────────────────┐
   │  W2          │◄────────│  W8 offline fit (spike early)
   │  exposure    │ prior   │  → land last (largest)     │
   └─────────────┘         └───────────────────────────┘

   W3 — DEFERRED (R1): off the critical path until a validated article-level lean
        signal exists; production stays outlet-first. Not a dependency of W8.
```

1. **W1** — first: low-risk, honest control, and it **unblocks W2**.
2. **W2** — after W1; sources its prior from W8's offline fit.
3. **W8** — prototype the offline MIND fit **early** (it de-risks the biggest claim and feeds W2), land
   the warm-start **last**; keep offline until the homogenization test passes. Uses **outlet lean**.
4. **W3** — **deferred (R1):** not before production; revisit behind a validated article-level lean
   signal (§W3). Production stays outlet-first.

**Independent:** W1 ⟂ (W8 offline fit). **Dependencies:** W2 ← W1 (and ← W8 prior). **W3 is deferred —
no longer a dependency of W8.**

## Estimated implementation complexity

| W | Complexity | Dominant cost |
|---|---|---|
| W1 | **Low–Med** | re-pin blend/parity goldens |
| W2 | **Med** | per-user reception read-path + shrinkage; gated by W1 |
| W3 | **Deferred (R1)** | blocked on a validated article-level lean signal; not costed pre-production |
| W8 | **High** | transfer + content-mapping (outlet lean) + decay + homogenization validation (+ optional retrieval) |

## Expected impact (qualitative — validate the magnitudes with the §7 tests)

| Dimension | W1 | W2 | W3 (deferred) | W8 |
|---|:--:|:--:|:--:|:--:|
| **Recommendation quality** | ○ (control, not feed) | ▲ dosing | — (deferred) | ▲▲▲ real collaboration + cold-start |
| **Information Health** | ▲▲ real agency | ▲▲ adaptive Open-Mindedness | — (outlet granularity retained) | ▲▲ earned (not manufactured) diversity |
| **Explainability** | ▲▲ honest control | ▲ dosing rationale | ○ honest by *not* asserting unreliable leans | ▽ needs provenance labels to *hold* |
| **Production readiness** | ▲ feature-complete control | ▲ personalization live | — (deferred) | ▲▲▲ real substrate + cold-start warm-start |

*(○ neutral · ▲ positive · ▲▲/▲▲▲ larger · ▽ at-risk-without-mitigation · — deferred)*

- **Recommendation quality:** dominated by **W8** (real collaborative signal); **W3 deferred (R1)** — no
  accuracy gain is available from the current classifier.
- **Information Health:** **W1 + W2** restore real, adaptive agency; viewpoint-metric accuracy stays at
  **outlet granularity** (W3 deferred).
- **Explainability:** **W1 improves** it; **W8 preserves** it *only if* provenance is labelled — the one
  place to hold the line. Outlet-first W3 keeps explanations honest by *not* asserting unreliable
  per-article leans.
- **Production readiness:** **W8** is the largest step (real substrate, cold-start warm-start), with
  **W1 + W2** making the surface feature-complete.

## Standing constraints (all four)

REPORT CONTRACT v1 schema is preserved throughout (golden *values* shift where feeds legitimately
change; schema/invariants hold); determinism and explain↔served parity are validated on every change;
and the transparent, viewpoint-aware, user-steerable philosophy is the acceptance bar — most sharply
for W8, where the transfer prior must be labelled, ranked by the explainable RWE walk, and bridged by
interpretable (event/outlet-lean) edges. **Hard gate:** clear the **MIND licence** before any production
use (W8). **W3's classifier gate is now moot (R1)** — W3 is deferred, production lean stays outlet-first,
and any future article-level signal must clear article-level validation *before* it is trusted (the
current classifier's κ 0.14 does not).
