import csv

import pytest

from src import adopt, config, realruns, store

FILES = ["dewa_test_v1.md"]


def row(item_id="INV-0001", **kw):
    base = {c: "" for c in config.RESULTS_COLUMNS}
    base.update({"item_id": item_id, "category": "Electrical", "normalized_name": "MCCB 250 A", "producer_candidates": "Schneider", "alias_notes": "alias",
                 "fit_status": "PARTIAL", "fit_notes": "fit notes kept", "compliance_verdict": "NEEDS_DOCUMENTS", "confidence": "0.55",
                 "human_decision": "pending", "notes": "old notes", "dubai_rule_refs": "dewa_test_v1.md#1", "evidence_urls": "https://old.example/a"})
    base.update(kw)
    return base


def trial(**kw):
    t = {"item_id": "INV-0001", "run_at": "2026-11-02T09:00:00+00:00", "verdict": "ALLOWED", "confidence": "0.75", "fit_status": "FIT",
         "rule_refs": "dewa_test_v1.md#4.5.1-Boards;dewa_test_v1.md#1.6-Standards", "evidence_urls": "https://www.se.com/ds.pdf", "docs_opened": "3",
         "cost_usd": "0.12", "tokens": "100", "models": "m", "source": "real-mode trial", "notes": "trial notes"}
    t.update(kw)
    return t


@pytest.fixture
def env(tmp_path):
    results = tmp_path / "results.csv"
    store.append_row(row(), results)
    store.append_row(row("INV-0002", normalized_name="other"), results)
    items = tmp_path / "items"
    items.mkdir()
    (items / "INV-0001.md").write_text("# old report\n\n## Final verdict\n\n**NEEDS_DOCUMENTS**\n", encoding="utf-8")
    rr = tmp_path / "rr"
    rr.mkdir()
    (rr / "INV-0001.md").write_text("# real-mode report text", encoding="utf-8")
    kw = dict(results_csv=results, items_dir=items, log_path=tmp_path / "decisions.csv", backup_dir=tmp_path / "bk", report_dir=rr,
              rule_files=FILES)
    return kw, results, items


def test_adopt_replaces_the_evidence_fields_and_keeps_the_rest(env):
    kw, results, items = env
    out = adopt.adopt_real_result("INV-0001", "A. Reviewer", "checked the datasheet", real={"INV-0001": trial()}, **kw)
    r = {x["item_id"]: x for x in store.read_rows(results)}
    new, other = r["INV-0001"], r["INV-0002"]
    assert (new["compliance_verdict"], new["confidence"], new["fit_status"]) == ("ALLOWED", "0.75", "FIT")
    assert new["dubai_rule_refs"].startswith("dewa_test_v1.md#4.5.1") and new["evidence_urls"] == "https://www.se.com/ds.pdf"
    assert new["normalized_name"] == "MCCB 250 A" and new["fit_notes"] == "fit notes kept" and new["alias_notes"] == "alias"  # kept from the main result
    assert "trial notes" in new["notes"] and "Adopted from the real-mode trial of 2026-11-02 by A. Reviewer" in new["notes"] and "NEEDS_DOCUMENTS 0.55" in new["notes"]
    assert other["normalized_name"] == "other" and other["compliance_verdict"] == "NEEDS_DOCUMENTS"  # other items untouched
    report = (items / "INV-0001.md").read_text(encoding="utf-8")
    assert report.startswith("# old report") and "## Adopted real-mode result" in report and "# real-mode report text" in report and "checked the datasheet" in report
    log = list(csv.DictReader((kw["log_path"]).open(encoding="utf-8")))
    assert log[0]["decision"] == "adopted-real-mode" and log[0]["reviewer"] == "A. Reviewer" and "NEEDS_DOCUMENTS 0.55 -> ALLOWED 0.75" in log[0]["comment"]
    assert out["decision_reset"] is False and adopt.latest_backup("INV-0001", kw["backup_dir"])


def test_a_decision_made_on_the_old_result_is_reset_but_pending_stays(env):
    kw, results, _ = env
    store.set_decision("INV-0001", "approved", results)
    out = adopt.adopt_real_result("INV-0001", "A. Reviewer", real={"INV-0001": trial(verdict="NEEDS_DOCUMENTS", confidence="0.65")}, **kw)
    assert out["decision_reset"] is True and store.read_rows(results)[0]["human_decision"] == "pending"


def test_nothing_is_adopted_without_a_name_a_trial_or_valid_evidence(env):
    kw, results, _ = env
    before = results.read_bytes()
    with pytest.raises(ValueError, match="name"):
        adopt.adopt_real_result("INV-0001", "  ", real={"INV-0001": trial()}, **kw)
    with pytest.raises(ValueError, match="no real-mode trial"):
        adopt.adopt_real_result("INV-0001", "x", real={}, **kw)
    with pytest.raises(ValueError, match="no row"):
        adopt.adopt_real_result("INV-0009", "x", real={"INV-0009": trial(item_id="INV-0009")}, **kw)
    with pytest.raises(ValueError, match="ALLOWED needs a cited rule and a supplier link"):
        adopt.adopt_real_result("INV-0001", "x", real={"INV-0001": trial(evidence_urls="")}, **kw)
    with pytest.raises(ValueError, match="files not in rules"):
        adopt.adopt_real_result("INV-0001", "x", real={"INV-0001": trial(rule_refs="other_file.pdf#1")}, **kw)
    with pytest.raises(ValueError, match="unknown verdict"):
        adopt.adopt_real_result("INV-0001", "x", real={"INV-0001": trial(verdict="MAYBE")}, **kw)
    assert results.read_bytes() == before and not kw["log_path"].exists()  # every refusal left the results exactly as they were


def test_restore_puts_back_the_previous_row_and_report_once(env):
    kw, results, items = env
    original_report = (items / "INV-0001.md").read_text(encoding="utf-8")
    adopt.adopt_real_result("INV-0001", "A. Reviewer", real={"INV-0001": trial()}, **kw)
    res = adopt.restore_adoption("INV-0001", "B. Reviewer", results_csv=results, items_dir=items, log_path=kw["log_path"], backup_dir=kw["backup_dir"])
    back = store.read_rows(results)[0]
    assert res["restored"] == ("NEEDS_DOCUMENTS", "0.55") and back["notes"] == "old notes" and back["dubai_rule_refs"] == "dewa_test_v1.md#1"
    assert (items / "INV-0001.md").read_text(encoding="utf-8") == original_report
    assert adopt.latest_backup("INV-0001", kw["backup_dir"]) is None
    with pytest.raises(ValueError, match="no adoption to undo"):
        adopt.restore_adoption("INV-0001", "B. Reviewer", results_csv=results, items_dir=items, log_path=kw["log_path"], backup_dir=kw["backup_dir"])
    decisions = [r["decision"] for r in csv.DictReader(kw["log_path"].open(encoding="utf-8"))]
    assert decisions == ["adopted-real-mode", "restored-main-result"]
