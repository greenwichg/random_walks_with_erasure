# Coverage Comparison — design

**What it answers:** for one article in a story cluster, *what did the rest of the coverage report
that this article does not — and what does this article have that nobody else does?*
**Method:** deterministic set algebra over features extracted by explicit rules. No LLM in the
answer path (§8 says why, and where one would be defensible).
**Status:** design only. Nothing implemented.
**Related:** `docs/STORY_CLUSTER_MERGES.md` + `docs/STORY_CLUSTER_QUORUM_VERIFICATION.md` (the
clusters this stands on), `docs/ARTICLE_INSIGHTS.md` (the generated sibling this deliberately is
not), `docs/SIGNAL_INTEGRITY.md` (the honesty rules this inherits).
**Date:** 2026-08-03.

---

## 1. The one-line thesis

An omission is a **counted fact about a set of documents**, not a judgement: *six of nine outlets
covering this event mention the cost figure; this one does not*. That is computable, reproducible,
citable, and cannot hallucinate — provided we are equally rigorous about the case where we simply
cannot see the text.

## 2. Honest inventory: what exists today

Verified in the code, not assumed:

| input | reality | consequence for this design |
|---|---|---|
| Story clusters | `story_service.build_stories` — deterministic, quorum-gated, with `clusterTrust` (`ok`/`low`/`unverified`) and `geoCoherence` | the comparison set comes free; trust gating is already available |
| Article text | `feed_articles.title`, `.description` always; **`.body` only when the feed sends `content:encoded` / Atom `content`** | **the central constraint** — a large share of the catalog is headline+blurb only (§3) |
| Entities | **no NER anywhere.** The only provider-extracted entities are **event locations** (`article_event_locations`, GDELT GKG, source-attributed) | entity work must be rule-based, or borrowed from the locations table |
| Quotations | not extracted today | rule-based extraction is feasible where `body` exists (§5.4) |
| Topics | `scored.category` (single label per article) | coarse; useful as a guard, not as a comparison axis |
| Publisher metadata | `outlet_registry` (lean, country, kind, credibility) + `publisher_identity` (name-form collapsing) + `publisher_metadata` | powers support-diversity and prevents syndication inflating counts |
| Lexical primitives | `clustering.title_tokens`, `idf_weights`, `weighted_jaccard` — deterministic, tested | reuse verbatim; do not write a second tokenizer |
| Language | `feed_articles.language` | needed to gate cross-language comparison (the catalog is multilingual) |

**The measurement that must precede implementation** (one read-only probe on the box, in the style
of the existing audit CLIs): for trusted clusters of ≥3 publishers — what fraction of members have
a non-empty `body`; the median and p10 body length per member; how many clusters have ≥3 members
that *all* have bodies; the distribution of `language` within clusters. Those four numbers decide
whether §5.3–5.4 are worth building or whether the feature ships as §5.1–5.2 only. Nothing below
should be built before they are known.

## 3. Architecture

```
story build (existing background seam, already off the request path)
        │
        ├─ for each cluster with clusterTrust != low and ≥ MIN_MEMBERS:
        │     1. gather members  (join coverage → feed_articles for description/body/language)
        │     2. extract FEATURES per member        (§5, pure functions, versioned)
        │     3. build the cluster FEATURE INDEX    (feature → supporting members)
        │     4. for each member: diff member vs index → COMPARISON
        │     5. score confidence per item          (§6)
        │
        ▼
   cache: (story_id, article_id, algo_version) → comparison   [regenerate when the cluster's
                                                               membership hash or algo_version
                                                               changes — same staleness discipline
                                                               as article_insights]
        ▼
   request path: one indexed read, attached to the story/analysis payload; miss ⇒ null, render nothing
```

Properties inherited deliberately from the existing feature: computed on the poller/story seam,
never on a request thread; cached with an explicit version + membership hash; a miss renders
nothing rather than a spinner.

**Determinism contract:** the comparison is a pure function of (member texts, membership, registry
snapshot, config, algo_version). Same inputs ⇒ byte-identical output. That makes it testable with
fixtures and diffable across releases — the same property that made the clustering work auditable.

## 4. The comparison unit

A **feature** is a normalized, evidence-carrying token of what an article says:

```
feature := (kind, key, evidence)
   kind     term | figure | entity | quote-voice | location | attribute
   key      the normalized comparison key (what makes two mentions "the same")
   evidence the exact source span + member id, so every claim can be shown, not asserted
```

Everything downstream is set algebra over features plus counting. Nothing is summarized, rewritten
or inferred — the UI can always show the reader the words the finding came from.

## 5. Algorithms, by tier

Tiers are ordered by text requirement. **Each ships independently**; L0 alone is already a
product.

### 5.1 L0 — Attribute comparison (needs no article text at all)

Pure metadata set-difference over the cluster:

- **Viewpoints absent** — lean buckets with zero publisher share. *Already computed* as
  `missingViewpoints` in `article_analyzer`; this design reuses it rather than recomputing.
- **Outlets covering it** that this reader's article is not among; count by *publisher identity*,
  not name form.
- **Timing** — is this article the first report, or N hours behind the earliest? (`publishedAt`
  spread is already in the story.)
- **Geography** — event countries present in the cluster's `article_event_locations` but absent
  from this article's rows. Source-attributed, never inferred from text.
- **Register/emotion mix** — the cluster's distribution vs this article's own scored values.

Confidence: **high by construction** (counted facts, no text interpretation). Failure mode: none
beyond cluster quality.

### 5.2 L1 — Salient-term deltas (title + description; works for every article)

1. Tokenize each member with the existing `title_tokens` over `title + description`.
2. Compute `idf_weights` **over the cluster**, so terms that every member shares (the event's own
   name) score low and distinguishing terms score high.
3. Subtract **publisher boilerplate**: a phrase recurring across that publisher's articles in
   *unrelated* clusters is furniture, not content (computable from the catalog; cheap, and the
   product already collapses publisher identity).
4. For each term absent from the target member, count supporting members and distinct supporting
   publishers.
5. Emit terms with support ≥ K publishers (K configurable, default 3) and salience above a floor.

This is the "what others emphasise that this doesn't" layer. It is **weak evidence of omission**
and must be labelled as such: absence from a 20-word blurb is not absence from the article. §6's
text-parity gate is what keeps this honest.

### 5.3 L2 — Figures and rule-based entities (needs body for real value)

- **Figures**: regex-extract number + unit + governing noun within a window (money, casualties,
  percentages, durations, dates). Normalize (`1.2 million` → `1200000`; currency symbols → ISO).
  Key = (unit, governing-noun-lemma). Two uses:
  - *omission*: a figure class reported by K+ outlets and absent here;
  - **discrepancy**: the same figure class with **different values** across outlets — arguably the
    most valuable output in the whole feature, and entirely factual because both values are quoted
    with their sources.
- **Entities without NER**: capitalized-sequence chunking (proper-noun runs, sentence-initial words
  excluded by a stopword/position rule), plus alias folding via the outlet registry for
  organisations it already knows. Deliberately conservative: precision over recall, because a false
  "X is missing" is worse than a missed one.
- **Locations**: from the locations table, not from text.

### 5.4 L3 — Quoted voices (needs body; the strongest output when available)

1. Extract quoted spans (straight and curly quotes, length ≥ N chars, balanced pairs).
2. Attribute a speaker: nearest proper-noun chunk within a window joined by an attribution verb
   (`said`, `told`, `according to`, `wrote`, plus a per-language list) — attribution is *skipped*
   when ambiguous rather than guessed.
3. Normalize speakers to a role/name key.
4. Compare **who is quoted** across members: *"Four outlets quote the developer; this article
   quotes only councillors."*

This answers the "whose viewpoint is absent" question with evidence instead of a model's
impression — and unlike an LLM's answer, every claim links to the sentence it came from.

### 5.5 Inversions worth emitting

- **Only here** — features present in this article and in no other member: an exclusive, a detail
  nobody else carried. The feature is not only a deficit report, and framing it as one would be a
  product mistake.
- **Everyone but here** — the high-support omissions (the headline finding).
- **Contested** — the figure discrepancies of §5.3.

## 6. Confidence — shown, not hidden

Each item carries its own components; the UI shows the counts, not a mystery score. This follows
the product's existing "counted facts, with n" convention.

| component | definition | why it matters |
|---|---|---|
| **support** | distinct *publisher identities* containing the feature | syndication of one wire story must not read as five outlets |
| **coverage share** | support ÷ comparable members | "6 of 9" is the number a reader can judge |
| **text parity** | this article's available chars ÷ median of the comparison set | the decisive guard (below) |
| **cluster trust** | existing `clusterTrust` / `geoCoherence` | a welded cluster produces nonsense comparisons |
| **salience** | in-cluster IDF of the feature | filters the event's own boilerplate |

**The hard rule — this is the design's most important line.** When text parity is poor (this
article is a stub and the comparison set has full bodies), the system must **never** say the
article *omitted* anything. It says: *"not present in the text we have for this article (headline
and summary only)."* Honest absence over confident wrongness is the same rule the registry follows
for unknown leans, and it is what keeps this feature from becoming a machine for defaming
publishers whose feeds are terse.

Ordinal output: **high** (support ≥ 3 publishers, parity ≥ 0.7, trust ok), **medium**, **low**;
low-confidence items are collapsed behind a disclosure rather than shown by default.

## 7. Gating — when the feature renders nothing

Refusing to answer is a feature. No comparison is produced when:

- `clusterTrust == low`, or the cluster fails the size/publisher floor (< 3 publishers);
- members span multiple `language` values and the target is not in the majority language
  (cross-language comparison is out of scope, §9);
- text parity is below the floor **and** no L0 attribute findings exist;
- the cluster is a known template/mill genre (see `docs/CONTENT_MILL_STORY_EVALUATION.md`) — daily
  box-office trackers "omit" each other's numbers by construction and the output would be noise.

## 8. Why not an LLM (and where one would be defensible)

A model asked "what does this article omit?" answers from world knowledge and produces plausible,
unverifiable, non-reproducible text — the exact failure this product refuses elsewhere. The
deterministic path gives citable evidence, byte-identical reruns, zero marginal cost, and testable
fixtures.

The one place a model would genuinely add power is **paraphrase alignment**: recognising that
"40 killed" and "the death toll reached 40" are the same claim, which set algebra misses (§9). If
that is ever worth paying for, the right shape is narrow and checkable: an *equivalence oracle*
asked only "do these two extracted spans state the same fact?" — a yes/no on two spans we already
have, never free-form generation, with the spans shown to the reader either way. That keeps the
output falsifiable. It is out of scope here.

## 9. Limitations, stated plainly

1. **Absence of a phrase is not absence of the fact.** Every finding is about *wording we can see*,
   and the copy must say so.
2. **Paraphrase blindness** — synonymy and rephrasing produce false omissions. Mitigated by
   requiring multi-publisher support and by conservative extraction; not eliminated.
3. **The stub-text ceiling** — for description-only articles, L2/L3 cannot run and L1 is weak.
   This is the dominant limitation and the reason §2's probe comes first.
4. **Multilingual catalogs** — comparison across languages is not attempted; a Vietnamese and an
   English report of the same event will not be compared.
5. **Cluster dependency** — the comparison inherits clustering quality. Quorum 0.2 fixed the weld
   class, but a wrong member still yields wrong comparisons; §7 gating is the containment.
6. **Structural asymmetry is not bias** — a wire brief legitimately carries less than a feature;
   the UI must not imply fault.
7. **Boilerplate and furniture** — mitigated by per-publisher subtraction, imperfectly.
8. **Quote attribution is hard** — ambiguous attributions are dropped, so voice comparison
   under-reports rather than guesses.
9. **No causal or evaluative comparison** — "this article is more sympathetic to X" is out of
   scope; that is what the generated insight and the lean registry are for.

## 10. UI sketches

**A. On the analysis / article page — a card beside "AI summary & framing"**

```
┌──────────────────────────────────────────────────────────────────────┐
│  Coverage comparison            9 outlets covering this story  ⓘ     │
│                                                                      │
│  REPORTED ELSEWHERE, NOT HERE                                        │
│   • The 340m public cost figure          6 of 9 outlets   [see 3 ▸]  │
│   • Residents' compensation objection    5 of 9 outlets   [see 3 ▸]  │
│   • Quotes from the developer            4 of 9 outlets   [see 2 ▸]  │
│                                                                      │
│  ONLY IN THIS ARTICLE                                                │
│   • The seven-hour hearing length        1 of 9 outlets              │
│                                                                      │
│  NUMBERS THAT DISAGREE                                               │
│   Public cost   340m — Harbour Gazette, 2 others                     │
│                 290m — Meridian Wire                                 │
│                                                                      │
│  VIEWPOINTS NOT IN THIS STORY'S COVERAGE     [Right] absent          │
│                                                                      │
│  Based on the text available for each article. This article: headline│
│  + summary only, so some detail may exist in the full piece. ▾ how   │
└──────────────────────────────────────────────────────────────────────┘
```

**B. Evidence disclosure** (the `[see 3 ▸]` affordance) — the point of the whole design:

```
   The 340m public cost figure — reported by 6 of 9 outlets
   ├ Meridian Wire   "…put the public cost at 340 million over the life…"   ↗
   ├ Ledger Daily    "…a headline figure of £340m across three years…"      ↗
   └ City Chronicle  "…the council's own briefing says 340 million…"        ↗
```

**C. Low-parity state — the honest one**

```
┌──────────────────────────────────────────────────────────────────────┐
│  Coverage comparison                                                 │
│  We only have this article's headline and summary, so we can't say   │
│  what its full text covers. Here's what the other 8 outlets reported:│
│   • cost figure (6) • compensation objection (5) • developer quotes (4)│
└──────────────────────────────────────────────────────────────────────┘
```

**D. Empty state** — "This article carries the same detail as the rest of the coverage we can
see." (Not a blank card; the absence of a finding is itself informative.)

**E. Story page variant** — a matrix, outlets × top features, ✓/–, sortable by lean bucket so a
reader can see whether an omission tracks the political spectrum or is idiosyncratic. This is the
most editorially powerful view and the most likely to be misread, so it needs the parity caveat
per row.

## 11. Build order

| phase | scope | gate to proceed |
|---|---|---|
| **0** | the §2 probe: body coverage, cluster sizes, language mix | numbers exist |
| **1** | L0 attributes + cache + API + card (no text analysis at all) | ships value with zero extraction risk |
| **2** | L1 salient terms + parity gate + evidence disclosure | manual read of 20 clusters: are the findings true? |
| **3** | L2 figures + discrepancies (bodies only) | precision spot-check ≥ agreed bar |
| **4** | L3 quoted voices | same |
| **5** | story-page matrix | after 2–4 have been read in anger |

Each phase is independently useful and independently revertible, and every phase after 1 is gated
on a **manual precision read**, not on a metric — because the failure mode here is a plausible
false accusation, which no aggregate catches.

## 12. Open questions

1. **Precision bar**: what false-positive rate makes a "not reported here" claim acceptable? I
   would argue ≤ 2% on a hand-read sample, because each error is a public statement about a
   publisher.
2. **Do we show the matrix (E) at all?** It is the strongest editorial artifact and the easiest to
   misread as a bias ranking.
3. **Attribution to outlets by name** in the evidence list — fine for public journalism, but worth
   a deliberate decision rather than a default.
4. **Retention**: comparisons are derived and cheap to recompute; cache them with the story view
   and expire with the cluster rather than keeping them forever.
