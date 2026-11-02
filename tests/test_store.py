import csv

import pytest

from src import config, store


def row(item_id, **kw):
    r = {"item_id": item_id, "processed_at": "t", "compliance_verdict": "UNKNOWN", "human_decision": "pending"}
    r.update(kw)
    return r


def test_header_written_and_lists_joined(tmp_path):
    p = tmp_path / "results.csv"
    store.append_row(row("INV-0001", producer_candidates=["A", "B"], notes="has, comma"), p)
    with p.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == config.RESULTS_COLUMNS
    assert rows[0]["producer_candidates"] == "A;B"
    assert rows[0]["notes"] == "has, comma"


def test_append_refuses_duplicate(tmp_path):
    p = tmp_path / "results.csv"
    store.append_row(row("INV-0001"), p)
    with pytest.raises(ValueError):
        store.append_row(row("INV-0001"), p)


def test_replace_only_that_row(tmp_path):
    p = tmp_path / "results.csv"
    for i in ("INV-0001", "INV-0002", "INV-0003"):
        store.append_row(row(i, notes="old"), p)
    store.replace_row(row("INV-0002", notes="new"), p)
    rows = store.read_rows(p)
    assert [r["item_id"] for r in rows] == ["INV-0001", "INV-0002", "INV-0003"]
    assert [r["notes"] for r in rows] == ["old", "new", "old"]


def test_replace_missing_row_fails(tmp_path):
    with pytest.raises(ValueError):
        store.replace_row(row("INV-0009"), tmp_path / "results.csv")


def test_utf8_roundtrip(tmp_path):
    p = tmp_path / "results.csv"
    store.append_row(row("INV-0001", notes="Ø 28 mm – café"), p)
    assert store.read_rows(p)[0]["notes"] == "Ø 28 mm – café"


def test_done_ids_empty_when_no_file(tmp_path):
    assert store.done_ids(tmp_path / "none.csv") == set()
