# W3B — Story-Level Viewpoint Aggregation (Audit + Plan, Docs Only)

**Status:** Design / audit only. **No code written.** Awaiting plan review before implementation.

**Goal (from `docs/W3_ROADMAP_REVISION.md`):** aggregate **outlet** viewpoints across the
articles of one story/event into a legible story-level viewpoint — "most coverage of this story
leans left; here is the right-leaning take" — using **outlet-level lean only**, never inferring
article-level lean. Signal reliability: **High (outlet lean)**.

**Hard constraints:** reuse existing story clustering · aggregate outlet lean only · deterministic ·
never infer article lean · no new ML / LLM · **do not change recommendation ranking** · preserve
REPORT CONTRACT v1 · preserve explain↔served parity · do not modify W1 / W2 / W3A or any
recommendation algorithm.

---

## Part A — Architecture audit (answers, with code references)

### 1. Where is story clustering implemented?
`examples/clustering.py` (the deterministic **union-find Jaccard** primitive; algorithm only),
invoked by `story_service.build_stories` → `clustering.cluster(...)` (`story_service.py:137-156`,
call at `:144`). `story_service.py` is the **single owner of Story construction**
(`story_service.py:1-9`); Discover and Stories both consume it and never build a Story
independently. It "never touches the recommendation engine" (`:9`).

### 2. How are story IDs generated?
`story_service._story_id(members)` (`:37-42`): `"st_" + sha1(anchor)[:16]`, where `anchor` is the
**representative (earliest-published) article's canonical URL**. Stable across rebuilds as coverage
grows (deliberately not a hash of all members). Deterministic.

### 3. Which data structures already contain story coverage?
The Story dict from `_build_story` (`:86-134`) **already aggregates outlet lean**:
- `distribution` — L/C/R fractions over **distinct publishers** (one vote per outlet), via
  `_distribution` (`:45-54`) using each member's `leanBucket`.
- `blindspotSide` — the under-covered side, `_blindspot` (`:57-64`).
- `publishers` / `publisherCount` / `publisherDiversity`; `coverage[]` — per-article
  `{publisher, lean, leanBucket, …}` (`_coverage`, `:67-76`).
These surface today via `api_fastapi.StoryModel` (`distribution: ViewpointModel`, `blindspotSide`)
and `story_intelligence.compute_coverage_statistics` → `politicalDistribution`
(`story_intelligence.py:181`).

**Finding:** story-level *outlet-lean aggregation already exists*. W3B adds a **summary** on top of
it (a named dominant side + a continuous mean), it does not build a new aggregation.

### 4. Which files already aggregate articles into stories?
- `story_service.py` — the owner (cluster → `_build_story` → `distribution`/`blindspot`).
- `clustering.py` — the clustering primitive.
- `discover.py` — `feed_article_to_article` (the shared Article serializer; sets `lean`/`leanBucket`
  from the **outlet** — `discover.py:66,80-81`, `lean = scored["lean"]` = registry outlet lean).
- `story_intelligence.py` — analytics over a built story (freshness/momentum/coverage stats).
- `evidence_resolver.py` — `story_index(store_)` (`:133-147`) builds a `url → {storyId, coverage}`
  index from `story_service.cluster_from_store` (the explain path's story source).
- `rec_sandbox.py` — `_stories_with` / `_story_membership` for the REPORT CONTRACT v1 story block.
- `audit_story_coverage.py` — offline coverage audit.

### 5. Which serving path should consume story-level viewpoint?
The **Story surfaces** (not the recommender): `GET /api/stories` (`api_fastapi.py:1421` →
`story_service.list_stories`), `GET /api/story/{id}` (`:1450` → `get_story`),
`GET /api/story/{id}/intelligence` (story intelligence), and Discover. Plus the **explainability**
surface `rec_explain._story_match_diag` (`rec_explain.py:285-286`, index from
`evidence_resolver.story_index`). **Not** the recommendation ranking — stories are a separate
surface; the rec engine never consumes them.

### 6. Can W3B be implemented entirely by reusing existing story clustering? **Yes — entirely.**
`build_stories` / `cluster_from_store` already cluster and already aggregate outlet lean
(`_distribution`). W3B computes a viewpoint **summary from the already-clustered members** — no new
clustering, no new signal, no re-fetch. Story IDs and cluster membership are untouched.

### 7. Smallest implementation that adds story viewpoint without changing ranking
Add the viewpoint at the **one** aggregation site so parity + determinism come for free:
- `story_service._viewpoint(members)` — derive from distinct-publisher **outlet** leans + the
  existing `_distribution`; attach `story["viewpoint"]` in `_build_story` (one line).
- Because the served path (`list_stories`/`get_story`) and the explain path
  (`evidence_resolver.story_index` → `cluster_from_store`) both build stories through
  **`story_service._build_story`**, the viewpoint is **identical on both** — parity by construction.
- Expose it additively on the Story contract (`StoryModel`) and in the explain `storyMatch`
  diagnostic. **Do not** add it to `rec_sandbox._story_membership` (keeps REPORT CONTRACT v1
  byte-identical — that function selects fields explicitly, `rec_sandbox.py:349-352`).
- **Touch nothing in the recommender** (`api_server` RWE/blend, `personalize`, W1/W2/W3A).

### 8. Which explainability surfaces must expose story viewpoint?
- `rec_explain._story_match_diag` (`rec_explain.py:285-286`) — the internal explain endpoint's
  per-card `storyMatch` diagnostic. Requires the story index to carry the viewpoint:
  `evidence_resolver.story_index` (`:133-147`) currently stores `{storyId, coverage}` per URL, so
  add `"viewpoint": s.get("viewpoint")` to that entry.
- The served `StoryModel` (`/api/story`, `/api/stories`) — the user-facing story view.
Both derive from `story_service` → the two agree by construction (the parity guarantee).
*(The AI Coach's story facts, `coach_service.py:673`, may surface it later — out of the minimal
scope.)*

### 9. Which regression tests will require updates?
- `test_story_service.py` — presence check is a **subset** (`for k in (...): assert k in s`,
  `:55-60`), so adding a key does **not** break it; add a `viewpoint` presence + determinism assert.
- `test_story_intelligence.py`, `test_story_match_regression.py`, `test_evidence_resolver.py` — the
  storyMatch/story now carries an extra additive field; assert its value, not an exact shape.
- `test_rec_sandbox.py` — **must stay byte-identical** (proves `_story_membership` unchanged →
  REPORT CONTRACT v1 preserved).
- `test_rec_explain.py` — explain↔served parity (storyMatch viewpoint == served viewpoint).
- **New** `test_story_viewpoint.py` — determinism, outlet-lean-only, dominant/mean correctness,
  parity, REPORT-CONTRACT safety.

### 10. Acceptance criteria (proves W3B correct)
1. **Deterministic:** the same cluster → an identical `viewpoint` across repeated builds.
2. **Outlet-lean-only:** `viewpoint` derives **only** from `coverage[].lean` (outlet lean); no call
   ever reads an article-level lean (there is none — `discover` sets `lean` from the outlet).
3. **Clustering reused:** story IDs, membership, and every pre-existing story field are
   byte-identical; only `viewpoint` is added.
4. **REPORT CONTRACT v1 preserved:** `test_rec_sandbox` byte-identity green (report story block
   unchanged).
5. **Explain↔served parity:** `/api/story/{id}.viewpoint` equals the explain `storyMatch.viewpoint`
   for the same story.
6. **No ranking change:** recommendation regression + W1 + W2 + W3A green; the recommender files are
   byte-unchanged (empty diff).
7. **Shadow before/after:** every existing story field identical; `viewpoint` is the only addition.

---

## Part B — Smallest implementation plan (for review — not yet built)

### B1. The one aggregation (`examples/story_service.py`)
Add two pure helpers and one line in `_build_story`. Sketch (final code at implementation time):

```python
def _dominant(dist: dict) -> str:
    """The majority side of the coverage, deterministic; 'balanced' on a tie."""
    order = ("left", "center", "right")
    top = max(order, key=lambda k: (dist[k], -order.index(k)))
    ties = [k for k in order if dist[k] == dist[top]]
    return "balanced" if len(ties) > 1 else top

def _viewpoint(members: list) -> dict:
    """Story-level viewpoint from OUTLET lean only (one vote per distinct publisher). Deterministic;
    never reads an article-level lean (there is none — coverage[].lean is the outlet's house lean)."""
    by_pub = {}
    for m in members:                       # one outlet lean per publisher
        by_pub.setdefault(m["publisher"], m.get("lean"))
    leans = sorted(v for v in by_pub.values() if isinstance(v, (int, float)))
    dist = _distribution(members)           # reuse the existing distinct-publisher L/C/R aggregation
    return {
        "dominant": _dominant(dist),                                   # "left|center|right|balanced"
        "meanLean": round(sum(leans) / len(leans), 4) if leans else 0.0,
        "spread":   round(leans[-1] - leans[0], 4) if len(leans) >= 2 else 0.0,
    }
```
In `_build_story` add `"viewpoint": _viewpoint(members),`. The existing `distribution` and
`blindspotSide` stay as-is (the viewpoint references, does not duplicate, them).

### B2. Explain surface (parity)
- `examples/evidence_resolver.py` — `story_index` entry becomes
  `{"storyId": s["id"], "coverage": s["coverage"], "viewpoint": s.get("viewpoint")}`.
- `examples/rec_explain.py` — `_story_match_diag` adds `"viewpoint": story.get("viewpoint")` to its
  returned dict (additive; identical to the served value by construction).

### B3. Contract (additive)
- `examples/api_fastapi.py` — add `StoryViewpointModel {dominant: str; meanLean: float; spread:
  float}` and `viewpoint: Optional[StoryViewpointModel] = None` on `StoryModel` (and, if the explain
  endpoint has a typed storyMatch model, there too). `response_model_exclude_none` keeps it omitted
  when absent. **`StoryModel` is a separate contract from REPORT CONTRACT v1** (which is the
  `rec_sandbox` report), so this does not touch v1.
- *(Optional, additive)* `story_intelligence.compute_coverage_statistics` may echo `viewpoint`
  beside `politicalDistribution`.

### B4. Explicitly NOT touched
`rec_sandbox._story_membership` (REPORT CONTRACT v1 story block), `api_server` RWE / blend /
`blend_plan_for` / ranking, `personalize`, `rwe/*`, and all W1 / W2 / W3A code — **zero diff**.

### B5. Validation plan
- **Deterministic aggregation** — build the same catalog twice; assert identical `viewpoint` per
  story (new test).
- **Outlet-lean-only** — assert `viewpoint` is a pure function of `coverage[].lean` (feed a cluster,
  vary only outlet leans, observe; grep-assert no article-lean source exists).
- **Explain↔served parity** — for a story with coverage, assert `get_story(...).viewpoint ==
  story_index(...)[url]["viewpoint"] == storyMatch(...).viewpoint`.
- **REPORT CONTRACT v1** — `test_rec_sandbox` byte-identity stays green.
- **Shadow before/after** — `examples/w3b_shadow.py` (offline, read-only): for every story, diff the
  full dict old-vs-new; assert the only delta is the added `viewpoint`, and print the per-story
  dominant / meanLean / blindspot table.
- **Regression** — recommendation regression suite + W1 (`test_api_server`/`test_api_fastapi`) + W2
  (`test_adaptive_exposure`) + W3A (`test_political_mask`) + story suites + full suite.

### B6. Files changed (summary)
| File | Change | Kind |
|---|---|---|
| `examples/story_service.py` | `_viewpoint` + `_dominant` + one line in `_build_story` | core (additive) |
| `examples/evidence_resolver.py` | add `viewpoint` to the story-index entry | additive |
| `examples/rec_explain.py` | add `viewpoint` to `storyMatch` diagnostic | additive |
| `examples/api_fastapi.py` | `StoryViewpointModel` + `StoryModel.viewpoint` | additive contract |
| `examples/story_intelligence.py` *(optional)* | echo `viewpoint` in coverage stats | additive |
| `tests/test_story_viewpoint.py` *(new)* | determinism / outlet-only / parity / contract | tests |
| `examples/w3b_shadow.py` *(new)* | before/after delta report | offline diagnostic |
| `tests/test_story_service.py` | add viewpoint presence + determinism assert | test update |

**Not touched:** `rec_sandbox.py`, `api_server.py` (recommender), `personalize.py`, `rwe/*`, W1/W2/W3A.

---

## Why this is safe by construction

- **Parity + determinism** come from putting the viewpoint in the **single** builder
  (`story_service._build_story`) both the served and explain paths already share.
- **REPORT CONTRACT v1** is untouched because its story block (`_story_membership`) selects fields
  explicitly and W3B does not add `viewpoint` there.
- **Ranking** is untouched because stories are not a recommender input; no `api_server` / `rwe` /
  `personalize` code changes.
- **No article-level lean** because `coverage[].lean` is, and remains, the outlet's registry lean;
  W3B only aggregates those across distinct publishers.

*Documentation only. No code was written or modified. Implementation awaits review of this plan.*
