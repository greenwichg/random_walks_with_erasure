"""Stage 3 · Explanation Validation.

For the scenario target and every served recommendation: the explanation must ``validate()`` clean
(its gate holds), it must be exactly one sentence (explanations are never combined), and its type
must be known. For the scenario target specifically, the resolved type (and variant, when the
fixture pins one) must match what the scenario was engineered to prove — so a failure names the
explanation type, not an opaque persona.
"""
from __future__ import annotations

import evidence_resolver as er

from . import Check, SENTENCE_SIGNATURES


def _signature_count(exp: dict) -> int:
    """How many distinct explanation-type signatures the message matches (must be exactly 1)."""
    msg = exp.get("message", "")
    return sum(1 for sig in SENTENCE_SIGNATURES.get(exp.get("type"), ()) if sig in msg)


def run(case) -> list:
    checks: list = []

    # -- the scenario target: the "why would I get THIS article?" proof -----
    tr = case.target_rec()
    if tr is not None and case.expected.get("targetType"):
        exp = er.resolve(tr, case.context, case.index)
        want_type = case.expected["targetType"]
        checks.append(Check("explanation", f"target resolves to {want_type}",
                            exp.get("type") == want_type,
                            f"got {exp.get('type')} — {exp.get('message')}"))
        if case.expected.get("targetVariant"):
            checks.append(Check("explanation", f"target variant is {case.expected['targetVariant']}",
                                exp.get("variant") == case.expected["targetVariant"],
                                f"got {exp.get('variant')}"))
        checks.append(Check("explanation", "target explanation validates",
                            er.validate(exp, tr, case.context, case.index) == [],
                            str(er.validate(exp, tr, case.context, case.index))))
    elif case.expected.get("targetTypeNot"):
        # e.g. same_publisher: the sibling must NOT claim story_match (suppression proof)
        exp = er.resolve(tr, case.context, case.index) if tr else {}
        forbidden = case.expected["targetTypeNot"]
        checks.append(Check("explanation", f"target must NOT be {forbidden}",
                            exp.get("type") != forbidden,
                            f"got {exp.get('type')} — {exp.get('message')}"))

    # -- every served recommendation ----------------------------------------
    bad_validate = bad_signature = bad_type = 0
    for r in case.recs:
        exp = er.resolve(r, case.context, case.index)
        if exp.get("type") not in er.TYPES:
            bad_type += 1
            continue
        if er.validate(exp, r, case.context, case.index) != []:
            bad_validate += 1
        if _signature_count(exp) != 1:
            bad_signature += 1

    n = len(case.recs)
    checks.append(Check("explanation", "every served explanation has a known type",
                        bad_type == 0, f"{bad_type}/{n} unknown"))
    checks.append(Check("explanation", "every served explanation validates",
                        bad_validate == 0, f"{bad_validate}/{n} failed validate()"))
    checks.append(Check("explanation", "exactly one explanation per recommendation (never combined)",
                        bad_signature == 0, f"{bad_signature}/{n} matched != 1 signature"))

    # -- the mixed_feed scenario: >= 2 distinct types across the feed --------
    if case.expected.get("minDistinctTypes"):
        kinds = {er.resolve(r, case.context, case.index).get("type") for r in case.recs}
        want = int(case.expected["minDistinctTypes"])
        checks.append(Check("explanation", f">= {want} distinct explanation types in the feed",
                            len(kinds) >= want, f"{sorted(k for k in kinds if k)}"))
    return checks
