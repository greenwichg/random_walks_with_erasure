# Interest Intensity — eight per-topic sliders, and the measured verification of their effect

**Status: adopted and verified 2026-08-17.** Feature `dccff79`, end-to-end regression `148084f`,
anchor retune `bce2d2a` (deployed). The schema lives in `examples/settings_service.py`
(`INTEREST_KEYS`, 1–10, neutral 5), the slider→topic mapping and the rank nudge in
`examples/api_server.py` (`_INTEREST_TOPICS`, `_INTEREST_ANCHORS`, `_interest_multiplier`,
`Backend._interest_rerank`), the explain mirror in `examples/rec_explain.py`, the API contract in
`examples/api_fastapi.py` (`InterestPrefsModel` / `InterestPrefsUpdate`), and the UI as the
"Interest intensity" card on Settings (icon · topic · slider · value rows, section-scoped Reset
to Defaults). The binding regression is
`tests/test_api_fastapi.py::test_interest_intensity_persists_and_moves_the_feed_end_to_end`.

## The eight sliders

The reference design (the X "Customize Your For You Algorithm" panel) names interest areas that
do not exist in this catalog, and a slider naming a topic the catalog doesn't carry would be a
dead knob (`docs/SIGNAL_INTEGRITY.md`: labels must not lie). The eight are therefore the closed
taxonomy's (`ingest.TAXONOMY`) eight non-political subjects: **Business, Technology, Science,
Health, Climate, Sports, Entertainment, Arts & Culture** (one slider spanning the adjacent Arts +
Culture topics). Deliberately absent:

- **Politics** — the feed's political composition is the `politicalOpenness` control's contract
  (the rwe-b slice admits political items only, W1); an intensity knob on the same axis would
  fight it.
- **Opinion** — a register lens, not a subject.
- **World / U.S.** — geography, owned by the Places settings.

## Persistence and reset

`interests` is a settings group with per-LEAF layering (the notification-matrix pattern): a
one-slider patch ships and merges as one leaf, so two devices cannot clobber each other's edits.
Values clamp to 1–10 with junk falling to the neutral 5 (never an extreme); unknown interest keys
drop; a stored blob from before the group existed gains all-5 — the value that maps to no
recommender parameter at all — so no legacy reader's feed moved on rollout, and no migration
exists in either direction. **Reset to Defaults** is a section-scoped header action that stages
all eight back to 5 through the page's normal draft + Save flow; it touches nothing outside the
card.

## Ranking integration

The established slider route, unchanged in shape: `rec_params_from_settings` maps only *moved*
sliders into `params["interests"]`, a lower-cased topic → weight dict (`artsCulture` fans out to
both topics; all-neutral contributes no key, so an untouched reader's feed is byte-identical by
construction). `Backend._interest_rerank` then re-sorts each strategy's **admitted** candidate
pool on `(rank + 1) / _interest_multiplier(weight)` — a stable sort, so same-topic model order
and the no-weights case are identical by construction. It runs before `_slice_select`, so the
rwe-b cross-cutting-first partition and the W1 bridge budget keep their guarantees; it is an
ORDER nudge, never an admission or exclusion, so the publisher cap and slot budgets mean what
they always meant. `rec_explain` applies the same shared helper at the same point (the 21a
served-vs-explained parity, pinned by test). The coach and analysis surfaces read the same params
function, so their suggestions follow the weights consistently.

## The verification (2026-08-17) and the 2× → 8× retune

The verification protocol — baseline at 5, persist-and-measure at 1 and 10, monotonicity, an
untouched political axis, a second interest, an automated `1 < 5 < 10` regression — was run
against production before any tuning. The first probes returned **null** (zero Sports/Technology
cards at every weight), and the diagnosis is recorded here because each step's artifact is a
lesson for the next instrument:

1. **`not_in_graph` is a third state.** The exclusion taxonomy distinguishes "not in the
   catalog", "in the catalog but not a recommendation-graph node", and "ranked below the
   cutoff". The first probe script conflated the first two and misread the output.
2. **`joined` counts URL-map resolution, not graph membership.** The candidate export's URL map
   covers all ~130k candidate rows; the graph holds 1,500. `reads: joined=5` therefore cannot
   prove a reader's reads are graph nodes.
3. **Recency-biased sampling cannot see the graph.** The graph is a ~2.5% subsample of the
   eligible pool, and discover's newest articles postdate the last refresh — sampling them
   found 0/120 in-graph, an artifact, not a fact.
4. **A mirror build proved the graph healthy.** Rebuilding exactly what the refresh cycle builds
   (same builder, env sizing, seed): 1,500 nodes with 189 Sports (180 clicked) and 53 Technology
   (51 clicked); the mirror's reader had Sports at raw rank 2 and was served 4 Sports cards at
   default. Composition, click sparsity, and subsampling were thereby cleared as causes.
5. **The live rank table quantified the real blocker.** For a topic-concentrated live reader
   (five political reads), in-graph Sports articles sat at raw ranks 404/720/1,075 — needing
   effective weights of ~337–1,344 on the original `w/5` curve, whose maximum boost was 2×. The
   mechanism was correct; the ceiling was too gentle to matter for exactly the reader an
   interest slider serves. (A positive control — the strength slider — proved params flow on the
   live measured path.)

**The retune (`bce2d2a`)**: `_INTEREST_ANCHORS = (0.2, 1.0, 8.0)` at slider 1/5/10,
piecewise-linear — the same tunable-anchor pattern as `_OPENNESS_BRIDGE_BUDGET` /
`_STRENGTH_BETA`. The demote side (5× rank penalty at weight 1) and the neutral identity are
byte-identical to the original curve (both verified correct); only the boost side strengthens,
to **8× at weight 10** — reaching items ranked within ~8× of the serving cutoff (the near-to-mid
walk neighborhood) while a rank-400 item still never overrides the model's head. A stronger
nudge; still never a hard flip.

## Results

**Regression corpus** (deterministic, seeded; weight-10 before → after the retune): Sports 4 → 7
cards (exposure 0.445 → 0.725), Technology 5 → 7 (0.562 → 0.711); weight-1 behaviour and the
restore identity unchanged. The regression pins: monotone exposure across `1 ≤ 5 ≤ 10` with
strict extremes for BOTH topics, persistence from both the write's echo and a fresh read,
`politicalOpenness` unmoved throughout, untouched topics' cards keeping their relative order,
and a return to 5 restoring the exact baseline feed.

**Live production** (verify reader, post-deploy): a generation whose baseline served **zero**
Sports and zero Technology — the hard case — gained **2 Sports cards (ranks 7/9, exposure
0.254)** and **1 Technology card (rank 7, 0.143)** at weight 10, with weight 1 ≤ baseline and
the restore to 5 byte-identical both times. In the pre-retune generation, where a Sports card
was present at baseline, weight 1 removed it — the demote side verified live. Note for future
probes: the refresh poller swaps generations underneath long-running verifications (it did,
twice); compare within a run, not across runs.

## Known structural limitations (future optimization items — not slider defects)

Both were measured during the verification; both bound how much inventory the slider has to
work with, and neither is changed by this feature:

1. **The recommendation graph is a 1,500-node uniform subsample.** `RWE_MAX_ITEMS` defaults to
   1,500 against a measured ~60,300 lean-eligible candidate rows (~2.5%); the subsample is
   random, not topic-stratified. Thin topics land accordingly (measured expectation per
   generation: Sports ~175 nodes, Technology ~52, Science ~9, Arts ~4) — the Science and
   Arts & Culture sliders are weak on the current corpus by construction. Raising the cap is an
   env-only change and stratified sampling a small builder change, but either alters build cost
   and feed composition and needs its own measurement.
2. **Technology is thinned upstream by lean-registry coverage.** Only 26% of Technology
   candidate rows (2,079 of 7,882) carry a resolvable outlet lean, versus 45–85% for most
   topics — niche tech outlets are unrated, and the graph builder keeps only lean-resolved rows
   (the documented unknown-outlet gap). Registry coverage work would deepen Technology (and
   Sports, at 45%) without touching the recommender.

## Rollback

`git revert bce2d2a` restores the 2× boost ceiling (curve only); reverting the feature commits
restores the pre-slider engine, and stored `interests` blobs then normalize away as unknown keys
— no data migration in either direction.

## Re-verification runbook (post-deploy)

```bash
cd /opt/ih && source deploy/ops/_compose.sh && dc exec -T api python - <<'PY'
import json, os, urllib.request
BASE = "http://127.0.0.1:8000"; SECRET = os.environ.get("RWE_INTERNAL_SECRET") or ""
UID = 6   # a measured verify reader; any signed-in reader past the read threshold works

def call(path, body=None, uid=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"X-IH-Auth": SECRET} if SECRET else {}),
                 **({"X-IH-User-Id": str(uid)} if uid is not None else {})})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())

def row(recs, label):
    pos = [i + 1 for i, r in enumerate(recs) if r["article"]["topic"] == label]
    return f"count={len(pos)} ranks={pos if pos else '-'} exposure={sum(1.0/p for p in pos):.3f}"

base = call("/api/recommendations", uid=UID)
for key, label in (("sports", "Sports"), ("technology", "Technology")):
    print(f"== {label} ==  w=5  {row(base, label)}")
    for w in (1, 10):
        call("/api/me/settings", {"interests": {key: w}}, uid=UID)
        print(f"  w={w}  {row(call('/api/recommendations', uid=UID), label)}")
    call("/api/me/settings", {"interests": {key: 5}}, uid=UID)
    same = [r["article"]["id"] for r in call("/api/recommendations", uid=UID)] == \
           [r["article"]["id"] for r in base]
    print(f"  restore w=5 -> baseline restored: {same}")
PY
```

Expected: weight-10 exposure at or above baseline for each topic (strictly above whenever the
graph holds reachable items), weight-1 at or below it, and every restore line `True`.
