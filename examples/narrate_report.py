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


def report_facts(rep: dict, domain: str = "news") -> dict:
    """Pull ONLY engine-computed numbers out of a ``user_report()`` dict into a flat,
    narratable structure. Every value here is measured -- nothing derived or invented.

    ``domain='reddit'`` (Politosphere): the viewpoint metrics sit on the **validated**
    behavioral axis (lean_corr 0.65), the 'source' is the subreddit community, and the
    text-derived metrics (topic/tone/open-mindedness) are structurally absent."""
    reddit = domain == "reddit"
    sc = rep.get("scores", {}) or {}
    left, center, right = (rep.get("viewpoint") or (None, None, None))
    src = "communities" if reddit else "publishers"

    facts = {("subreddits commented in" if reddit else "articles read"): rep.get("n_clicks")}
    if not reddit:
        facts["political articles"] = rep.get("n_political")
        facts["topic diversity (percentile vs other readers)"] = sc.get("Topic Diversity")
        facts["open-mindedness (percentile)"] = sc.get("Open-Mindedness")
    facts[("community diversity (percentile)" if reddit
           else "source diversity (percentile)")] = sc.get("Source Diversity")
    facts["viewpoint balance (percentile)"] = sc.get("Viewpoint Balance")
    facts["echo-chamber score (percentile, higher = less echo)"] = sc.get("Echo Chamber Score")

    # Source/community facts only when the dataset carries them. On MIND the URLs are MSN
    # with no publisher, so distinct_outlets is 0 -> emitting "0 publishers" would be a
    # false statement, not a measurement. On Reddit the subreddit is always present.
    if rep.get("distinct_outlets"):
        facts[f"share from top {src}"] = _pct(rep.get("top_n_share"))
        facts[f"distinct {src}"] = rep.get("distinct_outlets")
    top_src = ", ".join(o for o, _s in (rep.get("top_publishers") or [])[:5])
    if reddit:
        facts["top subreddits"] = top_src or None                 # communities == the 'topics'
    elif rep.get("distinct_outlets"):
        facts["top publishers"] = top_src or None

    if left is not None and left == left:                         # left==left filters NaN
        axis = " (validated behavioral axis, lean_corr 0.65)" if reddit else ""
        facts[f"political reading left/center/right{axis}"] = (
            f"{round(100 * left)}% / {round(100 * center)}% / {round(100 * right)}%")
    ml = rep.get("mean_lean")
    if ml is not None and ml == ml:
        facts["overall lean of political reading"] = (
            f"{ml:+.2f} on a -2..+2 scale ({'left' if ml < 0 else 'right'}-leaning)")

    if not reddit:
        facts["top topics"] = ", ".join(
            f"{c} ({round(100 * s)}%)" for c, s in (rep.get("top_categories") or [])) or None
        facts["under-read topics (below catalog rate)"] = ", ".join(
            c for c, *_ in (rep.get("blind_spots") or [])) or None
    return {k: v for k, v in facts.items() if v is not None}


def facts_to_text(facts: dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in facts.items())


_SYSTEM = (
    "You are a sharp, plain-spoken media-diet analyst. You are given a reader's metrics "
    "that an analytics engine COMPUTED. Write a brief report (3-4 sentences) that shows "
    "them their reading diet clearly. Lead with the single most striking fact.\n"
    "HARD RULES:\n"
    "1. Use ONLY the numbers you are given, EXACTLY as written. NEVER invent, estimate, "
    "derive, or sum them -- e.g., do not combine '75% / 25% / 0%' into '100%'; say '0% "
    "from the right' instead. Make un-numbered points qualitatively.\n"
    "2. Be concrete: name their top topics and their biggest blind spot.\n"
    "3. If a left/center/right split is given, name the side they under-consume, then add "
    "a short paragraph headed 'The other side, fairly:' that steelmans -- states the "
    "strongest good-faith version of -- that under-consumed viewpoint on ONE of their top "
    "political topics. Make explicit it is the other side's case, not your opinion.\n"
    "4. If candidate articles are provided, recommend 1-2 BY EXACT TITLE from that list "
    "only -- copy the title verbatim, never invent or paraphrase one.\n"
    "5. End with two concrete suggestions for this week.\n"
    "Tone: direct, plain, a touch wry. NO effusive praise or filler -- do not write "
    "'wonderful job', 'great to see', \"it's great that\", or 'snapshot'. Don't pad; get "
    "to the point."
)


def build_messages(facts_text: str, recs=None, domain: str = "news"):
    """(system, user) prompt pair. ``recs`` = real candidate titles/communities (optional)."""
    reddit = domain == "reddit"
    user = ("Reader's computed media-diet metrics (percentiles are vs other readers; "
            "50 = typical, higher = more diverse/balanced):\n" + facts_text)
    if reddit:
        user += ("\n\nNote: this reader is a Reddit commenter -- 'reading' means the political "
                 "subreddit COMMUNITIES they comment in, and the left/right metrics sit on a "
                 "VALIDATED behavioral axis. There is no topic-diversity or blind-spot figure.")
    if recs:
        what = "subreddit communities to follow" if reddit else "articles from the catalog"
        noun = "name" if reddit else "title"
        user += (f"\n\nReal candidate bridging {what} (the opposite side, ones the reader "
                 f"does not frequent) -- you MAY suggest 1-2 of these BY EXACT {noun.upper()} "
                 f"(never invent a {noun}):\n" + "\n".join(f"- {t}" for t in recs))
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


def check_title_grounding(narrative: str, recs) -> list:
    """Quoted article titles in the narrative that don't match any provided candidate ->
    a possibly invented recommendation. Soft check. Looks at quoted strings >=25 chars
    (headline-length; skips short quoted phrases like a section header) against the
    real recs."""
    quoted = re.findall(r"[\"“”]([^\"“”]{25,})[\"“”]", narrative or "")
    recs_l = [r.lower() for r in (recs or [])]
    flagged = []
    for q in quoted:
        ql = q.strip().lower()
        if not any(ql in r or r in ql for r in recs_l):
            flagged.append(q.strip())
    return flagged


def narrate(facts_text: str, call_fn, recs=None, domain: str = "news",
            retries: int = 4, backoff: float = 2.0) -> str:
    """Build the prompt, call the LLM (with backoff), return the narrative text."""
    system, user = build_messages(facts_text, recs, domain)
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


def _rank_demo_users(n_pol, mean_lean, eligible, min_pol: int = 4) -> list:
    """Eligible users ranked for a *striking* demo: most one-sided political reading
    (largest |mean_lean|) first, then most political material -- among readers with at
    least ``min_pol`` political clicks and a defined lean. Pure (vectorized) + testable."""
    import numpy as np
    npol = np.asarray(n_pol)
    ml = np.asarray(mean_lean, dtype=float)
    elig = np.asarray(list(eligible))
    mask = (npol[elig] >= min_pol) & np.isfinite(ml[elig])
    cand = elig[mask] if mask.any() else elig                    # fallback: any eligible
    return sorted((int(u) for u in cand),
                  key=lambda u: (abs(float(ml[u])), int(npol[u])), reverse=True)


def _pick_user(pop, mind, eligible) -> int:
    """Auto-pick the most one-sided eligible reader -- the one whose bubble (and steelman)
    makes the sharpest demo, not just the one with the most filled metrics."""
    return _rank_demo_users(pop["n_pol"], pop["mean_lean"], eligible)[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="ingested .npz (with ideology)")
    ap.add_argument("--domain", choices=["news", "reddit"], default="news",
                    help="news = MIND (text-lean axis); reddit = Politosphere (the "
                         "VALIDATED behavioral axis, lean_corr 0.65)")
    ap.add_argument("--user", type=int, default=None, help="user id (default: auto-pick)")
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini",
                    help="gemini = free (GEMINI_API_KEY); anthropic = paid")
    ap.add_argument("--model", default=None, help="model id (default per --provider)")
    ap.add_argument("--recs", default=None,
                    help="'|'-separated titles/communities to recommend (overrides the "
                         "built-in opposite-lean candidates; pass real RWE-B recs here)")
    ap.add_argument("--min-clicks", type=int, default=5)
    args = ap.parse_args()

    import numpy as np
    from rwe.mind import MINDData
    import health_report as hr

    mind = MINDData.load(args.npz)
    # reddit: the 'source' is the subreddit community (mind.titles), so Source Diversity
    # populates and the viewpoint metrics sit on the validated axis (see health_report).
    src = None if args.domain == "news" else np.asarray(mind.titles)
    pop = hr.compute(mind, min_clicks=args.min_clicks, source=src)
    eligible = hr._eligible_pool(pop, args.min_clicks)
    if len(eligible) == 0:
        raise SystemExit("no users above the click floor in this .npz")
    u = args.user if args.user is not None else _pick_user(pop, mind, eligible)

    rep = hr.user_report(pop, mind, int(u))
    facts = report_facts(rep, args.domain)
    facts_text = facts_to_text(facts)
    recs = (args.recs.split("|") if args.recs else bridge_candidates(mind, rep))

    print(f"=== Reader {u}: engine-computed facts (the LLM may use ONLY these) ===")
    print(facts_text, "\n")
    if recs:
        what = "subreddit communities" if args.domain == "reddit" else "articles"
        print(f"=== real candidate bridging {what} (opposite side) -- the LLM "
              "may recommend ONLY from these ===")
        for t in recs:
            print(f"  - {t}")
        print()

    model = args.model or _DEFAULT_MODELS[args.provider]
    print(f"narrating with {model} ({args.provider}) ...\n")
    narrative = narrate(facts_text, make_text_caller(args.provider, model), recs, args.domain)
    print("=== Information Health narrative ===\n" + narrative + "\n")

    unsupported = check_grounding(narrative, facts_text)
    if unsupported:
        print("⚠ grounding check (numbers): in the narrative but NOT in the metrics "
              f"(verify, may be invented): {', '.join(unsupported)}")
    else:
        print("✓ grounding check (numbers): every number traces to a computed metric.")

    bad_titles = check_title_grounding(narrative, recs)
    if recs and bad_titles:
        print("⚠ grounding check (titles): quoted titles not in the candidate list "
              f"(verify): {' | '.join(bad_titles)}")
    elif recs:
        print("✓ grounding check (titles): any recommended article came from the real list.")


if __name__ == "__main__":
    main()
