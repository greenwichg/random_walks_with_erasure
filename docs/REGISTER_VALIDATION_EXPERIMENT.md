# Register Validation Experiment — is `classify_register` safe for the recommendation pipeline?

> **Status:** experiment specification (documentation only — no code). It defines a **pre-registered,
> objective** test that decides whether the news-vs-opinion *register* signal (`classify_register.py`)
> is reliable enough to gate **within-outlet extremity** in the recommender (the "W3-Lite / register-
> gated extremity" track of `docs/W3_ROADMAP_REVISION.md`).
> **Decision it feeds:** the go/no-go on shipping register-gated extremity before production.
> **Companion docs:** `docs/W3_ROADMAP_REVISION.md` (why W3 changed), `docs/PRE_PRODUCTION_RECOMMENDATION_ROADMAP.md`
> (the R1 revision that deferred article-level lean), `docs/TODO.md` (the lean-axis investigation log).

---

## 1. Why article-level *lean* is no longer considered reliable enough for production

The repository has **exhaustively** measured text→article-level *ideology* and found it near-chance;
this is evidence, not opinion. The register question below exists precisely because lean failed.

| Signal (article-level, from text) | Result vs gold | Source |
|---|---|---|
| politicalBiasBERT, **headline**, Qbias/AllSides (in-distribution) | **κ = 0.007**, Spearman 0.02, ~20% acc, collapses to centre (2896/3000 → C) | `TODO.md:126–129` |
| politicalBiasBERT, **body 256 tok** | **κ = 0.001**, Spearman 0.065 | `TODO.md:129–133` |
| premsa (2nd model), **full body** — the best text result | **Spearman ~0.22**, side-only κ 0.30 | `TODO.md:136–139` |
| Two-BERT **agreement** (n=2,955), exact L/C/R | **Cohen's κ = 0.14** ("slight", 59% raw); side-only κ 0.575 | `lean_agreement.py`; `TODO.md:103–113` |
| **Ensemble** vs 40-item human gold | **no gain** (−0.05 vs single −0.09; n=40 underpowered) | `TODO.md:96–102` |
| **LLM** (Gemini 2.5 Flash, n=120), headline | **Spearman −0.28**, sign-acc 0.41 (negative) | `TODO.md:152–156` |
| text-lean vs human (headline classifier) | **~0.27 vs human** ("weak, model-sensitive proxy") | `PRODUCT_SIMULATION.md:79` |
| **Outlet lookup** vs AllSides gold (the winner) | **κ = 0.84 / side-only κ = 1.000 / Spearman 0.918** (~4× best text) | `TODO.md:140–141` |

**Conclusion (evidence):** article-level *lean* from text is not recoverable to production quality; the
**outlet registry** is ~4× better and remains the trusted anchor. The original W3 (confidence-gated
article-level lean via `classify_lean`) is therefore removed from the pre-production roadmap (R1).

### Why *register* is a *different* question — not the same failure

W3's motivating example ("a NYT sports piece and a NYT op-ed both `−1`") decomposes into two needs that
**do not require estimating lean**:

1. **Non-political content on the ideology axis** (sports) → a **political-mask** concern. RWE-B already
   admits *political-only* candidates (`api_server.py:1260`), so this is a `looks_political` completeness
   issue, not a lean issue.
2. **Opinion vs news within an outlet** (op-ed) → a **register** concern. `classify_register.py` is a
   **zero-shot NLI** *genre* classifier (news-report vs opinion/editorial), a categorically **different
   and generally easier task than ideology-from-text**.

*(Judgment, not evidence: genre is more learnable from text than ideology. Register's accuracy is
currently **unvalidated** — the docstring says "treat the score as approximate," and it is headline-
trained. That is exactly what this experiment resolves.)*

---

## 2. Objective

**Primary question (pre-registered):**
> On the **text modality the production pipeline actually feeds it**, does `classify_register` recover an
> article's true news-vs-opinion register accurately and reliably enough — **overall and within the
> outlets we bridge on** — to gate a within-outlet extremity adjustment without injecting harmful noise?

**The decision it produces:** a single, documented **GO / CONDITIONAL-GO / NO-GO** on the register-gated
extremity track (§W3-Lite of `W3_ROADMAP_REVISION.md`). GO ships it before production; NO-GO leaves
W3-Lite as story-level viewpoint + political-mask only (both un-gated and reliable).

**Non-goals:** this experiment does **not** attempt to rescue article-level lean, and it does **not**
by itself prove that register-gated extremity *improves* recommendations (that downstream question is a
separate check, noted in §Risks and in the roadmap's validation strategy).

---

## 3. Critical design constraint — match the production text modality

The lean failure was *partly* a headline-only limitation (premsa improved 0.08→0.22 with the body,
`TODO.md:136`). Therefore:

- **Test `classify_register` on the SAME text production will give it.** If production ingests full
  article text, test on full text; if it sees only titles/RSS excerpts, test on that. Record which.
- **Establish ground truth from full context** (byline, section, full article) — the *true* register is
  a property of the article. The gap between full-context truth and production-modality prediction is
  exactly what we are measuring.
- **Secondary arm (diagnostic):** also score the classifier on *full text* even if production uses
  excerpts, to learn whether modality is the limiter (mirrors the premsa body-vs-headline finding). This
  informs *how to deploy* register (e.g., "only where full text is available"), not the primary go/no-go.

---

## 4. Sampling methodology

**Population:** **political** articles from the production/beta corpus (register is only consumed on the
political slice that RWE-B bridges). Draw from the live ingested catalog, not MIND (MIND is publisher-
anonymised and headline-only — `TODO.md:161–184`).

**Why stratify:** opinion is the minority class in a news feed; a naïve random sample yields too few
op-eds to estimate opinion-class recall (the n=40 lean set was underpowered — `TODO.md:99–102`). We must
guarantee both classes are represented **without** letting the classifier pick its own test set.

**Strata (independent of the classifier — never stratify on the signal under test):**
1. **Register proxy (weak, independent):** an outlet's *own* section/URL/byline markers
   (`/opinion/`, `/editorial/`, "columnist", "op-ed") — a label the classifier never sees. Enrich to
   ~50/50 news vs opinion.
2. **Outlet lean:** left / center / right (registry) — so accuracy is measurable *within* each side.
3. **Outlet identity:** cover the top-N outlets we actually bridge on (accuracy must hold per-outlet).
4. **Topic:** spread across `classify_topic` categories (avoid an impeachment-style monoculture — the
   exact confound that sank the LLM lean run, `TODO.md:153–156`).

**Target size (power-driven):**
- **≥ 80 opinion + ≥ 80 news** via the enriched strata (≥ 160), **plus** a **~100-article natural-prior
  random** subset (for base-rate accuracy + calibration). **Total ≥ 200 (aim 240–260).**
- Rationale: ~80/class gives a 95% CI half-width ≈ ±0.09 on per-class accuracy at p≈0.8, and n≥150
  gives a usable CI on κ. This is the "not-underpowered" bar the lean work identified.

**Blinding:** raters see **only the article** (title + body + outlet), never the classifier's score, the
section proxy, or each other's labels. Randomise presentation order.

---

## 5. Human-rater protocol

**Raters:** **3** independent raters (2 minimum; 3 enables majority adjudication and a stronger
inter-rater estimate). Reuse the blind harness the lean work already built: `validate_lean.py
--sample … --raters r1 r2 r3` produces the stratified blind template and computes agreement
(`TODO.md:114–119`); the register variant swaps the 3-way lean label for a register label and swaps the
metrics (§6).

**Label schema (primary — binary):**
- **NEWS** — straight reporting: events/facts, sourced, no authorial thesis.
- **OPINION** — op-ed, editorial, column, or analysis advancing an authorial thesis/argument.

**Secondary (recorded, not required to pass): a 3-way** NEWS / ANALYSIS / OPINION, since "analysis"
is the fuzzy middle; collapse ANALYSIS→OPINION for the primary metric but keep it to diagnose the
boundary (this is the register analogue of the lean work's *centre-vs-lean* boundary problem,
`TODO.md:108`).

**Rubric (given to raters, with 3–5 worked examples per class):** decide on the article's **function**,
not its topic or its outlet's lean. A politically-charged *report* is NEWS; a calm *column* is OPINION.
When genuinely mixed, label by the dominant function and flag "mixed."

**Adjudication:** items where raters split are resolved by majority (3 raters) or a documented
tie-break rubric (2 raters). Keep the disagreement set — it defines the task's hard boundary.

---

## 6. Inter-rater agreement — the precondition gate (run this FIRST)

**If humans cannot agree on register, the task is ill-posed and the classifier cannot be blamed or
trusted.** So inter-rater agreement is computed and gated **before** the classifier is evaluated
(the same discipline the lean harness enforces — `TODO.md:117–118`).

- **Metric:** Fleiss' κ (3 raters) or Cohen's κ (2 raters) on the binary label; also % raw agreement.
- **Precondition thresholds:**
  - **κ_raters ≥ 0.60** ("substantial") → task well-defined; proceed to classifier evaluation.
  - **0.40 ≤ κ_raters < 0.60** → task is fuzzy; proceed **but** interpret classifier results only on
    the *high-consensus* subset (items all raters agree on), and cap ambitions accordingly.
  - **κ_raters < 0.40** → **task ill-posed → NO-GO** for register-gated extremity, independent of the
    classifier. (Fall back to story-level viewpoint + political-mask.)

---

## 7. Evaluation metrics (classifier vs human consensus)

Evaluated against the adjudicated human consensus label, on the production text modality (§3):

1. **Cohen's κ** (chance-corrected) — the headline number; directly comparable to the lean κ's above.
2. **Accuracy** and **per-class precision / recall / F1**, with **OPINION recall** called out (the
   product cares most about *catching opinion* to raise its extremity).
3. **ROC-AUC** of the continuous `P(reporting)` against the binary consensus — measures whether the
   *score* (not just the thresholded label) ranks articles correctly; the extremity gate will use the
   score, so its ranking quality matters.
4. **Calibration** of `P(reporting)` (reliability curve / ECE) — does 0.8 mean 0.8? The extremity
   adjustment is monotone in the score, so miscalibration (the exact politicalBiasBERT failure mode,
   `TODO.md:138`) directly distorts placement.
5. **Per-outlet and per-lean breakdown** — κ within each of the top-N bridged outlets and within
   left/center/right. The use case is *within-outlet*, so a good global κ with a bad within-outlet κ
   is a **fail in disguise**.
6. **Modality delta (secondary):** production-modality κ vs full-text κ — quantifies whether excerpts
   are the limiter (deploy-scope input, not a pass gate).

*(Optional cheap pre-screen: `llm_label.py` can provide a model-vs-model second opinion before spending
rater time — but it is **convergent validity, weaker than human gold** (`TODO.md:144–160`) and never
substitutes for the rater consensus.)*

---

## 8. Pass / fail thresholds (pre-registered)

Anchored to the repo's own scale: lean scored **κ ≈ 0.14** (fail); the outlet anchor scores **κ ≈
0.84** (strong). Register need not reach the outlet bar — it gates a *modest magnitude* adjustment — but
it must be clearly and reliably better than the failed lean signal.

| Outcome | Conditions (all must hold) | Meaning |
|---|---|---|
| **GO** | κ ≥ **0.60** **and** OPINION recall ≥ **0.70** **and** accuracy ≥ **0.80** **and** no top-N outlet below κ **0.40** **and** ROC-AUC ≥ **0.80** **and** calibration not grossly off (ECE ≤ **0.15**) | Substantial, reliable, within-outlet-valid → ship register-gated extremity |
| **CONDITIONAL-GO** | κ in **[0.40, 0.60)** with OPINION recall ≥ 0.70 and ROC-AUC ≥ 0.75 | Moderate → ship **only** as a **high-confidence, conservative** gate: adjust extremity **only** for scores in the extreme deciles (clear opinion), smaller max adjustment, and **only** in outlets that individually pass κ ≥ 0.6 |
| **NO-GO** | κ < **0.40**, or OPINION recall < 0.60, or a majority of top-N outlets below κ 0.40, or the precondition (§6) fails | Register-gated extremity is unsafe → W3-Lite = story-level viewpoint + political-mask only |

**Pre-registration rule:** these thresholds are fixed **before** the data is unblinded. No post-hoc
threshold tuning (the roadmap's determinism/honesty discipline applied to the experiment itself).

---

## 9. Go / no-go decision tree

```
 START
   │
   ▼
 §6 Inter-rater κ_raters ?
   ├─ < 0.40 ───────────────────────────► NO-GO  (task ill-posed; not the classifier's fault)
   ├─ 0.40–0.60 ─► evaluate on high-consensus subset only ─┐
   └─ ≥ 0.60 ─────► evaluate on full set ───────────────────┤
                                                            ▼
                                              §7 classifier vs consensus
                                                            │
                              ┌─────────────────────────────┼─────────────────────────────┐
                              ▼                             ▼                             ▼
                    κ ≥ 0.60 & recall ≥ .70        0.40 ≤ κ < 0.60               κ < 0.40  OR
                    & acc ≥ .80 & AUC ≥ .80         & recall ≥ .70 & AUC ≥ .75    recall < .60 OR
                    & every outlet κ ≥ .40          (else →)                      most outlets κ < .40
                    & ECE ≤ .15                          │                             │
                              │                          ▼                             ▼
                              ▼                 CONDITIONAL-GO                        NO-GO
                            GO             (high-confidence, per-outlet,        (story-level viewpoint
                    (ship register-gated    conservative extremity only)         + political-mask only;
                     extremity)                                                  register stays research)
```

**Every terminal node also records:** the numbers, the sample, the raters, the modality, and the
per-outlet table — so the decision is reproducible and auditable, not a vibe.

---

## 10. Expected risks

- **Opinion under-representation** → weak OPINION-recall estimate. *Mitigation:* the enriched 50/50
  strata (§4); report CIs, not point estimates.
- **Ill-posed task** (raters disagree on analysis-vs-opinion). *Mitigation:* the §6 precondition gate;
  the 3-way secondary label to isolate the boundary.
- **Modality mismatch** (excerpts too thin, like the lean headline problem). *Mitigation:* the §3
  secondary full-text arm quantifies it and scopes deployment.
- **Miscalibrated score** distorts a monotone adjustment. *Mitigation:* the calibration metric (§7.4)
  and, on CONDITIONAL-GO, restricting to extreme deciles.
- **Topic confound** (a monoculture sample flatters or sinks the classifier — the LLM-lean trap,
  `TODO.md:153`). *Mitigation:* topic stratification (§4.4).
- **Threshold gaming / over-fit to this sample.** *Mitigation:* pre-registration (§8); ideally a
  held-out confirmation slice.
- **"Passes classification but doesn't help recommendations."** Register accuracy is *necessary, not
  sufficient*. A passing gate authorises building the adjustment; a **separate downstream check**
  (rec_sandbox: does register-gated extremity change bridges defensibly, with explain↔served parity and
  contract-v1 held?) authorises defaulting it on. This is called out here so GO is not mistaken for
  "done."

---

## 11. Success criteria

The experiment **succeeds as an experiment** (regardless of GO/NO-GO) when it delivers:

1. A **pre-registered** spec (this document) fixed before unblinding.
2. A **blind, stratified, adequately-powered** labeled set (≥ 200; ≥ 80/class) on the **production text
   modality**, produced via the existing `validate_lean --raters` harness (register variant).
3. **Inter-rater agreement** reported first, gating task validity.
4. The **full §7 metric panel** — κ, accuracy, per-class F1, OPINION recall, ROC-AUC, calibration, and
   the **per-outlet / per-lean** breakdown — vs human consensus.
5. A single **GO / CONDITIONAL-GO / NO-GO** verdict from the §9 tree, with all numbers recorded.
6. A one-line feed into `docs/W3_ROADMAP_REVISION.md` updating the register-gated-extremity track's
   status.

**The experiment lets us decide objectively** whether register can safely enter the recommendation
pipeline — with a number on the same scale (κ) that condemned lean (0.14) and validated the outlet
anchor (0.84), and a pre-committed threshold that removes the decision from anyone's discretion.

---

## Appendix — metric definitions & harness reuse

- **Cohen's / Fleiss' κ:** chance-corrected agreement; `(p_o − p_e)/(1 − p_e)` (`lean_agreement.py:71`).
  1 = perfect, 0 = chance, <0 = worse than chance.
- **ROC-AUC:** P(a random opinion article scores lower `P(reporting)` than a random news article).
- **ECE (Expected Calibration Error):** mean |confidence − accuracy| across score bins.
- **Harness:** `validate_lean.py --sample N` (blind stratified template) + `--raters r1 r2 r3` (inter-
  rater + consensus validation), already unit-tested (`TODO.md:114–119`); the register variant substitutes
  a binary label and the §7 metrics. `llm_label.py` provides an optional convergent-validity pre-screen
  (weaker than human gold). No production code is touched to run this.
