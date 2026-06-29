"""Tests for examples/build_source_map.py + the bundled outlet_lean.csv."""

import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_source_map", ROOT / "examples" / "build_source_map.py")
bsm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsm)

from rwe.mind import _load_source_map, _norm, load_lean_table


def test_read_catalog_tsv_and_csv(tmp_path):
    tsv = tmp_path / "cat.tsv"
    tsv.write_text("news_id\tpublisher\ttitle\nN1\tFox News\tA\nN2\tCNN\tB\n")
    rows = bsm.read_catalog(str(tsv), "news_id", "publisher")
    assert rows == [("N1", "Fox News"), ("N2", "CNN")]
    csv = tmp_path / "cat.csv"
    csv.write_text("article_id,source\nN1,Reuters\nN2,Vox\n")
    rows = bsm.read_catalog(str(csv), "article_id", "source")
    assert rows == [("N1", "Reuters"), ("N2", "Vox")]


def test_read_catalog_missing_column_errors(tmp_path):
    p = tmp_path / "cat.tsv"
    p.write_text("news_id\ttitle\nN1\tA\n")
    try:
        bsm.read_catalog(str(p), "news_id", "publisher")
        assert False, "expected SystemExit on missing column"
    except SystemExit:
        pass


def test_write_source_map_skips_missing(tmp_path):
    out = tmp_path / "sm.tsv"
    n = bsm.write_source_map([("N1", "Fox News"), ("N2", ""), ("N3", "None"), ("", "CNN")], str(out))
    assert n == 1
    # the written file round-trips through the ingest's own loader
    sm = _load_source_map(str(out))
    assert sm == {"N1": "Fox News"}


def test_cli_end_to_end_feeds_ingest_loader(tmp_path):
    cat = tmp_path / "articles.tsv"
    cat.write_text("news_id\tpublisher\nN1\tBreitbart\nN2\tMother Jones\nN3\t\n")
    out = tmp_path / "source_map.tsv"
    r = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "build_source_map.py"),
         "--catalog", str(cat), "--id-col", "news_id", "--source-col", "publisher",
         "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sm = _load_source_map(str(out))
    assert sm == {"N1": "Breitbart", "N2": "Mother Jones"}      # N3 skipped (no publisher)


def test_bundled_outlet_lean_table_is_well_formed():
    t = load_lean_table(str(ROOT / "examples" / "data" / "outlet_lean.csv"))
    assert all(-2 <= v <= 2 for v in t.values()) and len(t) >= 40
    assert t[_norm("Fox News")] > 0 and t[_norm("Mother Jones")] < 0   # oriented L<0<R
    assert t[_norm("Reuters")] == 0                                    # wire service ~ centre
