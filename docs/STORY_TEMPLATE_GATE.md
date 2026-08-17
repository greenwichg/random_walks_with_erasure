# The sole-template-evidence gate — why announcement headlines welded, and the measured fix

**Status: adopted 2026-08-17.** `RWE_CLUSTER_TEMPLATE_GATE=1` is a compose default
(`deploy/docker-compose.yml`, the same lost-env-file discipline as the quorum/veto knobs; `0` is
the kill switch). The rule lives in `examples/story_service.py` (`TEMPLATE_TOKENS`,
`template_gate`, `_template_closure`, composed through the existing `evidence` hook in both the
primary build and the `_repair` re-cluster — `clustering.py` is untouched); the instruments that
measured it are `examples/audit_template_edges.py` (Phase A) and `examples/audit_template_gate.py`
(Phase B). Commits: rule `a880406`, instrument join-fix `ccbd966`, adoption default with this doc.

## The symptom

A production story titled "Mirzapur The Movie: Trailer, Cast, Release Date And Everything You
Must Know" serving five unrelated articles as one event: the MCU X-Men cast reveal at D23 (two
publishers — the only genuine pair), The Paper season 2, the DJI Osmo 360 II, and the Mirzapur
film. Reported from a story-page screenshot, then production-confirmed edge by edge.

## The trace (production-confirmed, 2026-08-17)

Four edges pass the shipped gate (sim ≥ 0.28, shared ≥ 3); the component is a **chain** — no
direct edge connects DJI to either X-Men article:

| edge | j | shared tokens | verdict |
|---|---|---|---|
| X-Men(Radio) ↔ X-Men(Forbes) | 0.400 | cast, d23, men, revealed | real — distinctive `men`, `d23` |
| X-Men(Radio) ↔ Paper S2 | **0.444** | cast, date, release, revealed | false — 100% template |
| Paper S2 ↔ Mirzapur | 0.333 | cast, date, release, trailer | false — 100% template |
| DJI ↔ Mirzapur | 0.308 | date, everything, know, release | false — 100% template |

The strongest edge in the component is a false one. Why no guard fired:

* **link_quorum 0.2** needs `ceil(0.2 × cross-pairs)` supporters; that is **1** until cross-pairs
  reach 6, and every step of this weld had ≤ 4 — the quorum is a mega-chain brake by arithmetic,
  and a five-article chain completes under it (`need=1 hits=1 -> MERGE`, all four steps).
* **X4 geo veto / repair**: entertainment articles carry no event geography, so located consensus
  never exists and `clusterTrust` is honestly *unknown*, never LOW — repair cannot trigger.
* **X5b** only adds merges. Notably, the two covered articles (Forbes and The Paper both carry
  rich extracted entities) share **zero** names — the corroboration signal discriminated
  perfectly where extraction ran; it ran on 2 of 5 articles. Coverage, not rule design — the
  X6 finding in miniature.

## Lineage — how this fix was found

The X6 investigation (X's SimClusters tweet similarity, studied from source) proposed a second
sparse representation corroborating cluster growth; its Phase 0 audit **killed** that channel on
coverage (24% entity / 18% geo; blind to 100% of the recorded mis-cluster exhibits; the E-rules
failed the pre-registered <2% good-loss bar at 2.39%). This gate is the same *architectural*
idea — an edge needs evidence beyond one signal class — rebuilt on the signal every article
carries: its own tokens. The failure mode is the known boilerplate-chaining class (the calendar
"news in brief, July 21" merge fixed by stop-listing), but with a twist that forbids the known
fix: stop-listing `cast`/`revealed` would cut the genuine X-Men edge from 4 shared tokens to 2
(< min_shared) and split the real story. These tokens carry real within-story signal; they only
must not be the *sole* evidence — the "distinctive tokens" concept `MIN_SHARED_TOKENS`'s own
docstring states, enforced at the edge level.

## The rule

An edge must share ≥ 1 token outside the lexicon. **The lexicon** (Phase A registration,
verbatim — twelve tokens, no post-hoc additions): `cast, date, episode, everything, know,
premiere, release, revealed, season, specs, teaser, trailer`. Template tokens keep counting
toward Jaccard, so recall inside genuine stories is untouched. One closure serves admission,
quorum cross-pair scoring, and the repair re-cluster (the `article_tokens` discipline: one rule,
or repair re-splits on a disagreement). Off/unset is byte-identical by construction, pinned by
test alongside the anchor exhibit's resolution.

## The measurements

**Phase A** (`audit_template_edges.py`, 26,565 post-exclusion articles): of **19,001** edges
passing the production gate, exactly **3** rest solely on the lexicon — all three the anchor
exhibit's false edges; 0 cross-story, 0 unstoried. Kill metric: 0.03% of intra-story pairs
against the pre-registered ≤ 2% bar. Hand-read arm: every sole-template edge in the window is a
bridge. (The instrument's raw "47 fragmenting stories" figure is an artifact — 46 are merge-pass
lobes that fragment under the null too; the marginal effect is 1 story. Phase B's null-control
exists because of this.)

**Phase B** (`audit_template_gate.py`, 27,307 articles, production environment, all ten
pre-registered bars): exhibit resolves exactly as predicted (weld dissolved; X-Men pair a story;
three detached); bad clusters 2 → 2 (mean 0.965 flat); droppedOut **3 of 5,989 (0.05%)** vs the
5% bar, Entertainment separately 2 of 268 (0.75%); stories 1,479 → 1,479; largest 72 → 72; build
time 1.00×; both sides byte-deterministic; null-control attributes fragmentation to exactly one
story. The complete production diff of the rule is: one story dissolves, one forms from its two
genuine members, three articles return to Discover/Search as singletons.

## Instrument lessons (recorded so the traps keep compounding)

* The X6 verdict line encoded `catch > 0` where the registration said "non-trivial share" — the
  printed PASS was overruled by the criterion as registered. Verdict lines now defer the
  hand-read arm explicitly.
* The Phase B runner's exhibit bars first ran with a canonical-URL lookup against the
  display-URL-keyed membership index and convicted the rule of the instrument's join bug (the
  membership diff in the same output showed the pair correctly clustered). Both URL forms,
  in **every** instrument, always — this repo has now paid for that join four times.

## Deliberately not done

* **No stop-listing of the template tokens** — measured to split the genuine pair (above).
* **No lexicon changes without measurement** — `need` ("everything you need to know") is the
  known candidate; any change goes through `audit_template_edges.py`'s census + kill criterion
  first, never straight into the rule.
* **Not applied to `_merge_duplicates` / X5b** — those passes compare cluster profiles, not
  pairwise edges; different mechanism, its own evidence rules.

## Rollback

`RWE_CLUSTER_TEMPLATE_GATE=0` in `deploy/.env` (or removing the compose default) restores
pre-gate clustering exactly; no data was migrated and no stored row changed.

## Verification runbook (post-deploy)

```bash
cd /opt/ih && source deploy/ops/_compose.sh && dc exec -T api python - <<'PY'
import sys
sys.path.insert(0, "/app/examples")
import story_service, store as store_mod
st = store_mod.Store(None)
rows = story_service._fetch(st)
stories = story_service.build_stories(rows, entities=story_service._entities_for(st, rows))
hits = [s for s in stories
        if any("mirzapur" in (c.get("headline") or "").lower() or
               "x-men" in (c.get("headline") or "").lower() for c in s["coverage"])]
for s in hits:
    print(f"{s['totalCoverage']} members: {s['title'][:64]}")
    for c in s["coverage"]:
        print(f"   - {c['publisher']}: {(c.get('headline') or '')[:60]}")
PY
```

Expected while the exhibit remains in the window: the X-Men pair as a 2-member story, no story
containing Mirzapur/DJI/Paper alongside it. (The exhibit ages out within days;
`audit_template_gate.py` remains runnable at any time — its baseline side passes
`template=False` explicitly, so the comparison stays meaningful after adoption.)
