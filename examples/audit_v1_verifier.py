"""audit_v1_verifier.py — V1-prime: the same-event verifier benchmark, offline, one model per run.

**The model under test is chosen by ``--adapter`` and named on the report's first line.** Two
arms: ``gemini`` (gemini-2.5-flash, the original V1-prime run) and ``claude`` — which is not a
second research model but the PRODUCTION judge itself: ``event_identity.ClaudeAdapter`` with its
own prompt and the deployed ``RWE_EVENT_JUDGE_MODEL``, wrapped so the harness scores exactly the
verdicts the serving path would persist. That is the Stage 0.4 gate (docs/
CLUSTERING_APPROACHES_RESEARCH.md §4): ``RWE_EVENT_JUDGE=1`` is earned by this arm passing V1b on
the labeled sheet, not by the Gemini arm having passed it. The model sits behind an adapter so
the harness, verdict schema, quote-verification, stability replays, and every pre-registered bar
stay identical whatever model is plugged in.

Offline evaluation ONLY: reads the provenance-labeled sheet (audit_v1_labelset), calls the
model per pair, writes verdicts to a JSONL store (append + resume — reruns skip judged pairs,
the verdict-store discipline), and scores the V1-prime sections. It wires nothing: no
clustering change, no production configuration, no integration.

Pre-registered V1-prime bars (restated 2026-08-17 after the provenance redesign; the two
original error-rate bars are NOT measurable on this set and are reported as BOUNDS only):

  V1a (label-free, all pairs): uncertain-rate <= 25% overall and <= 40% per class;
      quote-verification >= 99% (a verdict whose quoted spans are not substrings of its
      inputs is DEMOTED to uncertain and counted as a quote failure); five-replay stability
      >= 98% on the replay sample; verdict symmetry >= 99% on the swapped sample.
  V1b (the exhibit gate): the human-exhibit pairs, each printed with its verdict; ONE
      same_event on a labeled-different exhibit is disqualifying — the KILL condition.
  V1c (bounds, never point estimates): errors against rule-labeled pairs per provenance
      tier; zero errors on n pairs licenses only a 3/n (95%) upper bound.
  V1d (diagnostics, never bars): agreement with the retained drafts; the verdict
      distribution over undetermined pairs; and the emitted human shortlist — draft
      disagreements + non-uncertain verdicts on undetermined pairs.

Fail-closed everywhere: API errors after bounded retries record verdict "uncertain" with
source "api-error"; malformed JSON likewise. Temperature 0 is NOT assumed to be determinism —
that is what the stability metric measures.

A run whose store carries api-error records beyond a trace (2% of pairs) is declared VOID —
it measured transport availability, not the model, and no bar is scored as tested (the first
live run recorded 372/390 exactly this way: 369 rate-limit 429s at free-tier pacing). Five
CONSECUTIVE api-error records trip a circuit breaker that aborts the pass (exit 1) instead of
retrying for hours against a dead transport. Either way resume re-judges api-error records
and error-flagged sidecar entries while keeping every real verdict, so re-running the same
command after the transport problem is fixed heals the store in place.

Requires GEMINI_API_KEY in the environment (the stack's existing convention). Cost at
gemini-2.5-flash pricing is cents for the full run; actual token usage is totalled from the
API's own usageMetadata and printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODEL = "gemini-2.5-flash"
ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent?key={key}")

SYSTEM = """You judge whether two news articles report the SAME news event, per this rubric:
Same event = the same occurrence: the same actors (who) doing/undergoing the same thing (what)
at the same instance in time (when) and place (where). Not the same topic, beat, series,
franchise, or person.
Rules:
1. An article's event is what it REPORTS, not what it mentions. Comparative headlines and
   background references never make two events one.
2. A continuing occurrence is ONE event across its updates (day-2 and day-3 of the same film's
   run; one race's election-day arc; successive casualty counts of one disaster).
3. Recurring series instances are DIFFERENT events (daily prices, market wraps); different
   matches/fixtures of a competition are different events.
4. Same person/organization does not mean same event. BUT coverage published in direct
   reaction to an event (obituaries, retrospectives, galleries, what-we-know explainers about
   that occurrence) belongs to the SAME event family.
5. Wording, framing, and language are irrelevant: paraphrase and translation never separate;
   shared template vocabulary never joins.
6. Numbers/ordinals naming the instance are identity anchors (day 2 of film A vs day 21 of
   film B: different); conflicting figures for one occurrence are still one event.
7. Place disagreement on the same template is decisive (the same headline shape in two cities
   is two events).
Answer uncertain when the text genuinely underdetermines the question. Quote the exact
substrings of each side's text that ground your verdict."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING",
                    "enum": ["same_event", "different_event", "uncertain"]},
        "who_a": {"type": "STRING"}, "who_b": {"type": "STRING"},
        "what_a": {"type": "STRING"}, "what_b": {"type": "STRING"},
        "quoted_span_a": {"type": "STRING"}, "quoted_span_b": {"type": "STRING"},
        "reason": {"type": "STRING"},
    },
    "required": ["verdict", "quoted_span_a", "quoted_span_b", "reason"],
}


def side_text(s: dict) -> str:
    bits = [f"headline: {s.get('headline') or ''}"]
    if s.get("dek"):
        bits.append(f"dek: {s['dek']}")
    bits.append(f"published: {s.get('publishedAt') or 'unknown'}")
    if s.get("entities"):
        bits.append(f"extracted entities: {', '.join(s['entities'])}")
    if s.get("countries"):
        bits.append(f"event countries: {', '.join(s['countries'])}")
    return "\n".join(bits)


def make_prompt(a: dict, b: dict) -> str:
    return (f"ARTICLE A:\n{side_text(a)}\n\nARTICLE B:\n{side_text(b)}\n\n"
            f"Do A and B report the same news event?")


_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


class GeminiAdapter:
    """The model adapter. The harness depends only on ``name`` and ``verdict(a, b)`` — a
    different model slots in by implementing the same two members.

    Transport discipline (learned from the first live run, which recorded 372/390 pairs as
    fail-closed transport errors): HTTP errors are read for their status and body; 4xx other
    than 429 fails fast (retrying a rejected request cannot succeed); 429/5xx honors the
    server's RetryInfo retryDelay when present, else exponential backoff capped at 60s,
    across six attempts. ``sleep`` paces between calls — free-tier gemini-2.5-flash allows
    10 requests/min, so pass --sleep 6.5 there; paid tiers can run the default. Thinking is
    disabled (thinkingBudget 0): the verdict must come from the rubric applied to the text,
    and an invisible thinking budget consuming maxOutputTokens is a malformed-response
    failure shape."""

    name = MODEL

    def __init__(self, key: str, sleep: float = 0.35):
        self.key = key
        self.sleep = sleep
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    def verdict(self, a: dict, b: dict) -> dict:
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": make_prompt(a, b)}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseSchema": SCHEMA, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingBudget": 0}},
        }).encode()
        url = ENDPOINT.format(model=MODEL, key=self.key)
        last = None
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, data=body,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    payload = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode(errors="replace")
                except Exception:                     # noqa: BLE001 — body may be unreadable
                    detail = ""
                last = f"HTTP {e.code}: {(detail or str(e.reason))[:300]}"
                if e.code == 429 or e.code >= 500:
                    m = _RETRY_DELAY.search(detail)
                    time.sleep(float(m.group(1)) if m else min(60.0, 5.0 * (2 ** attempt)))
                    continue
                break                                  # 400/401/403/404: fail fast, fail closed
            except Exception as e:                    # noqa: BLE001 — network/timeout shapes
                last = f"{type(e).__name__}: {e}"
                time.sleep(min(30.0, 2.0 * (2 ** attempt)))
                continue
            usage = payload.get("usageMetadata") or {}
            self.tokens_in += int(usage.get("promptTokenCount") or 0)
            self.tokens_out += int(usage.get("candidatesTokenCount") or 0)
            self.calls += 1
            try:
                text = payload["candidates"][0]["content"]["parts"][0]["text"]
                out = json.loads(text)
                if out.get("verdict") not in ("same_event", "different_event", "uncertain"):
                    raise ValueError("bad verdict enum")
            except Exception as e:                    # noqa: BLE001 — malformed body
                last = f"malformed response: {type(e).__name__}: {e}"
                continue
            time.sleep(self.sleep)
            return out
        return {"verdict": "uncertain", "quoted_span_a": "", "quoted_span_b": "",
                "reason": f"api-error after retries: {last}", "_api_error": True}


class ClaudeHarnessAdapter:
    """The PRODUCTION judge, scored by this harness — ``event_identity.ClaudeAdapter`` behind the
    two-member contract (``name``, ``verdict(a, b)``).

    Deliberately NOT a re-implementation with the harness's Gemini prompt: the question Stage
    0.4 asks is whether the adapter that would serve — its system prompt, its JSON contract, its
    quote demotion, its transport discipline — clears the V1 bars. So the sheet's sides are
    mapped onto the article shape the worker hands the adapter (``headline`` / ``description`` /
    ``publishedAt``; the sheet's ``entities`` and ``countries`` are NOT shown, because production
    does not show them), and the adapter's ``quote_a``/``quote_b`` come back as the harness's
    ``quoted_span_a``/``quoted_span_b`` so :func:`quote_ok` re-checks them against the sheet's
    text exactly as it does for any other arm. An adapter-side demotion (a decisive verdict whose
    quotes were not substrings of its inputs) arrives as ``uncertain`` with its reason.
    ``_api_error`` passes through as the fail-closed marker the store and breaker read."""

    def __init__(self, inner):
        self._inner = inner
        self.name = getattr(inner, "name", "claude")

    @property
    def calls(self) -> int:
        return getattr(self._inner, "calls", 0)

    @property
    def tokens_in(self) -> int:
        return getattr(self._inner, "tokens_in", 0)

    @property
    def tokens_out(self) -> int:
        return getattr(self._inner, "tokens_out", 0)

    @staticmethod
    def _article(side: dict) -> dict:
        return {"headline": side.get("headline") or "", "description": side.get("dek") or "",
                "publishedAt": side.get("publishedAt") or ""}

    def verdict(self, a: dict, b: dict) -> dict:
        out = self._inner.verdict(self._article(a), self._article(b))
        rec = {"verdict": out.get("verdict", "uncertain"),
               "quoted_span_a": out.get("quote_a", "") or "",
               "quoted_span_b": out.get("quote_b", "") or "",
               "reason": (out.get("_demoted") and f"adapter demoted: {out['_demoted']}")
                         or out.get("reason", "") or ""}
        if out.get("_api_error"):
            rec["_api_error"] = True
            rec["reason"] = str(out["_api_error"])
        return rec


_WS = re.compile(r"\s+")


def quote_ok(span: str, s: dict) -> bool:
    """Mechanical anti-hallucination check: the quoted span (whitespace-normalized,
    case-insensitive) must occur in the side's presented text — exactly what the model was
    shown (``side_text``: headline, dek, published, entities, countries). Checking a narrower
    surface than the model's actual input would manufacture false demotions. Empty spans fail."""
    q = _WS.sub(" ", (span or "")).strip().lower()
    if not q:
        return False
    hay = _WS.sub(" ", side_text(s)).strip().lower()
    return q in hay


def main(argv=None, adapter=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", required=True, help="v1_labeled.jsonl (audit_v1_labelset)")
    ap.add_argument("--out", required=True, help="verdict store (JSONL; append+resume)")
    ap.add_argument("--stability-sample", type=int, default=50)
    ap.add_argument("--replays", type=int, default=5)
    ap.add_argument("--symmetry-sample", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0, help="debug: judge only the first N pairs")
    ap.add_argument("--sleep", type=float, default=0.35,
                    help="pace between calls (seconds); free-tier gemini-2.5-flash allows "
                         "10 requests/min — use 6.5 there")
    ap.add_argument("--adapter", choices=("gemini", "claude"), default="gemini",
                    help="which model arm: 'gemini' (gemini-2.5-flash, GEMINI_API_KEY) or "
                         "'claude' — the PRODUCTION judge (event_identity.ClaudeAdapter, "
                         "ANTHROPIC_API_KEY, model RWE_EVENT_JUDGE_MODEL unless --model). The "
                         "claude arm is the Stage 0.4 enablement gate")
    ap.add_argument("--model", default=None,
                    help="claude arm only: override the model id (default: the deployed "
                         "RWE_EVENT_JUDGE_MODEL, so the gate measures what would serve)")
    args = ap.parse_args(argv)

    rows = [json.loads(l) for l in open(args.pairs, encoding="utf-8")]
    if not any(r.get("label_source") for r in rows):
        print("the pairs sheet carries no label_source — this runner scores the "
              "provenance-labeled sheet (audit_v1_labelset output), not the raw emission.")
        return 1
    rows.sort(key=lambda r: r["pair_id"])
    if args.limit:
        rows = rows[: args.limit]

    if adapter is None and args.adapter == "claude":
        import event_identity
        key = event_identity.api_key()
        if not key:
            print("ANTHROPIC_API_KEY is not set — refusing to run. (Set it in deploy/.env; the "
                  "api service passes it through, so `dc run --rm -T api …` carries it.)")
            return 1
        adapter = ClaudeHarnessAdapter(
            event_identity.ClaudeAdapter(key, model=args.model, sleep=args.sleep))
    elif adapter is None:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            print("GEMINI_API_KEY is not set — refusing to run.")
            return 1
        adapter = GeminiAdapter(key, sleep=args.sleep)
    print(f"MODEL UNDER TEST     : {adapter.name}")
    if isinstance(adapter, ClaudeHarnessAdapter):
        print(f"  This benchmark measures the PRODUCTION judge adapter on {adapter.name} — the "
              f"Stage 0.4 gate for RWE_EVENT_JUDGE=1.")
    else:
        print(f"  This benchmark measures {adapter.name} — a research arm, not the production "
              f"judge.")
    print(f"pairs                : {len(rows)}  (store: {args.out}; judged pairs are skipped)")

    # verdict store: append + resume
    store: dict = {}
    if os.path.exists(args.out):
        for l in open(args.out, encoding="utf-8"):
            v = json.loads(l)
            store[v["pair_id"]] = v
    out_f = open(args.out, "a", encoding="utf-8")

    breaker = {"consec": 0, "tripped": False}

    def _transport(is_err: bool) -> bool:
        """Five consecutive api-error records trip the breaker: a dead transport earns an
        abort and a resume, not hours of futile retry churn (the first live run's shape)."""
        breaker["consec"] = breaker["consec"] + 1 if is_err else 0
        breaker["tripped"] = breaker["tripped"] or breaker["consec"] >= 5
        return breaker["tripped"]

    def _abort_transport() -> int:
        judged = sum(1 for r in rows if r["pair_id"] in store)
        errs = sum(1 for r in rows
                   if r["pair_id"] in store and store[r["pair_id"]].get("api_error"))
        print(f"\nTRANSPORT CIRCUIT BREAKER — five consecutive api-error records; aborting "
              f"instead of retrying against a dead transport.")
        print(f"  store keeps {judged - errs} real verdicts; {errs} api-error records and "
              f"{len(rows) - judged} unjudged pairs re-judge on the next run of the same "
              f"command. No bar was tested.")
        return 1

    t0 = time.perf_counter()
    for r in rows:
        prior = store.get(r["pair_id"])
        if prior is not None and not prior.get("api_error"):
            continue                                   # real verdicts are kept; errors re-judge
        v = adapter.verdict(r["a"], r["b"])
        rec = {"pair_id": r["pair_id"], "model": adapter.name, "raw": v}
        qa, qb = quote_ok(v.get("quoted_span_a"), r["a"]), quote_ok(v.get("quoted_span_b"), r["b"])
        rec["quote_ok"] = bool(qa and qb)
        rec["verdict"] = v["verdict"] if rec["quote_ok"] else "uncertain"
        rec["demoted"] = (not rec["quote_ok"]) and v["verdict"] != "uncertain"
        rec["api_error"] = bool(v.get("_api_error"))
        store[r["pair_id"]] = rec
        out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out_f.flush()
        if _transport(rec["api_error"]):
            break
    elapsed = time.perf_counter() - t0
    if breaker["tripped"]:
        return _abort_transport()

    # stability replays + symmetry, on the sorted head samples
    sample = rows[: args.stability_sample]
    stable = flips = rep_errs = sym_errs = 0
    replay_path = args.out + ".replays"
    replays: dict = {}
    if os.path.exists(replay_path):
        for l in open(replay_path, encoding="utf-8"):
            v = json.loads(l)
            if v.get("api_error"):
                continue                               # transport absences re-run, never score
            replays.setdefault(v["pair_id"], []).append(v["verdict"])
    with open(replay_path, "a", encoding="utf-8") as rf:
        for r in sample:
            if breaker["tripped"]:
                break
            have = replays.get(r["pair_id"], [])
            while len(have) < args.replays - 1:      # the store verdict is replay #1
                v = adapter.verdict(r["a"], r["b"])
                qa = quote_ok(v.get("quoted_span_a"), r["a"])
                qb = quote_ok(v.get("quoted_span_b"), r["b"])
                verdict = v["verdict"] if (qa and qb) else "uncertain"
                entry = {"pair_id": r["pair_id"], "verdict": verdict}
                if v.get("_api_error"):
                    entry["api_error"] = True
                    rep_errs += 1
                rf.write(json.dumps(entry) + "\n")
                have.append(verdict)
                if _transport(bool(v.get("_api_error"))):
                    break
            if breaker["tripped"]:
                break
            base = store[r["pair_id"]]["verdict"]
            if all(h == base for h in have):
                stable += 1
            else:
                flips += 1
    sym_sample = rows[: args.symmetry_sample]
    sym_ok = 0
    sym_path = args.out + ".sym"
    syms: dict = {}
    if os.path.exists(sym_path):
        for l in open(sym_path, encoding="utf-8"):
            v = json.loads(l)
            if v.get("api_error"):
                continue                               # transport absences re-run, never score
            syms[v["pair_id"]] = v["verdict"]
    with open(sym_path, "a", encoding="utf-8") as sf:
        for r in sym_sample:
            if breaker["tripped"]:
                break
            if r["pair_id"] not in syms:
                v = adapter.verdict(r["b"], r["a"])
                qa = quote_ok(v.get("quoted_span_a"), r["b"])
                qb = quote_ok(v.get("quoted_span_b"), r["a"])
                syms[r["pair_id"]] = v["verdict"] if (qa and qb) else "uncertain"
                entry = {"pair_id": r["pair_id"], "verdict": syms[r["pair_id"]]}
                if v.get("_api_error"):
                    entry["api_error"] = True
                    sym_errs += 1
                sf.write(json.dumps(entry) + "\n")
                if _transport(bool(v.get("_api_error"))):
                    break
            if syms[r["pair_id"]] == store[r["pair_id"]]["verdict"]:
                sym_ok += 1
    if breaker["tripped"]:
        return _abort_transport()

    return score_run(rows, store, model_name=adapter.name, stable=stable, flips=flips,
                     sym_ok=sym_ok, sym_n=len(sym_sample), rep_errs=rep_errs,
                     sym_errs=sym_errs,
                     usage=f"{adapter.calls} calls, {adapter.tokens_in:,} in / "
                           f"{adapter.tokens_out:,} out tokens, {elapsed:.0f}s judging pass")


def score_run(rows, store, *, model_name, has_spans=True, stable=0, flips=0, sym_ok=0,
              sym_n=0, rep_errs=0, sym_errs=0, usage="") -> int:
    """The V1-prime scoring, shared by every arm.

    One implementation, or two arms drift apart and the comparison stops being
    apples-to-apples (the ``article_tokens`` discipline, applied to measurement). Arms whose
    model emits no quoted spans (a similarity score, not a reasoned verdict) pass
    ``has_spans=False``: the quote-check is then reported n/a rather than scored as 0%, which
    would fail a span-less arm on a bar that cannot apply to it."""
    from collections import Counter
    verdicts = {r["pair_id"]: store[r["pair_id"]] for r in rows}
    n = len(rows)
    unc = sum(1 for r in rows if verdicts[r["pair_id"]]["verdict"] == "uncertain")
    by_class_unc: dict = {}
    by_class_n: dict = {}
    for r in rows:
        c = r["class"].split(":")[0]
        by_class_n[c] = by_class_n.get(c, 0) + 1
        if verdicts[r["pair_id"]]["verdict"] == "uncertain":
            by_class_unc[c] = by_class_unc.get(c, 0) + 1
    quotes_ok = sum(1 for r in rows if verdicts[r["pair_id"]].get("quote_ok"))
    demoted_n = sum(1 for r in rows if verdicts[r["pair_id"]].get("demoted"))
    api_errors = sum(1 for r in rows if verdicts[r["pair_id"]].get("api_error"))

    print(f"\n-- V1a: label-free properties (bars pre-registered) --   [model: {model_name}]")
    print(f"  uncertain-rate : {unc}/{n} ({unc / max(1, n):.1%})   bar <= 25%")
    for c in sorted(by_class_n):
        u, m = by_class_unc.get(c, 0), by_class_n[c]
        print(f"    {c:<16} {u}/{m} ({u / m:.0%})   bar <= 40%")
    if has_spans:
        print(f"  quote-check    : {quotes_ok}/{n} ({quotes_ok / max(1, n):.1%})   bar >= 99%")
        if quotes_ok < n:
            print(f"    failures: {demoted_n} decided verdicts demoted, {api_errors} api-error "
                  f"fail-closed, {n - quotes_ok - demoted_n - api_errors} uncertain with "
                  f"unverified spans")
    else:
        print(f"  quote-check    : n/a — this arm emits a similarity score, not quoted "
              f"evidence (the bar cannot apply; the absence is itself a finding)")
    print(f"  stability      : {stable}/{stable + flips} "
          f"({stable / max(1, stable + flips):.1%})   bar >= 98%"
          + (f"   (replay api errors: {rep_errs})" if rep_errs else ""))
    print(f"  symmetry       : {sym_ok}/{sym_n} "
          f"({sym_ok / max(1, sym_n):.1%})   bar >= 99%"
          + (f"   (swap api errors: {sym_errs})" if sym_errs else ""))
    print(f"  usage          : {usage}")

    print(f"\n-- V1b: the exhibit gate (authoritative pairs; one false-same is disqualifying) --")
    kill = False
    for r in rows:
        if r.get("label_source") != "human-exhibit":
            continue
        v = verdicts[r["pair_id"]]["verdict"]
        ok = v == r["label"]
        wrong_same = (r["label"] == "different_event" and v == "same_event")
        kill = kill or wrong_same
        mark = "DISQUALIFYING" if wrong_same else ("ok" if ok else "miss")
        print(f"  [{mark:>13}] {r['class'][8:]:<22} label={r['label']:<15} verdict={v}")

    print(f"\n-- V1c: rule-tier bounds (never point estimates) --")
    for tier, want in (("rule:no-affinity", "different_event"), ("rule:near-dup", "same_event")):
        tier_rows = [r for r in rows if r.get("label_source") == tier]
        if not tier_rows:
            continue
        errs = sum(1 for r in tier_rows
                   if verdicts[r["pair_id"]]["verdict"] not in (want, "uncertain"))
        m = len(tier_rows)
        if errs == 0:
            print(f"  {tier:<18} 0/{m} contradictions -> licenses only an upper bound "
                  f"of {min(1.0, 3.0 / m):.1%} (95%)")
        else:
            print(f"  {tier:<18} {errs}/{m} contradictions ({errs / m:.1%}) — bound not "
                  f"licensed; hand-read these")

    print(f"\n-- V1d: diagnostics (never bars) --")
    drafts = [r for r in rows if r.get("label_source") == "draft-only"]
    agree = sum(1 for r in drafts
                if verdicts[r["pair_id"]]["verdict"] == r.get("draft_label"))
    print(f"  draft agreement: {agree}/{len(drafts)} "
          f"({agree / max(1, len(drafts)):.0%} — diagnostic only)")
    und = [r for r in rows if r.get("label_source") == "undetermined"]
    dist = Counter(verdicts[r["pair_id"]]["verdict"] for r in und)
    print(f"  undetermined verdicts: {dict(dist)}")
    shortlist = ([r["pair_id"] for r in drafts
                  if verdicts[r["pair_id"]]["verdict"] not in (r.get("draft_label"), "uncertain")]
                 + [r["pair_id"] for r in und
                    if verdicts[r["pair_id"]]["verdict"] != "uncertain"][:40])
    print(f"  human shortlist ({len(shortlist)} pairs — draft disagreements + decided "
          f"undetermined): {shortlist[:12]}{' …' if len(shortlist) > 12 else ''}")

    print(f"\n-- verdict --")
    v1a_ok = (unc / max(1, n) <= 0.25
              and all(by_class_unc.get(c, 0) / by_class_n[c] <= 0.40 for c in by_class_n)
              and (quotes_ok / max(1, n) >= 0.99 or not has_spans)
              and stable / max(1, stable + flips) >= 0.98
              and sym_ok / max(1, sym_n) >= 0.99)
    if kill:
        print(f"  KILL — {model_name} produced same_event on a labeled-different exhibit; "
              f"the verifier design fails its gate on this model.")
    elif api_errors > max(2, int(0.02 * n)):
        print(f"  VOID — {api_errors}/{n} store records are api-error fail-closed: this run "
              f"measured transport availability, not {model_name}, and no bar was tested. "
              f"Diagnose the recorded reasons, fix the transport (pacing/quota), and re-run — "
              f"resume re-judges api-error records and keeps every real verdict.")
    elif v1a_ok:
        print(f"  SCREENING PASS for {model_name} — all measurable V1-prime bars met. This "
              f"licenses continuing the design, NOT production wiring: the registered 1%/3% "
              f"error bars remain unmeasurable without the human shortlist above.")
    else:
        print(f"  FAIL — one or more V1a bars missed for {model_name}; see the numbers "
              f"above. Per the standing rule: report, do not tune around it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
