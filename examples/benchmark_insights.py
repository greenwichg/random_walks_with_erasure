"""benchmark_insights.py — compare Article Insights providers/models on one fixed golden set.

Why this exists: choosing a provider is an empirical question about *this* prompt, *this*
contract and *this* article mix. A model that writes beautiful prose but trips the no-label rule
on a third of political stories is worse here than a duller one that never does — and no vendor
benchmark can tell you that, because the thing being measured is the product's own validator.

    python examples/benchmark_insights.py --list
    python examples/benchmark_insights.py --out docs/INSIGHTS_BENCHMARK.md
    python examples/benchmark_insights.py --targets ollama/llama3.2:1b --repeats 3
    python examples/benchmark_insights.py --articles quake,contested --json run.json

**It does not change production behaviour and does not contain a second copy of it.** Every call
goes through the real port (``insights_provider.from_env``) and the real policy
(``article_insights.generate`` — the same grounding prompt, the same JSON contract, the same
2–4-sentence bound and no-label rule). The harness only *selects* targets by setting the same
environment variables an operator would set, times the calls, and tabulates what came back. A
change in the report is therefore a change in the model, never in the harness.

Adding a model is one object in ``data/insights_benchmark_targets.json``. Adding a vendor is one
adapter class in ``insights_provider.py`` (production), after which its models configure here
like any other — the harness has no per-vendor branches except the optional token meter below,
which degrades to "n/a" for anything it does not recognise.

Token accounting, honestly: the provider port returns text, not usage — deliberately, because
the application has no use for token counts. Rather than change the port for a dev tool, the
meter hooks each vendor's transport *inside this process only* (Ollama's HTTP response carries
``prompt_eval_count`` / ``eval_count``; the Anthropic SDK's message carries ``usage``). If that
hook fails for any reason the run continues and the row reads ``n/a`` with costs marked as
estimates from character counts.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import article_insights          # the real policy: prompt, contract, validation
import insights_provider         # the real port: selection by environment

DATA = pathlib.Path(__file__).resolve().parent / "data"
GOLDEN_SET = DATA / "insights_golden_set.json"
TARGETS = DATA / "insights_benchmark_targets.json"

#: Rough characters-per-token, used ONLY when a vendor exposes no usage numbers. Labelled as an
#: estimate everywhere it reaches the report — a made-up number presented as measured is worse
#: than an honest blank.
CHARS_PER_TOKEN = 4.0


@dataclass
class Target:
    name: str
    provider: str
    model: Optional[str] = None
    env: dict = field(default_factory=dict)
    pricing: dict = field(default_factory=dict)
    notes: str = ""
    enabled: bool = True


@dataclass
class Call:
    """One article through one target."""
    article_id: str
    ok: bool
    ms: float
    failure_kind: str = ""        # "" | "transport" | "validation"
    failure: str = ""
    payload: Optional[dict] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    chars_in: int = 0
    chars_out: int = 0


# --------------------------------------------------------------------------- #
# Token meter — vendor transport hooks, harness-process only.
# --------------------------------------------------------------------------- #

class _Spy:
    """A read-once HTTP response stand-in: the body is buffered so the meter and the production
    code both see it. Mirrors only what ``OllamaProvider.complete`` uses (``read``, ``status``)."""

    def __init__(self, body: bytes, status: int):
        self._body, self.status = body, status

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextlib.contextmanager
def _meter(provider):
    """Yield a dict that fills with ``{in, out}`` token counts for the most recent call.

    Fail-soft by construction: any exception in the hook leaves the box empty (``n/a`` in the
    report) and never disturbs the call being measured."""
    box: dict = {}
    if isinstance(provider, insights_provider.OllamaProvider):
        real = urllib.request.urlopen

        def spy(req, *a, **k):
            resp = real(req, *a, **k)
            try:
                body = resp.read()
            except Exception:
                return resp
            try:
                data = json.loads(body)
                if isinstance(data, dict) and "prompt_eval_count" in data:
                    box["in"] = data.get("prompt_eval_count")
                    box["out"] = data.get("eval_count")
            except Exception:
                pass
            return _Spy(body, getattr(resp, "status", 200))

        urllib.request.urlopen = spy
        try:
            yield box
        finally:
            urllib.request.urlopen = real
        return

    client = getattr(provider, "_client", None)
    msgs = getattr(client, "messages", None)
    if msgs is not None and hasattr(msgs, "create"):
        real_create = msgs.create

        def create(**kw):
            msg = real_create(**kw)
            try:
                usage = getattr(msg, "usage", None)
                if usage is not None:
                    box["in"] = getattr(usage, "input_tokens", None)
                    box["out"] = getattr(usage, "output_tokens", None)
            except Exception:
                pass
            return msg

        msgs.create = create
        try:
            yield box
        finally:
            msgs.create = real_create
        return

    yield box                                   # unknown vendor → no usage, honest n/a


# --------------------------------------------------------------------------- #
# Loading + running
# --------------------------------------------------------------------------- #

def load_articles(path: pathlib.Path, only: "list[str] | None" = None) -> list:
    doc = json.loads(path.read_text())
    arts = doc["articles"]
    if only:
        want = set(only)
        arts = [a for a in arts if a["id"] in want]
        missing = want - {a["id"] for a in arts}
        if missing:
            raise SystemExit(f"no such article id(s): {sorted(missing)}")
    return arts


def load_targets(path: pathlib.Path, only: "list[str] | None" = None) -> list:
    doc = json.loads(path.read_text())
    out = [Target(**{k: v for k, v in t.items() if k in Target.__dataclass_fields__})
           for t in doc["targets"]]
    if only:
        want = set(only)
        out = [t for t in out if t.name in want]
        missing = want - {t.name for t in out}
        if missing:
            raise SystemExit(f"no such target(s): {sorted(missing)}")
    return [t for t in out if t.enabled]


@contextlib.contextmanager
def _env_for(target: Target):
    """Exactly the environment an operator would set for this target, restored afterwards.

    This is the harness's whole selection mechanism — proof in itself that switching provider or
    model is configuration, since the benchmark never imports a vendor SDK or names one."""
    prior = dict(os.environ)
    os.environ["RWE_INSIGHTS_PROVIDER"] = target.provider
    if target.model:
        os.environ["RWE_INSIGHTS_MODEL"] = target.model
    else:
        os.environ.pop("RWE_INSIGHTS_MODEL", None)
    for k, v in (target.env or {}).items():
        os.environ[k] = os.path.expandvars(str(v))
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(prior)


def run_target(target: Target, articles: list, repeats: int, verbose: bool = False) -> dict:
    """All articles × repeats through one target. Never raises: an unavailable provider is a
    reported outcome, not a crashed benchmark."""
    with _env_for(target):
        provider = insights_provider.from_env()
        if provider is None:
            hint = getattr(insights_provider._REGISTRY.get(target.provider), "unavailable_hint",
                           "provider unavailable")
            return {"target": target, "skipped": hint, "calls": [],
                    "model": target.model or "(default)"}
        model = article_insights.model_name(provider)
        calls: list = []
        for art in articles:
            payload_in = {"headline": art["headline"], "description": art.get("description", ""),
                          "body": art.get("body")}
            chars_in = len(article_insights.article_text(payload_in))
            for _ in range(repeats):
                with _meter(provider) as usage:
                    t0 = time.perf_counter()
                    try:
                        out = article_insights.generate(payload_in, provider)
                        ms = (time.perf_counter() - t0) * 1000
                        calls.append(Call(art["id"], True, ms, payload=out,
                                          tokens_in=usage.get("in"), tokens_out=usage.get("out"),
                                          chars_in=chars_in,
                                          chars_out=len(json.dumps(out))))
                    except ValueError as e:                 # the contract rejected the answer
                        ms = (time.perf_counter() - t0) * 1000
                        calls.append(Call(art["id"], False, ms, "validation", str(e)[:160],
                                          tokens_in=usage.get("in"), tokens_out=usage.get("out"),
                                          chars_in=chars_in))
                    except Exception as e:                  # transport / vendor failure
                        ms = (time.perf_counter() - t0) * 1000
                        calls.append(Call(art["id"], False, ms, "transport",
                                          f"{type(e).__name__}: {e}"[:160], chars_in=chars_in))
                if verbose:
                    c = calls[-1]
                    print(f"    {target.name:26} {c.article_id:10} "
                          f"{'ok ' if c.ok else 'FAIL'} {c.ms:8.1f} ms  {c.failure[:60]}",
                          flush=True)
        return {"target": target, "skipped": None, "calls": calls, "model": model}


# --------------------------------------------------------------------------- #
# Aggregation + report
# --------------------------------------------------------------------------- #

def summarize(run: dict) -> dict:
    t: Target = run["target"]
    calls = run["calls"]
    ok = [c for c in calls if c.ok]
    lat = sorted(c.ms for c in calls) or [0.0]
    tin = [c.tokens_in for c in calls if c.tokens_in]
    tout = [c.tokens_out for c in calls if c.tokens_out]
    # Each side is measured or estimated on its own: a vendor that reports output tokens but not
    # input ones must not make the input column look measured.
    avg_in = statistics.mean(tin) if tin else (statistics.mean([c.chars_in for c in calls] or [0])
                                              / CHARS_PER_TOKEN)
    avg_out = statistics.mean(tout) if tout else (
        statistics.mean([c.chars_out for c in ok] or [0]) / CHARS_PER_TOKEN)
    measured = bool(tin) and bool(tout)
    price = t.pricing or {}
    cost_1k = ((avg_in * price.get("input_per_mtok", 0.0)
                + avg_out * price.get("output_per_mtok", 0.0)) / 1e6) * 1000
    return {
        "name": t.name, "model": run.get("model"), "notes": t.notes,
        "skipped": run["skipped"], "calls": len(calls),
        "ok": len(ok),
        "transport_fail": len([c for c in calls if c.failure_kind == "transport"]),
        "validation_fail": len([c for c in calls if c.failure_kind == "validation"]),
        "pass_rate": (len(ok) / len(calls)) if calls else 0.0,
        "p50": statistics.median(lat), "p95": lat[max(0, int(len(lat) * 0.95) - 1)],
        "avg_in": avg_in, "avg_out": avg_out, "measured_tokens": measured,
        "cost_1k": cost_1k, "priced": bool(price.get("input_per_mtok")
                                           or price.get("output_per_mtok")),
        "failures": _failure_counts(calls),
    }


def _failure_counts(calls: list) -> list:
    counts: dict = {}
    for c in calls:
        if not c.ok:
            key = (c.failure_kind, c.failure.split(" (")[0][:80])
            counts[key] = counts.get(key, 0) + 1
    return sorted(({"kind": k[0], "reason": k[1], "n": n} for k, n in counts.items()),
                  key=lambda d: -d["n"])


def _fmt_tokens(s: dict) -> str:
    tag = "" if s["measured_tokens"] else " est"
    return f"{s['avg_in']:.0f} / {s['avg_out']:.0f}{tag}"


def _fmt_cost(s: dict) -> str:
    if not s["priced"]:
        return "$0.00 (local)"
    tag = "" if s["measured_tokens"] else " (est)"
    return f"${s['cost_1k']:.2f}{tag}"


def report_markdown(summaries: list, runs: list, articles: list, *, repeats: int,
                    note: str = "") -> str:
    L: list = []
    A = L.append
    A("# Article Insights — provider/model benchmark\n")
    A(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')} · "
      f"**golden set:** {len(articles)} articles (`data/insights_golden_set.json` v"
      f"{json.loads(GOLDEN_SET.read_text())['version']}) · **repeats:** {repeats}\n")
    A("Produced by `examples/benchmark_insights.py`, which runs every target through the "
      "**production** prompt, contract and validator (`article_insights.generate`) via the "
      "**production** provider port. Nothing about the pipeline is re-implemented here, so a "
      "difference below is a difference between models.\n")
    if note:
        A(f"> **Run note:** {note}\n")

    A("\n## Results\n")
    A("| target | model | calls | pass rate | transport fail | validation fail | "
      "p50 ms | p95 ms | tokens in/out | est. cost / 1k articles |")
    A("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for s in summaries:
        if s["skipped"]:
            A(f"| `{s['name']}` | — | — | **skipped** | — | — | — | — | — | — |")
            continue
        A(f"| `{s['name']}` | {s['model']} | {s['calls']} | **{s['pass_rate']:.0%}** | "
          f"{s['transport_fail']} | {s['validation_fail']} | {s['p50']:.0f} | {s['p95']:.0f} | "
          f"{_fmt_tokens(s)} | {_fmt_cost(s)} |")
    for s in summaries:
        if s["skipped"]:
            A(f"\n*`{s['name']}` skipped — {s['skipped']}.*")

    A("\n### Reading this table\n")
    A("- **pass rate** is what fraction of calls produced a *servable* artifact: the vendor "
      "answered **and** the answer survived the 2–4-sentence bound, the complete-bias-object "
      "check and the no-left/right-label rule. It is the number that matters — a failed "
      "validation costs a retry and an empty panel, not a bad artifact.")
    A("- **transport fail** vs **validation fail** separates \"the vendor broke\" from \"the "
      "model wrote something the product refuses to serve\".")
    A("- **tokens** are the vendor's own counts where it reports them; rows marked `est` are "
      "characters ÷ 4 and are approximations, as are the costs derived from them.")
    A("- **cost / 1k articles** uses the operator-maintained prices in "
      "`data/insights_benchmark_targets.json`. Vendor prices move — verify before quoting.")

    A("\n## Failure breakdown\n")
    any_fail = False
    for s in summaries:
        if s["skipped"] or not s["failures"]:
            continue
        any_fail = True
        A(f"**`{s['name']}`**\n")
        A("| n | kind | reason |")
        A("|---:|---|---|")
        for f in s["failures"]:
            A(f"| {f['n']} | {f['kind']} | {f['reason']} |")
        A("")
    if not any_fail:
        A("No failures on this run.\n")

    A("\n## Samples — the same article across targets\n")
    A("Quality is not a number. These are verbatim artifacts so the difference can be *read*.\n")
    by_target = {r["target"].name: r for r in runs}
    for art in articles:
        rows = []
        for s in summaries:
            r = by_target.get(s["name"])
            if not r or r["skipped"]:
                continue
            hit = next((c for c in r["calls"] if c.article_id == art["id"] and c.ok), None)
            fail = next((c for c in r["calls"] if c.article_id == art["id"] and not c.ok), None)
            if hit or fail:
                rows.append((s["name"], hit, fail))
        if not rows:
            continue
        A(f"\n### `{art['id']}` — {art['genre']}\n")
        A(f"> {art['headline']}\n")
        A(f"*probes: {'; '.join(art.get('probes', []))}*\n")
        for name, hit, fail in rows:
            A(f"**{name}**\n")
            if hit is None:
                A(f"- ✗ rejected — {fail.failure_kind}: {fail.failure}\n")
                continue
            p = hit.payload
            b = p["bias"]
            A(f"- **summary** — {p['summary']}")
            A(f"- **framing** — {b['framing']}")
            A(f"- **tone** — {b['tone']}")
            A(f"- **loaded language** — {', '.join(b['loadedLanguage']) or '(none)'}")
            A(f"- **omissions** — {b['omissions']}")
            A(f"- **viewpoint** — {b['viewpoint']}\n")

    A("\n## Method & caveats\n")
    A("- One process, sequential calls, no warm-up discarded: local models show first-call "
      "load cost in p95, which is honest — production pays it too after an idle period.")
    A("- The golden set is fixed and synthetic (original text, no publisher copy), so runs are "
      "comparable over time and the file can live in the repo.")
    A("- Ten articles is a shape, not a population estimate. Treat a pass-rate difference of a "
      "few points as noise; treat a model that fails a whole genre as signal.")
    A("- The harness sets only the environment variables an operator sets. It never imports a "
      "vendor SDK, so it cannot drift from how production selects a provider.")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--targets", help="comma-separated target names (default: all enabled)")
    ap.add_argument("--articles", help="comma-separated golden-set ids (default: all)")
    ap.add_argument("--repeats", type=int, default=1, help="calls per article (default 1)")
    ap.add_argument("--out", help="write the markdown report here")
    ap.add_argument("--json", dest="json_out", help="write raw per-call results here")
    ap.add_argument("--note", default="", help="a line stamped into the report (run context)")
    ap.add_argument("--list", action="store_true", help="show configured targets and exit")
    ap.add_argument("--quiet", action="store_true", help="no per-call progress")
    ap.add_argument("--golden-set", default=str(GOLDEN_SET))
    ap.add_argument("--targets-file", default=str(TARGETS))
    args = ap.parse_args(argv)

    targets = load_targets(pathlib.Path(args.targets_file),
                           args.targets.split(",") if args.targets else None)
    if args.list:
        print(f"{'target':30} {'provider':10} {'model':22} notes")
        for t in targets:
            print(f"{t.name:30} {t.provider:10} {(t.model or '(default)'):22} {t.notes}")
        return 0

    articles = load_articles(pathlib.Path(args.golden_set),
                             args.articles.split(",") if args.articles else None)
    print(f"benchmarking {len(targets)} target(s) × {len(articles)} article(s) × "
          f"{args.repeats} repeat(s)")
    runs = []
    for t in targets:
        print(f"  {t.name} …", flush=True)
        runs.append(run_target(t, articles, args.repeats, verbose=not args.quiet))
        if runs[-1]["skipped"]:
            print(f"    skipped — {runs[-1]['skipped']}")
    summaries = [summarize(r) for r in runs]

    print()
    print(f"{'target':30} {'pass':>6} {'p50 ms':>9} {'cost/1k':>12}")
    for s in summaries:
        if s["skipped"]:
            print(f"{s['name']:30} {'skip':>6}")
            continue
        print(f"{s['name']:30} {s['pass_rate']:>5.0%} {s['p50']:>9.0f} {_fmt_cost(s):>12}")

    md = report_markdown(summaries, runs, articles, repeats=args.repeats, note=args.note)
    if args.out:
        pathlib.Path(args.out).write_text(md)
        print(f"\nreport → {args.out}")
    if args.json_out:
        raw = [{"target": r["target"].name, "model": r.get("model"), "skipped": r["skipped"],
                "calls": [vars(c) for c in r["calls"]]} for r in runs]
        pathlib.Path(args.json_out).write_text(json.dumps(raw, indent=2))
        print(f"raw    → {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
