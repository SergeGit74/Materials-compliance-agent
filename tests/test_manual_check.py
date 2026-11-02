import csv

import pytest

from src import appcore, config, inventory, models as mdl, pipeline, realruns
from src.inventory import InventoryError


def test_make_does_not_write_and_save_appends_once(tmp_path):
    manual = tmp_path / "manual_items.csv"
    row = inventory.make_manual_item("circuit breaker 3 pole 160 A", manual_path=manual, results_ids={"NEW-0001"})
    assert row["Item_ID"] == "NEW-0002" and row["Category"] == "Unspecified" and not manual.exists()  # nothing is reserved until saved
    inventory.save_manual_item(row, manual, {"NEW-0001"})
    assert [r["Item_ID"] for r in csv.DictReader(manual.open(encoding="utf-8"))] == ["NEW-0002"]
    with pytest.raises(InventoryError, match="no longer the next free id"):
        inventory.save_manual_item(row, manual, {"NEW-0001"})  # saved twice, or another entry took the id
    again = inventory.add_manual_item("fire damper 400 x 300", manual_path=manual, results_ids={"NEW-0001"})
    assert again["Item_ID"] == "NEW-0003"


def test_estimate_is_free_in_mock_and_measured_in_real():
    m = appcore.manual_estimate(False)
    assert m["cost"] == (0.0, 0.0, 0.0) and "placeholder" in m["basis"]
    r = appcore.manual_estimate(True, {})
    assert r["cost"] == appcore.MEASURED_COST and r["seconds"] == appcore.MEASURED_SECONDS and "2026-11" in r["basis"]
    trials = {f"INV-{i:04d}": {"cost_usd": f"{0.10 + i * 0.01:.3f}", "seconds": str(40 + i * 2)} for i in range(1, 21)}
    r2 = appcore.manual_estimate(True, trials)
    assert r2["cost"][0] < r2["cost"][1] < r2["cost"][2] and r2["seconds"][0] < r2["seconds"][1] < r2["seconds"][2]
    assert "from the trial records" in r2["basis"] and r2["cost"][1] == round(0.10 + 11 * 0.01, 3)


def test_the_trial_record_keeps_the_seconds(tmp_path):
    p = tmp_path / "real.csv"
    row = {c: "" for c in config.RESULTS_COLUMNS}
    row.update(compliance_verdict="NEEDS_DOCUMENTS", confidence="0.65", fit_status="FIT", dubai_rule_refs=[], evidence_urls=[], notes="n")
    realruns.save("INV-0001", row, "# r", cost_usd=0.1, tokens=5, docs_opened=1, models="m", seconds=61.4, path=p, report_dir=tmp_path / "rr")
    assert realruns.load(p)["INV-0001"]["seconds"] == "61"


def test_the_pipeline_returns_what_the_manual_page_shows():
    item = inventory.make_manual_item("circuit breaker 3 pole 160 A", manual_path=config.ROOT / "does_not_exist.csv")
    res = pipeline.run_item(item, mdl.MockModels(), pipeline.Budget(15, 300000))
    assert res["candidates"] == [] and res["normalized"]["canonical_name"] and res["opened_documents"] == [] and "fit_status" in res


def test_stage_messages_name_what_is_being_done(tmp_path, monkeypatch):
    from tests.test_clients import FakeAnthropic, FakeSearch, ITEM, idx, resp
    hits = [{"title": "Datasheet", "url": "https://www.se.com/ds.pdf", "snippet": ""}]
    fake = FakeAnthropic(
        resp({"canonical_name": "MCCB", "product_type": "moulded case circuit breaker", "category": "Electrical", "specs_text": "3P, 250 A",
              "confidence": 0.8, "too_vague": False, "vague_reason": ""}),
        resp({"candidates": [{"producer": "Other Maker", "source_url": "https://www.se.com/ds.pdf", "specs_text": "3P, 250 A", "alias_note": "", "source_type": "maker"}]}),
        resp({"rule_refs": ["dewa_test_v1.md#4.5-Switchgear"], "verdict": "NEEDS_DOCUMENTS", "notes": "n", "supplier_urls": []}),
        resp({"rejected_claims": [], "notes": "ok", "corrected_notes": ""}))
    from src import clients
    monkeypatch.setattr(clients, "fetch_document", lambda url, http=None, limit=0, terms=None: {"url": url, "kind": "pdf", "text": "Datasheet text. " * 30, "raw": b"%PDF-1.4 x", "links": []})
    seen = []
    m = clients.RealModels(anthropic_client=fake, search=FakeSearch(hits), index=idx(tmp_path), say=seen.append)
    pipeline.run_item(ITEM, m, pipeline.Budget(15, 300000))
    joined = " | ".join(seen)
    for part in ("Identifying the item", "Searching the web", "Found 1 search result", "Read pdf", "Extracting makers, analogues",
                 "Found 1 candidate(s) from 1 maker", "Looking for the Dubai rules", "Checking the candidates against the rules", "Independent verification"):
        assert part in joined, part
    assert seen.index(next(s for s in seen if s.startswith("Searching"))) < seen.index(next(s for s in seen if s.startswith("Independent")))  # in order
