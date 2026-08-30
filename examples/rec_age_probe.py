#!/usr/bin/env python3
"""What a half-life would actually do to the recommendation corpus, measured on the LIVE catalogue.

The recency knob (``RWE_REC_RECENCY_HALFLIFE_DAYS``) trades currency against source diversity, and
neither side of that trade should be guessed. This builds the corpus repeatedly — once per candidate
half-life, from the real exported CSV — and prints what changes:

    age        median / p90 / share under 7 days, of the articles that reach the pool
    diversity  distinct publishers, and the left / centre / right split

Read-only: it exports to a temp file, never touches the serving corpus, and sets the half-life only
inside this process. Run it BEFORE setting the variable, and again after, so the change is a
measurement rather than a preference.

    dc run --rm -T api python examples/rec_age_probe.py
    dc run --rm -T api python examples/rec_age_probe.py 0 14 7 3
"""
import collections
import csv
import datetime as dt
import os
import pathlib
import sys
import tempfile

# Lives in examples/ because that is what the image copies (deploy/Dockerfile.api takes rwe,
# examples and scripts — never deploy/). A probe under deploy/ops/ cannot be run inside the api
# container at all, which is how the first version of this shipped as an instruction that could
# not execute.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np                       # noqa: E402
import feed_source                       # noqa: E402
import simulate_users as su              # noqa: E402
import store as store_mod                # noqa: E402


def _rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def main(argv):
    halflives = [float(a) for a in argv[1:]] or [0.0, 14.0, 7.0, 3.0, 1.0]
    max_items = int(os.environ.get("RWE_MAX_ITEMS") or 1500)

    st = store_mod.Store()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = tmp.name
    written = feed_source.export_catalog_csv(
        st, path, max_per_outlet=int(os.environ.get("RWE_FEED_MAX_PER_OUTLET") or 0) or None)
    rows = _rows(path)
    print(f"corpus export: {written} rows  ->  subsample of {max_items}\n")
    if not rows:
        print("nothing exported — is RWE_RECS_SOURCE=feed and the catalogue non-empty?")
        return 1
    if not rows[0].get("published_at"):
        print("the export carries no published_at column — this build predates the recency work,\n"
              "so every half-life below would read as OFF. Deploy first, then re-run.")
        return 1

    now = dt.datetime.now(dt.timezone.utc)
    ages_all = []
    for r in rows:
        try:
            when = dt.datetime.fromisoformat(str(r["published_at"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        ages_all.append((now - when).total_seconds() / 86400.0)
    if ages_all:
        a = np.array(ages_all)
        print(f"catalogue spans {a.min():.1f}–{a.max():.1f} days (median {np.median(a):.1f})\n")

    print(f"{'half-life':>10} {'median':>8} {'p90':>7} {'<7d':>7} {'publishers':>11} "
          f"{'left/centre/right':>20}")
    prior = os.environ.get("RWE_REC_RECENCY_HALFLIFE_DAYS")
    try:
        for h in halflives:
            if h > 0:
                os.environ["RWE_REC_RECENCY_HALFLIFE_DAYS"] = str(h)
            else:
                os.environ.pop("RWE_REC_RECENCY_HALFLIFE_DAYS", None)
            cat = su.catalog_from_qbias(path, max_items=max_items, seed=0)
            ages, pubs, leans = [], set(), collections.Counter()
            for ident, outlet in zip(cat.ids, cat.outlets):
                row = rows[int(str(ident)[1:])]
                try:
                    when = dt.datetime.fromisoformat(str(row["published_at"]).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                ages.append((now - when).total_seconds() / 86400.0)
                pubs.add(str(outlet))
                leans[str(row.get("bias_rating") or "").split()[-1] or "?"] += 1
            a = np.array(ages) if ages else np.array([float("nan")])
            print(f"{('OFF' if h == 0 else f'{h:g}d'):>10} {np.median(a):7.1f}d {np.percentile(a, 90):6.1f}d "
                  f"{100 * (a < 7).mean():6.1f}% {len(pubs):11d} "
                  f"{leans['left']:>6}/{leans['center']}/{leans['right']}")
    finally:
        if prior is None:
            os.environ.pop("RWE_REC_RECENCY_HALFLIFE_DAYS", None)
        else:
            os.environ["RWE_REC_RECENCY_HALFLIFE_DAYS"] = prior
        pathlib.Path(path).unlink(missing_ok=True)

    print("\nPick the largest half-life that gets the age where you want it. Publisher count and the\n"
          "lean split are the guardrails: if either moves materially, lengthen it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
