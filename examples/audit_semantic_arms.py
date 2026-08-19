"""audit_semantic_arms.py — can a BERT-family model do event identity as well as an LLM?

READ-ONLY offline evaluation on the SAME V1 benchmark sheet, the SAME event-identity rubric
(docs/EVENT_IDENTITY_RUBRIC.md), and the SAME scoring code (audit_v1_verifier.score_run) as the
LLM arm. Nothing here touches clustering, production configuration, or the served image: the
model libraries install into an ephemeral container, and the weights land in a mounted cache.

Three arms, one question — is the cheap semantic model good enough to REPLACE the LLM verifier,
or is it only a first-stage filter?

  arm 1  bi-encoder + cosine   sentence embeddings, one vector per article, cosine per pair.
                               Article vectors cache, so the marginal pair costs a dot product.
  arm 2  cross-encoder         both texts in one forward pass, cross-attention, one score.
                               No caching possible; the per-pair cost is the model.
  arm 3  the LLM verifier      read from the V1-prime verdict store, not re-run here.

THE HYPOTHESIS UNDER TEST (registered before measurement, so the numbers can falsify it):
embedding similarity is a smoothed, synonym-aware SIBLING of the token overlap our clusterer
already computes. The verifier band was constructed as the set of pairs where token overlap is
ambiguous, so in-band the two signals should be highly correlated, and a second signal that
correlates with the first cannot resolve what the first could not. Concretely, the template,
recurring-series, comparative-mention, and same-template-different-place exhibits should score
HIGH cosine while being different events (false merges), and the reactive-coverage families
(the Hayden retrospective, the UK alert family) should score LOW while being the same event
(false splits). This is the X6 finding's shape — a channel blind to exactly the exhibits — and
X6 was killed on it. The exception, where embeddings hold information the lexical layer
physically cannot have, is paraphrase and cross-language pairs.

PRE-REGISTERED BARS (fixed before any score was computed; a missed bar is reported, never
tuned around):

  Threshold discipline — the decision threshold is calibrated ONLY on the rule-labeled tiers
  (58 rule:no-affinity different + 7 rule:near-dup same), then FROZEN before the exhibits are
  scored. A threshold fitted on twelve hand-verified exhibits would be reporting its own
  training data.

  S1 (replacement, disqualifying): zero false merges on the labeled-different exhibits at the
      frozen threshold — the same exhibit gate the LLM arm faces.
  S2 (replacement): >= 10/12 exhibits correct.
  S3 (replacement): <= 1 contradiction on the 58 rule:no-affinity pairs (the easy differents;
      an arm that misses these is unusable at any stage).
  S4 (narrowing-filter role): there must exist a frozen band (lo, hi) that auto-decides
      >= 30% of pairs with <= 1% errors among the pairs it decides. No such band => the
      "cheap filter in front of the LLM" role is KILLED, not tuned.
  S5 (candidate-generation role): the fraction of rule:no-affinity pairs (zero shared tokens
      by construction, all labeled different) scoring above `hi`. High means the arm would
      flood candidate generation with false merges — the recall role fails too.

Latency, footprint, and cost are measured on the host that runs this, and printed with the CPU
and device so the numbers are attributable. Determinism is NOT a quality result for these arms:
a fixed-weight model in eval mode is deterministic by construction, so stability is reported
as 100% with that caveat rather than celebrated against the LLM's replay bar.

Multilingual: the cross-language exhibit (Garmin CIRQA JA/EN) has aged out of the production
window and is NOT in the current sheet, so multilingual performance is NOT measurable as
benchmark evidence here. The --probe section runs a small set of CLEARLY SYNTHETIC constructed
pairs to test the mechanism, reported separately and never mixed into the scored benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_v1_verifier import score_run          # noqa: E402  (one scoring implementation)

# Checkpoints. Monolingual and multilingual are BOTH carried for arm 1 because the
# cross-language question is exactly where the two diverge.
BI_MODELS = ("sentence-transformers/all-MiniLM-L6-v2",
             "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
CROSS_MODELS = ("cross-encoder/stsb-roberta-base",
                "cross-encoder/nli-deberta-v3-small")

# Supplementary probe — CONSTRUCTED, NOT PRODUCTION TEXT. These exist to test the mechanism on
# shapes the current sheet cannot cover (cross-language above all). They are never scored into
# the benchmark and never called evidence about production.
PROBE = (
    ("multilingual/translation", "same_event",
     "Garmin CIRQA smart ring listed in certification database",
     "ガーミンのスマートリング「CIRQA」が認証データベースに登録される"),
    ("multilingual/different-product", "different_event",
     "Garmin CIRQA smart ring appears in certification filing",
     "ガーミン、新型スマートウォッチ「フェニックス」を発表"),
    ("paraphrase", "same_event",
     "Mass shooting reported at Seattle Center",
     "Gunfire erupts near Seattle's Space Needle, several hurt"),
    ("template/announcement", "different_event",
     "The Paper season 2: cast, release date and everything you need to know",
     "Mirzapur The Movie: trailer, cast, release date and everything you must know"),
    ("recurring-series", "different_event",
     "Antam gold price rises Rp 5,000 on Monday",
     "Antam gold price falls Rp 3,000 on Tuesday"),
    ("comparative-mention", "different_event",
     "Vishwanath and Sons box office Day 2: Suriya's film trails behind Jana Nayagan",
     "Jana Nayagan box office Day 21: Vijay's film crosses Rs 300 crore"),
    ("same-person/different-event", "different_event",
     "Luigi Mangione arraigned in Manhattan court on federal charges",
     "Luigi Mangione spotted at Pennsylvania diner weeks before arrest"),
    ("reactive-coverage-family", "same_event",
     "Hayden Panettiere dies at 36, family confirms",
     "Hayden Panettiere: a life in photos"),
    ("same-template/different-place", "different_event",
     "Human remains found in Scarborough park, police investigating",
     "Human remains found near Palomar Mountain, sheriff says"),
)


def text_of(side: dict) -> str:
    """The semantic arms see the headline and dek — the same words the LLM is shown, minus the
    structured time/entity lines an embedding cannot use. That asymmetry is itself a result:
    the ordinal and place reasoning rules 6 and 7 demand has nowhere to live in a cosine."""
    h = (side.get("headline") or "").strip()
    d = " ".join((side.get("dek") or "").split())
    return f"{h}. {d}".strip() if d else h


def _nonascii(s: str) -> float:
    return sum(1 for c in s if ord(c) > 127) / max(1, len(s))


def cross_script(a: dict, b: dict) -> bool:
    """Heuristic cross-language flag: one side substantially non-ASCII, the other not."""
    ra, rb = _nonascii(text_of(a)), _nonascii(text_of(b))
    return (ra > 0.15) != (rb > 0.15)


class BiEncoderArm:
    """Embeddings + cosine. Article vectors are computed once and reused, which is how this
    would run in production and why its marginal per-pair cost is a dot product."""

    has_spans = False
    kind = "bi-encoder"

    def __init__(self, model_id: str, cache_dir: str):
        self.name = model_id
        self.cache_dir = cache_dir
        self.model = None
        self.encode_seconds = 0.0
        self.pair_seconds = 0.0
        self.n_texts = 0

    def load(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(self.name, cache_folder=self.cache_dir)
        return self

    def scores(self, rows) -> list:
        import numpy as np
        texts, index = [], {}
        for r in rows:
            for side in (r["a"], r["b"]):
                t = text_of(side)
                if t not in index:
                    index[t] = len(texts)
                    texts.append(t)
        self.n_texts = len(texts)
        t0 = time.perf_counter()
        vecs = self.model.encode(texts, batch_size=32, convert_to_numpy=True,
                                 normalize_embeddings=True, show_progress_bar=False)
        self.encode_seconds = time.perf_counter() - t0
        t0 = time.perf_counter()
        out = [float(np.dot(vecs[index[text_of(r["a"])]], vecs[index[text_of(r["b"])]]))
               for r in rows]
        self.pair_seconds = time.perf_counter() - t0
        return out


class CrossEncoderArm:
    """Cross-attention over both texts. No caching is possible — every pair is a forward pass,
    so this is the arm whose cost scales with pair volume rather than article volume."""

    has_spans = False
    kind = "cross-encoder"

    def __init__(self, model_id: str, cache_dir: str):
        self.name = model_id
        self.cache_dir = cache_dir
        self.model = None
        self.encode_seconds = 0.0
        self.pair_seconds = 0.0
        self.n_texts = 0

    def load(self):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(self.name, cache_folder=self.cache_dir)
        return self

    def scores(self, rows) -> list:
        pairs = [(text_of(r["a"]), text_of(r["b"])) for r in rows]
        t0 = time.perf_counter()
        raw = self.model.predict(pairs, batch_size=16, show_progress_bar=False)
        self.pair_seconds = time.perf_counter() - t0
        out = []
        for v in raw:
            try:                                   # NLI heads emit 3 logits; STS heads emit 1
                v = list(v)
                m = max(v)
                exp = [pow(2.718281828, x - m) for x in v]
                out.append(exp[-1] / sum(exp))     # entailment-ish channel, last label
            except TypeError:
                out.append(float(v))
        return out


def calibrate(rows, scores) -> float:
    """Single decision threshold, chosen ONLY on the rule-labeled tiers, then frozen.

    Maximizes balanced accuracy over (rule:no-affinity = different, rule:near-dup = same).
    The exhibits do not participate: a threshold fitted on the twelve hand-verified pairs
    would make the exhibit gate a report of its own training data."""
    pos = [s for r, s in zip(rows, scores) if r.get("label_source") == "rule:near-dup"]
    neg = [s for r, s in zip(rows, scores) if r.get("label_source") == "rule:no-affinity"]
    if not pos or not neg:
        return 0.5
    best, best_t = -1.0, 0.5
    lo, hi = min(pos + neg), max(pos + neg)
    for i in range(201):
        t = lo + (hi - lo) * i / 200.0
        tpr = sum(1 for s in pos if s >= t) / len(pos)
        tnr = sum(1 for s in neg if s < t) / len(neg)
        if (tpr + tnr) / 2 > best:
            best, best_t = (tpr + tnr) / 2, t
    return best_t


def labeled(rows, scores):
    """(score, truth) for every pair carrying a usable label, exhibits included."""
    out = []
    for r, s in zip(rows, scores):
        if r.get("label") in ("same_event", "different_event"):
            out.append((s, r["label"]))
    return out


def widest_band(rows, scores, max_err=0.01, min_vol=0.30):
    """S4: the widest (lo, hi) whose decided regions hold <= max_err error, with the volume it
    auto-decides. Returns None when no band clears the volume bar — a kill, not a knob."""
    pts = labeled(rows, scores)
    if not pts:
        return None
    vals = sorted({s for s, _ in pts})
    best = None
    for lo in vals:
        for hi in vals:
            if hi < lo:
                continue
            decided = [(s, y) for s, y in pts if s <= lo or s >= hi]
            if not decided:
                continue
            errs = sum(1 for s, y in decided
                       if (s >= hi and y == "different_event") or (s <= lo and y == "same_event"))
            if errs / len(decided) > max_err:
                continue
            vol = len(decided) / len(pts)
            if vol >= min_vol and (best is None or vol > best[2]):
                best = (lo, hi, vol, errs, len(decided))
    return best


def run_arm(arm, rows, out_dir, llm_store=None) -> dict:
    print(f"\n{'=' * 78}\nARM: {arm.name}   ({arm.kind})\n{'=' * 78}")
    try:
        t0 = time.perf_counter()
        arm.load()
        load_s = time.perf_counter() - t0
    except Exception as e:                          # noqa: BLE001 — report, never substitute
        print(f"  CHECKPOINT UNAVAILABLE — {type(e).__name__}: {str(e)[:200]}")
        print(f"  This arm is NOT measured. No substitute checkpoint was used.")
        return {"name": arm.name, "available": False}

    scores = arm.scores(rows)
    thr = calibrate(rows, scores)
    print(f"  loaded in {load_s:.1f}s; frozen threshold {thr:.4f} "
          f"(calibrated on rule tiers only, exhibits excluded)")

    store = {}
    for r, s in zip(rows, scores):
        store[r["pair_id"]] = {
            "pair_id": r["pair_id"], "model": arm.name, "score": s,
            "verdict": "same_event" if s >= thr else "different_event",
            "quote_ok": False, "demoted": False, "api_error": False,
        }
    with open(os.path.join(out_dir, f"scores_{arm.name.replace('/', '_')}.jsonl"),
              "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(store[r["pair_id"]]) + "\n")

    # -- the exhibit table: the eight named cases, with raw scores ------------------------- #
    print(f"\n-- exhibit scores at the frozen threshold (the named failure cases) --")
    ex = [r for r in rows if r.get("label_source") == "human-exhibit"]
    for r in sorted(ex, key=lambda r: -store[r["pair_id"]]["score"]):
        s = store[r["pair_id"]]["score"]
        v = store[r["pair_id"]]["verdict"]
        wrong_same = r["label"] == "different_event" and v == "same_event"
        wrong_diff = r["label"] == "same_event" and v == "different_event"
        mark = ("FALSE MERGE" if wrong_same else
                "false split" if wrong_diff else "ok")
        print(f"  {s:+.4f}  [{mark:>11}]  {r['class'][8:]:<22} truth={r['label']}")

    n_fm = sum(1 for r in ex if r["label"] == "different_event"
               and store[r["pair_id"]]["verdict"] == "same_event")
    n_fs = sum(1 for r in ex if r["label"] == "same_event"
               and store[r["pair_id"]]["verdict"] == "different_event")
    n_ok = sum(1 for r in ex if store[r["pair_id"]]["verdict"] == r["label"])
    noaff = [r for r in rows if r.get("label_source") == "rule:no-affinity"]
    n_s3 = sum(1 for r in noaff if store[r["pair_id"]]["verdict"] == "same_event")

    print(f"\n-- pre-registered bars --")
    print(f"  S1 false merges on exhibits : {n_fm}          bar = 0 (disqualifying)"
          f"   {'PASS' if n_fm == 0 else 'FAIL'}")
    print(f"  S2 exhibits correct         : {n_ok}/{len(ex)}       bar >= 10/12"
          f"   {'PASS' if n_ok >= 10 else 'FAIL'}")
    print(f"  S3 no-affinity contradictions: {n_s3}/{len(noaff)}      bar <= 1"
          f"   {'PASS' if n_s3 <= 1 else 'FAIL'}")
    band = widest_band(rows, scores)
    if band:
        lo, hi, vol, errs, dec = band
        print(f"  S4 filter band              : [{lo:.4f}, {hi:.4f}] auto-decides {vol:.0%} "
              f"of labeled pairs with {errs}/{dec} errors   PASS")
    else:
        print(f"  S4 filter band              : none exists at >=30% volume and <=1% error"
              f"   FAIL — the narrowing-filter role is killed for this arm")
    hi_thr = band[1] if band else thr
    n_s5 = sum(1 for r in noaff if store[r["pair_id"]]["score"] >= hi_thr)
    print(f"  S5 no-affinity above `hi`   : {n_s5}/{len(noaff)} ({n_s5 / max(1, len(noaff)):.0%})"
          f" — zero-overlap pairs the arm would surface as same-event candidates")
    print(f"  false splits on exhibits    : {n_fs} (not a bar; the reverse-error direction)")

    # -- footprint, latency, cost ----------------------------------------------------------- #
    per_pair_ms = (arm.pair_seconds / max(1, len(rows))) * 1000
    enc_ms = (arm.encode_seconds / max(1, arm.n_texts)) * 1000 if arm.n_texts else 0.0
    rss = 0
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    rss = int(line.split()[1]) // 1024
    except Exception:                               # noqa: BLE001 — non-Linux hosts
        pass
    print(f"\n-- footprint and latency (this host) --")
    print(f"  peak RSS {rss} MB; load {load_s:.1f}s; "
          f"{arm.n_texts} texts encoded at {enc_ms:.1f} ms/text; "
          f"{per_pair_ms:.1f} ms/pair scoring")
    daily = 451                                     # V0's measured band rate, pairs/day
    if arm.kind == "bi-encoder":
        cpu_day = (enc_ms * 2 * daily + per_pair_ms * daily) / 1000
    else:
        cpu_day = per_pair_ms * daily / 1000
    print(f"  at the measured band rate ({daily} pairs/day): {cpu_day:.1f} CPU-seconds/day "
          f"— against the LLM arm's measured $3.27/day")
    return {"name": arm.name, "available": True, "threshold": thr, "scores": scores,
            "store": store, "s1": n_fm, "s2": n_ok, "s3": n_s3, "s4": band, "s5": n_s5,
            "ms_pair": per_pair_ms, "rss": rss}


def run_probe(arm) -> None:
    print(f"\n-- SUPPLEMENTARY probe: CONSTRUCTED pairs, NOT production text, NOT benchmark "
          f"evidence --")
    print(f"   (the cross-language exhibit aged out of the sheet; this tests the mechanism "
          f"only)")
    rows = [{"pair_id": f"probe_{i}", "class": f"probe:{k}",
             "a": {"headline": ha, "dek": ""}, "b": {"headline": hb, "dek": ""},
             "label": lab, "label_source": "synthetic-probe"}
            for i, (k, lab, ha, hb) in enumerate(PROBE)]
    scores = arm.scores(rows)
    for r, s in zip(rows, scores):
        print(f"  {s:+.4f}  {r['class'][6:]:<28} truth={r['label']}"
              f"{'   [cross-script]' if cross_script(r['a'], r['b']) else ''}")


def main(argv=None, arms=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", required=True, help="v1_labeled.jsonl (audit_v1_labelset)")
    ap.add_argument("--out-dir", required=True, help="directory for per-arm score files")
    ap.add_argument("--cache-dir", default="/out/hfcache", help="model weight cache (mounted)")
    ap.add_argument("--llm-store", default=None,
                    help="the V1-prime verdict store, for the arm-3 comparison column")
    ap.add_argument("--probe", action="store_true", help="run the synthetic probe set")
    ap.add_argument("--full-report", action="store_true",
                    help="also run the shared V1a-V1d scoring for each arm")
    args = ap.parse_args(argv)

    rows = [json.loads(l) for l in open(args.pairs, encoding="utf-8")]
    if not any(r.get("label_source") for r in rows):
        print("the pairs sheet carries no label_source — run audit_v1_labelset first.")
        return 1
    rows.sort(key=lambda r: r["pair_id"])
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"BENCHMARK           : {args.pairs} ({len(rows)} pairs)")
    print(f"RUBRIC              : docs/EVENT_IDENTITY_RUBRIC.md v1 (unchanged)")
    print(f"HOST                : {platform.processor() or platform.machine()}, "
          f"{os.cpu_count()} cpus, {platform.python_version()}")
    try:
        import torch
        print(f"TORCH               : {torch.__version__}, cuda={torch.cuda.is_available()}")
    except Exception:                               # noqa: BLE001 — torch is optional here
        print(f"TORCH               : not importable (arms will report unavailable)")
    n_cross = sum(1 for r in rows if cross_script(r["a"], r["b"]))
    print(f"CROSS-SCRIPT PAIRS  : {n_cross} of {len(rows)} — multilingual is "
          f"{'measurable' if n_cross >= 5 else 'NOT measurable on this sheet'}")

    if arms is None:
        arms = ([BiEncoderArm(m, args.cache_dir) for m in BI_MODELS]
                + [CrossEncoderArm(m, args.cache_dir) for m in CROSS_MODELS])

    results = []
    for arm in arms:
        res = run_arm(arm, rows, args.out_dir)
        results.append(res)
        if res.get("available") and args.full_report:
            n = len(rows)
            score_run(rows, res["store"], model_name=arm.name, has_spans=False,
                      stable=n, flips=0, sym_ok=n, sym_n=n,
                      usage=f"deterministic in eval mode — stability and symmetry are "
                            f"100% by construction, not a quality result")
        if res.get("available") and args.probe:
            run_probe(arm)

    # -- arm 3: the LLM column, read from its store, never re-run here --------------------- #
    print(f"\n{'=' * 78}\nARM 3: the LLM verifier (quality reference)\n{'=' * 78}")
    llm = {}
    if args.llm_store and os.path.exists(args.llm_store):
        for l in open(args.llm_store, encoding="utf-8"):
            v = json.loads(l)
            llm[v["pair_id"]] = v
        errs = sum(1 for v in llm.values() if v.get("api_error"))
        if errs > max(2, int(0.02 * len(rows))):
            print(f"  the LLM store is VOID ({errs}/{len(llm)} api-error records): there is no "
                  f"LLM reference measurement to compare against yet. The BERT arms above are "
                  f"measured; the comparison column is PENDING, not zero.")
            llm = {}
        else:
            ex = [r for r in rows if r.get("label_source") == "human-exhibit"]
            fm = sum(1 for r in ex if r["label"] == "different_event"
                     and llm.get(r["pair_id"], {}).get("verdict") == "same_event")
            ok = sum(1 for r in ex if llm.get(r["pair_id"], {}).get("verdict") == r["label"])
            unc = sum(1 for v in llm.values() if v.get("verdict") == "uncertain")
            print(f"  exhibits correct {ok}/{len(ex)}; false merges {fm}; "
                  f"uncertain {unc}/{len(llm)} ({unc / max(1, len(llm)):.0%})")
    else:
        print(f"  no LLM verdict store supplied (--llm-store): comparison column PENDING.")

    print(f"\n{'=' * 78}\nCOMPARISON\n{'=' * 78}")
    print(f"  {'arm':<52} {'S1 fm':>6} {'S2':>6} {'S3':>6} {'ms/pair':>8}")
    for r in results:
        if not r.get("available"):
            print(f"  {r['name']:<52} {'unavailable — not measured':>30}")
            continue
        print(f"  {r['name']:<52} {r['s1']:>6} {r['s2']:>4}/12 {r['s3']:>6} "
              f"{r['ms_pair']:>8.1f}")
    print(f"  {'LLM verifier':<52} "
          f"{'pending' if not llm else 'measured — see above':>30}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
