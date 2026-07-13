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


# --------------------------------------------------------------------------- #
# CLI reader resolution: prefer the persisted demo account over the synthetic one.
# The notebook provisions a demo account (provider "dev" / demo@infodiet.local) with seeded
# reading history. When it exists, the CLI's "demo" reader should resolve to that MEASURED user
# so the report shows real history + relationship analysis; otherwise it falls back to the
# synthetic Backend.demo_user. This is CLI-only and READ-ONLY: it rewrites the spec's readers
# before evaluate() runs — the engine, evaluate(), and the report contract are untouched (at the
# library level ``{"kind": "demo"}`` still means the synthetic reader).
# --------------------------------------------------------------------------- #
_DEMO_PROVIDER = "dev"
_DEMO_ACCOUNT_ID = "demo@infodiet.local"


def _persisted_demo_user_id(store) -> "int | None":
    """The user id of the notebook-provisioned demo account, or None if it hasn't been
    provisioned. Read-only: it SELECTs the identity and NEVER creates one (unlike
    ``upsert_user_by_identity``), so it is safe even against a read-only store. Never raises."""
    try:
        import store as store_mod
        with store.session() as s:
            ident = s.scalar(store_mod.select(store_mod.Identity).where(
                store_mod.Identity.provider == _DEMO_PROVIDER,
                store_mod.Identity.provider_account_id == _DEMO_ACCOUNT_ID))
            return int(ident.user_id) if ident is not None else None
    except Exception:
        return None


def _resolve_demo_readers(readers: list, store) -> "tuple[list, str | None]":
    """Rewrite each synthetic ``demo`` reader to the persisted demo account when it exists, so the
    CLI shows the seeded history instead of the synthetic ``Backend.demo_user``. Returns
    ``(readers, note)`` — ``note`` is a short human line, or None when there is no ``demo`` reader
    to resolve. Read-only; every other reader kind passes through untouched."""
    if not any(r.get("kind") == "demo" for r in readers):
        return readers, None
    uid = _persisted_demo_user_id(store)
    if uid is None:
        return readers, "Persisted demo account not found; using synthetic demo reader."
    resolved = [{"kind": "user", "id": uid} if r.get("kind") == "demo" else r for r in readers]
    return resolved, f"Resolved persisted demo account (user:{uid})."


# =========================================================================== #
# Human render — the Recommendation Investigation Report.
#
# This is a READ-ONLY PRESENTATION LAYER, exactly like the web frontend: it takes
# the report (the frozen source of truth from evaluate(), never modified here) and
# may additionally enrich the DISPLAY with read-only store lookups — resolving a
# card's URL to its catalog title/category/lean, and a real (user:) reader's stored
# reads for context/history. It NEVER alters the report, the ranking, the
# explanations, or the evaluation, and the --json path never touches this code.
# Synthetic (demo/row) readers have no persisted history — that is stated honestly,
# never fabricated.
# =========================================================================== #
_DISPOSITION_TEXT = {
    "evaluated": "scored and added to the evaluated corpus",
    "already_in_candidate": "already present in the corpus",
    "dropped_freshness": "dropped — older than the freshness window (never recommendable)",
}
_VERDICT_TEXT = {
    "recommended": "Recommended to this reader",
    "seen_excluded": "Already read by this reader (never re-recommended)",
    "below_cutoff": "Ranked, but below every strategy's cutoff (not shown)",
    "not_in_graph": "In the catalog but not a recommendable node (e.g. unknown outlet / unresolved lean)",
    "not_in_catalog": "Not found in the catalog",
}
_STATUS_TEXT = {
    "below_threshold": "this reader hasn't read enough for a measured feed",
    "not_built": "the corpus did not build, so there is no feed",
}
_STRATEGY_FULL = {None: "blended feed", "rwe-b": "RWE-B (bridging) only",
                  "rwe-d": "RWE-D (discovery) only", "adaptive": "Adaptive only"}
_STRATEGY_TAG = {"rwe-b": "RWE-B", "rwe-d": "RWE-D", "adaptive": "Adaptive", "story": "Story"}
# explanation.type -> a short "Why" label for the stacked feed cards. Display only: derived
# straight from the report's existing explanation.type (an engine output), never recomputed.
_WHY_SHORT = {
    "bridge": "Bridge Article",
    "story_match": "Story Match",
    "new_publisher": "New Publisher",
    "topic_continuity": "Matches Your Topics",
    "long_tail": "Long-tail Discovery",
    "coverage_breadth": "Broadens Coverage",
}


def _glyphs():
    """✓/✗/rules etc. on a UTF-8 console; ASCII fallback elsewhere (no encode crash on a
    legacy Windows code page)."""
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    if "utf" in enc:
        return {"ok": "✓", "no": "✗", "dot": "·", "arrow": "→", "rule": "═",
                "sub": "─", "bul": "•"}
    return {"ok": "[+]", "no": "[x]", "dot": "-", "arrow": "->", "rule": "=",
            "sub": "-", "bul": "*"}


def _reader_label(r: dict) -> str:
    if r["kind"] == "demo":
        return "demo reader"
    if r["kind"] == "user":
        return f"reader #{r.get('id')} (signed-in)"
    return f"synthetic reader (row {r.get('row')})"


def _params_label(p) -> str:
    if not p:
        return "default settings"
    names = {"beta": "strength", "epsilon": "openness"}
    return ", ".join(f"{names.get(k, k)} {v}" for k, v in sorted(p.items()))


def _lean_bucket(lean) -> str:
    if lean is None:
        return "unknown"
    if lean <= -0.5:
        return "left"
    if lean >= 0.5:
        return "right"
    return "center"


def _lean_phrase(lean) -> str:
    b = _lean_bucket(lean)
    if b == "unknown":
        return "political lean unknown"
    return {"left": "Left", "center": "Center", "right": "Right"}[b] + f" (lean {lean})"


def _meta_line(category: str, lean, g: dict) -> str:
    """One stacked-card descriptor line, ``Category {bullet} Lean`` (e.g. ``Politics • Right``),
    so a read and a recommendation line up field-for-field. Falls back to just the lean word when
    a card has no category. Read-only display; nothing here is computed from the ranking."""
    lean_word = _lean_bucket(lean).capitalize()          # Left / Center / Right / Unknown
    return f"{category} {g['bul']} {lean_word}" if category else lean_word


def _status_phrase(status: str) -> str:
    if status in _STATUS_TEXT:
        return _STATUS_TEXT[status]
    if str(status).startswith("error:"):
        return f"the engine raised {str(status).split(':', 1)[1]}"
    return str(status)


def _verdict_phrase(x: dict) -> str:
    if x.get("status") != "ok":
        return _status_phrase(x.get("status"))
    return _VERDICT_TEXT.get(x.get("verdict"), str(x.get("verdict")))


def _ranks_line(by_strategy: dict) -> "str | None":
    parts = [f"{s} #{v['rank']}" for s, v in sorted((by_strategy or {}).items())
             if v.get("rank") is not None]
    return ("ranked " + " · ".join(parts)) if parts else None


# --------------------------------------------------------------------------- #
# Read-only presentation enrichment (store lookups; never influences evaluation).
# --------------------------------------------------------------------------- #
def _catalog_article(store, url) -> "dict | None":
    """Resolve a canonical URL to its FeedArticle for DISPLAY — title / publisher /
    category / lean. Read-only and best-effort: any miss or failure returns None so the
    caller falls back to the report's publisher/url. Never raises."""
    if store is None or not url:
        return None
    try:
        row = store.get_feed_article(ingest.canonical_url(str(url)))
        if not row:
            return None
        sc = row.get("scored") or {}
        return {"title": row.get("title") or sc.get("title") or "",
                "publisher": engine._prettify(row.get("publisher") or sc.get("outlet") or "")
                or "Unknown",
                "category": engine._prettify(sc.get("category")) if sc.get("category") else "",
                "lean": sc.get("lean")}
    except Exception:
        return None


def _reader_history(store, reader: dict) -> "list | None":
    """Newest-first stored reads for a real (user:) reader, as display rows
    {title, publisher, category, lean}. None for synthetic (demo/row) readers or on any
    failure — the caller then shows the honest 'no persistent history' message. Read-only."""
    if store is None or reader.get("kind") != "user":
        return None
    try:
        rows = store.list_reads(int(reader["id"]))
    except Exception:
        return None
    out = []
    for r in rows:
        sc = r.get("scored") or {}
        out.append({"title": sc.get("title") or "(untitled)",
                    "publisher": engine._prettify(sc.get("outlet") or "") or "Unknown",
                    "category": engine._prettify(sc.get("category")) if sc.get("category") else "",
                    "lean": sc.get("lean"),
                    "url": ingest.canonical_url(str(r.get("canonicalUrl") or ""))})
    return out


def _history_stats(reads: list) -> dict:
    from collections import Counter
    pubs, topics = Counter(), Counter()
    dist = {"left": 0, "center": 0, "right": 0, "unknown": 0}
    for r in reads:
        if r["publisher"]:
            pubs[r["publisher"]] += 1
        if r["category"]:
            topics[r["category"]] += 1
        dist[_lean_bucket(r["lean"])] += 1
    return {"total": len(reads), "publishers": pubs, "topics": topics, "leanDist": dist}


def _profile_phrase(dist: dict) -> str:
    known = {k: dist[k] for k in ("left", "center", "right") if dist[k]}
    if not known:
        return "not enough political reads to characterise"
    top = sorted(known, key=known.get, reverse=True)
    names = {"left": "Left", "center": "Center", "right": "Right"}
    return "mostly " + " / ".join(names[k] for k in top[:2])


# --------------------------------------------------------------------------- #
# The report.
# --------------------------------------------------------------------------- #
def _render(report: dict, store=None, db: "str | None" = None,
            elapsed_ms: "float | None" = None) -> str:
    """The Recommendation Investigation Report — a plain-English, sectioned view of the
    report, optionally enriched (read-only) from ``store``. Presentation only; the report,
    ranking, explanations, and evaluation are never altered, and --json is unaffected.

    Reader-type detection is DATA-DRIVEN: a reader that resolves to a store user with reads is
    "measured" (real user, or the persisted demo / exhibit ACCOUNT accessed as ``user:<id>``);
    one with an id but no reads is "measured, no reads yet"; a synthetic corpus reader
    (``demo`` / ``row`` in the sandbox) has no persisted history and says so honestly."""
    g = _glyphs()
    out: list = []

    def sub(title: str) -> None:
        out.append(f"\n{title}\n" + g["sub"] * len(title))

    def mark(flag) -> str:
        return g["ok"] if flag else g["no"]

    spec = report["spec"]
    feeds = report["feeds"]
    inj = report["injected"]
    ev = report["corpus"].get("evaluated") or {}
    served_urls = {ingest.canonical_url(str(c.get("url") or c.get("id") or ""))
                   for f in feeds for c in (f.get("served") or [])}

    # reader reads, resolved ONCE per reader (read-only store lookup), keyed by reader identity
    hist = {(_reader_label(r)): _reader_history(store, r) for r in spec["readers"]}

    def history_of(reader) -> "list | None":
        return hist.get(_reader_label(reader))

    def enriched_title(url, fallback=None) -> str:
        a = _catalog_article(store, url)
        return (a and a.get("title")) or fallback or (str(url or "")[:70])

    out.append(g["rule"] * 70)
    out.append(" Recommendation Investigation Report")
    out.append(g["rule"] * 70)

    # ---- 1. EVALUATION SUMMARY --------------------------------------------- #
    sub("1. Evaluation Summary")
    out.append(f"  Database:     {db or '(store)'}")
    out.append(f"  Reader(s):    {', '.join(_reader_label(r) for r in spec['readers'])}")
    out.append("  Strategy:     "
               + ", ".join(_STRATEGY_FULL.get(s, str(s)) for s in spec["strategies"]))
    out.append(f"  Injected:     {len(inj)} article" + ("" if len(inj) == 1 else "s"))
    out.append("  Comparison:   " + ("on (baseline vs evaluated)" if spec.get("compare") else "off"))
    if any(spec["params"]):
        out.append("  Parameters:   " + " | ".join(_params_label(p) for p in spec["params"]))

    for e in inj:
        appears = e["canonicalUrl"] in served_urls
        st = e.get("story") or {}
        out.append("")
        out.append(f"  Injected Article   {mark(e['disposition'] != 'dropped_freshness')} "
                   + ("Accepted" if e["disposition"] != "dropped_freshness" else "Rejected (stale)"))
        out.append(f"  Recommendation     {mark(appears)} "
                   + ("Appears in recommendation feed" if appears else "Not in the feed"))
        out.append(f"  Story Match        {mark(st.get('matched'))} "
                   + ("Matched a story cluster" if st.get("matched") else "None"))
        out.append(f"  Graph              {mark(e['graphNode'] is True)} "
                   + ("Connected" if e["graphNode"] is True else "Not a graph node"))

    out.append("\n  Overall Result")
    if not ev.get("built"):
        out.append(f"    {g['no']} The corpus did not build ({ev.get('error')}), so no "
                   "recommendations were produced.")
    elif inj:
        e = inj[0]
        if e["graphNode"] is True:
            out.append("    The injected article entered the recommendation graph"
                       + (" and appears in the feed." if e["canonicalUrl"] in served_urls
                          else ", but did not make the served feed."))
        elif e["disposition"] == "dropped_freshness":
            out.append("    The injected article was too old to be recommendable (dropped).")
        else:
            out.append("    The injected article did not become a recommendable graph node.")
    else:
        out.append("    Evaluated the reader's recommendation feed (no article injected).")
    if report.get("diff"):
        tot = {"entered": 0, "left": 0, "moved": 0}
        for d in report["diff"]["perFeed"]:
            for k in tot:
                tot[k] += len(d[k])
        out.append(f"    Feed change: Entered {tot['entered']} {g['dot']} Left {tot['left']} "
                   f"{g['dot']} Moved {tot['moved']} (across {len(report['diff']['perFeed'])} feed(s))")
    if elapsed_ms is not None:
        out.append(f"    Execution time: {elapsed_ms / 1000.0:.2f}s")

    # ---- 2. READER CONTEXT -------------------------------------------------- #
    sub("2. Reader Context")
    for r in spec["readers"]:
        reads = history_of(r)
        out.append(f"  {_reader_label(r)}")
        if reads is None:
            out.append("    This evaluation uses a synthetic reader generated from the "
                       "recommendation corpus.")
            out.append("    No persisted reading history exists for this reader.")
            out.append("    (The persisted demo / exhibit account is a measured user — "
                       "investigate it with --reader user:<id>.)")
            continue
        if not reads:
            out.append("    Measured reader with no stored reads yet — the feed below comes "
                       "from the corpus, not a reading history.")
            continue
        s = _history_stats(reads)
        d = s["leanDist"]
        out.append(f"    Total reads:            {s['total']}")
        out.append(f"    Political profile:      {_profile_phrase(d)}")
        out.append(f"    Political distribution: Left {d['left']} {g['dot']} Center {d['center']} "
                   f"{g['dot']} Right {d['right']}"
                   + (f" {g['dot']} Unknown {d['unknown']}" if d['unknown'] else ""))
        out.append("    Top publishers:         "
                   + (", ".join(f"{p} ({n})" for p, n in s["publishers"].most_common(5)) or "—"))
        out.append("    Top topics:             "
                   + (", ".join(f"{t} ({n})" for t, n in s["topics"].most_common(5)) or "—"))

    # ---- 3. READING HISTORY ------------------------------------------------- #
    # Stacked (Title / Publisher / Category • Lean) so each read lines up field-for-field with
    # the Recommendation Feed below, for a direct visual comparison. The actual stored titles
    # are shown, never a summary. Read-only; the report and evaluation are untouched.
    sub("3. Reading History")
    for r in spec["readers"]:
        reads = history_of(r)
        if reads is None:
            out.append(f"  {_reader_label(r)}: not available — synthetic reader "
                       "(see Reader Context).")
            continue
        if not reads:
            out.append(f"  {_reader_label(r)}: no stored reads for this measured reader yet.")
            continue
        shown = reads[:10]
        head = f"Reading History ({len(reads)} reads)"
        head += (", newest first" if len(shown) == len(reads)
                 else f", showing the newest {len(shown)}")
        out.append(f"  {_reader_label(r)} — {head}")
        for i, a in enumerate(shown, 1):
            out.append("")
            out.append(f"    {i:>2}.")
            out.append(f"        {a['title']}")
            out.append(f"        {a['publisher']}")
            out.append(f"        {_meta_line(a['category'], a['lean'], g)}")
        out.append("")

    # ---- 4. EXPERIMENT ------------------------------------------------------ #
    if inj:
        sub("4. Experiment")
        for e in inj:
            sc = e["scored"]
            out.append(f"  {e['title'] or e['url']}")
            out.append(f"    Publisher:      {sc['outlet'] or 'unknown'}")
            out.append(f"    Category:       {sc['category'] or '—'}")
            out.append(f"    Political lean: {_lean_phrase(sc['lean'])}")
            out.append("    Evaluation:")
            out.append(f"      {mark(e['disposition'] != 'dropped_freshness')} "
                       f"{_DISPOSITION_TEXT.get(e['disposition'], e['disposition'])}")
            out.append(f"      {mark(e['graphNode'] is True)} "
                       + ("added to the recommendation graph as " + str(e['resolvedId'])
                          if e['graphNode'] is True else "not a recommendable graph node"))
            stm = (e.get("story") or {}).get("matched")
            out.append(f"      {mark(stm)} "
                       + ("part of a story cluster" if stm else "no story match"))
            appears = e["canonicalUrl"] in served_urls
            out.append(f"      {mark(appears)} "
                       + ("eligible and shown in the feed" if appears
                          else "not shown in the served feed"))
            for x in e["exclusions"]:
                out.append(f"      For {_reader_label(x['reader'])} "
                           f"({_params_label(x['params'])}): {_verdict_phrase(x)}")
                rl = _ranks_line(x.get("byStrategy"))
                if rl:
                    out.append(f"          {rl}")

    # ---- 5. RECOMMENDATION FEED --------------------------------------------- #
    # Same stacked layout as Reading History (Title / Publisher / Category • Lean) so the reader's
    # reads and the current recommendations can be read side by side, plus the engine's own
    # explanation reasons under "Why". Enrichment is read-only; ranking/explanations are the
    # report's, shown verbatim.
    sub("5. Recommendation Feed")
    for f in feeds:
        out.append(f"  {_reader_label(f['reader'])}, "
                   f"{_STRATEGY_FULL.get(f['strategy'], str(f['strategy']))}"
                   + (f", {_params_label(f['params'])}" if f["params"] else ""))
        if f["status"] != "ok":
            out.append(f"    No recommendations — {_status_phrase(f['status'])}.")
            continue
        if not f["served"]:
            out.append("    No recommendations were generated for this reader/strategy.")
            continue
        for c in f["served"]:
            a = _catalog_article(store, c.get("url"))
            title = (a and a.get("title")) or c.get("publisher") or "(untitled)"
            publisher = (a and a.get("publisher")) or c.get("publisher") or "Unknown"
            category = a.get("category") if a else ""
            lean = a.get("lean") if a else None
            tag = _STRATEGY_TAG.get(c.get("strategy"), c.get("strategy"))
            out.append("")
            out.append(f"    {c['rank']:>2}.")
            out.append(f"        {title}")
            out.append(f"        {publisher}")
            out.append(f"        {_meta_line(category, lean, g)}" + (f"   [{tag}]" if tag else ""))
            ex = c.get("explanation") or {}
            whys = (["Cross-cutting"] if c.get("crossCutting") else []) \
                + ([_WHY_SHORT[ex["type"]]] if ex.get("type") in _WHY_SHORT else [])
            if whys:
                out.append("        Why")
                for w in whys:
                    out.append(f"          {g['ok']} {w}")

    # ---- 6. RELATIONSHIP ANALYSIS ------------------------------------------- #
    sub("6. Relationship Analysis")
    _any_rel = False
    for r in spec["readers"]:
        reads = history_of(r)
        rfeeds = [f for f in feeds if f["reader"] == _reader_echo(r)
                  and f["status"] == "ok" and f["served"]]
        if not rfeeds:
            continue
        _any_rel = True
        out.append(f"  {_reader_label(r)}")
        # enrich the served cards once
        cards = [{"publisher": (_catalog_article(store, c.get("url")) or {}).get("publisher")
                  or c.get("publisher") or "Unknown",
                  "category": (_catalog_article(store, c.get("url")) or {}).get("category") or "",
                  "lean": (_catalog_article(store, c.get("url")) or {}).get("lean"),
                  "cross": bool(c.get("crossCutting")),
                  "type": (c.get("explanation") or {}).get("type"),
                  "url": ingest.canonical_url(str(c.get("url") or ""))}
                 for f in rfeeds for c in f["served"]]
        feed_pubs = {c["publisher"] for c in cards}
        feed_cats = {c["category"] for c in cards if c["category"]}
        if reads:
            s = _history_stats(reads)
            read_pubs = set(s["publishers"])
            read_cats = [t for t, _ in s["topics"].most_common()]
            read_urls = {a["url"] for a in reads if a["url"]}
            out.append("    Reading Pattern")
            out.append(f"      {g['bul']} {_profile_phrase(s['leanDist'])} political reading")
            heavy = ", ".join(p for p, _ in s["publishers"].most_common(3))
            if heavy:
                out.append(f"      {g['bul']} Heavy exposure to {heavy}")
            if read_cats:
                out.append(f"      {g['bul']} Strong interest in {', '.join(read_cats[:3])}")
            new_pubs = sorted(feed_pubs - read_pubs)
            maintained = [t for t in read_cats[:4] if t in feed_cats]
            overlap = read_urls & {c["url"] for c in cards}
            out.append("    Recommendation Behaviour")
            cross = any(c["cross"] for c in cards)
            out.append(f"      {mark(cross)} Introduces cross-cutting / opposing viewpoints")
            out.append(f"      {mark(new_pubs)} Introduces {len(new_pubs)} new publisher(s)"
                       + (f": {', '.join(new_pubs[:4])}" if new_pubs else ""))
            for t in maintained:
                out.append(f"      {g['ok']} Maintains {t} coverage")
            out.append(f"      {mark(any(c['type'] == 'bridge' for c in cards))} "
                       "Includes bridge articles")
            out.append(f"      {mark(not overlap)} Avoids already-read articles"
                       + ("" if not overlap else f" ({len(overlap)} overlap!)"))
        else:
            out.append("    Reading Pattern")
            out.append("      (this reader is synthetic or has no reads — no history to compare "
                       "against; the recommendation behaviour below is described on its own)")
            out.append("    Recommendation Behaviour")
            out.append(f"      {mark(any(c['cross'] for c in cards))} "
                       "Includes cross-cutting viewpoints")
            out.append(f"      {mark(any(c['type'] == 'bridge' for c in cards))} "
                       "Includes bridge articles")
            out.append(f"      {g['bul']} Publishers in the feed: "
                       + ", ".join(sorted(feed_pubs)[:5]))
    if not _any_rel:
        out.append("  No served feed to analyse (no recommendations were produced).")

    # ---- 7. FEED CHANGES ---------------------------------------------------- #
    sub("7. Feed Changes")
    if not report.get("diff"):
        out.append("  No baseline to compare against — run with --compare to see how the "
                   "feed changes.")
    else:
        for d in report["diff"]["perFeed"]:
            out.append(f"  {_reader_label(d['reader'])}, "
                       f"{_STRATEGY_FULL.get(d['strategy'], str(d['strategy']))}")
            if d["identical"]:
                out.append("    No change — the feed is identical to the baseline.")
                continue
            if d["entered"]:
                out.append("    New Recommendations")
                for k in d["entered"][:8]:
                    out.append(f"      {g['ok']} {enriched_title(k)}")
            if d["left"]:
                out.append("    Removed Recommendations")
                for k in d["left"][:8]:
                    out.append(f"      {g['no']} {enriched_title(k)}")
            if d["moved"]:
                out.append("    Rank Changes")
                for m in d["moved"][:8]:
                    out.append(f"      Position {m['from']} {g['arrow']} {m['to']}   "
                               f"{enriched_title(m['key'])}")

    # ---- 8. DEVELOPER OBSERVATIONS ------------------------------------------ #
    sub("8. Developer Observations")
    out.append("  (engineering observations, not objective measurements)")
    measured = next(((r, history_of(r)) for r in spec["readers"] if history_of(r)), None)
    any_cards = any(f["served"] for f in feeds if f["status"] == "ok")
    if not any_cards:
        out.append("  No recommendations were generated, so there is nothing to observe about "
                   "the feed.")
    elif measured:
        r, reads = measured
        s = _history_stats(reads)
        rfeeds = [f for f in feeds if f["reader"] == _reader_echo(r) and f["status"] == "ok"]
        cards = [{"publisher": (_catalog_article(store, c.get("url")) or {}).get("publisher")
                  or c.get("publisher"), "cross": bool(c.get("crossCutting")),
                  "url": ingest.canonical_url(str(c.get("url") or ""))}
                 for f in rfeeds for c in f["served"]]
        new_pubs = sorted({c["publisher"] for c in cards} - set(s["publishers"]))
        overlap = {a["url"] for a in reads if a["url"]} & {c["url"] for c in cards}
        out.append(f"  The reader's political reading is {_profile_phrase(s['leanDist'])}.")
        if new_pubs:
            out.append(f"  The engine introduced {len(new_pubs)} publisher(s) the reader has "
                       f"not read ({', '.join(new_pubs[:4])})"
                       + (" while surfacing cross-cutting viewpoints."
                          if any(c["cross"] for c in cards) else "."))
        out.append("  No already-read articles were recommended."
                   if not overlap else
                   f"  WARNING: {len(overlap)} already-read article(s) appeared in the feed.")
        out.append("  Recommendation behaviour appears consistent with the observed reading "
                   "history.")
    else:
        out.append("  This evaluation uses a synthetic reader, so there is no stored reading "
                   "history to compare against.")
        out.append("  The observations above describe the engine's recommendation profile for "
                   "the synthetic reader.")

    # ---- 9. RECOMMENDATION EXPLANATION MATRIX ------------------------------- #
    sub("9. Recommendation Explanation Matrix")
    cols = [("Bridge", lambda c, e: e.get("type") == "bridge"),
            ("Cross-cutting", lambda c, e: bool(c.get("crossCutting"))),
            ("New Pub", lambda c, e: e.get("type") == "new_publisher"),
            ("Long-tail", lambda c, e: e.get("type") == "long_tail"),
            ("Story", lambda c, e: e.get("type") == "story_match")]
    _any_matrix = False
    for f in feeds:
        if f["status"] != "ok" or not f["served"]:
            continue
        _any_matrix = True
        out.append(f"  {_reader_label(f['reader'])}, "
                   f"{_STRATEGY_FULL.get(f['strategy'], str(f['strategy']))}")
        out.append("    #   " + "  ".join(f"{name:^13}" for name, _ in cols))
        for c in f["served"]:
            ex = c.get("explanation") or {}
            cells = "  ".join(f"{(g['ok'] if fn(c, ex) else ' '):^13}" for _, fn in cols)
            out.append(f"    {c['rank']:>2}  {cells}")
    if not _any_matrix:
        out.append("  No recommendations were generated, so there is no explanation matrix.")

    # ---- 10. TECHNICAL DIAGNOSTICS ------------------------------------------ #
    sub("10. Technical Diagnostics")
    for label, title in (("evaluated", "Evaluated corpus"), ("baseline", "Baseline corpus")):
        c = report["corpus"].get(label)
        if not c:
            continue
        if not c.get("built"):
            v = c.get("validation") or {}
            out.append(f"  {title}: not built — {c.get('error')} "
                       f"({', '.join(v.get('failures') or []) or 'no detail'})")
            continue
        gph = c.get("graph") or {}
        out.append(f"  {title}: built {g['dot']} items={c.get('items')} {g['dot']} "
                   f"candidateSig={c.get('candidateSig')}")
        if gph:
            out.append(f"    Graph: users={gph.get('users')} items={gph.get('items')} "
                       f"edges={gph.get('edges')}")
        v = c.get("validation") or {}
        if v:
            out.append(f"    Validation: eligible={v.get('eligible')} "
                       f"failures={v.get('failures')} perBucket={v.get('perBucket')}")
    for f in feeds:
        ex = next((x for e in inj for x in e["exclusions"]
                   if x["reader"] == f["reader"] and x["params"] == f["params"]), None)
        pu = (ex or {}).get("paramsUsed")
        if pu:
            out.append(f"  Params in effect [{_reader_label(f['reader'])}, "
                       f"{_STRATEGY_FULL.get(f['strategy'], str(f['strategy']))}]: {pu}")
            break
    if report.get("diff"):
        for d in report["diff"]["perFeed"]:
            out.append(f"  diff [{_reader_label(d['reader'])}, "
                       f"{_STRATEGY_FULL.get(d['strategy'], str(d['strategy']))}]: "
                       f"entered={len(d['entered'])} left={len(d['left'])} "
                       f"moved={len(d['moved'])} identical={d['identical']}")
    for x in report["asked"]:      # --ask "why (not) this article?" verdicts (developer queries)
        out.append(f"  Asked: {x['article']}")
        out.append(f"    For {_reader_label(x['reader'])} ({_params_label(x['params'])}): "
                   f"{_verdict_phrase(x)}")
        rl = _ranks_line(x.get("byStrategy"))
        if rl:
            out.append(f"      {rl}")
    if elapsed_ms is not None:
        out.append(f"  Execution time: {elapsed_ms:.1f} ms")
    for n in report["notes"]:
        out.append(f"  note: {n}")

    return "\n".join(out)


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
                    help="demo (persisted demo account if present, else synthetic) | user:<id> | "
                         "row:<n> (repeatable; default demo)")
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
    import time as _time
    store_ = store_mod.Store(args.db)
    # CLI-only reader resolution (read-only): prefer the notebook's persisted demo account — a
    # measured user with seeded history — over the synthetic demo reader. Rewrites the spec's
    # readers BEFORE evaluate(), so --json remains the faithful serialization of evaluate()'s
    # report for the resolved spec. The note goes to stderr, keeping --json's stdout pure.
    spec["readers"], _demo_note = _resolve_demo_readers(
        spec.get("readers") or [{"kind": "demo"}], store_)
    if _demo_note:
        print(_demo_note, file=sys.stderr)
    _t0 = _time.perf_counter()
    report = evaluate(store_, spec)          # the report is the source of truth (unchanged)
    elapsed_ms = (_time.perf_counter() - _t0) * 1000.0

    payload = _json.dumps(report, indent=1, sort_keys=True)   # --json path: BYTE-IDENTICAL
    if args.out:
        pathlib.Path(args.out).write_text(payload + "\n", encoding="utf-8")
    # The human render is a read-only PRESENTATION layer: it may enrich display from the store
    # (catalog titles, a real reader's history) but never alters the report or the evaluation.
    print(payload if args.json
          else _render(report, store=store_, db=args.db, elapsed_ms=elapsed_ms))
    return 0 if report["corpus"]["evaluated"]["built"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
