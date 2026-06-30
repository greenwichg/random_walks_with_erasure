"""Thin web UI for the Information Health Report + LLM narrative — the demo artifact.

Wraps the existing pipeline behind a tiny **dependency-free** web server (Python stdlib
``http.server`` — no Flask, nothing to ``pip install``), so the whole thing is *clickable*
instead of notebook output: the health-report card is the existing ``render_html``; the
AI narrative + steelman + grounding checks + bridging recommendations come from
``narrate_report`` (candidates chosen by the real **RWE-B recommender**). It degrades
gracefully — with no API key it still shows the card and the recommender output; set
``GEMINI_API_KEY`` to light up the narrative.

    export GEMINI_API_KEY=...                                   # optional (free Gemini)
    python examples/app.py --npz mind_full.npz --domain news
    python examples/app.py --npz politosphere_mi200.npz --domain reddit   # validated axis
    # open http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import html
import http.server
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling examples
import numpy as np
import health_report as hr
import narrate_report as nr
from rwe.mind import MINDData

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Information Health</title><style>
body{{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}}
a{{color:#2563eb;text-decoration:none}} a:hover{{text-decoration:underline}}
.chip{{display:inline-block;background:#eef2ff;border:1px solid #c7d2fe;border-radius:999px;padding:.25rem .8rem;margin:.2rem;font-size:13px}}
.card{{border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;margin:1rem 0}}
iframe{{width:100%;border:0;height:600px}}
.narr{{background:#f8fafc;border-left:4px solid #6366f1;padding:1rem 1.2rem;border-radius:8px;white-space:pre-wrap}}
.ok{{color:#15803d;font-weight:600}} .warn{{color:#b45309;font-weight:600}}
.recs li{{margin:.25rem 0}} h1{{font-size:1.45rem}} h2{{font-size:1.1rem;margin-top:1.6rem}}
.muted{{color:#64748b;font-size:13px}} code{{background:#f1f5f9;padding:.1rem .3rem;border-radius:4px}}
</style></head><body>{body}</body></html>"""


def _index_body(picks, domain) -> str:
    chips = " ".join(f'<a class="chip" href="/report?user={u}">reader {u}</a>' for u in picks)
    return (f"<h1>Information Health Report</h1>"
            f"<p class='muted'>A wellness check for your media diet — domain <b>{html.escape(domain)}</b>. "
            f"Pick a reader (these are the most one-sided, for a clear demo) or "
            f"<a href='/report'>surprise me</a>.</p>"
            f"<div>{chips}</div>"
            f"<form action='/report'><p style='margin-top:1rem'>…or a specific id: "
            f"<input name='user' size='6'> <button>Go</button></p></form>")


def _checks_html(nums, titles, has_titles) -> str:
    n = ("<span class='ok'>✓ every number traces to a computed metric</span>" if not nums
         else "<span class='warn'>⚠ numbers not found in the metrics: "
              f"{html.escape(', '.join(nums))}</span>")
    t = ""
    if has_titles:
        t = ("<br><span class='ok'>✓ every recommended title is from the real list</span>"
             if not titles else "<br><span class='warn'>⚠ titles not in the list: "
             f"{html.escape(' | '.join(titles))}</span>")
    return f"<p class='muted' style='margin-top:.6rem'>{n}{t}</p>"


def _report_body(u, card_html, narr_html, recs, domain) -> str:
    what = "communities" if domain == "reddit" else "articles"
    recs_html = "".join(f"<li>{html.escape(t)}</li>" for t in recs) or "<li>(none)</li>"
    return (f"<p><a href='/'>&larr; all readers</a></p><h1>Reader {html.escape(str(u))}</h1>"
            f"<div class='card'><iframe srcdoc=\"{html.escape(card_html, quote=True)}\"></iframe></div>"
            f"<h2>AI narrative &amp; steelman</h2>{narr_html}"
            f"<h2>Bridging {what} — chosen by the RWE-B recommender</h2>"
            f"<ul class='recs'>{recs_html}</ul>")


def make_renderer(npz: str, domain: str, provider: str, model):
    """Load + compute once; return ``render(path, query) -> html`` (server-agnostic, so it
    is unit-testable without starting a server)."""
    mind = MINDData.load(npz)
    src = None if domain == "news" else np.asarray(mind.titles)
    # wire in the optional enrichment files if they exist (so Reporting / Emotional /
    # Attention / Open-Mindedness populate once you've run the classifiers, like # 8d).
    import glob
    register = emotion = selective = None
    if os.path.exists("register.csv"):
        register = hr._load_item_csv("register.csv", mind.dataset.item_ids)["reporting"]
    if os.path.exists("emotion.csv"):
        emotion = hr._load_item_csv("emotion.csv", mind.dataset.item_ids)
    beh = [b for b in glob.glob("**/behaviors.tsv", recursive=True) if "fixture" not in b]
    if beh:
        selective = hr.selective_exposure_array(mind, beh[0])
    pop = hr.compute(mind, source=src, register=register, emotion=emotion, selective=selective)
    eligible = hr._eligible_pool(pop, 5)
    if len(eligible) == 0:
        raise SystemExit("no eligible users (>=5 clicks) in this .npz")
    picks = nr._rank_demo_users(pop["n_pol"], pop["mean_lean"], eligible)[:8]
    mdl = model or nr._DEFAULT_MODELS[provider]

    def render(path: str, query: dict) -> str:
        if path == "/":
            return _PAGE.format(body=_index_body(picks, domain))
        q = (query.get("user", [""])[0] or "").strip()
        u = int(q) if q.lstrip("-").isdigit() else nr._pick_user(pop, mind, eligible)
        rep = hr.user_report(pop, mind, int(u))
        card = hr.render_html([rep], labels=hr._LABELS[domain])
        facts_text = nr.facts_to_text(nr.report_facts(rep, domain))
        recs = nr.rweb_recommendations(mind, rep) or nr.bridge_candidates(mind, rep)

        if os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
            try:
                narrative = nr.narrate(
                    facts_text, nr.make_text_caller(provider, mdl), recs, domain)
                checks = _checks_html(nr.check_grounding(narrative, facts_text),
                                      nr.check_title_grounding(narrative, recs), bool(recs))
                narr_html = f"<div class='narr'>{html.escape(narrative)}</div>{checks}"
            except Exception as e:                              # API hiccup -> still show the rest
                narr_html = f"<p class='muted'>(narrative unavailable: {html.escape(str(e))})</p>"
        else:
            narr_html = ("<p class='muted'>Set <code>GEMINI_API_KEY</code> and restart to enable "
                         "the AI narrative + steelman. The card and the recommender output below "
                         "are live without it.</p>")
        return _PAGE.format(body=_report_body(u, card, narr_html, recs, domain))

    return render


def _make_handler(render):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path not in ("/", "/report"):
                self.send_error(404)
                return
            try:
                body = render(parsed.path, parse_qs(parsed.query))
            except Exception as e:                              # never 500 the demo
                body = _PAGE.format(body=f"<h1>error</h1><pre>{html.escape(str(e))}</pre>")
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):                              # keep the console quiet
            pass
    return Handler


def _key_state():
    return "ON" if (os.environ.get("GEMINI_API_KEY")
                    or os.environ.get("ANTHROPIC_API_KEY")) else "OFF — set GEMINI_API_KEY"


def serve(npz: str, domain: str, provider: str, model, host: str, port: int) -> None:
    """Blocking server (CLI entry point)."""
    render = make_renderer(npz, domain, provider, model)
    print(f"serving http://{host}:{port}  (domain={domain}, "
          f"narrative={_key_state()}) — Ctrl-C to stop")
    http.server.ThreadingHTTPServer((host, port), _make_handler(render)).serve_forever()


_BG_SERVER = None


def serve_background(npz: str, domain: str = "news", provider: str = "gemini",
                     model=None, host: str = "0.0.0.0", port: int = 8000):
    """Start (or **restart**) the app in a background thread — safe to call repeatedly from
    a notebook. It shuts down any prior instance first, so a re-run reloads fresh data and
    any newly-created enrichment CSVs (emotion.csv / register.csv) without a port clash."""
    global _BG_SERVER
    import threading
    if _BG_SERVER is not None:                                 # tear down the previous one
        try:
            _BG_SERVER.shutdown()
            _BG_SERVER.server_close()
        except Exception:
            pass
    render = make_renderer(npz, domain, provider, model)       # reloads compute + CSVs
    _BG_SERVER = http.server.ThreadingHTTPServer((host, port), _make_handler(render))
    threading.Thread(target=_BG_SERVER.serve_forever, daemon=True).start()
    print(f"serving on port {port}  (domain={domain}, narrative={_key_state()})")
    return _BG_SERVER


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="ingested .npz (with ideology)")
    ap.add_argument("--domain", choices=["news", "reddit"], default="news")
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini")
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    serve(args.npz, args.domain, args.provider, args.model, args.host, args.port)


if __name__ == "__main__":
    main()
