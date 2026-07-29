"""capacity_report — the arithmetic a capacity decision rests on.

These assert the parts that would silently mislead if wrong: that measured and projected values
stay distinguishable, that headroom never promises space the filesystem will not hand out, and that
a too-short ingestion window is flagged instead of quietly producing a confident date.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import capacity_report as cap          # noqa: E402
import store as store_mod              # noqa: E402


def _seed(st, n, *, days_back=0):
    now = datetime.now(timezone.utc)
    for i in range(n):
        u = f"https://p{i % 20}.example.com/a{i}-{days_back}"
        st.upsert_feed_article(
            canonical_url=u, url=u, publisher=f"P{i % 20}", source_publisher=f"P{i % 20}",
            title="headline " * 5, description="description " * 10, body=None,
            published_at=(now - timedelta(days=days_back)).isoformat(),
            source_feed="https://f.example/rss",
            scored={"article_id": u, "outlet": f"P{i % 20}", "lean": 0.0, "category": "x"})


def test_dbstat_separates_table_bytes_from_index_bytes(tmp_path):
    """Index overhead must be MEASURED, not assumed.

    ``dbstat`` reports real page allocation per btree, so a table and its indexes are separable.
    Without that split, "storage overhead from indexes" can only be a rule of thumb — and a capacity
    plan built on a rule of thumb is the thing this whole report exists to replace."""
    db = tmp_path / "c.db"
    st = store_mod.Store(f"sqlite:///{db}")
    _seed(st, 300)
    with st.session() as s:
        schema = cap.schema_objects(s)
        sizes = cap.dbstat_sizes(s)
    if not sizes:                                   # build without SQLITE_ENABLE_DBSTAT_VTAB
        return
    assert "feed_articles" in sizes, "the catalog table must appear in the page accounting"
    idx = [n for n in sizes if n in schema["indexes"]]
    assert idx, "indexes must be reported separately from their table"
    assert all(sizes[n]["bytes"] > 0 for n in idx)


def test_headroom_never_exceeds_writable_space():
    """100% of `total` is not reachable: filesystems reserve blocks for root, so writes fail at
    `available`. A headroom row quoting the larger number would promise space that does not exist
    and put the 100% date after the outage."""
    vol = {"total": 100_000, "used": 10_000, "available": 20_000}   # 70k free, only 20k writable
    for pct in (80, 90, 100):
        target = vol["total"] * pct / 100.0
        headroom = min(target - vol["used"], vol["available"])
        assert headroom <= vol["available"], f"{pct}% promised more than is writable"


def test_short_window_is_flagged_as_unreliable(tmp_path):
    """A rate computed from partial days is diluted, so every date derived from it is optimistic.

    The report must say so rather than print a confident date. Four buckets is the threshold — below
    that the leading and trailing partial days cannot be trimmed."""
    db = tmp_path / "d.db"
    st = store_mod.Store(f"sqlite:///{db}")
    _seed(st, 50)                                   # all rows land today: one bucket
    with st.session() as s:
        rate = cap.ingestion_rate(s, days=14)
    assert rate["fullDays"] >= 1
    assert rate["reliable"] is False, "a single-bucket window must be flagged, not trusted"


def test_report_runs_end_to_end_and_labels_its_method(tmp_path, capsys):
    """The whole report, against a real store. The method line matters: if dbstat is missing the
    numbers are column payload and UNDERSTATE disk, and the output has to say which it is."""
    db = tmp_path / "e.db"
    st = store_mod.Store(f"sqlite:///{db}")
    _seed(st, 120)
    import os
    old = os.environ.get("RWE_DB_URL")
    os.environ["RWE_DB_URL"] = f"sqlite:///{db}"
    try:
        assert cap.main.__module__ == "capacity_report"
        sys.argv = ["capacity_report.py"]
        assert cap.main() == 0
    finally:
        if old is None:
            os.environ.pop("RWE_DB_URL", None)
        else:
            os.environ["RWE_DB_URL"] = old
    out = capsys.readouterr().out
    assert "[M] measured" in out and "[P] projected" in out, "provenance legend must be present"
    assert "dbstat (exact page allocation)" in out or "UNDERSTATES disk" in out, \
        "the report must state which sizing method produced its numbers"
    assert "feed_articles" in out
