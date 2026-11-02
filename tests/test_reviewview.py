import csv

import pytest

from src import config, reviewview, store


def row(**kw):
    base = {c: "" for c in config.RESULTS_COLUMNS}
    base.update({"item_id": "INV-0001", "category": "Electrical", "normalized_name": "MCCB", "compliance_verdict": "NEEDS_DOCUMENTS",
                 "confidence": "0.5", "human_decision": "pending", "dubai_rule_refs": "a.pdf#4.5.1;b.pdf#Appendix-1"})
    base.update(kw)
    return base


INDEX = """| File | Issuer | Title | Version date | Source URL | Date added | Confirmed by |
|---|---|---|---|---|---|---|

| a.pdf | dewa | DEWA Regulations | 2022-09 | http://x | 2026-10-18 | Reviewer |
| b.pdf | uae-civil-defence (federal) | UAE FLSC | 2018-09 | http://y | 2026-11-01 | adopted by DBC A.4.3 |
"""


def test_index_scope_and_rules_cited(tmp_path):
    p = tmp_path / "INDEX.md"
    p.write_text(INDEX, encoding="utf-8")
    (tmp_path / "a.pdf").write_bytes(b"x")
    idx = reviewview.read_index(p)
    assert [e["file"] for e in idx] == ["a.pdf", "b.pdf"]
    cited = reviewview.rules_cited(row(), idx, tmp_path)
    assert cited[0]["clause"] == "4.5.1" and cited[0]["scope"] == "Dubai" and cited[0]["in_rules_folder"]
    assert cited[1]["scope"].startswith("Federal") and not cited[1]["in_rules_folder"]
    assert reviewview.rules_cited(row(dubai_rule_refs=""), idx, tmp_path) == []
    assert reviewview.citations_per_rulebook([row(), row(item_id="INV-0002", dubai_rule_refs="a.pdf#1")]) == {"a.pdf": 2, "b.pdf": 1}


def test_missing_documents_takes_the_latest_statement(tmp_path):
    (tmp_path / "INV-0001.md").write_text("# x\n\n- **Missing documents:** old list\n\n## Redo\n- **Documents that would settle it:** newest list\n", encoding="utf-8")
    assert reviewview.missing_documents("INV-0001", tmp_path) == ["newest list"]
    (tmp_path / "INV-0002.md").write_text("## Open questions for the human\n\n- Which model?\n- Which site?\n\n## Run log\n", encoding="utf-8")
    assert reviewview.missing_documents("INV-0002", tmp_path) == ["Which model?", "Which site?"]
    assert reviewview.missing_documents("INV-0009", tmp_path) == []


def test_filter_sort_and_summary():
    rows = [row(), row(item_id="INV-0002", compliance_verdict="ALLOWED", confidence="0.6", human_decision="approved", normalized_name="Cable"),
            row(item_id="INV-0003", compliance_verdict="UNKNOWN", confidence="0.2", human_decision="skipped")]
    items = {}
    s = reviewview.summary(rows, items)
    assert s["total"] == 2 and s["verdicts"] == {"NEEDS_DOCUMENTS": 1, "ALLOWED": 1} and s["pending"] == 1 and s["approved"] == 1
    assert [r["item_id"] for r in reviewview.filter_rows(rows, items, decisions=["pending"])] == ["INV-0001"]
    assert [r["item_id"] for r in reviewview.filter_rows(rows, items, query="cable")] == ["INV-0002"]
    assert [r["item_id"] for r in reviewview.sort_rows(rows[:2], "Verdict (best first)")] == ["INV-0002", "INV-0001"]
    assert reviewview.needs_confirmation(rows[0], "approved") and reviewview.needs_confirmation(rows[1], "approved") is None


def test_record_decision_logs_and_requires_a_name(tmp_path):
    results = tmp_path / "results.csv"
    store.append_row(row(), results)
    log = tmp_path / "decisions.csv"
    with pytest.raises(ValueError):
        reviewview.record_decision("INV-0001", "approved", "  ", "", results, log)
    assert store.read_rows(results)[0]["human_decision"] == "pending" and not log.exists()
    reviewview.record_decision("INV-0001", "rejected", "A. Reviewer", "no datasheet", results, log)
    assert store.read_rows(results)[0]["human_decision"] == "rejected"
    hist = reviewview.decision_history("INV-0001", log)
    assert hist[0]["reviewer"] == "A. Reviewer" and hist[0]["verdict_at_decision"] == "NEEDS_DOCUMENTS" and hist[0]["comment"] == "no datasheet"
    reviewview.record_decision("INV-0001", "pending", "", "", results, log)  # putting back to pending needs no name
    assert len(reviewview.decision_history("INV-0001", log)) == 2
    with pytest.raises(ValueError):
        reviewview.record_decision("INV-9999", "rejected", "x", "", results, log)
