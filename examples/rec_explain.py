"""Recommendation Explainability — a read-only observer over the exact models that serve
``/api/recommendations`` (Commit 21a, Level 1 + Level 2 of the validation framework).

The principle (the same one behind the Metric Validation Pipeline): **every explanation shown
must be traceable to real evidence produced by the recommender** — walk scores, ranks, graph
connectivity, measured outlet shares — never inferred from templates or placeholder values.

What this module does:

* rebuilds nothing and re-ranks nothing — it observes the same cached ``FeedbackGraph`` and the
  same per-request models (``Backend._model_for``) the serving path uses, and replicates the
  serving plan (single strategy = top-12; blended feed = 6 RWE-B + 4 RWE-D + 4 Adaptive with
  first-seen dedup) so its output can be pinned equal to ``/api/recommendations`` by tests;
* attaches per-recommendation **evidence**: per-strategy score/rank (the strategy-contribution
  table), rank-based match band (``strong`` / ``good`` / ``candidate`` — ranking language, not a
  statistical confidence), the cross-cutting derivation, three-tier outlet familiarity
  (``never`` / ``rarely`` / ``familiar``), item-degree percentile (the long-tail evidence behind
  RWE-D), and a two-hop connectivity summary (how many of the reader's graph reads co-connect);
* answers "why wasn't article X recommended?" with a truthful exclusion taxonomy:
  ``recommended`` · ``seen_excluded`` (you read it) · ``below_cutoff`` (ranked, with the numbers)
  · ``not_in_graph`` (in the catalog but not a recommendable node) · ``not_in_catalog``.
  Saved status is **not** an exclusion — the engine has no saved-based filter — and the payload
  says so rather than inventing one.

Influence percentages (leave-one-out attribution) and counterfactuals are Commit 21c; the
independent ranking recomputation is Commit 21d.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Optional

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import api_server as engine  # noqa: E402  (the serving engine — helpers shared for parity)
import health_report as hr   # noqa: E402
from ingest import canonical_url as _canonical_url  # noqa: E402

STRATEGIES = ("rwe-b", "rwe-d", "adaptive")
#: Must mirror ``Backend._serialize_recommendations`` — pinned equal by the parity tests.
BLEND_PLAN = (("rwe-b", 6), ("rwe-d", 4), ("adaptive", 4))
SINGLE_K = 12


def match_band(rank: int, candidates: int) -> str:
    """Ranking language for how high a pick sat among a strategy's candidates (NOT a probability
    or a confidence interval): top decile → ``strong``, top three deciles → ``good``, else
    ``candidate``."""
    pct = rank_percentile(rank, candidates)
    if pct >= 90:
        return "strong"
    if pct >= 70:
        return "good"
    return "candidate"


def rank_percentile(rank: int, candidates: int) -> float:
    """Percentile of a 0-based rank among ``candidates`` ranked items (rank 0 → 100.0)."""
    if candidates <= 1:
        return 100.0
    return round(100.0 * (1.0 - rank / candidates), 1)


def url_index_from(url_by_id: dict) -> dict:
    """Invert an ``id → url`` map into ``url/canonical-url/id → id`` (the same join the
    Personalizer uses), so an exclusion query accepts an article id, a URL, or its canonical."""
    out = {}
    for iid, u in (url_by_id or {}).items():
        out.setdefault(str(iid), str(iid))
        if u:
            for key in (str(u), _canonical_url(str(u))):
                out.setdefault(key, str(iid))
    return out


def _viewpoint_shift(corpus, u: int, col: int, n_political: int) -> Optional[dict]:
    """The "Estimated effect" evidence (21a.2, D2): the report's own viewpoint computation —
    same functions, same confidence weighting — with this one article appended. ``None`` when
    the article isn't political or the reader is below the report's political-read minimum:
    the drawer must never show a projection the report itself wouldn't stand behind."""
    mind = corpus.mind
    pos = np.asarray(mind.item_positions, dtype=float)
    pol = np.asarray(mind.political, dtype=bool)
    if col >= pos.size or not (bool(pol[col]) and np.isfinite(pos[col])):
        return None
    conf = corpus.pop.get("item_confidence")
    ppos = hr._political_positions(mind, u)
    pconf = hr._political_confidence(mind, u, conf) if conf is not None else None
    cur = hr.viewpoint_shares(ppos, weights=pconf)
    if not np.isfinite(cur[0]):
        return None                                   # below the report's own minimum
    a_pos = float(pos[col])
    if pconf is not None:
        c = np.asarray(conf, dtype=float)
        a_w = float(c[col]) if np.isfinite(c[col]) else 1.0
        after = hr.viewpoint_shares(np.append(ppos, a_pos), weights=np.append(pconf, a_w))
    else:
        after = hr.viewpoint_shares(np.append(ppos, a_pos))

    def pct(t):
        return {"left": round(100 * t[0], 1), "center": round(100 * t[1], 1),
                "right": round(100 * t[2], 1)}

    return {"current": pct(cur), "after": pct(after), "estimated": True,
            "basis": (f"the report's viewpoint computation (confidence-weighted, over your "
                      f"{n_political} political reads) with this one article appended")}


def _topic_share(corpus, u: int, col: int) -> Optional[dict]:
    """This article's topic as a fraction of the reader's history ("Politics is 42% of your
    reading") — straight from the population matrices the report reads."""
    try:
        cat = str(np.asarray(corpus.mind.categories)[col])
        i = [str(c) for c in corpus.pop["cat_u"]].index(cat)
        share = float(hr.shares(np.asarray(corpus.pop["UC"][u], dtype=float))[i])
        return {"topic": cat, "share": round(share, 3)}
    except Exception:
        return None


def _params_used(model, strategy: str, params: Optional[dict]) -> dict:
    """Echo the hyperparameters actually in effect on the model instance (never the request)."""
    used = {"source": "sliders" if params else "defaults"}
    eps = getattr(model, "epsilon", None)
    if eps is not None and strategy != "rwe-d":
        e = np.asarray(eps, dtype=float)
        used["epsilon"] = float(e) if e.ndim == 0 else "per-user"
    beta = getattr(model, "beta", None)
    if beta is not None and strategy == "rwe-d":
        used["beta"] = float(beta)
    if strategy == "adaptive":
        used["exposure"] = "neutral (measured exposure not wired — copy states this)"
    return used


def explain(backend, corpus, rec, u: int, strategy: Optional[str] = None,
            params: Optional[dict] = None, article: Optional[str] = None,
            reads_meta: Optional[dict] = None, url_to_id: Optional[dict] = None) -> dict:
    """Explain the exact feed ``(corpus, rec, u, strategy, params)`` would serve.

    Read-only: reuses ``Backend._model_for`` (the serving models) and replicates the serving
    plan; tests pin the resulting article ids equal to ``Backend._serialize_recommendations``.
    ``article`` (id or URL) additionally asks "why was/wasn't THIS article in the feed?".
    """
    mind = corpus.mind
    uid = np.asarray(mind.dataset.user_ids)[u]
    rows = np.flatnonzero(np.asarray(rec.rec_dataset.user_ids) == uid)
    if rows.size == 0:
        return {"error": "reader has no row in the recommendation graph"}
    row = int(rows[0])
    fg = rec.fg
    seen = set(int(i) for i in fg.seen_items(row))

    rep = hr.user_report(corpus.pop, mind, u)
    user_side = float(np.sign(rep.get("mean_lean") or 0.0))
    familiarity = engine._familiarity_of(corpus.pop, u)

    # --- per-strategy model observations (the strategy-contribution evidence) -----------------
    per = {}
    for s in STRATEGIES:
        model = engine.Backend._model_for(rec, s, params)
        scores = model.scores(np.array([row]))[0]
        ranked = [int(j) for j in model.recommend(np.array([row]), top_k=int(fg.n),
                                                  exclude_seen=True)[0] if int(j) >= 0]
        per[s] = {"model": model, "scores": scores, "ranking": ranked,
                  "rank_of": {j: i for i, j in enumerate(ranked)},
                  "params_used": _params_used(model, s, params)}

    # --- replicate the serving selection (plan → admission → slice order → first-seen dedup) --
    # Mirrors Backend._rec_cols_of exactly: walk the full ranking, keep the columns the strategy's
    # slice admits (Commit R1: rwe-b admits political items only), order the slice via the SAME
    # Backend._slice_select (Commit R1.5: rwe-b serves cross-cutting items first), then dedup
    # first-seen across strategies — the shared helpers keep the parity guarantee byte-exact.
    # ``slice_js`` records which ranked items occupy each strategy's slots — the honest meaning
    # of "inSlice" now that admission/ordering can skip over or reorder raw ranks.
    plan = ((strategy, SINGLE_K),) if strategy in STRATEGIES else BLEND_PLAN
    chosen, seen_cols, dedup_dropped = [], set(), 0
    slice_js: dict = {s: set() for s in STRATEGIES}
    for s, kk in plan:
        admitted, j_of_col = [], {}
        for j in per[s]["ranking"]:
            col = rec.id2col.get(str(rec.rec_ids[j]))
            if col is None or not engine.Backend._slice_admits(mind, s, col):
                continue
            admitted.append(col)
            j_of_col.setdefault(col, int(j))
        for col in engine.Backend._slice_select(mind, s, admitted, kk, user_side):
            j = j_of_col[col]
            slice_js[s].add(j)               # an admitted slot, even if dedup then drops it
            if col in seen_cols:
                dedup_dropped += 1
                continue
            seen_cols.add(col)
            chosen.append((col, j, s))

    item_deg = np.asarray(fg.item_degrees, dtype=float)

    def _evidence(col: int, j: int, chosen_by: str) -> dict:
        art = backend._serialize_article(corpus, col)
        by_strategy = {}
        for s in STRATEGIES:
            r = per[s]["rank_of"].get(j)
            by_strategy[s] = {
                "score": float(per[s]["scores"][j]),
                "rank": (r + 1) if r is not None else None,          # 1-based for humans
                # Whether this item occupies one of the strategy's admitted slice slots (Commit R1:
                # admission can skip non-admitted ranks, so a slot-holder's raw rank may exceed the
                # slice size — membership, not rank arithmetic, is the truthful signal).
                "inSlice": int(j) in slice_js[s],
            }
        r0 = per[chosen_by]["rank_of"].get(j, 0)
        candidates = len(per[chosen_by]["ranking"])
        co_readers = fg.A[:, j]
        co_items = np.asarray((fg.A.T @ co_readers).todense()).ravel()
        within2 = int(sum(1 for i in seen if i != j and co_items[i] > 0))
        return {
            "articleId": art["id"], "url": art.get("url"), "headline": art["headline"],
            "publisher": art["publisher"], "lean": art["lean"],
            "topic": art.get("topic"), "publishedAt": art.get("publishedAt"),
            "chosenBy": chosen_by,
            "rank": r0 + 1,
            "scorePercentile": rank_percentile(r0, candidates),
            "match": match_band(r0, candidates),
            "byStrategy": by_strategy,
            "crossCutting": {
                "value": engine._cross_of(user_side, art["lean"], bool(art.get("political"))),
                "userMeanLean": round(float(rep.get("mean_lean") or 0.0), 3),
                "articleLean": float(art["lean"]),
                "articlePolitical": bool(art.get("political")),
                "gate": "political article, opposite sign and |lean| >= 0.5",
            },
            "outletFamiliarity": familiarity(art["publisher"]),
            "leanGap": round(abs(float(art["lean"]) - float(rep.get("mean_lean") or 0.0)), 2),
            "topicShare": _topic_share(corpus, u, col),
            "viewpointShift": _viewpoint_shift(corpus, u, col, int(rep.get("n_political") or 0)),
            "longTail": {
                "itemDegree": int(item_deg[j]),
                "degreePercentile": round(100.0 * float(np.mean(item_deg <= item_deg[j])), 1),
            },
            "connectivity": {"readsWithinTwoHops": within2, "graphReads": len(seen)},
        }

    recommendations = [_evidence(col, j, s) for col, j, s in chosen]

    trace = {
        "reader": {"row": row, "corpusRow": int(u),
                   "reads": reads_meta or {"total": len(seen), "joined": None},
                   "meanLean": round(float(rep.get("mean_lean") or 0.0), 3)},
        "graph": {"users": int(fg.m), "items": int(fg.n), "edges": int(fg.A.nnz)},
        "strategies": {s: {"paramsUsed": per[s]["params_used"],
                           "candidates": len(per[s]["ranking"]),
                           "seenExcluded": len(seen)} for s in STRATEGIES},
        "plan": [{"strategy": s, "slice": kk} for s, kk in plan],
        "dedupDropped": dedup_dropped,
        "served": len(recommendations),
    }

    out = {
        "trace": trace,
        "recommendations": recommendations,
        "notes": [
            "Saved status does not affect recommendations — the engine has no saved-based "
            "exclusion; only read articles are excluded (seen-exclusion).",
            "Adaptive runs at neutral exposure for real readers (measured exposure is not "
            "wired into AdaptiveRWEB); the card copy states this rather than over-claiming.",
        ],
    }

    if article:
        out["exclusion"] = _exclusion(backend, rec, per, plan, seen, chosen, article, url_to_id)
    return out


def _exclusion(backend, rec, per, plan, seen, chosen, article: str,
               url_to_id: Optional[dict]) -> dict:
    """The truthful "why (not) this article?" verdict — see the module docstring taxonomy."""
    lookup = url_to_id if url_to_id is not None else url_index_from(getattr(backend, "url_by_id", {}))
    q = str(article).strip()
    aid = lookup.get(q) or lookup.get(_canonical_url(q)) or q

    item_of = {str(rec.rec_ids[j]): j for j in range(len(rec.rec_ids))}
    j = item_of.get(str(aid))
    if j is None:
        in_catalog = str(aid) in (getattr(backend, "url_by_id", {}) or {})
        return {"article": q, "resolvedId": str(aid),
                "verdict": "not_in_graph" if in_catalog else "not_in_catalog",
                "detail": ("in the catalog but not a node in the recommendation graph — the "
                           "corpus builder keeps only recommendable articles (e.g. lean "
                           "unresolved is the documented unknown-outlet gap)" if in_catalog
                           else "no catalog article with this id/URL")}
    if int(j) in seen:
        return {"article": q, "resolvedId": str(aid), "verdict": "seen_excluded",
                "detail": "you read this article — a reader's own reads are never re-recommended"}
    if any(jj == int(j) for _c, jj, _s in chosen):
        s = next(s for _c, jj, s in chosen if jj == int(j))
        return {"article": q, "resolvedId": str(aid), "verdict": "recommended",
                "detail": f"in the served feed via {s}"}
    slices = dict(plan)
    by = {s: {"rank": (per[s]["rank_of"].get(int(j)) + 1
                       if per[s]["rank_of"].get(int(j)) is not None else None),
              "score": float(per[s]["scores"][int(j)]),
              "cutoff": slices.get(s)} for s in per}
    return {"article": q, "resolvedId": str(aid), "verdict": "below_cutoff",
            "byStrategy": by,
            "detail": "ranked by every strategy but outside each one's served slice"}
