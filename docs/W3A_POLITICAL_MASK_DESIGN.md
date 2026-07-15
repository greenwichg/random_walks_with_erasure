# W3A — Political Mask Design (Documentation Only)

**Status:** Design / audit only. No production code changed. W3A replaces the crude
`looks_political` substring heuristic with a derivation from the **already-computed, curated**
`classify_topic` taxonomy — no new signal, no new metadata, no network, no LLM.

**Premise:** the political mask is a **product-wide shared definition** (`ingest.py:68` — "ONE
definition product-wide… the Information Health metrics, the cross-cutting gate, and the bridge
explanations can never disagree about what 'political' means"). Sharpening it is high-leverage
but touches every consumer, so it is a *deliberate, tested behaviour change* — not byte-identical.

---

## 1. How `looks_political` works today

`examples/ingest.py:65-74`:

```python
_POLITICAL_HINTS = ("politic", "election", "/opinion")          # URL path
_POLITICAL_CATEGORY_HINTS = ("politic", "election", "opinion")  # category

def looks_political(url="", category="") -> bool:
    path = urlsplit(url).path.lower() if url else ""
    cat = (category or "").lower()
    return (any(h in path for h in _POLITICAL_HINTS)
            or any(h in cat for h in _POLITICAL_CATEGORY_HINTS))
```

It is a **raw substring test** over the URL path and the category string. It never reads the
**title or description**, and it uses **no word boundaries**. At the scorer (`ingest.py:407-409`)
it is applied even though the canonical topic has *already* been computed one line above
(`category = classify_topic(url, source_category, title, description)`, `ingest.py:404`). It is
also called from `simulate_users.py:201`, `migrate_topics.py:45`, and the corpus loaders.

The mask feeds: the cross-cutting gate `_cross_of(user_side, lean, political)`
(`api_server.py:157`), the Information Health report's political/cross-cutting/Open-Mindedness
computations (`health_report.py` `_political_positions:428`, `_eligible_pool:802`,
`compute:190` `min_political`), and the bridge/viewpoint explanations (`rec_explain.py:82`).

---

## 2. False positives (flagged political, but not)

The substring test has no word boundaries and conflates *opinion* with *politics*, so it
over-fires across every non-political topic. **Empirically confirmed:**
`looks_political(category="selection") → True`.

| Topic | False-positive driver | Example |
|---|---|---|
| **Sports** | `"election"` ⊂ **"selection"**; `"opinion"` catches sports columns | "Team **selection** for the final"; a `/opinion/` match preview |
| **Business** | `"election"` ⊂ "stock **selection**"; `"opinion"` catches market commentary | "Portfolio **selection** strategy"; a business `/opinion/` op-ed |
| **Technology** | `"opinion"` catches tech op-eds; `/opinion/` path | "Why the new phone disappoints — **opinion**" |
| **Entertainment** | `"election"` ⊂ "cast **selection**"; `"opinion"` catches reviews | "**Selection** of Oscar nominees"; a film **opinion** review |
| **Science** | `"election"` ⊂ "natural **selection**" | "Natural **selection** in finches" |

`classify_topic` gets all of these right — `classify_topic(title="Team selection announced")`
→ `""`; `classify_topic(title="Natural selection in finches")` → `""` — because `_rx`
(`ingest.py:175-178`) wraps every lexicon term in `\b(?:…)\b` ("law never fires on lawn"), and
Opinion is its own taxonomy topic, not a synonym for Politics.

---

## 3. False negatives (political, but not flagged)

`looks_political` misses any political story whose URL/category lacks the literal substrings
`politic` / `election` / `opinion`, because it never consults the title and does not know the
institutional vocabulary. **Empirically confirmed:** `looks_political(category="congress") → False`
while `classify_topic(title="Congress passes spending bill") → "Politics"`.

- **Institutional categories** that `classify_topic._CATEGORY_ALIASES` maps to Politics but the
  substring test misses: `congress`, `white house`, `supreme court`, `government`, `policy`,
  `justice`, `diplomacy`, `geopolitics` (`ingest.py:98-103`).
- **Title-only political stories** filed under a **geographic/generic** section (`/us-news/`,
  `/world/`, category "National", "Top Stories") whose headline names an institution or figure
  (`trump`, `biden`, `congress`, `senate`, `immigration`, `sanctions`, `tariffs`, `impeachment`)
  — the rich `_TOPIC_LEXICON` Politics block (`ingest.py:188-217`) catches these on the **title**,
  which `looks_political` never reads.
- **Non-US politics** without the literal token: `parliament`, `downing street`, `kremlin`,
  `brexit`, `nato`, `ceasefire` — all in the lexicon, none containing `politic`/`election`.

---

## 4. Existing signals that could improve it (no new metadata)

All already in the repository, deterministic, no network:

1. **`classify_topic`** (`ingest.py:290`) — THE canonical classifier. Resolves in confidence
   order: source category aliases → topical URL section → subject lexicon over title then
   description → geographic URL. Precision-first (`ingest.py:185` "a wrong hit would be a lie").
2. **`_CATEGORY_ALIASES`** (`ingest.py:95-149`) — curated label→topic map incl. the institutional
   Politics vocabulary the substring test misses.
3. **`_TOPIC_LEXICON` Politics block** (`ingest.py:188-217`) — ~90 word-boundary patterns
   (figures, institutions, offices, elections/process, courts, parties, diplomacy/policy).
4. **`_lexicon_topic(text)`** (`ingest.py:265`) — run the lexicon over arbitrary text (e.g. an
   Opinion headline) to recover political op-eds.
5. **The `Opinion` taxonomy topic** — lets political vs non-political opinion be separated instead
   of conflated.
6. **Stored topic** — `classify_topic`'s output is persisted per article (the C3 migration
   `migrate_topics.py` already reclassifies stored topics), so the political flag can be re-derived
   from existing rows with no re-ingest.

---

## 5. Can W3A be implemented entirely with existing metadata? **Yes.**

At the scorer (`ingest.py:404-405`) the article's canonical topic is **already computed** by
`classify_topic(url, source_category, title, description)` before the political flag is set.
Deriving the mask from that topic is a **zero-new-signal, zero-extra-computation** change: no new
classifier, no title re-parse, no network, no LLM, no schema field. For already-stored rows the
persisted topic is sufficient (re-derive via a one-shot migration, mirroring C3's
`migrate_topics.py`).

---

## 6. Smallest production-safe implementation

**Core (Option B — one function, all callers inherit, preserves the "ONE definition" invariant):**
redefine `looks_political` to delegate to the canonical classifier.

```python
def looks_political(url="", category="") -> bool:
    return classify_topic(url=url, source_category=category) == "Politics"
```

This alone fixes both confirmed bugs (`"selection"` → not political; `"congress"` → Politics) and
every category-alias false negative, while every existing caller
(`simulate_users`, `migrate_topics`, corpus loaders) inherits the fix unchanged.

**Recommended core + recall (Option A — derive at the scorer, where the title is available),**
because the scorer already holds title/description and already computed the full topic:

```python
# ingest.py Scorer.score, replacing the looks_political(...) call:
political = (raw.political if raw.political is not None
             else (category == "Politics"                       # category already = classify_topic(...)
                   or (category == "Opinion"                    # keep political op-eds…
                       and _lexicon_topic(raw.title) == "Politics")))  # …drop sports/other op-eds
```

The `Opinion` clause is the one deliberate nuance: a political op-ed stays political; a sports or
entertainment op-ed no longer does. Both options reuse only existing functions.

**Rollout (production-safe):**
1. Land Option B (shared function) + the scorer's Opinion refinement.
2. One-shot **migration** to re-derive `political` on stored articles/reads from their stored topic
   (reuse the `migrate_topics.py` pattern; idempotent; read-only elsewhere).
3. Optional **feature flag / shadow**: compute both masks, log the per-article delta and the
   resulting cross-cutting-count shift, before flipping the definition — so the metric change is
   observed, not surprising.

No serving-contract or schema change is required; `political` is an existing field.

---

## 7. Regression tests required

- **Golden FP/FN table** (new, `tests/test_ingest.py` / `tests/test_topic_classifier.py`):
  `selection`/`natural selection`/`stock selection`/`cast selection` → **not** political;
  `congress`/`white house`/`supreme court`/`government`/`policy`/`brexit`/`nato` → political;
  sports/business/entertainment **opinion** → **not** political; a political **op-ed** → political;
  a Biden/Congress headline under `/us-news/` → political.
- **Update existing `looks_political` assertions** that encode the old substring behaviour (the
  mask semantics change on purpose — re-baseline them, do not silently keep them).
- **Health-report deltas** (`tests/test_narrate_report.py`, metric pipeline): assert the *intended*
  direction — political subset gets cleaner; Open-Mindedness / cross-cutting counts move only on
  articles whose mask actually flipped.
- **W1 regression** (`tests/test_api_server.py` / `test_api_fastapi.py`): the served `crossCutting`
  count and the openness→bridge-budget mapping still hold (the budget mechanism is unchanged; only
  the eligible-item set sharpens).
- **W2 regression** (`tests/test_adaptive_exposure.py`): the three invariants (rate-not-count,
  adaptive-slice-only, ε-floor) are mask-independent and must stay green; `shownCross`/`openedCross`
  reflect the new mask.
- **Migration test**: idempotent re-derivation; touches only the `political` field; read-only for
  reads/settings/tokens.
- **REPORT CONTRACT v1** (`tests/test_rec_sandbox.py`): byte-identity will **not** hold for readers
  whose feed contains reclassified articles — document the intended delta and re-record the goldens;
  new/no-signal readers stay identical.

---

## 8. Information Health metrics that should improve

- **Open-Mindedness** (cross-cutting reception): the numerator/denominator stop counting
  sports-opinion and "selection" noise as political cross-cutting → the metric measures *real*
  political viewpoint-crossing.
- **Viewpoint diversity / political balance** (`health_report._political_positions`,
  `_eligible_pool` `min_political`): computed over a **cleaner political subset** — fewer spurious
  members, more recovered institutional-politics members.
- **Cross-cutting / bridge classification** (`_cross_of`): bridges are political opposite-side
  items, not mislabelled sports columns.
- **Reporting Ratio / Confidence on political items**: denoised denominator.

All improvements are *precision/recall of the mask*, not new metrics.

---

## 9. Does W3A affect W1, W2, or W8?

**There are two independent political detectors — W3A targets only the product one.**
`ingest.looks_political` (product; URL+category) is distinct from `rwe.mind._is_political`
(`rwe/mind.py:232`; MIND subcategory+title, used by `political_subset`). W3A changes the **product**
mask only.

- **W1 (openness → bridge budget):** the bridge/cross-cutting *classification* consumes the mask
  via `_cross_of`. W1's **mechanism is untouched** (the 4/6/8 slot budget, `blend_plan_for`), but
  the **set of bridge-eligible items sharpens** → regression the served `crossCutting` count and
  the openness mapping. Expected direction: fewer spurious bridges, more genuinely-political ones.
- **W2 (adaptive exposure from measured reception):** `shownCross`/`openedCross` count cross-cutting
  recs via the mask (`_cross_of`). A cleaner mask ⇒ a **cleaner reception signal** ⇒ exposure is
  computed from real political cross-cutting. Mechanism (κ shrinkage, gate, ε-floor) unchanged; the
  three W2 invariants are mask-independent and stay green. Input distribution shifts — worth a
  before/after on a measured reader.
- **W8:**
  - **MIND research path (W8A prototype, `fit_ideology`)** — uses the **click matrix** (no mask) and,
    for `--political-only`, the **separate** `rwe.mind._is_political`. **Unaffected by W3A.**
  - **Production behavioral graph (W8B / Stage C)** — built from real reads, whose `political` flag
    comes from the **product** mask. So the future production graph and its `--political-only`
    diagnostics **benefit** from W3A (cleaner political subset for the axis-proxy / ideological
    metrics). No change to graph *structure* (edges are clicks), only to which items are labelled
    political.

---

## Architecture & risks

- **Single-definition invariant preserved.** Option B keeps `looks_political` as the one shared
  entry point; Option A additionally enriches the scorer where the title exists. Both keep the
  product from developing two disagreeing notions of "political."
- **Deliberate behaviour change, not byte-identity.** The mask *will* move for real articles;
  regression is about the **intended direction**, the migration, and the untouched *mechanisms* of
  W1/W2/W8 — not sameness.
- **Precision-first, matching `classify_topic`'s stance** (`ingest.py:185`): prefer a missed edge
  case (degrades to non-political — honest) over a wrong political label (a lie). The Opinion clause
  is the only recall add, and it is gated by the same precision lexicon.
- **Risk — losing pure-opinion breadth:** conflating all opinion as political inflated recall; some
  genuinely political opinion filed with no political vocabulary in the headline could now be
  missed. Mitigation: the `_lexicon_topic(title)` Opinion clause; measured via the shadow delta.
- **Risk — corpus/simulator drift:** `simulate_users` and the loaders inherit the new definition;
  the synthetic corpus's political share will shift. Mitigation: re-baseline the sim goldens.

---

## Decision

W3A is implementable **entirely from existing metadata**, at **near-zero cost** (the topic is
already computed and stored), and it fixes **empirically confirmed** false positives ("selection")
and false negatives ("congress"). Recommend proceeding with **Option B + the scorer Opinion
refinement + a one-shot migration**, staged behind a shadow-delta measurement, with the regression
suite in §7 as the gate. No serving, schema, or REPORT-CONTRACT structure changes; W1/W2/W8
*mechanisms* are untouched — only the shared political label they consume becomes more accurate.

*Documentation only. No code was modified.*
