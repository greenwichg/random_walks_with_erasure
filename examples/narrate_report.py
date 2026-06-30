"""Turn a user's Information Health Report into a warm, plain-language narrative with an
LLM -- the generative 'brain' on top of the deterministic metrics.

THE RULE (enforced, not hoped-for): the LLM **narrates numbers the engine computed**; it
never invents statistics. ``report_facts()`` pulls only real, measured values out of a
``health_report.user_report()`` dict; the prompt forbids new numbers; and
``check_grounding()`` flags any number in the output that is not in those facts. So the
recommender + metrics stay the source of truth, and the LLM adds what it is actually good
at: the warmth, the blind-spot explanation, and a good-faith *steelman* of the viewpoint
the reader under-consumes.

This is the demo-facing layer of the project: generative where it adds value, a validated
engine everywhere a bare LLM wrapper would hallucinate.

Usage (free Gemini by default -- set GEMINI_API_KEY; see examples/llm_label.py)::

    python examples/narrate_report.py --npz mind_full.npz                 # auto-pick a reader
    python examples/narrate_report.py --npz mind_full.npz --user 9677
    python examples/narrate_report.py --npz mind_full.npz --provider anthropic
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling examples
from llm_label import _DEFAULT_MODELS, _call_with_retry, make_text_caller


def _pct(x):
    return None if x is None else f"{round(100 * float(x))}%"


def report_facts(rep: dict) -> dict:
    """Pull ONLY engine-computed numbers out of a ``user_report()`` dict into a flat,
    narratable structure. Every value here is measured -- nothing derived or invented."""
    sc = rep.get("scores", {}) or {}
    left, center, right = (rep.get("viewpoint") or (None, None, None))
    facts = {
        "articles read": rep.get("n_clicks"),
        "political articles": rep.get("n_political"),
        "topic diversity (percentile vs other readers)": sc.get("Topic Diversity"),
        "source diversity (percentile)": sc.get("Source Diversity"),
        "viewpoint balance (percentile)": sc.get("Viewpoint Balance"),
        "echo-chamber score (percentile, higher = less echo)": sc.get("Echo Chamber Score"),
        "open-mindedness (percentile)": sc.get("Open-Mindedness"),
        "share from top publishers": _pct(rep.get("top_n_share")),
        "distinct publishers": rep.get("distinct_outlets"),
    }
    if left is not None and left == left:                         # left==left filters NaN
        facts["political reading left/center/right"] = (
            f"{round(100 * left)}% / {round(100 * center)}% / {round(100 * right)}%")
    ml = rep.get("mean_lean")
    if ml is not None and ml == ml:
        facts["overall lean of political reading"] = (
            f"{ml:+.2f} on a -2..+2 scale ({'left' if ml < 0 else 'right'}-leaning)")
    facts["top topics"] = ", ".join(
        f"{c} ({round(100 * s)}%)" for c, s in (rep.get("top_categories") or [])) or None
    facts["under-read topics (below catalog rate)"] = ", ".join(
        c for c, *_ in (rep.get("blind_spots") or [])) or None
    facts["top publishers"] = ", ".join(
        o for o, _s in (rep.get("top_publishers") or [])[:4]) or None
    return {k: v for k, v in facts.items() if v is not None}


def facts_to_text(facts: dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in facts.items())


_SYSTEM = (
    "You are a warm, encouraging information-health coach. You are given a reader's "
    "media-diet metrics that an analytics engine COMPUTED. Write a short report (4-6 "
    "sentences) that helps them see their reading diet clearly.\n"
    "HARD RULES:\n"
    "1. Use ONLY the numbers you are given. NEVER invent, estimate, or derive any "
    "statistic, percentage, or count that is not in the data. To make a point you have no "
    "number for, make it qualitatively -- with no number.\n"
    "2. Be specific: name their top topics, their main publishers, and their biggest "
    "blind spot.\n"
    "3. If a left/center/right split is given, say which side they under-consume, then add "
    "a short paragraph headed 'The other side, fairly:' that steelmans -- states the "
    "strongest good-faith version of -- that under-consumed viewpoint on ONE of their top "
    "political topics. Make explicit it is the other side's case, not your own opinion.\n"
    "4. End with exactly two concrete, doable suggestions for this week.\n"
    "Tone: like a supportive coach. Never preachy, moralizing, or medical."
)


def build_messages(facts_text: str, recs=None):
    """(system, user) prompt pair. ``recs`` = real candidate article titles (optional)."""
    user = ("Reader's computed media-diet metrics (percentiles are vs other readers; "
            "50 = typical, higher = more diverse/balanced):\n" + facts_text)
    if recs:
        user += ("\n\nReal candidate bridging articles from the catalog -- you MAY suggest "
                 "1-2 of these BY TITLE (never invent a title):\n"
                 + "\n".join(f"- {t}" for t in recs))
    user += "\n\nWrite the report now, obeying the HARD RULES."
    return _SYSTEM, user


def extract_numbers(text: str) -> set:
    """All numeric tokens (ints, decimals; the % sign is dropped) as strings."""
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def check_grounding(narrative: str, facts_text: str) -> list:
    """Numbers the LLM emitted that are NOT in the facts -> likely invented. Soft check
    (a warning, not a failure): '1'/'2' are allowed for 'two suggestions' etc."""
    allowed = extract_numbers(facts_text) | {"1", "2"}
    return sorted(n for n in extract_numbers(narrative) if n not in allowed)


def narrate(facts_text: str, call_fn, recs=None, retries: int = 4, backoff: float = 2.0) -> str:
    """Build the prompt, call the LLM (with backoff), return the narrative text."""
    system, user = build_messages(facts_text, recs)
    return _call_with_retry(call_fn, system, user, retries, backoff).strip()


def bridge_candidates(mind, rep: dict, k: int = 5) -> list:
    """A few REAL catalog headlines on the side the reader under-consumes (opposite their
    mean lean) that they did not click -- grounded 'what to read' candidates. (Swap in true
    RWE-B recommendations via --recs for the production version.)"""
    import numpy as np
    ml = rep.get("mean_lean")
    if ml is None or ml != ml:
        return []
    pos = np.asarray(mind.item_positions, dtype=float)
    pol = np.asarray(mind.political, dtype=bool)
    titles = np.asarray(mind.titles)
    A = mind.dataset.matrix.tocsr()
    u = rep["user"]
    read = set(A.indices[A.indptr[u]:A.indptr[u + 1]].tolist())
    want_right = ml < 0                                          # under-consumes opposite side
    cand = [i for i in range(len(pos)) if pol[i] and np.isfinite(pos[i]) and i not in read
            and (pos[i] > 0.5 if want_right else pos[i] < -0.5)]
    cand.sort(key=lambda i: -abs(pos[i]))                        # most clearly opposite first
    return [str(titles[i]) for i in cand[:k]]


def _pick_user(pop, mind, eligible) -> int:
    """The eligible reader with the most filled metrics (richest demo narrative)."""
    import health_report as hr
    best, best_n = int(eligible[0]), -1
    for u in eligible:
        rep = hr.user_report(pop, mind, int(u))
        n = sum(v is not None for v in rep["scores"].values())
        if n > best_n:
            best, best_n = int(u), n
    return best


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="ingested MIND .npz (with ideology)")
    ap.add_argument("--user", type=int, default=None, help="user id (default: auto-pick)")
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini",
                    help="gemini = free (GEMINI_API_KEY); anthropic = paid")
    ap.add_argument("--model", default=None, help="model id (default per --provider)")
    ap.add_argument("--recs", default=None,
                    help="'|'-separated article titles to recommend (overrides the "
                         "built-in opposite-lean candidates; pass real RWE-B recs here)")
    ap.add_argument("--min-clicks", type=int, default=5)
    args = ap.parse_args()

    from rwe.mind import MINDData
    import health_report as hr

    mind = MINDData.load(args.npz)
    pop = hr.compute(mind, min_clicks=args.min_clicks)
    eligible = hr._eligible_pool(pop, args.min_clicks)
    if len(eligible) == 0:
        raise SystemExit("no users above the click floor in this .npz")
    u = args.user if args.user is not None else _pick_user(pop, mind, eligible)

    rep = hr.user_report(pop, mind, int(u))
    facts = report_facts(rep)
    facts_text = facts_to_text(facts)
    recs = (args.recs.split("|") if args.recs else bridge_candidates(mind, rep))

    print(f"=== Reader {u}: engine-computed facts (the LLM may use ONLY these) ===")
    print(facts_text, "\n")

    model = args.model or _DEFAULT_MODELS[args.provider]
    print(f"narrating with {model} ({args.provider}) ...\n")
    narrative = narrate(facts_text, make_text_caller(args.provider, model), recs)
    print("=== Information Health narrative ===\n" + narrative + "\n")

    unsupported = check_grounding(narrative, facts_text)
    if unsupported:
        print("⚠ grounding check: numbers in the narrative NOT found in the metrics "
              f"(verify, may be invented): {', '.join(unsupported)}")
    else:
        print("✓ grounding check: every number in the narrative traces to a computed metric.")


if __name__ == "__main__":
    main()
