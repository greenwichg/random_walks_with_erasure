"""Lightweight smoke test for the Browser Extension Playground Colab notebook.

Validates the notebook structurally (valid nbformat, the expected sections, safe defaults) and
that its moving parts hold together — the pure-Python cells compile, the embedded runtime helper
module compiles and its pure functions work, and the experiment kit it shells out to exists with
the exact stages the notebook offers. It does NOT execute the notebook (which boots the engine,
builds the web app, and opens a tunnel); the full pipeline behind it is covered by the extension
tests (tests/test_extension_value_chain.py, tests/test_content_lifecycle.py) and the kit itself.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
NB = ROOT / "deploy" / "browser_extension_playground.ipynb"


def _nb():
    return json.loads(NB.read_text())


def _text():
    return "".join("".join(c["source"]) for c in _nb()["cells"])


def _cell(marker):
    for c in _nb()["cells"]:
        src = "".join(c["source"])
        if marker in src:
            return src
    raise AssertionError("no cell contains: " + marker)


def test_notebook_is_valid_nbformat():
    nb = _nb()
    assert nb["nbformat"] == 4
    assert len(nb["cells"]) == 14 and all("cell_type" in c for c in nb["cells"])


def test_notebook_has_the_expected_sections():
    text = _text()
    for marker in ("5-minute Quick Start", "What's happening?",           # DX: intro + diagram
                   "1 · Setup", "2 · Launch", "2b · Status", "3 · What the extension is",
                   "4 · Install the extension", "5 · Generate your extension token",
                   "6 · Connection test", "7 · Read a real article",
                   "8 · Live validation dashboard", "9 · Developer Playground",
                   "10 · Run the automated experiment kit", "11 · Debugging guide",
                   "12 · Reset the playground"):
        assert marker in text, "missing notebook section: " + marker


def test_notebook_reuses_the_existing_surfaces_not_new_ones():
    """The playground is an orchestration layer: product-notebook launch recipes, the real token
    endpoint, the extension's own connection-test request shape, and the experiment kit."""
    text = _text()
    assert "examples/extension_experiment.py" in text          # Section 10 = the kit, unchanged
    assert "/api/me/tokens" in text                            # existing token endpoint
    assert '"reads": []' in text                               # the extension's own test payload
    assert "/api/internal/refresh" in text                     # existing diagnostics
    assert "demo@infodiet.local" in text                       # same demo identity as the app
    # the poller only cycles with a feeds spec — the launch cell must set it (quiet-feed lesson)
    assert '"RWE_RSS_FEEDS": "deploy/rss_feeds.example.txt"' in text


def test_launch_never_fetches_sources_upfront():
    """Launching must not run an ingestion pass — the background poller (and the user's own
    extension reads) seed the catalog, and the status cards explain an empty one instead."""
    launch = _cell("2 · Launch")
    assert "rss_ingest" not in launch
    assert "def catalog_card" in launch and "def refresh_card" in launch   # the cards ship in the helper
    assert "ui.catalog_card()" in launch                                   # rendered right after launch
    assert "Waiting for RSS ingestion" in launch                           # the empty-catalog explainer
    status = _cell("2b · Status")
    assert "ui.catalog_card()" in status and "ui.refresh_card()" in status


def test_notebook_defaults_are_safe():
    text = _text()
    assert 'CORPUS = "live-feed"' in text
    assert "TUNNEL = True" in text
    assert "SIMULATE_READ = False" in text                     # a real browser read by default
    assert "REGENERATE = False" in text
    assert "CLEAR_DEMO_READS = False" in text                  # reset never drops data by default
    assert "FULL_SHUTDOWN = False" in text
    assert 'GITHUB_TOKEN = ""' in text                         # no credential baked in


def test_kit_stage_choices_match_the_kit():
    """The STAGE dropdown must offer exactly the stages the kit implements (plus "all")."""
    src = _cell("10 · Run the automated experiment kit")
    assert '["all", "2", "3", "4", "5", "6", "8", "9", "10"]' in src
    kit = (ROOT / "examples" / "extension_experiment.py").read_text()
    assert "stages = {2:" in kit                               # the kit's stage table start
    for n in (2, 3, 4, 5, 6, 8, 9, 10):
        assert f"stage{n}" in kit or f"{n}: p.stage" in kit


def test_pure_python_cells_compile():
    """Every cell except Setup (which uses Colab magics) must be valid pure Python."""
    for c in _nb()["cells"]:
        src = "".join(c["source"])
        if c["cell_type"] != "code" or "1 · Setup" in src:
            continue
        compile(src, "cell", "exec")


def test_embedded_helper_module_compiles_and_works(tmp_path):
    """Section 2 writes _playground_ui.py; extract it, compile it, and drive the pure parts
    (status blocks, slug join, state file) against a temp directory."""
    launch = _cell("2 · Launch")
    module_src = launch.split("write_text(r'''")[1].split("''')")[0]
    compile(module_src, "_playground_ui.py", "exec")
    ns = {"__file__": str(tmp_path / "_playground_ui.py"), "__name__": "_playground_ui"}
    exec(module_src, ns)                                       # no side effects beyond sys.path
    assert ns["slug_of"]("https://www.bbc.com/news/articles/abc-123") == "abc-123"
    assert ns["block"]("ok", "x") is True and ns["block"]("wait", "x") is False
    ns["save_state"](publicUrl="https://x.trycloudflare.com", uid=7)
    st = ns["load_state"]()
    assert st["publicUrl"].startswith("https://") and st["uid"] == 7
    assert (tmp_path / "playground_state.json").exists()       # state is module-dir anchored
    assert ns["card"]("ok", "Catalog Status", [("FeedArticles", 152)]) is True
    assert ns["_age"](None) == "never yet"


def test_runtime_artifacts_are_gitignored():
    gi = (ROOT / ".gitignore").read_text()
    for artifact in ("/_playground_ui.py", "/playground_state.json",
                     "/engine.log", "/web.log", "/cf.log", "/cloudflared"):
        assert artifact in gi, artifact + " missing from .gitignore"


def test_privacy_explainer_present():
    text = _text()
    assert "never article text" in text or "never collects" in text.lower()
    assert "browsing history" in text
