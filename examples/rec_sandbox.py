"""Recommendation Evaluation Engine (Layer 2) — evaluate counterfactual corpus compositions
against the UNCHANGED recommendation engine, ephemerally and read-only.

The approved layering (2026-07 architecture review):

    Layer 1  the existing engine — owns ALL recommendation logic (ranking, scoring, selection,
             dedup, clustering, validation, explanation). This module never reimplements any of
             it; every recommendation-shaped number in a report is produced by a Layer-1 call:
             ``corpus_refresh.build_candidate_for`` / ``RefreshManager.build_active`` (the hot
             swap's build-aside constructor — tempfile CSV, explicit profile, never activated),
             ``Backend.recommendations`` / ``Personalizer.recommendations``,
             ``Backend.explain_recommendations`` / ``Personalizer.explain`` (rec_explain),
             ``story_service.build_stories``, ``evidence_resolver.resolve``, ``ingest.Scorer``.
    Layer 2  THIS module. One public entry point, ``evaluate(store, spec, baseline=None)``,
             which only orchestrates Layer-1 calls and assembles a JSON-safe report. The only
             native computations are presentation math: the baseline diff and report assembly.
    Layer 3  clients (CLI, regression goldens, a future developer page, Article Analyzer
             tooling) — they render or assert on the report and add NO evaluation logic.

Hard invariants (each pinned by tests/test_rec_sandbox.py):

    * ZERO writes: the store is only read; every Personalizer this module creates is
      ``persist=False``; the corpus CSV is a tempfile that ``build_active`` deletes itself;
      nothing under ``data/`` is touched; no environment variable is mutated.
    * Ephemeral: the built engine stack lives only in the returned scope; the serving
      ``app.active`` (if any) is never read or replaced.
    * Deterministic: the report is a pure function of (store snapshot, spec, RWE_* env,
      freshness clock-day). It carries NO timestamps.
    * Baseline reuse rule: a provided ``baseline`` object is consulted for its ``.backend``
      ONLY — never its personalizer (a serving personalizer persists report snapshots; this
      module wraps its own ``persist=False`` one around the same backend).
    * Honest gates: injected articles pass the SAME candidacy gates as production (C4
      freshness via ``corpus_health.fresh_articles``; the qbias builder's lean-resolvability
      drop; ``corpus_validation.validate_corpus`` over the whole composition). A gate firing is
      reported, never bypassed — set the engine's own env (e.g. ``RWE_FEED_MAX_AGE_DAYS=0``)
      to relax a gate, exactly as production would.

REPORT CONTRACT v1 (stable; bump REPORT_VERSION on any breaking change)
=======================================================================

evaluate() returns one JSON-safe dict::

    {
      "reportVersion": 1,
      "spec": {                       # the normalized spec that was evaluated (echo)
        "inject":    [{"url", "title"}],          # identity echo only, not the full payloads
        "ask":       [str],                        # extra exclusion queries (urls/ids)
        "readers":   [{"kind": "demo"} | {"kind": "row", "row": int} | {"kind": "user", "id": int}],
        "strategies": [null | "rwe-b" | "rwe-d" | "adaptive"],
        "params":    [null | {"beta"?: float, "epsilon"?: float}],
        "questions": ["feed", "exclusion", "story", "explanation"],   # sections computed
        "compare":   bool,
      },
      "corpus": {
        "evaluated": {                # the composition WITH injections
          "built": bool,              # false => "error" says why (e.g. "validation_failed")
          "error": str | null,
          "candidateSize": int,       # rows handed to the builder (post-gates, post-dedup)
          "candidateSig": str,        # corpus_refresh.candidate_signature (deterministic)
          "items": int | null,        # corpus items the builder kept
          "graph": {"users", "items", "edges"} | null,
          "validation": {"eligible": bool, "failures": [code], "perBucket": {...} | null},
        },
          "baseline": {...same shape; plus "provided": true when a baseline object was
                     supplied — its own candidate_sig/item_count are reported and no
                     validation is re-run...} | null,     # only when spec.compare
      },
      "injected": [                   # one entry per spec.inject article, in order
        {
          "url", "canonicalUrl", "title", "publisher",
          "scored": {"outlet", "lean": float|null, "category", "political": bool|null},
          "disposition": "evaluated" | "already_in_candidate" | "dropped_freshness",
          "resolvedId": "Q<i>" | null,   # id in the evaluated corpus CSV (null: not exported)
          "graphNode": bool | null,      # a recommendable node of the evaluated graph?
          "story": {"matched": bool, "storyId"?, "articleCount"?, "publisherCount"?,
                    "distribution"?} | null,          # null when "story" not asked / not built
          "exclusions": [               # one per reader x params (blend plan), Layer-1 verdicts
            {"reader", "params", "status": "ok" | "below_threshold" | "not_built",
             "verdict"?, "detail"?, "byStrategy"?, "paramsUsed"?}
          ],
        }
      ],
      "asked": [ ...same shape as injected[].exclusions, plus "article": str... ],
      "feeds": [                      # one per reader x strategy x params, evaluated corpus
        {"reader", "strategy", "params",
         "status": "ok" | "below_threshold" | "not_built" | "error:<Type>",
         "served": [{"rank", "id", "url", "publisher", "strategy", "crossCutting", "reason",
                     "explanation"?: {"type", "message"}}]}
      ],
      "diff": {                       # only when spec.compare and both corpora built
        "perFeed": [{"reader", "strategy", "params", "identical": bool,
                     "entered": [key], "left": [key],
                     "moved": [{"key", "from": rank, "to": rank}]}]
      } | null,                       # key = CANONICAL URL (raw id fallback on URL-less corpora)
      "notes": [str],                 # honest caveats (e.g. max_items subsampling in effect)
    }

FROZEN (v1, 2026-07-13). Evolution policy: additive-only within v1 (new optional fields, new
``notes``); any breaking change (rename, removal, meaning change) bumps ``reportVersion`` to 2
— clients dispatch on ``reportVersion`` and must never rely on unknown-field absence.

Field stability classes:

    STRUCTURAL — stable within v1: every identity, verdict, rank, count, flag, and section
        shape above. Regression goldens should be built from these.
    COPY — carried verbatim from Layer 1 and may evolve with product copy without a version
        bump: ``served[].reason`` (the serializer's evidence-gated template), ``served[]
        .explanation.message`` (the resolver's final sentence — the public API mirrors THIS
        into its own ``reason``; the sandbox deliberately reports both), exclusion ``detail``
        strings, and ``notes``. Goldens should avoid pinning these unless copy itself is under
        test.
    CORPUS-RELATIVE VALUES — the ``resolvedId`` / feed ``id`` fields are stable *fields* whose
        Q{i} values are meaningful only within one report's evaluated corpus; cross-report and
        cross-corpus identity is ALWAYS the canonical URL.

``injected[].publisher`` vs ``injected[].scored.outlet``: deliberately both — ``publisher`` is
the row identity the corpus builder consumes (its precedence: row publisher, else scored
outlet), ``scored.outlet`` is the scorer's registry resolution; they differ only for
pre-shaped FeedArticle inputs whose row publisher overrides the scored payload.

Reader kinds: ``demo`` = the corpus's synthetic demo reader (``Backend.demo_user``); ``row`` =
an explicit synthetic reader row; ``user`` = a real stored reader, served through the measured
augmented pipeline when they pass the read threshold (below it the report says so instead of
guessing). ``params`` are the slider-mapped hyperparameters exactly as the serving route passes
them (``api_server.rec_params_from_settings`` output shape, or a plain ``{"beta": ...}``).

Interpretation notes (also emitted in ``notes`` where they apply): rankings are computed over
the deterministic simulated population — like every interaction in a live-feed corpus — so read
them as engine behavior, not audience prediction; and all cross-corpus comparisons are keyed by
canonical URL because ``Q{i}`` indices and the demo-reader identity are corpus-relative.
"""
from __future__ import annotations

import math
import os
import pathlib
import sys
from types import SimpleNamespace
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import api_server as engine            # noqa: E402  Layer 1: the engine + serializers
import corpus_health                   # noqa: E402  Layer 1: freshness gate + thresholds
import corpus_refresh                  # noqa: E402  Layer 1: candidate + build-aside constructor
import evidence_resolver               # noqa: E402  Layer 1: the explanation vocabulary
import ingest                          # noqa: E402  Layer 1: URL normalization + scoring
import personalize                     # noqa: E402  Layer 1: the measured (augmented) pipeline
import rec_explain                     # noqa: E402  Layer 1: trace / evidence / exclusion verdicts
import rss_ingest                      # noqa: E402  Layer 1: make_scorer (the ONE scoring config)
import story_service                   # noqa: E402  Layer 1: story clustering (pure builder)

REPORT_VERSION = 1

QUESTIONS = ("feed", "exclusion", "story", "explanation")
_READER_KINDS = ("demo", "row", "user")


# --------------------------------------------------------------------------- #
# Spec normalization.
# --------------------------------------------------------------------------- #
def _normalize_spec(spec: Optional[dict]) -> dict:
    s = dict(spec or {})
    readers = list(s.get("readers") or [{"kind": "demo"}])
    questions = [q for q in (s.get("questions") or QUESTIONS) if q in QUESTIONS]
    return {
        "inject": list(s.get("inject") or []),
        "ask": [str(a) for a in (s.get("ask") or [])],
        "readers": readers,
        "strategies": list(s.get("strategies") or [None]),
        "params": list(s.get("params") or [None]),
        "questions": questions,
        "compare": bool(s.get("compare", False)),
    }


# --------------------------------------------------------------------------- #
# Injected articles -> FeedArticle-shaped rows (the shape build_candidate_for
# yields and export_candidate_csv projects; keys are read with .get throughout
# Layer 1, so extra keys are harmless and missing ones degrade honestly).
# --------------------------------------------------------------------------- #
def _as_row(article: dict, scorer: "ingest.Scorer") -> dict:
    a = dict(article or {})
    if isinstance(a.get("scored"), dict):                 # already FeedArticle-shaped
        url = a.get("url") or a.get("canonicalUrl") or ""
        a.setdefault("canonicalUrl", ingest.canonical_url(url) if url else "")
        a.setdefault("source_feed", "sandbox")
        return a
    url = ingest.normalize_url(str(a.get("url") or ""))
    raw = ingest.RawRead(url=url, title=str(a.get("title") or ""),
                         outlet=str(a.get("outlet") or a.get("publisher") or ""),
                         category=str(a.get("category") or ""),
                         political=a.get("political"),
                         subtitle=str(a.get("subtitle") or ""),
                         description=str(a.get("description") or ""))
    sr = scorer.score(raw)                                # pure; NEVER score_with_cache (no writes)
    scored = {"article_id": sr.article_id, "outlet": sr.outlet, "category": sr.category,
              "lean": sr.lean, "political": sr.political, "title": sr.title,
              "emotion": getattr(sr, "emotion", None), "register": getattr(sr, "register", None)}
    return {"canonicalUrl": sr.article_id, "url": url, "title": sr.title or "",
            "publisher": sr.outlet, "description": a.get("description") or "",
            "publishedAt": a.get("publishedAt"), "source_feed": "sandbox", "scored": scored}


def _scored_summary(row: dict) -> dict:
    sc = row.get("scored") or {}
    lean = sc.get("lean")
    try:
        lean = float(lean)
        lean = None if not math.isfinite(lean) else lean
    except (TypeError, ValueError):
        lean = None
    return {"outlet": sc.get("outlet") or row.get("publisher") or "",
            "lean": lean, "category": sc.get("category") or "",
            "political": sc.get("political")}


# --------------------------------------------------------------------------- #
# The ephemeral build (Layer 1's build-aside constructor, on a detached manager).
# --------------------------------------------------------------------------- #
def _detached_build(store_, candidate: list, thresholds: dict, generation: int):
    """``RefreshManager.build_active`` without a manager that owns any app state: tempfile CSV,
    explicit profile (no env mutation), sanity-checked, NEVER activated. Returns
    ``(active_or_None, validation_result_or_None, error_or_None)``."""
    mgr = corpus_refresh.RefreshManager(SimpleNamespace())      # detached: diagnostics-only self
    return mgr.build_active(store_, candidate, thresholds, generation=generation)


def _corpus_block(active, result, error, candidate: list) -> dict:
    block = {
        "built": active is not None,
        "error": error,
        "candidateSize": len(candidate),
        "candidateSig": corpus_refresh.candidate_signature(candidate),
        "items": int(active.item_count) if active is not None else None,
        "graph": None,
        "validation": None,
    }
    if active is not None:
        fg = active.backend.rec.fg
        block["graph"] = {"users": int(fg.m), "items": int(fg.n), "edges": int(fg.A.nnz)}
    if result is not None:
        block["validation"] = {
            "eligible": bool(result.eligible),
            "failures": [f.get("code") for f in (result.failures or [])],
            "perBucket": (result.metrics or {}).get("perBucket"),
        }
    return block


# --------------------------------------------------------------------------- #
# Readers, feeds, exclusions — thin passthroughs to the serving entry points.
# --------------------------------------------------------------------------- #
def _reader_echo(reader: dict) -> dict:
    kind = str(reader.get("kind") or "demo")
    out = {"kind": kind if kind in _READER_KINDS else "demo"}
    if out["kind"] == "row":
        out["row"] = int(reader.get("row", 0))
    if out["kind"] == "user":
        out["id"] = int(reader.get("id", 0))
    return out


def _feed_for(backend, pers, reader: dict, strategy, params):
    """The exact feed the serving route would produce for this reader on this backend —
    ``Personalizer.recommendations`` for a measured real user, ``Backend.recommendations`` for
    a synthetic row. Returns ``(status, recs)``."""
    kind = reader["kind"]
    try:
        if kind == "user":
            uid = int(reader["id"])
            if not pers.has_measured(uid):
                return "below_threshold", []
            return "ok", pers.recommendations(uid, strategy, params)
        row = int(backend.demo_user) if kind == "demo" else int(reader["row"])
        return "ok", backend.recommendations(row, strategy, params)
    except Exception as e:                                # honest failure, never a crash
        return f"error:{type(e).__name__}", []


def _cards(recs: list, explain_ctx, story_idx, want_explanation: bool) -> list:
    cards = []
    for i, r in enumerate(recs):
        art = r.get("article") or {}
        card = {"rank": i + 1, "id": art.get("id"), "url": art.get("url"),
                "publisher": art.get("publisher"), "strategy": r.get("strategy"),
                "crossCutting": bool(r.get("crossCutting")), "reason": r.get("reason")}
        if want_explanation and explain_ctx is not None:
            try:                                          # resolver = Layer 1's ONE vocabulary
                ex = evidence_resolver.resolve(r, explain_ctx, story_idx)
                card["explanation"] = {"type": ex.get("type"), "message": ex.get("message")}
            except Exception:
                pass                                      # explanation stays absent, feed stands
        cards.append(card)
    return cards


def _exclusion_for(backend, pers, reader: dict, params, article: str) -> dict:
    """The truthful "why (not) this article?" verdict from the SAME observer the internal
    explain endpoint serves (``rec_explain``), for one reader/params combination."""
    entry = {"reader": _reader_echo(reader), "params": params, "status": "ok"}
    kind = entry["reader"]["kind"]
    try:
        if kind == "user":
            uid = int(reader["id"])
            if not pers.has_measured(uid):
                return {**entry, "status": "below_threshold"}
            out = pers.explain(uid, None, params, article=article)
        else:
            row = int(backend.demo_user) if kind == "demo" else int(reader["row"])
            out = backend.explain_recommendations(row, None, params, article=article)
    except Exception as e:
        return {**entry, "status": f"error:{type(e).__name__}"}
    if out.get("error"):
        return {**entry, "status": "error", "detail": out["error"]}
    exc = out.get("exclusion") or {}
    trace = out.get("trace") or {}
    entry.update({k: exc.get(k) for k in ("verdict", "detail", "byStrategy") if k in exc})
    entry["paramsUsed"] = {s: v.get("paramsUsed")
                           for s, v in (trace.get("strategies") or {}).items()}
    return entry


# --------------------------------------------------------------------------- #
# Story membership (the pure cluster builder over catalog rows + injections).
# --------------------------------------------------------------------------- #
def _stories_with(store_, extra_rows: list) -> "tuple[list, dict]":
    """Stories over the SAME bounded row set the Story Service clusters, plus the injected
    rows — and a local ``canonical url -> {storyId, coverage}`` index in the exact shape
    ``evidence_resolver.resolve`` consumes (built locally so this module never populates the
    resolver's process-wide memo with sandbox compositions)."""
    rows = story_service._fetch(store_) + list(extra_rows)
    stories = story_service.build_stories(rows)
    index: dict = {}
    for s in stories:
        for m in s.get("coverage") or []:
            key = m.get("id") or m.get("url")
            if key:
                index[ingest.canonical_url(str(key))] = {"storyId": s["id"],
                                                         "coverage": s["coverage"]}
    return stories, index


def _story_membership(stories: list, canon: str) -> dict:
    for s in stories:
        for m in s.get("coverage") or []:
            key = str(m.get("id") or m.get("url") or "")
            if key and ingest.canonical_url(key) == canon:
                return {"matched": True, "storyId": s["id"],
                        "articleCount": s.get("totalCoverage"),
                        "publisherCount": s.get("publisherCount"),
                        "distribution": s.get("distribution")}
    return {"matched": False}


# --------------------------------------------------------------------------- #
# Diff (presentation math only — compares two FINISHED feed listings).
# --------------------------------------------------------------------------- #
def _card_key(card: dict) -> str:
    """Cross-corpus identity of a served card: the canonical URL. ``Q{i}`` ids are
    corpus-relative (a one-row composition change shifts every index), so they must never be
    compared across builds; a URL-less corpus (synthetic profile) falls back to the id, where
    cross-build identity is best-effort only."""
    url = card.get("url")
    return ingest.canonical_url(str(url)) if url else str(card.get("id") or "")


def _diff_feed(evaluated: list, base: list) -> dict:
    a = {_card_key(c): c["rank"] for c in base if _card_key(c)}
    b = {_card_key(c): c["rank"] for c in evaluated if _card_key(c)}
    entered = [k for k in b if k not in a]
    left = [k for k in a if k not in b]
    moved = [{"key": k, "from": a[k], "to": b[k]} for k in b if k in a and a[k] != b[k]]
    return {"identical": not entered and not left and not moved,
            "entered": entered, "left": left, "moved": moved}


# --------------------------------------------------------------------------- #
# JSON safety (report hygiene only; never changes what Layer 1 computed).
# --------------------------------------------------------------------------- #
def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    item = getattr(obj, "item", None)                     # numpy scalars
    if callable(item):
        return _json_safe(item())
    return str(obj)


# --------------------------------------------------------------------------- #
# The single public entry point.
# --------------------------------------------------------------------------- #
def evaluate(store_, spec: Optional[dict] = None, baseline=None) -> dict:
    """Evaluate a (possibly injected) corpus composition with the unchanged engine.

    ``store_`` is only read. ``spec`` — see the REPORT CONTRACT in the module docstring.
    ``baseline`` (optional, used only when ``spec["compare"]``): any object with a ``.backend``
    attribute (e.g. the serving ``corpus_refresh.Active``); ONLY its backend is used — this
    module wraps its own ``persist=False`` personalizer around it, never the provided one."""
    s = _normalize_spec(spec)
    notes: list = []
    thresholds = corpus_health.thresholds_from_env()
    scorer = rss_ingest.make_scorer()                     # the ONE scoring configuration

    # -- injected articles: score, then face the SAME candidacy gates as production ---------
    rows = [_as_row(a, scorer) for a in s["inject"]]
    fresh = set(map(id, corpus_health.fresh_articles(rows)))
    base_candidate = corpus_refresh.build_candidate_for(store_, thresholds)
    existing = {str(a.get("canonicalUrl") or "") for a in base_candidate}

    injected_entries: list = []
    candidate = list(base_candidate)
    for row in rows:
        canon = str(row.get("canonicalUrl") or "")
        entry = {"url": row.get("url"), "canonicalUrl": canon,
                 "title": row.get("title") or "", "publisher": row.get("publisher") or "",
                 "scored": _scored_summary(row),
                 "disposition": "evaluated", "resolvedId": None, "graphNode": None,
                 "story": None, "exclusions": []}
        if id(row) not in fresh:
            entry["disposition"] = "dropped_freshness"    # C4: production would never rank it
        elif canon and canon in existing:
            entry["disposition"] = "already_in_candidate"
        else:
            candidate.append(row)                         # read-demand-exemption precedent: append
            existing.add(canon)
        injected_entries.append((row, entry))

    eff_max = corpus_refresh._sizing_kwargs().get("max_items", 1500)
    if len(candidate) > eff_max:
        notes.append(f"candidate ({len(candidate)}) exceeds max_items ({eff_max}); the qbias "
                     "builder subsamples deterministically — injected articles may be dropped")

    # -- the ephemeral evaluated build (never activated; tempfile CSV; no env mutation) ------
    active, result, error = _detached_build(store_, candidate, thresholds, generation=-1)
    corpus_block = {"evaluated": _corpus_block(active, result, error, candidate),
                    "baseline": None}

    backend = active.backend if active is not None else None
    pers = personalize.Personalizer(backend, store_, persist=False) if backend is not None else None

    # -- resolve injected ids against the built corpus --------------------------------------
    if backend is not None:
        url_idx = rec_explain.url_index_from(getattr(backend, "url_by_id", {}) or {})
        graph_ids = {str(x) for x in backend.rec.rec_ids}
        for row, entry in injected_entries:
            if entry["disposition"] == "dropped_freshness":
                continue
            rid = url_idx.get(str(row.get("url") or "")) or url_idx.get(entry["canonicalUrl"])
            entry["resolvedId"] = rid
            entry["graphNode"] = bool(rid is not None and rid in graph_ids)

    # -- story membership (pure builder over the Story Service's own row set) ---------------
    stories, story_idx = ([], {})
    if "story" in s["questions"] or "explanation" in s["questions"]:
        extra = [row for row, e in injected_entries if e["disposition"] == "evaluated"]
        stories, story_idx = _stories_with(store_, extra)
        for row, entry in injected_entries:
            if entry["disposition"] != "dropped_freshness" and "story" in s["questions"]:
                entry["story"] = _story_membership(stories, entry["canonicalUrl"])

    # -- feeds (+ optional resolver explanations) on the evaluated corpus --------------------
    feeds: list = []
    if "feed" in s["questions"]:
        want_expl = "explanation" in s["questions"]
        ctx_cache: dict = {}

        def _ctx(reader):
            key = (reader["kind"], reader.get("id"), reader.get("row"))
            if key not in ctx_cache:
                try:
                    if reader["kind"] == "user":
                        ctx_cache[key] = pers.explanation_context(int(reader["id"]))
                    else:
                        row = (int(backend.demo_user) if reader["kind"] == "demo"
                               else int(reader["row"]))
                        ctx_cache[key] = backend.explanation_context(row)
                except Exception:
                    ctx_cache[key] = None
            return ctx_cache[key]

        for reader in s["readers"]:
            r = _reader_echo(reader)
            for strategy in s["strategies"]:
                for params in s["params"]:
                    if backend is None:
                        feeds.append({"reader": r, "strategy": strategy, "params": params,
                                      "status": "not_built", "served": []})
                        continue
                    status, recs = _feed_for(backend, pers, r, strategy, params)
                    ctx = _ctx(r) if (want_expl and status == "ok") else None
                    feeds.append({"reader": r, "strategy": strategy, "params": params,
                                  "status": status,
                                  "served": _cards(recs, ctx, story_idx, want_expl)})

    # -- exclusion verdicts for injected + asked articles ------------------------------------
    asked: list = []
    if "exclusion" in s["questions"]:
        for row, entry in injected_entries:
            if entry["disposition"] == "dropped_freshness":
                continue
            for reader in s["readers"]:
                for params in s["params"]:
                    if backend is None:
                        entry["exclusions"].append({"reader": _reader_echo(reader),
                                                    "params": params, "status": "not_built"})
                        continue
                    entry["exclusions"].append(
                        _exclusion_for(backend, pers, reader, params,
                                       str(row.get("url") or entry["canonicalUrl"])))
        for q in s["ask"]:
            for reader in s["readers"]:
                for params in s["params"]:
                    e = ({"reader": _reader_echo(reader), "params": params,
                          "status": "not_built"} if backend is None
                         else _exclusion_for(backend, pers, reader, params, q))
                    asked.append({"article": q, **e})

    # -- baseline + diff (compare mode) -------------------------------------------------------
    diff = None
    if s["compare"]:
        if baseline is not None:
            # Baseline reuse rule: ONLY .backend is consulted (plus its own metadata when the
            # object is a corpus_refresh.Active) — never the provided personalizer.
            base_backend = baseline.backend
            fg = base_backend.rec.fg
            corpus_block["baseline"] = {
                "built": True, "error": None, "provided": True,
                "candidateSize": None,
                "candidateSig": getattr(baseline, "candidate_sig", None),
                "items": int(getattr(baseline, "item_count", 0)
                             or len(base_backend.mind.dataset.item_ids)),
                "graph": {"users": int(fg.m), "items": int(fg.n), "edges": int(fg.A.nnz)},
                "validation": None,
            }
        else:
            base_active, base_result, base_error = _detached_build(
                store_, base_candidate, thresholds, generation=-2)
            base_backend = base_active.backend if base_active is not None else None
            corpus_block["baseline"] = _corpus_block(base_active, base_result, base_error,
                                                     base_candidate)
        if backend is not None and base_backend is not None and "feed" in s["questions"]:
            base_pers = personalize.Personalizer(base_backend, store_, persist=False)
            per_feed = []
            for f in feeds:
                if f["status"] != "ok":
                    continue
                b_status, b_recs = _feed_for(base_backend, base_pers, f["reader"],
                                             f["strategy"], f["params"])
                if b_status != "ok":
                    continue
                d = _diff_feed(f["served"], _cards(b_recs, None, {}, False))
                per_feed.append({"reader": f["reader"], "strategy": f["strategy"],
                                 "params": f["params"], **d})
            diff = {"perFeed": per_feed}

    notes.append("rankings are computed over the deterministic simulated population — engine "
                 "behavior, not audience prediction; comparisons are keyed by canonical URL "
                 "because Q{i} ids and the demo reader are corpus-relative")

    report = {
        "reportVersion": REPORT_VERSION,
        "spec": {**{k: s[k] for k in ("ask", "readers", "strategies", "params",
                                      "questions", "compare")},
                 "inject": [{"url": e["url"], "title": e["title"]}
                            for _, e in injected_entries]},
        "corpus": corpus_block,
        "injected": [e for _, e in injected_entries],
        "asked": asked,
        "feeds": feeds,
        "diff": diff,
        "notes": notes,
    }
    return _json_safe(report)


# =========================================================================== #
# S2 — the first client: a thin CLI. Orchestration + rendering ONLY: it builds
# a spec from flags (optionally merged over a --spec JSON file), calls
# evaluate(), and formats the returned report. It never recomputes, filters,
# reorders, or augments report data — the --json output IS the library's
# report, byte-for-byte (pinned by tests).
# =========================================================================== #
def _now_minus(days: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _presets() -> dict:
    """Spec-side injection templates (synthetic probes, fresh by construction). These are
    INPUTS — article dicts handed to evaluate() — not report logic. For story-specific
    probes (matching a real cluster's tokens), craft the article via --spec / --inject-url."""
    return {
        "left": [{"url": "https://theguardian.com/politics/sandbox-left-probe",
                  "title": "government policy debate over election reform bill",
                  "publishedAt": _now_minus(0.2)}],
        "right": [{"url": "https://foxnews.com/politics/sandbox-right-probe",
                   "title": "senate republicans challenge white house border policy",
                   "publishedAt": _now_minus(0.2)}],
        "center": [{"url": "https://reuters.com/world/sandbox-center-probe",
                    "title": "lawmakers reach compromise on federal budget measure",
                    "publishedAt": _now_minus(0.2)}],
        "breaking": [{"url": "https://apnews.com/article/sandbox-breaking-probe",
                      "title": "breaking supreme court issues major ruling on voting rights",
                      "publishedAt": _now_minus(0.02)}],
        "duplicate": [{"url": "https://reuters.com/world/sandbox-dup-a",
                       "title": "wildfire evacuation orders expand across northern county",
                       "publishedAt": _now_minus(0.3)},
                      {"url": "https://bbc.com/news/sandbox-dup-b",
                       "title": "evacuation orders expand as northern county wildfire grows",
                       "publishedAt": _now_minus(0.25)}],
        "low_quality": [{"url": "https://totally-unknown-blog.example/hot-take",
                         "title": "you will not believe this one weird trick",
                         "publishedAt": _now_minus(0.1)}],
    }


def _parse_reader(text: str) -> dict:
    kind, _, val = text.partition(":")
    if kind == "demo":
        return {"kind": "demo"}
    if kind in ("user", "row") and val.lstrip("-").isdigit():
        return {"kind": kind, ("id" if kind == "user" else "row"): int(val)}
    raise SystemExit(f"--reader must be demo, user:<id>, or row:<n> (got {text!r})")


def _fmt_reader(r: dict) -> str:
    return r["kind"] + (f":{r.get('id', r.get('row'))}" if r["kind"] != "demo" else "")


def _fmt_params(p) -> str:
    import json as _json
    return "-" if not p else _json.dumps(p, sort_keys=True)


def _render(report: dict) -> str:
    """Human formatting of the report — display truncation only, no data transformation."""
    L: list = []
    for label in ("evaluated", "baseline"):
        c = report["corpus"].get(label)
        if not c:
            continue
        v = c.get("validation") or {}
        L.append(f"corpus[{label}]{' (provided)' if c.get('provided') else ''}: "
                 f"built={c['built']} items={c['items']} graph={c['graph']} "
                 f"eligible={v.get('eligible')} failures={v.get('failures')}"
                 + (f" error={c['error']}" if c.get("error") else ""))
    for e in report["injected"]:
        sc = e["scored"]
        L.append(f"\ninjected: {e['url']}")
        L.append(f"  disposition={e['disposition']} graphNode={e['graphNode']} "
                 f"resolvedId={e['resolvedId']} outlet={sc['outlet']!r} "
                 f"lean={sc['lean']} topic={sc['category']!r}")
        if e.get("story") is not None:
            st = e["story"]
            L.append(f"  story: matched={st['matched']}"
                     + (f" id={st.get('storyId')} articles={st.get('articleCount')} "
                        f"publishers={st.get('publisherCount')}" if st["matched"] else ""))
        for x in e["exclusions"]:
            head = (f"  verdict[{_fmt_reader(x['reader'])} params={_fmt_params(x['params'])}]"
                    f": {x.get('verdict') or x['status']}")
            L.append(head)
            for sname, bs in sorted((x.get("byStrategy") or {}).items()):
                L.append(f"    {sname}: rank={bs.get('rank')} score={bs.get('score'):.4g} "
                         f"inSlice={bs.get('inSlice')}")
    for x in report["asked"]:
        L.append(f"\nasked: {x['article']} [{_fmt_reader(x['reader'])} "
                 f"params={_fmt_params(x['params'])}] -> {x.get('verdict') or x['status']}")
    for f in report["feeds"]:
        L.append(f"\nfeed[{_fmt_reader(f['reader'])} strategy={f['strategy'] or 'blend'} "
                 f"params={_fmt_params(f['params'])}]: {f['status']}")
        for c in f["served"]:
            ex = c.get("explanation") or {}
            L.append(f"  #{c['rank']:>2} [{c['strategy']}] {c['publisher']} — "
                     f"{(c.get('url') or c.get('id') or '')[:72]}"
                     + (" (cross)" if c.get("crossCutting") else "")
                     + (f"  <{ex['type']}>" if ex else ""))
    if report.get("diff"):
        for d in report["diff"]["perFeed"]:
            L.append(f"\ndiff[{_fmt_reader(d['reader'])} strategy={d['strategy'] or 'blend'} "
                     f"params={_fmt_params(d['params'])}]: "
                     + ("identical" if d["identical"] else
                        f"+{len(d['entered'])} entered / -{len(d['left'])} left / "
                        f"~{len(d['moved'])} moved"))
            for k in d["entered"][:5]:
                L.append(f"  + {k}")
            for k in d["left"][:5]:
                L.append(f"  - {k}")
            for m in d["moved"][:5]:
                L.append(f"  ~ {m['key']} #{m['from']} -> #{m['to']}")
    for n in report["notes"]:
        L.append(f"\nnote: {n}")
    return "\n".join(L)


def main(argv=None) -> int:
    """CLI client of :func:`evaluate`. Exit codes: 0 = report produced and the evaluated
    corpus built; 2 = report produced but the evaluated corpus did NOT build (the report's
    ``corpus.evaluated.error`` says why) — useful for scripting gates."""
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        prog="rec_sandbox", description="Recommendation Evaluation Sandbox (internal tool): "
        "evaluate counterfactual corpus compositions against the unchanged engine, ephemerally "
        "and read-only. Tip: pass a read-only SQLite URL "
        "(sqlite:///file:path.db?mode=ro&uri=true) to make isolation kernel-enforced.")
    ap.add_argument("--db", required=True, help="store URL (e.g. sqlite:///data/ih.db)")
    ap.add_argument("--spec", help="JSON spec file ('-' = stdin); flags below EXTEND it")
    ap.add_argument("--preset", action="append", default=[], choices=sorted(_presets()),
                    help="append a canned injection scenario (repeatable)")
    ap.add_argument("--inject-url", help="ad-hoc single injection: article URL")
    ap.add_argument("--inject-title", default="", help="ad-hoc injection: title")
    ap.add_argument("--inject-published", default=None, help="ad-hoc injection: ISO timestamp")
    ap.add_argument("--inject-outlet", default="", help="ad-hoc injection: outlet override")
    ap.add_argument("--ask", action="append", default=[],
                    help="extra 'why (not) this article?' URL/id (repeatable)")
    ap.add_argument("--reader", action="append", default=[],
                    help="demo | user:<id> | row:<n> (repeatable; default demo)")
    ap.add_argument("--strategy", action="append", default=[],
                    help="blend | rwe-b | rwe-d | adaptive (repeatable; default blend)")
    ap.add_argument("--params", action="append", default=[],
                    help='JSON params dict, e.g. {"beta": 0.8} (repeatable)')
    ap.add_argument("--questions", action="append", default=[], choices=list(QUESTIONS),
                    help="restrict computed sections (repeatable; default all)")
    ap.add_argument("--compare", action="store_true", help="also build a baseline and diff")
    ap.add_argument("--json", action="store_true", help="print the raw report JSON")
    ap.add_argument("--out", help="also write the report JSON to this file")
    args = ap.parse_args(argv)

    spec: dict = {}
    if args.spec:
        text = (sys.stdin.read() if args.spec == "-"
                else pathlib.Path(args.spec).read_text(encoding="utf-8"))
        spec = _json.loads(text)
    inject = list(spec.get("inject") or [])
    for name in args.preset:
        inject.extend(_presets()[name])
    if args.inject_url:
        one = {"url": args.inject_url, "title": args.inject_title}
        if args.inject_published:
            one["publishedAt"] = args.inject_published
        if args.inject_outlet:
            one["outlet"] = args.inject_outlet
        inject.append(one)
    spec["inject"] = inject
    spec["ask"] = list(spec.get("ask") or []) + args.ask
    if args.reader:
        spec["readers"] = list(spec.get("readers") or []) + [_parse_reader(r)
                                                             for r in args.reader]
    if args.strategy:
        spec["strategies"] = list(spec.get("strategies") or []) + \
            [None if s == "blend" else s for s in args.strategy]
    if args.params:
        spec["params"] = list(spec.get("params") or []) + [_json.loads(p) for p in args.params]
    if args.questions:
        spec["questions"] = args.questions
    if args.compare:
        spec["compare"] = True

    import store as store_mod
    report = evaluate(store_mod.Store(args.db), spec)

    payload = _json.dumps(report, indent=1, sort_keys=True)
    if args.out:
        pathlib.Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload if args.json else _render(report))
    return 0 if report["corpus"]["evaluated"]["built"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
