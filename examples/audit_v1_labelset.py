"""audit_v1_labelset.py — provenance-aware labels for the V1 benchmark, without pretending.

READ-ONLY pipeline over the emitted golden-pairs sheet (audit_verifier_band --emit-pairs).
The manual labeling pass is unavailable, so this assigns labels ONLY where deterministic
evidence is safe against our own recorded counterexamples, and refuses everywhere else.
Nothing here is called human ground truth: every final label carries ``label_source`` and
``label_evidence``, and the statistical section states plainly what the resulting set can and
cannot power.

Provenance tiers (precedence order; first that fires wins):

  human-exhibit    — the rubric-ratified exhibit pairs (docs/EVENT_IDENTITY_RUBRIC.md): the
                     production failures and families hand-verified this session. The ONLY
                     tier treated as authoritative.
  rule:no-affinity — different_event when the pair shares ZERO headline tokens AND zero
                     extracted entities AND (topics differ where both are known, OR published
                     > 48h apart). The time/topic guard exists because a same-day, same-topic,
                     zero-overlap pair is exactly the cross-language shape (the Garmin
                     exhibit) — those stay undetermined.
  rule:near-dup    — same_event (TIER-2, reported separately) when headline Jaccard >= 0.75
                     with >= 2 shared non-lexicon tokens, published within 36h, and no
                     corroborated located conflict. High bar; fires rarely.
  draft-only       — the 112 pre-filled drafts (exhibit drafts excluded) stay DRAFTS: the
                     ``draft_label`` field is preserved, ``label`` stays empty, and the pair
                     counts as needing human judgment. Never silently promoted.
  undetermined     — everything else: ``label`` = "uncertain" with source ``undetermined``,
                     meaning "no safe label exists", NOT "the correct verdict is uncertain".
                     Excluded from any accuracy arithmetic; the report says so.

Rules that were CONSIDERED AND REJECTED, with their killing exhibit, so they are not
re-proposed: pairwise geo-disjointness (X6 measured it vetoing the Ronaldo and Zhu Rongji
same-event pairs — extraction gaps and multi-site families make pair-level geo unsafe);
mutual subject anchoring as a POSITIVE same-event labeler (Toronto/Palomar: "human remains
found" is both template and subject, mutually lead-anchored, different events); intra-story
co-membership (the recorded welds are intra-story).

Self-check: every rule is replayed over the authoritative exhibit pairs, and any
contradiction kills the run loudly — the rules are validated against the only truth we hold.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clustering                # noqa: E402
import story_service             # noqa: E402
import store as store_mod        # noqa: E402
from audit_verifier_band import BOX_OFFICE   # noqa: E402  (the registered extension, verbatim)

NO_AFFINITY_MIN_GAP_H = 48.0
NEAR_DUP_MIN_J = 0.75
NEAR_DUP_MIN_SHARED_NONLEX = 2
NEAR_DUP_MAX_GAP_H = 36.0


def _toks(side: dict) -> frozenset:
    return clustering.title_tokens(side.get("headline") or "")


def _gap_hours(a: dict, b: dict) -> float:
    ta = clustering.parse_time(a.get("publishedAt") or "")
    tb = clustering.parse_time(b.get("publishedAt") or "")
    if ta is None or tb is None:
        return float("inf")
    return abs((ta - tb).total_seconds()) / 3600.0


def rule_no_affinity(a: dict, b: dict, topic_a, topic_b) -> "dict | None":
    ta, tb = _toks(a), _toks(b)
    if ta & tb:
        return None
    ents_a = set(a.get("entities") or ())
    ents_b = set(b.get("entities") or ())
    if ents_a & ents_b:
        return None
    gap = _gap_hours(a, b)
    topics_differ = bool(topic_a and topic_b and topic_a != topic_b)
    if not (topics_differ or gap > NO_AFFINITY_MIN_GAP_H):
        return None                    # same-day same/unknown topic: the cross-language shape
    return {"label": "different_event", "source": "rule:no-affinity",
            "evidence": {"sharedTokens": 0, "sharedEntities": 0,
                         "gapHours": None if gap == float("inf") else round(gap, 1),
                         "topics": [topic_a, topic_b]}}


def rule_near_dup(a: dict, b: dict) -> "dict | None":
    ta, tb = _toks(a), _toks(b)
    j = clustering.jaccard(ta, tb)
    if j < NEAR_DUP_MIN_J:
        return None
    lex = story_service.TEMPLATE_TOKENS | BOX_OFFICE
    nonlex = (ta & tb) - lex
    if len(nonlex) < NEAR_DUP_MIN_SHARED_NONLEX:
        return None
    if _gap_hours(a, b) > NEAR_DUP_MAX_GAP_H:
        return None
    ca = frozenset(a.get("countries") or ())
    cb = frozenset(b.get("countries") or ())
    if ca and cb and not (ca & cb):
        return None                    # located conflict: refuse, never decide
    return {"label": "same_event", "source": "rule:near-dup",
            "evidence": {"jaccard": round(j, 3), "sharedNonLexicon": sorted(nonlex)[:6],
                         "tier": 2}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", required=True, help="the emitted v1_pairs.jsonl")
    ap.add_argument("--db", default=None,
                    help="store URL for the topic join (urls that aged out degrade gracefully)")
    ap.add_argument("--out", required=True, help="labeled sheet output path (JSONL)")
    args = ap.parse_args(argv)

    rows = [json.loads(l) for l in open(args.pairs, encoding="utf-8")]
    keys = {}
    keys_path = args.pairs + ".keys"
    if os.path.exists(keys_path):
        for l in open(keys_path, encoding="utf-8"):
            k = json.loads(l)
            keys[k["pair_id"]] = (k["url_a"], k["url_b"])

    # topic join back through the store — the sheet is publisher/topic-blind by design, and the
    # no-affinity rule's guard wants topics. Aged-out urls simply yield None (the rule then
    # requires the time gap instead).
    topic_of: dict = {}
    if args.db is not None or True:
        try:
            st = store_mod.Store(args.db)
            for r in story_service._fetch(st):
                cat = ((r.get("scored") or {}).get("category") or "").strip() or None
                for k in (r.get("canonicalUrl"), r.get("url")):
                    if k and k not in topic_of:
                        topic_of[k] = cat
        except Exception as e:                        # noqa: BLE001 — degrade, loudly
            print(f"  !! topic join unavailable ({type(e).__name__}) — "
                  f"no-affinity falls back to the time-gap guard alone")

    counts: dict = {}
    contradictions = []
    needs_human = 0
    for r in rows:
        ua, ub = keys.get(r["pair_id"], (None, None))
        topic_a, topic_b = topic_of.get(ua), topic_of.get(ub)
        fired = (rule_no_affinity(r["a"], r["b"], topic_a, topic_b)
                 or rule_near_dup(r["a"], r["b"]))

        if r["class"].startswith("exhibit:"):
            # authoritative — and the self-check: a rule contradicting an exhibit dies here.
            r["label"] = r["draft_label"]
            r["label_source"] = "human-exhibit"
            r["label_evidence"] = {"rubricRule": r.get("draft_rule", "")}
            if fired and fired["label"] != r["label"] and r["label"] in (
                    "same_event", "different_event"):
                contradictions.append((r["pair_id"], r["class"], fired))
        elif fired:
            r["label"] = fired["label"]
            r["label_source"] = fired["source"]
            r["label_evidence"] = fired["evidence"]
        elif r.get("draft_label"):
            r["label"] = ""                            # drafts are NEVER promoted silently
            r["label_source"] = "draft-only"
            r["label_evidence"] = {"draft": r["draft_label"]}
            needs_human += 1
        else:
            r["label"] = "uncertain"
            r["label_source"] = "undetermined"
            r["label_evidence"] = {}
            needs_human += 1
        counts[r["label_source"]] = counts.get(r["label_source"], 0) + 1

    if contradictions:
        print("RULE SELF-CHECK FAILED — a deterministic rule contradicts an authoritative "
              "exhibit label; the rule is wrong, the exhibit is not:")
        for pid, cls, fired in contradictions:
            print(f"  {cls} {pid}: rule said {fired}")
        return 1

    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_label: dict = {}
    for r in rows:
        by_label[r["label"] or "(unlabeled draft)"] = by_label.get(
            r["label"] or "(unlabeled draft)", 0) + 1
    print(f"labeled sheet        : {args.out}  ({len(rows)} pairs)")
    print(f"\n-- coverage by label --")
    for k, v in sorted(by_label.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<20} {v:>4}")
    print(f"\n-- provenance --")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<20} {v:>4}")
    print(f"\n-- human judgment still required --")
    print(f"  {needs_human} pairs (draft-only + undetermined) have no safe label; they are "
          f"excluded from any accuracy arithmetic")

    # -- statistical strength, stated without pretending ------------------------------------ #
    n_same = sum(1 for r in rows if r["label"] == "same_event")
    n_diff = sum(1 for r in rows if r["label"] == "different_event")
    n_same_auth = sum(1 for r in rows if r["label"] == "same_event"
                      and r["label_source"] == "human-exhibit")
    n_diff_auth = sum(1 for r in rows if r["label"] == "different_event"
                      and r["label_source"] == "human-exhibit")
    print(f"\n-- statistical strength for V1 --")
    print(f"  labeled same_event      : {n_same} ({n_same_auth} authoritative)")
    print(f"  labeled different_event : {n_diff} ({n_diff_auth} authoritative)")
    ub_diff = 3.0 / n_diff if n_diff else float("inf")
    ub_same = 3.0 / n_same if n_same else float("inf")
    print(f"  rule-of-three 95% upper bounds at zero observed errors: "
          f"false-same <= {ub_diff:.1%} (needs n>=300 for the registered 1% bar); "
          f"false-different <= {ub_same:.1%} (needs n>=100 for 3%)")
    print(f"  => the registered V1 error bars are {'MEASURABLE' if n_diff >= 300 and n_same >= 100 else 'NOT measurable on this set'}"
          f" — see the V1-prime restructure in the run report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
