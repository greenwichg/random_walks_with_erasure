"""Tests for examples/llm_label.py (the LLM second-opinion annotator).

No real API calls — the network boundary is the injected ``call_fn``, so these
exercise the parsing/batching/writing and the round-trip into the ingest loader.
"""

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "llm_label", ROOT / "examples" / "llm_label.py")
ll = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ll)

from rwe.mind import _load_positions_map


def test_read_template_tsv_with_position_column(tmp_path):
    # exactly the layout validate_lean.py --sample writes (position left blank)
    p = tmp_path / "tmpl.tsv"
    p.write_text("news_id\tposition\ttitle\nN1\t\tSenate passes tax cut\nN2\t\tLocal team wins\n")
    rows = ll.read_template(str(p))
    assert rows == [("N1", "Senate passes tax cut"), ("N2", "Local team wins")]


def test_read_template_csv_and_default_columns(tmp_path):
    p = tmp_path / "tmpl.csv"
    p.write_text("news_id,title\nN1,A headline\nN2,Another\n")
    assert ll.read_template(str(p)) == [("N1", "A headline"), ("N2", "Another")]
    # no recognized header -> first col = id, last col = title
    q = tmp_path / "noheader.tsv"
    q.write_text("X1\tsome title here\nX2\tmore\n")
    assert ll.read_template(str(q)) == [("X2", "more")]  # first line consumed as header


def test_parse_labels_valid_and_malformed():
    text = json.dumps({"labels": [
        {"id": "N1", "lean": -1, "reason": "progressive framing"},
        {"id": "N2", "lean": 1, "reason": "tax-cut boosterism"},
        {"id": "N3", "lean": "bad"},          # non-int lean -> skipped
        {"lean": 0, "reason": "no id"},        # missing id -> skipped
    ]})
    got = ll.parse_labels(text)
    assert got == {"N1": (-1, "progressive framing"), "N2": (1, "tax-cut boosterism")}


def test_label_headlines_batches_and_merges():
    rows = [(f"N{i}", f"title {i}") for i in range(5)]
    seen = []

    def fake_call(system, user):
        # capture how the rows were batched, answer every id in this chunk
        ids = [ln.split("\t")[0] for ln in user.strip().splitlines() if ln.startswith("N")]
        seen.append(len(ids))
        return json.dumps({"labels": [{"id": i, "lean": 0, "reason": "x"} for i in ids]})

    labels = ll.label_headlines(rows, fake_call, batch=2)
    assert seen == [2, 2, 1]                    # 5 rows -> chunks of 2,2,1
    assert set(labels) == {f"N{i}" for i in range(5)}


def test_write_labels_round_trips_through_ingest_loader(tmp_path):
    rows = [("N1", "t1"), ("N2", "t2"), ("N3", "t3")]
    labels = {"N1": (-1, "left framing"),
              "N2": (1, "right, with a comma in the reason")}  # N3 unlabeled
    out = tmp_path / "llm_labels.csv"
    n = ll.write_labels(labels, rows, str(out), "claude-opus-4-8")
    assert n == 2

    text = out.read_text()
    assert text.startswith("# LLM-annotator labels (convergent validity")  # provenance stamp
    assert "claude-opus-4-8" in text.splitlines()[0]

    # the file is exactly what validate_lean.py --against / ingest --positions-csv read
    pos = _load_positions_map(str(out))
    assert pos == {"N1": -1.0, "N2": 1.0}        # comment + header skipped; comma-in-reason safe


def test_end_to_end_label_then_validate(tmp_path):
    # full path with no network: template -> fake LLM -> labels -> loader
    tmpl = tmp_path / "tmpl.tsv"
    tmpl.write_text("news_id\tposition\ttitle\n"
                    "N1\t\tProgressives push Green New Deal\n"
                    "N2\t\tConservatives rally for tax cuts\n"
                    "N3\t\tCity council debates parking\n")
    rows = ll.read_template(str(tmpl))

    truth = {"N1": -1, "N2": 1, "N3": 0}

    def fake_call(system, user):
        ids = [ln.split("\t")[0] for ln in user.strip().splitlines() if ln.startswith("N")]
        return json.dumps({"labels": [
            {"id": i, "lean": truth[i], "reason": "r"} for i in ids]})

    labels = ll.label_headlines(rows, fake_call, batch=20)
    out = tmp_path / "out.csv"
    ll.write_labels(labels, rows, str(out), "claude-opus-4-8")
    assert _load_positions_map(str(out)) == {"N1": -1.0, "N2": 1.0, "N3": 0.0}


def test_parse_labels_strips_fences_and_tolerates_garbage():
    # a model that wraps its JSON in a ```json fence still parses
    fenced = "```json\n" + json.dumps(
        {"labels": [{"id": "N1", "lean": 1, "reason": "r"}]}) + "\n```"
    assert ll.parse_labels(fenced) == {"N1": (1, "r")}
    # garbage / empty -> empty dict, never raises (one bad batch can't abort the run)
    assert ll.parse_labels("sorry, I can't do that") == {}
    assert ll.parse_labels("") == {}
    assert ll.parse_labels(None) == {}


def test_make_caller_unknown_provider_errors():
    try:
        ll.make_caller("bogus", "m")
        assert False, "expected SystemExit on unknown provider"
    except SystemExit:
        pass


def test_default_models_cover_both_providers():
    assert ll._DEFAULT_MODELS["gemini"].startswith("gemini")      # free tier
    assert ll._DEFAULT_MODELS["anthropic"].startswith("claude")   # paid
