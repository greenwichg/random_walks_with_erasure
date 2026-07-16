# W8 Data Feasibility — Proof, not a survey

**Question posed:** does there exist *any* legally obtainable approach that validates W8 — *learning
ideological positions from real user reading behaviour* — as close as possible to our production
architecture? Challenge every blocker; build pipelines from partial datasets; reject only with proof.

**Verdict (up front):** **Qualified YES.** W8's *mechanism* can be validated today on real behaviour,
legally and reproducibly. What genuinely **cannot** be obtained from any public dataset — and this is
provable by exhaustion — is the platonic artifact: *self-reported* user ideology co-located with
*production-identical passive reading* in one corpus. That specific thing requires a paid panel or your
own instrumented traffic. So the honest answer is neither "trivially yes" nor "no solution exists": it
is a bounded solution with one named, proven residual. Details, and the A/B/C/D picks, below.

---

## 0. What W8 actually requires (reduction, from the code — not assumed)

The production estimator is `rwe.IdeologyModel.fit(R, S, anchor)` — a 1-D spatial (ideal-point)
logistic model that places **users and items on one shared latent scale** from a user×item feedback
matrix `R` (`rwe/ideology.py:87-231`; `FeedbackGraph` consumes the same binary `A`, `rwe/graph.py:47`).
Item ideology enters as `item_positions ∈ [-2,2]` (`rwe/mind.py:259`) via one of three code paths:
an **outlet-lean join** (`source_map` → `load_lean_table`), a **co-click fit** (`fit_ideology`, which
also reports `lean_corr` against any known lean), or a **text classifier** (`set_item_positions` from
`classify_lean.py`).

So "validate W8" decomposes into **two distinct claims**, and conflating them is the mistake that makes
the problem look unsolvable:

- **Claim P (predictive / structural):** reading behaviour yields a latent user axis that *aligns with
  item ideological lean* and *predicts held-out cross-cutting reading*. Needs: user×article matrix +
  **item** ideology labels.
- **Claim I (interpretive / external):** that axis *is the users' real political ideology*. Needs the
  above **plus external USER ideology** to correlate against.

Every candidate below is judged on which claim it can carry. The five entities the brief names map
cleanly: **user, article, interaction** = the matrix `R`; **outlet** = the `source_map`;
**ideological orientation** = `item_positions` (Claim P) and, for Claim I, an external user label.

**The minimal sufficient dataset** is therefore: *a bipartite (real user × real news article)
implicit-feedback matrix, where articles carry an outlet with an ideological label, and — for Claim I —
users carry an independent ideology signal.* Hold that specification; every rejection below is a proof
that a source cannot supply it, and every acceptance is a construction that can.

---

## 1. Classify the raw material (so combinations are principled, not random)

| Class | Sources | What it supplies | What it can NEVER supply alone |
|---|---|---|---|
| **A. Interaction-bearing** (user × news-article) | **MIND**, Adressa, EB-NeRD, Globo/G1, Plista, **Reddit (Pushshift)** | user, article, interaction | — (this is the scarce class) |
| **B. Item + outlet + lean** (no users) | GDELT, Event Registry, Media Cloud, Common Crawl / CC-NEWS, NewsAPI, RSS archives, AllSides/MBFC/Ad Fontes, Baly bias corpus, SemEval hyperpartisan | outlet, item-lean label | **user, interaction** |
| **C. Reconstruction bridges** | Internet Archive/Wayback, Wikidata, headline↔domain join, MIND WikidataId ⋈ GDELT GKG entities | recover a missing join (article→outlet) | any entity by itself |
| **D. User-ideology oracles** | ANES/CES (survey), Reddit partisan-sub membership, *(Bakshy/FB, Barberá/Twitter — see §3)* | external **user** ideology | article-level interactions |

**Immediate structural facts (proven, not opinion):**
- Class **B** has **zero users and zero interactions** → it can *never* fit a user ideal point → it is
  only ever an *ingredient* (outlet identity + lean), never a W8 solution. This disposes of GDELT, Event
  Registry, Media Cloud, Common Crawl, NewsAPI, RSS archives, Wikidata, and the article-bias corpora as
  standalone answers. They re-enter only as the label/reconstruction layer for a Class-A matrix.
- Class **A** is the whole game, and it is small. Publicly, only **MIND-family** and **Reddit** provide
  user×news-article interaction at scale. Everything reduces to: *can we ideologically label the items,
  and can we externally label the users, of one of these two?*

---

## 2. Pipelines built from combinations — each adjudicated on the six axes

Axes: **(1) legal (2) reproducible (3) sustainable (4) five entities (5) engineering (6) production-closeness.**

### P0 — MIND alone + co-click `fit_ideology` (the current state)
Fits users+items on a latent axis from clicks only. **(4)** user✓ article✓ interaction✓ outlet✗
ideology = *latent, unlabelled*. **Fatal flaw (proof):** `fit_ideology`'s own `lean_corr`
(`rwe/mind.py:348-357`) needs *some* known lean to confirm the axis is ideological — MIND supplies
none, so the axis is uninterpretable. P0 can *run* but cannot *validate* (Claim P fails for want of a
ground-truth to correlate to). This is the real blocker, stated precisely. **Rejected as a validation.**

### Pα — MIND ⋈ reconstructed outlet ⋈ AllSides/MBFC lean  *(production-like)*
Reconstruct the missing `source_map` (news-id → outlet) that the code already expects
(`rwe/mind.py:18`, `build_source_map.py`) by **joining MIND article (title, timestamp, WikidataId
entities) to a domain-labelled news corpus** (Media Cloud, GDELT, or CC-NEWS from MIND's Oct–Nov 2019
window), then domain → lean.
- **(1) Legal:** MIND = Microsoft Research License (research ✓, commercial/redistribution ✗); Media
  Cloud/GDELT/CC-NEWS = open/research; lean from MBFC/AllSides (use an openly-licensed or
  academically-redistributed table, as `outlet_lean.csv` already advises — *"replace with official
  AllSides or MBFC and cite"*, `rwe/mind.py:34-38`). **Green for research; not for shipping a trained
  model.**
- **(2) Reproducible:** MIND is a fixed download; CC-NEWS/GDELT are immutable archives; the join is
  deterministic. ✓ (the one soft spot is Media Cloud API availability — mitigate with the CC-NEWS/GDELT
  archival route).
- **(3) Sustainable:** it is a *one-time offline validation*, so "sustainable" = re-runnable → ✓.
- **(4) Five entities:** ALL present — outlet + ideology are the reconstructed layer.
- **(5) Engineering:** **High.** Fuzzy headline/date/entity join + coverage evaluation + lean table
  curation. The novel lever: MIND ships **WikidataId** title/abstract entities and GDELT GKG **also**
  tags Wikidata/entities → joining on *shared entities + date + fuzzy title* raises match precision well
  above naive string match.
- **(6) Production-closeness:** **Highest.** Passive news clicks, US outlets, the *exact* production
  data shape, driving the real code path (`recommender_inputs` → `IdeologyModel.fit`; or
  `set_item_positions`).
- **Carries:** **Claim P** fully (item axis vs outlet lean; user axis validated *predictively* on
  held-out reading). **Not Claim I** (MIND user IDs are anonymised and linkable to nothing — proven dead
  end for external user ideology).
- **Residual risk (honest):** join **coverage/precision is unmeasured**. The repo notes "no public
  [MSN→publisher] mapping exists" — true for *direct URL resolution*, but the *headline+entity+date
  join* is a different, untested route. **Go/no-go = a spike that measures match rate on political MIND
  items; commit only if coverage clears a preset bar (e.g. ≥50% of clicked political items).**

### Pα′ — MIND + `classify_lean.py` text lean (no outlet)  *(cheap item labels)*
Label each MIND item's lean from title+abstract directly (`set_item_positions`). **Proven weak:** the
repo's *own* measurement is that two bias classifiers agree at **Cohen's κ = 0.14**
(`classify_lean.py:11`, `lean_agreement.py`) — barely above chance. Validating an ideal-point axis
against κ=0.14 labels proves almost nothing. **Rejected as primary; accept only as a same-day sanity
pre-check** before the Pα join.

### Pβ — Reddit (Pushshift) news-link submissions/comments ⋈ domain→lean ⋈ partisan-sub user proxy  *(research, dual-axis)*
Reframe the Politosphere blocker. Its premise — "items are subreddits, not articles" — only bites if you
use *subreddits* as items. **Use the link submissions as the item layer instead:** a submission/comment
on a news URL is a *user × article* interaction whose article carries a *real outlet domain* → lean;
and the same user carries an *independent* ideology signal from **which partisan subreddits they are
active in** (Politosphere's subreddit lean, or membership in r/Conservative vs r/democrats, etc.).
- **(1) Legal:** public content; Pushshift academic dumps are the standard research corpus (on
  archive.org / Academic Torrents). Reddit's post-2023 Data API terms restrict *commercial* use and
  redistribution → **research-defensible, product-risky.**
- **(2) Reproducible:** dumps are static archives → ✓ (soft spot: continued hosting; mirrored widely).
- **(3) Sustainable:** offline → ✓; a *live* Reddit feed is not (API cost/ToS) — irrelevant to validation.
- **(4) Five entities:** user✓ article✓(submitted URL) interaction✓(submit/comment) outlet✓(domain)
  ideology: **item ✓ AND user ✓** — the *only* public pipeline that supplies the user label.
- **(5) Engineering:** **High** (TB-scale Pushshift parsing, domain resolution, proxy design, holdout).
- **(6) Production-closeness:** **Medium.** The interaction is *active sharing/commenting*, not passive
  feed reading; the population is Reddit-skewed. But the *data shape* (user×article×outlet-lean) matches
  production.
- **Carries:** **Claim P** AND **Claim I** — the latter via an **anti-circularity design**: learn user
  positions *only* from news reading in *neutral* subs (r/news, r/politics), then validate them against
  *partisan-sub membership* held out of the fit. That breaks the "the proxy is the same as the signal"
  objection and is a legitimate, publishable validation of the interpretive claim.

### Pγ — Pα (item axis on MIND) + Pβ (user axis on Reddit)  *(method cross-validation)*
Not one population, but the **strongest real-data evidence for the whole of W8**: prove the *method*
recovers known **outlet lean** on MIND (Claim P, production-shape data) *and* known **user ideology** on
Reddit (Claim I, dual-axis data). Two real datasets, each carrying the half the other lacks.

### Pδ — ANES/CES self-reported ideology + media-diet items  *(aggregate user oracle)*
Public election surveys carry *self-reported ideology* + coarse media-consumption items ("do you watch
Fox/MSNBC/CNN"). **No article-level interactions** → cannot train, and cannot fit per-user ideal points.
But it is a clean, permanent **oracle** to cross-check at the outlet/aggregate level: does an ideal-point
placement of "a self-identified conservative Fox viewer" land right? **Supporting evidence only.**

---

## 3. Rejections, each with a proof (not "didn't find it")

- **Wikipedia Clickstream / Wikinews:** data is *aggregate* referrer→article **counts**, with **no
  per-user rows** → there is no `R` to fit an ideal point → fails "user" + "interaction" by
  construction. **Impossible, not merely inconvenient.**
- **Adressa / EB-NeRD / Globo / Plista:** real user×news clicks, but **single-publisher or no lean
  spectrum** (the repo states EB-NeRD/Adressa are single-publisher, `rwe/mind.py:21`) → the item axis is
  *degenerate* (one outlet) → a left–right item dimension cannot exist → Claim P is unfittable.
  **Proven insufficient for ideology** (usable only as interaction-shape references).
- **GDELT / Event Registry / Media Cloud / CC-NEWS / NewsAPI / RSS archives / Wikidata:** Class B →
  **zero users/interactions** → cannot fit any user position. **Proven ingredient-only.**
- **Crossref / OpenAlex:** academic works + citations. Wrong domain on three axes — journals ≠ news
  outlets, citing ≠ news *reading*, scholarly community ≠ media lean — so it cannot match production even
  if repurposed. OpenAlex's only W8 use is *finding* other datasets. **Rejected on domain mismatch.**
- **Facebook / Bakshy et al. 2015 (Science):** the near-perfect artifact — 10.1M users with
  *self-reported ideology* + shared-URL outlet alignment, i.e. Claim I *and* Claim P in one set. **Only
  aggregate results were released; the microdata is Facebook-internal and was never public.** Proves the
  concept is real; **proves, by its non-release, that the ideal set is not legally obtainable.**
- **Twitter / X:** fresh collection is gone (brief confirmed). The historical signal that *did* exist
  (Barberá-style **follow** graphs / derived ideal points on Zenodo/dataverse) is **follows, not
  reading** → wrong signal for W8 regardless of availability. **The blocker is real and also moot.**
- **Commercial panels (comScore / Nielsen / YouGov Pulse):** these *do* fuse passive reading + user
  ideology — but they are **paid, licensed, non-redistributable** → fail "legally obtainable" in the
  open/reproducible sense the brief intends. Named for completeness; **out of scope by the legality
  constraint.**
- **Kaggle / Zenodo / HuggingFace / GitHub / archive.org:** **distribution channels, not datasets.**
  Everything on them that fuses user-reading + user-ideology + news-outlet at article level is a re-host
  of the above (mostly MIND). A channel search yields **no new capability** — proven by the Class-A
  enumeration in §1 (only MIND-family + Reddit exist; the rest are B/C/D).

---

## 4. The core impossibility, proved by exhaustion

Claim I needs *external user ideology* co-located with *news-article interactions*. The interaction-
bearing class (§1.A) is **exhaustively**: {MIND-family, Reddit}. Then:
- MIND-family: user IDs anonymised, linkable to nothing → **no** external user ideology obtainable.
- Reddit: user ideology available **only as a behavioural proxy** (partisan-sub membership), **never as
  self-report.**

Therefore **no public dataset supplies self-reported user ideology attached to article-level reading.**
The only sources that ever did are platform-internal (Facebook/Twitter) or commercial panels — both
excluded by the legality constraint. **This is a proof, not a failure to search:** the artifact is
absent from the only class that could contain it. What remains legally reachable for Claim I is (a) the
Reddit *proxy* (strong, publishable, not self-report), or (b) *your own users* (production traffic or a
recruited survey panel). That is the residual, and it is irreducible.

---

## 5. Deliverables

### A. Best **practical** solution
**Pα-lite: MIND + Media-Cloud headline/date reconstruction of `source_map` + an MBFC/AllSides lean
table**, gated by a coverage spike. Media Cloud is purpose-built for media-*source identity* over
exactly MIND's window, free for research, US-centric. Fastest route to a defensible **Claim P**
validation in the exact production shape. **First action: measure the join match-rate on political MIND
items; proceed only past the preset bar.** If Media Cloud access is awkward, substitute the archival
CC-NEWS/GDELT route (same join, immutable inputs).

### B. Best **research** solution
**Pγ: dual-axis method cross-validation.** Item axis on MIND-with-reconstructed-lean (Claim P,
production-shape); user axis on Reddit/Pushshift news-link reading with the **partisan-sub anti-
circularity holdout** (Claim I). Two real behavioural datasets, each carrying the half the other lacks —
the strongest, most publishable evidence that the *method* recovers both real outlet lean and real user
ideology. Cross-check aggregates against ANES/CES (Pδ).

### C. Best **production-like** solution
**Pα full: MIND passive-click matrix → `recommender_inputs` → `IdeologyModel.fit`, with item lean from
the reconstructed `source_map` + AllSides table**, and the user axis validated *predictively* (held-out
cross-lean reading), not interpretively. This exercises the real production estimator on real passive
news reading with real US-outlet labels — the highest fidelity to what ships. Its ceiling is explicit:
it validates Claim P, **not** Claim I (no user ground truth in MIND).

### D. Can W8 be validated **today**, without production traffic?
**Yes for the mechanism; no for the platonic ideal — and the gap is proven, not open-ended.**
- **Claim P (behaviour → ideologically-aligned, predictive latent axis): YES, today**, legally and
  reproducibly, via Pα on real MIND clicks.
- **Claim I (that axis = users' real ideology): YES to research-credible strength** via the Reddit proxy
  (Pβ/Pγ with holdout); **NO to the self-report standard** on any public data (§4 proof). Closing *that*
  last inch needs a paid panel or your own instrumented users.
- So do not wait for production traffic to *de-risk W8's science* — Pα/Pγ can be run now. Do wait for
  your own users (or fund a panel) if, and only if, the bar you insist on is *self-reported* ideology of
  *your* reading population. Given W8's product purpose, the Reddit-proxy + MIND-predictive combination
  is a sufficient and honest green light; the self-report standard is a *nice-to-have you can only buy
  or grow*, not a prerequisite the open world can hand you.

---

## 6. Evidence / Engineering judgement / Speculation

**Evidence (in-repo, verifiable):** production estimator + inputs (`rwe/ideology.py:87-231`,
`rwe/graph.py:47`, `rwe/mind.py` `recommender_inputs`/`fit_ideology`/`set_item_positions`); the three
item-lean code paths and their caveats; text-lean **κ=0.14** (`classify_lean.py:11`, `lean_agreement.py`);
`outlet_lean.csv` illustrative + "replace with AllSides/MBFC"; MIND URL = MSN, no publisher, `source_map`
required (`rwe/mind.py:18`, `_outlet_from_url`); EB-NeRD/Adressa single-publisher (`rwe/mind.py:21`).

**Engineering judgement (well-established dataset properties):** MIND = MSN, Oct 2019, ships
title/abstract/WikidataId entities + timestamped behaviours, Microsoft Research License; Pushshift Reddit
dumps historical on archive.org/Academic Torrents, Reddit API restricted post-2023; GDELT open with
domains + GKG entity tags; Media Cloud media-source-centric, free research API, US collections; CC-NEWS
WARC since 2016; ANES/CES public with ideology + media items; Bakshy/FB microdata never released; Twitter
follow-graphs ≠ reading. That MIND-entity ⋈ GDELT-GKG-entity joining beats naive title matching.

**Speculation (must be measured before committing):** the **actual coverage/precision** of the
MIND→outlet headline/entity/date join (the Pα go/no-go); current Media Cloud API stability and Pushshift
mirror availability; the exact strength of the Reddit partisan-sub proxy as a stand-in for self-reported
ideology; the licence cleanliness of any specific redistributed AllSides/MBFC table.

*Research analysis only. No code written or modified.*
