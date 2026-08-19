"""audit_country_interaction.py — did the country selector disturb the older tuning controls?

READ-ONLY. It writes no settings, changes no configuration, and touches no threshold: it derives
per-request parameters from settings dicts held in memory, asks the live recommender for the
resulting feeds, and compares them. The reader's stored preferences are never read or written, so
running this cannot alter anyone's account.

The question is interference. Political openness, Recommendation strength and the eight Interest
Intensity sliders all shipped before the country selector, and the country nudge now shares one
sort key with the interest nudge and runs inside the same blend. That is exactly the shape where a
new control quietly eats an old one, so each claim below is measured rather than reasoned about:

  A. Settings layer — selecting a country changes ONLY the country field. Every other stored
     preference survives normalize_settings untouched, including the political axis.
  B. Parameter mapping — rec_params_from_settings emits `country` alongside, never instead of,
     `openness` / `beta` / `interests`.
  C. Openness invariance — the RWE-B bridge-slot budget (blend_plan_for) is byte-identical with
     and without a country at every openness value. Political Viewpoint Diversity is that slider.
  D. Strength invariance — the RWE-D `beta` a request resolves is identical with and without a
     country.
  E. The served feed, over the five scenarios the audit asks for:
       1  Global + defaults                     (the baseline)
       2  country + defaults
       3  country + interest at 10
       4  country + interest at 1
       5  Global restored                       (must equal 1 byte for byte)
     with the monotonicity claim `1 <= 5 <= 10` re-checked WHILE a country is selected, since that
     is the combination the interest curve was never measured under.

Exposure is rank-weighted (sum of 1/(rank+1) over the topic's cards), the same measure the
Interest Intensity verification used: it rises when a topic's cards move up OR when more appear,
so a nudge that only reorders is still visible.

    dc exec -T api python examples/audit_country_interaction.py --engine-user 1 --country IN
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np                # noqa: E402
import feed_source                # noqa: E402
import settings_service as ss     # noqa: E402
import store as store_mod         # noqa: E402


def _fmt(ok: bool) -> str:
    return "PASS" if ok else "**FAIL**"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--country", default="IN", help="ISO alpha-2 to select in scenarios 2-4")
    ap.add_argument("--interest", default="business",
                    help="an Interest Intensity slider key (settings_service.INTEREST_KEYS)")
    ap.add_argument("--engine-user", type=int, default=0,
                    help="a real reader's engine user id; omitted probes the base demo reader")
    args = ap.parse_args(argv)
    want = args.country.strip().upper()
    key = args.interest.strip()
    if key not in ss.INTEREST_KEYS:
        print(f"--interest must be one of {', '.join(ss.INTEREST_KEYS)}")
        return 2

    import api_server as engine

    failures = []

    def check(label, ok, detail=""):
        print(f"  [{_fmt(ok)}] {label}{('  — ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    # -- A. settings layer ------------------------------------------------------------------- #
    print(f"-- A. selecting a country changes only the country --")
    tuned = {"politicalOpenness": 20, "recommendationStrength": 80,
             "interests": {**{k: 5 for k in ss.INTEREST_KEYS}, key: 9},
             "readingGoalMinutes": 45, "weeklyReport": False, "edition": "GB"}
    before = ss.normalize_settings(tuned)
    after = ss.normalize_settings(tuned, {"recommendationCountry": want})
    changed = {k for k in before if before[k] != after.get(k)}
    check("only recommendationCountry changes", changed == {"recommendationCountry"},
          f"changed={sorted(changed)}")
    check("political axis preserved", after["politicalOpenness"] == 20,
          f"politicalOpenness={after['politicalOpenness']}")
    check("strength preserved", after["recommendationStrength"] == 80)
    check("interest values preserved", after["interests"] == before["interests"])
    check("Places edition untouched", after["edition"] == "GB")
    restored = ss.normalize_settings(after, {"recommendationCountry": None})
    check("reset to Global restores the original settings exactly", restored == before)

    # -- B. parameter mapping ----------------------------------------------------------------- #
    print(f"\n-- B. the country key is added, never substituted --")
    p_tuned = engine.rec_params_from_settings(tuned) or {}
    p_both = engine.rec_params_from_settings({**tuned, "recommendationCountry": want}) or {}
    check("openness survives", p_both.get("openness") == p_tuned.get("openness"),
          f"{p_tuned.get('openness')} -> {p_both.get('openness')}")
    check("beta survives", p_both.get("beta") == p_tuned.get("beta"),
          f"{p_tuned.get('beta')} -> {p_both.get('beta')}")
    check("interests survive", p_both.get("interests") == p_tuned.get("interests"))
    check("country added", p_both.get("country") == want)
    check("no other key appears or disappears",
          set(p_both) - {"country"} == set(p_tuned), f"{sorted(p_both)}")

    # -- C/D. openness + strength invariance --------------------------------------------------- #
    print(f"\n-- C. blend plan (Political Openness / Viewpoint Diversity) is country-blind --")
    for openness in (0, 20, 50, 80, 100):
        a = engine.blend_plan_for(engine.rec_params_from_settings({"politicalOpenness": openness}))
        b = engine.blend_plan_for(engine.rec_params_from_settings(
            {"politicalOpenness": openness, "recommendationCountry": want}))
        check(f"openness {openness:>3}: plan identical", a == b, f"{a}")

    print(f"\n-- D. RWE-D beta (Recommendation Strength) is country-blind --")
    for strength in (0, 25, 50, 75, 100):
        a = (engine.rec_params_from_settings({"recommendationStrength": strength}) or {}).get("beta")
        b = (engine.rec_params_from_settings(
            {"recommendationStrength": strength, "recommendationCountry": want}) or {}).get("beta")
        check(f"strength {strength:>3}: beta identical", a == b, f"beta={a}")

    # -- E. the served feed --------------------------------------------------------------------- #
    print(f"\n-- E. the served feed over the five scenarios --")
    st = store_mod.Store(None)
    feed_csv = feed_source.prepare(st) if feed_source.enabled() else None
    if not feed_csv:
        print("  the recommender is not sourcing from the live feed — no served comparison.")
        return 1 if failures else 0
    from types import SimpleNamespace

    def _int_env(n):
        v = os.environ.get(n)
        return int(v) if v and v.isdigit() else None

    os.environ["RWE_QBIAS"] = feed_csv
    os.environ["RWE_PROFILE"] = "qbias"
    ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                         behaviors=None, lean_tau=None, domain=None,
                         n_users=_int_env("RWE_N_USERS"), max_items=_int_env("RWE_MAX_ITEMS"),
                         seed=_int_env("RWE_SEED") or 0)
    be = engine.Backend(engine.resolve_profile(ns))
    be.attach_url_resolver(feed_source.load_url_map(feed_csv))
    be.attach_country_resolver(feed_source.load_country_map(feed_csv))

    if args.engine_user:
        import personalize
        pers = personalize.Personalizer(be, st, persist=False)
        if not pers.has_measured(args.engine_user):
            print(f"  user {args.engine_user} is below the measured threshold — demo path, "
                  f"nothing reader-specific to compare.")
            return 1
        serve = lambda p: pers.recommendations(args.engine_user, None, p)
        who = f"engine user {args.engine_user} (personalized)"
    else:
        serve = lambda p: be.recommendations(be.demo_user, None, p)
        who = "base demo reader"

    topics = engine._INTEREST_TOPICS.get(key, ())
    labels = {engine._prettify(t) for t in topics}
    country_of = {str(k): frozenset(v) if not isinstance(v, str) else frozenset({v})
                  for k, v in be.country_by_id.items()}

    def exposure(recs):
        return sum(1.0 / (i + 1) for i, r in enumerate(recs)
                   if (r.get("article") or {}).get("topic") in labels)

    def describe(recs):
        arts = [r["article"] for r in recs]
        return {
            "ids": [a["id"] for a in arts],
            "cards": len(recs),
            "country": sum(1 for a in arts if want in country_of.get(str(a["id"]), ())),
            "topic": sum(1 for a in arts if a.get("topic") in labels),
            "exposure": exposure(recs),
            "plan": Counter(r.get("strategy") for r in recs),
            "cross": sum(1 for r in recs if r.get("crossCutting")),
            # Cross-cutting BY STRATEGY. A drop in the total means nothing on its own: rwe-b is
            # the slice whose contract is opposing perspectives, and a card in another slice that
            # happens to be cross-cutting is incidental. Only a fall in rwe-b's own count is a
            # degraded guarantee.
            "cross_by": Counter(r.get("strategy") for r in recs if r.get("crossCutting")),
            "leans": Counter((r.get("article") or {}).get("leanLabel")
                             or (r.get("article") or {}).get("lean") for r in recs),
            "pubs": len({(a.get("publisher") or "") for a in arts}),
        }

    scen = [
        ("1 Global + defaults", {}),
        ("2 country + defaults", {"recommendationCountry": want}),
        (f"3 country + {key}=10", {"recommendationCountry": want,
                                   "interests": {**{k: 5 for k in ss.INTEREST_KEYS}, key: 10}}),
        (f"4 country + {key}=1", {"recommendationCountry": want,
                                  "interests": {**{k: 5 for k in ss.INTEREST_KEYS}, key: 1}}),
        ("5 Global restored", {"recommendationCountry": None}),
    ]
    out = {}
    print(f"\n  reader: {who};  country {want};  interest '{key}' -> topics {sorted(labels)}\n")
    print(f"  {'scenario':<26} {'cards':>5} {want:>4} {'topic':>6} {'exposure':>9} "
          f"{'cross':>6} {'pubs':>5}  plan")
    for label, settings in scen:
        d = describe(serve(engine.rec_params_from_settings(settings)))
        out[label] = d
        print(f"  {label:<26} {d['cards']:>5} {d['country']:>4} {d['topic']:>6} "
              f"{d['exposure']:>9.3f} {d['cross']:>6} {d['pubs']:>5}  {dict(d['plan'])}")

    s1, s2, s3, s4, s5 = (out[label] for label, _ in scen)
    print(f"\n-- verdicts --")
    check("5 restores the baseline feed byte for byte", s1["ids"] == s5["ids"])
    check("selecting a country raises its own coverage", s2["country"] >= s1["country"],
          f"{s1['country']} -> {s2['country']}")
    check("interest monotonic 1 <= 5 <= 10 WITH a country selected",
          s4["exposure"] <= s2["exposure"] <= s3["exposure"],
          f"low {s4['exposure']:.3f} <= default {s2['exposure']:.3f} <= high {s3['exposure']:.3f}")
    check("the interest nudge still moves the feed under a country",
          s3["ids"] != s4["ids"])
    check("bridging allocation unchanged in every scenario",
          all(d["plan"].get("rwe-b") == s1["plan"].get("rwe-b") for d in (s2, s3, s4, s5)),
          f"rwe-b={s1['plan'].get('rwe-b')}")
    # The bar that matters: the BRIDGING slice's own cross-cutting count. The overall total is
    # reported beside it, but a fall there is only meaningful if rwe-b caused it — every other
    # slice is free to change composition, which is the whole point of a country preference.
    base_b = s1["cross_by"].get("rwe-b", 0)
    check("bridging's OWN cross-cutting count never falls",
          all(d["cross_by"].get("rwe-b", 0) >= base_b for d in (s2, s3, s4, s5)),
          f"rwe-b cross-cutting baseline {base_b}, "
          f"scenarios {[d['cross_by'].get('rwe-b', 0) for d in (s2, s3, s4, s5)]}")
    total_ok = all(d["cross"] >= s1["cross"] for d in (s2, s3, s4, s5))
    print(f"  [{'PASS' if total_ok else 'note'}] overall cross-cutting total "
          f"{s1['cross']} -> {[d['cross'] for d in (s2, s3, s4, s5)]}"
          f"{'' if total_ok else '  — incidental cross-cutting outside bridging; see the split below'}")
    for label, _ in scen:
        print(f"      {label:<26} {dict(out[label]['cross_by'])}")
    check("no scenario serves a short feed",
          all(d["cards"] == s1["cards"] for d in (s2, s3, s4, s5)))

    print(f"\n-- conclusion --")
    if failures:
        print(f"  {len(failures)} check(s) FAILED — the country selector disturbed existing "
              f"tuning behaviour:")
        for f in failures:
            print(f"    * {f}")
        return 1
    print(f"  No interference found. The country preference adds a key and a sort dimension; the "
          f"openness budget, the strength beta, the stored interest values and their monotonic "
          f"ranking effect are unchanged, and Global restores the exact baseline feed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
