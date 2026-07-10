"""Stage 2 · Evidence Validation Engine.

The heart of Phase 1: prove every explanation's evidence is a **subset of the reader context the
resolver was handed** — it never invents a fact. This is the permanent invariant behind the
product promise "every recommendation is justified by your real reading history".

Where Stage 3 asks *"does the gate hold?"* (``evidence_resolver.validate``), Stage 2 asks the
stricter question *"is every value in the evidence drawn from the inputs?"* — a resolver that
computed a new fact (say, a viewpoint share it wasn't given) would pass validate() but fail here.
"""
from __future__ import annotations

import evidence_resolver as er

from . import Check


def _context_facts(ctx: dict, index: dict) -> dict:
    """The universe of facts the resolver was allowed to use — read urls + their publishers, the
    reader's top topics, and the story ids/members reachable from the index."""
    reads = ctx.get("reads") or []
    return {
        "read_urls": {r.get("url") for r in reads},
        "read_pub": {r.get("url"): str(r.get("publisher") or "") for r in reads},
        "top_topics": {str(t) for t in (ctx.get("top_topics") or [])},
        "story_ids": {v["storyId"] for v in index.values()},
    }


def evidence_subset_of_context(exp: dict, rec: dict, ctx: dict, index: dict) -> list:
    """Return the list of evidence facts that are NOT traceable to the context (empty = the
    resolver invented nothing). Complements ``validate`` — same inputs, stricter question."""
    facts = _context_facts(ctx, index)
    ev = exp.get("evidence") or {}
    art = rec.get("article") or {}
    pub = str(art.get("publisher") or "")
    topic = str(art.get("topic") or "")
    bad: list = []
    t = exp.get("type")

    if t == "story_match":
        if ev.get("storyId") not in facts["story_ids"]:
            bad.append(f"storyId {ev.get('storyId')!r} not in the reader's story index")
        if ev.get("readUrl") not in facts["read_urls"]:
            bad.append("cited readUrl was not in the reader's history (invented read)")
        elif ev.get("readPublisher") != facts["read_pub"].get(ev.get("readUrl")):
            bad.append("readPublisher does not match the cited read's real publisher")
        story = index.get(er._canon(str(art.get("url") or art.get("id") or "")))
        members = {er._canon(str(m["url"])) for m in (story or {}).get("coverage", []) if m.get("url")}
        if not story or ev.get("readUrl") not in members:
            bad.append("cited read is not a member of the recommended article's story")
        if ev.get("recPublisher") != pub:
            bad.append("recPublisher does not match the recommended article")
    elif t == "topic_continuity":
        if ev.get("topic") != topic:
            bad.append("evidence topic is not the article's topic")
        for tt in ev.get("topTopics") or []:
            if str(tt) not in facts["top_topics"]:
                bad.append(f"topTopic {tt!r} not among the reader's top topics")
    elif t == "new_publisher":
        band = (ctx.get("familiarity") or (lambda _p: {}))(pub).get("band")
        if ev.get("band") != band:
            bad.append(f"evidence band {ev.get('band')!r} != recomputed familiarity {band!r}")
        if ev.get("publisher") != pub:
            bad.append("evidence publisher does not match the article")
    elif t == "bridge":
        if bool(ev.get("crossCutting")) != bool(rec.get("crossCutting")):
            bad.append("evidence crossCutting disagrees with the recommendation")
    elif t == "long_tail":
        if ev.get("strategy") != "rwe-d":
            bad.append("evidence strategy is not rwe-d")
    # coverage_breadth carries no factual claim, so there is nothing to invent.
    return bad


def run(case) -> list:
    """Stage 2 over the scenario target (if any) and every served recommendation."""
    checks: list = []
    probes = []
    tr = case.target_rec()
    if tr is not None:
        probes.append(("target", tr))
    for r in case.recs:
        probes.append((str((r.get("article") or {}).get("id") or "?")[:48], r))

    invented = 0
    resolved_ok = 0
    for label, rec in probes:
        exp = er.resolve(rec, case.context, case.index)
        if not exp or exp.get("type") not in er.TYPES:
            checks.append(Check("evidence", f"resolved[{label}]", False, "no valid explanation"))
            continue
        resolved_ok += 1
        bad = evidence_subset_of_context(exp, rec, case.context, case.index)
        if bad:
            invented += 1
            checks.append(Check("evidence", f"evidence⊆context[{label}]", False,
                                f"{exp['type']}: " + "; ".join(bad)))

    checks.append(Check("evidence", "every recommendation resolved to an explanation",
                        resolved_ok == len(probes), f"{resolved_ok}/{len(probes)}"))
    checks.append(Check("evidence", "evidence ⊆ context (resolver invents no facts)",
                        invented == 0, f"{invented} explanation(s) referenced facts not in context"))
    return checks
