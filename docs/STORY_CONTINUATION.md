# Story Continuation — operations

What the feature is, what it costs, how to turn it on, and how to tell whether it is working.
The product design and its rationale live in `docs/STORY_CONTINUATION_DESIGN.md` (approved, frozen);
this file is for whoever is operating it.

## What it does

A reader clicks **Read article**, the publisher opens in a new tab, and some minutes later they come
back to Hidden View. At that moment — and only then — the card they read from offers one thing:
another outlet's account of the *same event*, from the opposite side of the rated spectrum.

It is not a recommendation. It touches no blend-plan slot, no `DEFAULT_BLEND_PLAN` total, no
`blend_plan_for` arithmetic, no explanation ladder. The feed's own one-card story slot
(`RWE_STORY_SLOT`) is unchanged and remains the fallback for readers who return in a later session.

## Switches

| variable | default | effect |
|---|---|---|
| `RWE_STORY_CONTINUATION` | `0` (off) | While off, `GET /api/me/continuation` exists and always answers `null`, so the client contract is live and exercisable before the surface is. |
| `RWE_CONTINUATION_MAX_AGE_H` | `4` | How long after a read the offer still makes sense. The design's 4 h is a stated guess; `continuation_shown.minutesSinceRead` measures the real decay curve and this retunes it without a rebuild. |

Both are in `deploy/docker-compose.yml`, whose `environment:` block is an **allowlist** — a variable
absent from it can never reach the container.

Turning it on, and off again:

```bash
# on
printf 'RWE_STORY_CONTINUATION=1\n' | sudo tee -a deploy/.env
sudo bash deploy/ops/restart.sh api

# off
sudo sed -i '/^RWE_STORY_CONTINUATION=/d' deploy/.env
sudo bash deploy/ops/restart.sh api
```

> **Never invoke `docker compose` directly on the production host.** Production merges
> `docker-compose.yml` **and** `docker-compose.aws.yml` plus `--env-file deploy/.env`, and the AWS
> override replaces the api service's volumes with the host bind-mount `/opt/ih/data:/app/data`. A
> bare `docker compose -f deploy/docker-compose.yml up -d api` recreates the container against the
> base file's empty named volume instead, and the engine comes up serving an empty database. Every
> production compose call goes through `dc()` in `deploy/ops/_compose.sh` — i.e. through
> `restart.sh` / `update.sh`.

## Cost

Sub-millisecond warm. One dict lookup on the TTL-cached story index the recommendations path already
builds, a scan of that one cluster, and the reader's stored reads. **No table, no worker, no model,
no network, no writes.**

Measured in production 2026-08-03 (59.2k articles, 1,757 stories): one index build 64.7 ms, then 100
hits averaging 6.6 ms. The endpoint never builds the index inline — a boot-window miss answers
`null` rather than spending ~24 s of a request thread clustering.

## Is it working?

```bash
# the offline resolver over the real store: gate-by-gate attrition + the eligible rate
docker exec deploy-api-1 nice -n 19 python examples/audit_continuation.py --inline

# the structural ceiling across all stories (a stride, not the head of the ranking)
docker exec deploy-api-1 nice -n 19 python examples/audit_continuation.py --inline --ceiling --sample 800

# the LIVE endpoint for one reader: auth, flag, payload shape, index metrics
docker exec deploy-api-1 python examples/audit_continuation.py --serve --email you@example.com
```

Notes that matter for reading the output:

* **`--inline` is needed under `docker exec`.** The story-view cache is a per-process global, so a
  fresh process starts cold. On a 2-vCPU host that build is real CPU for tens of seconds — `nice -n
  19` keeps it yielding to the API, and it is worth running at a quiet moment.
* **`--serve` warms first.** A restarted server has a cold index and `/api/me/continuation` never
  builds one, so the probe drives `/api/recommendations` until `rec_story_index_hit_total` moves.
  Probing before that returns nulls meaning "cold cache", not "no offer".
* **`eligible AT CLICK TIME` is the number to read**, not `eligible NOW`. Every stored read older
  than the freshness window fails the last gate by construction, so the raw rate over a backlog is
  ~0 however well the feature works.
* **`anchor_aged_out` is a measurement artifact**, not a finding: the article left the catalog after
  it was read. At prefetch the reader has just clicked something in the catalog by construction.

## Measured baseline (2026-08-03)

| measure | value |
|---|---|
| offers from real reads, through the live endpoint | **9 of 99 (9.1%)**, 0 errors |
| structural ceiling over cluster members (n=800) | 25.6% |
| dominant loss, realized | `not_clustered` — ~21% of the clustering window is in any cluster |
| dominant loss, structural | `anchor_unrated` 35.8%, `no_opposing_sibling` 30.1% |

**Registry lean coverage is not the lever** it was assumed to be: `audit_registry_coverage` shows
4,318 untracked outlets of which only 35 sit in a one-short story, worth 40 claims between them, and
the high-volume unrated outlets are deliberately unrated (aggregator / wire / research / forum).
Cluster membership is the binding constraint, and loosening clustering admission re-opens the merge
defects in `docs/STORY_CLUSTER_MERGES.md`.

## Analytics

All six events go through `lib/analytics.track`, so they land wherever the configured provider
points.

| event | fired by | answers |
|---|---|---|
| `continuation_eligible` | `lib/continuation.prefetchContinuation` | How often can this fire at all? |
| `continuation_armed` | same, after storage succeeds | The gap from `eligible` is **client-side loss** — quota, private mode, disabled storage. |
| `continuation_shown` | `ContinuationStrip` on a qualifying return | Armed→shown ratio; catches dwell-gate and mount losses. |
| `continuation_opened` | the CTA | Click-through, and the decay curve that should replace the 4 h guess. |
| `continuation_dismissed` | the × | Irritation. High dismissal at `impressionIndex: 1` means the *offer* is wrong, not the timing. |
| `continuation_all_outlets` | the story link | If this beats `opened`, readers prefer the overview — swap the CTAs. |

Success is **not** click-through. The product goal is exposure to alternative perspectives, so the
measure is whether readers who take a continuation subsequently read more opposite-lean articles
organically than a matched cohort who did not. Guardrail: reads per session must not fall.

## Known gaps

* **Non-political stories are eligible.** Registry leans are an *outlet-level* political rating, so a
  sports result or a box-office story inherits its publisher's. Two of the nine production offers
  were a Commonwealth Games mile and a film's Chinese box office, where the strip would say an outlet
  "is rated right of centre" about a finishing time. `_TEMPLATE_PATTERNS` catches betting and obituary
  mills, not ordinary sports and entertainment. A topic gate is designed but deliberately not shipped
  in v1 — see the design's §9.2.
* **Mounted on three surfaces.** Discover, Recommendations, and the story page's coverage list.
  History, Saved, Search and the analyzer also use `ReadArticleButton` and do not yet mount the
  strip — a read from those arms a candidate that nothing renders.

  The story page is the surface with the best odds by construction: every row there is already a
  cluster member, so the membership gate that rejects ~4 in 5 Discover cards passes automatically.
  It suppresses the "View all N outlets" link, which would point at the page the reader is on, and
  reports `surface: "story"` on `continuation_shown` — comparing that against `card` is the point of
  the field, since one blended armed→shown ratio would hide a large structural difference.
* **Signed-in only.** Without read history the unread gate cannot be evaluated, and an anonymous
  answer would be a guess.
