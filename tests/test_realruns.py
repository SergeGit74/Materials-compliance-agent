import csv
from types import SimpleNamespace as NS

from src import config, realruns, reviewview, store


def row(**kw):
    base = {c: "" for c in config.RESULTS_COLUMNS}
    base.update({"item_id": "INV-0001", "category": "Electrical", "normalized_name": "MCCB", "compliance_verdict": "NEEDS_DOCUMENTS",
                 "confidence": "0.5", "human_decision": "pending", "fit_status": "FIT", "notes": "n", "dubai_rule_refs": ["a.pdf#1"],
                 "evidence_urls": ["https://x.example/a"]})
    base.update(kw)
    return base


def test_save_replaces_one_item_keeps_the_others_and_writes_the_report(tmp_path):
    p, d = tmp_path / "real.csv", tmp_path / "rr"
    realruns.save("INV-0001", row(compliance_verdict="ALLOWED"), "# report 1", cost_usd=0.1234, tokens=5000, docs_opened=3, models="m1", path=p, report_dir=d)
    realruns.save("INV-0002", row(item_id="INV-0002"), "# report 2", cost_usd=0.1, tokens=1, docs_opened=0, models="m1", path=p, report_dir=d)
    realruns.save("INV-0001", row(compliance_verdict="UNKNOWN"), "# report 1b", cost_usd=0.2, tokens=2, docs_opened=1, models="m2", path=p, report_dir=d)
    got = realruns.load(p)
    assert sorted(got) == ["INV-0001", "INV-0002"] and got["INV-0001"]["verdict"] == "UNKNOWN" and got["INV-0001"]["cost_usd"] == "0.2000"
    assert got["INV-0001"]["rule_refs"] == "a.pdf#1" and (d / "INV-0001.md").read_text(encoding="utf-8") == "# report 1b"
    assert realruns.load(tmp_path / "none.csv") == {}


def test_compare_and_review_cells_and_filter():
    main = row(compliance_verdict="NEEDS_DOCUMENTS")
    same = {"verdict": "NEEDS_DOCUMENTS", "confidence": "0.65"}
    diff = {"verdict": "ALLOWED", "confidence": "0.75"}
    assert realruns.compare(main, same) == "same" and realruns.compare(main, diff) == "differs" and realruns.compare(main, None) == ""
    assert reviewview.real_cells(main, diff) == ("ALLOWED 0.75", "differs") and reviewview.real_cells(main, None) == ("", "")
    rows = [row(item_id="INV-0001"), row(item_id="INV-0002"), row(item_id="INV-0003")]
    real = {"INV-0001": same, "INV-0002": diff}
    ids = lambda f: [r["item_id"] for r in reviewview.filter_rows(rows, {}, real=real, real_filter=f)]
    assert ids("all") == ["INV-0001", "INV-0002", "INV-0003"] and ids("has_trial") == ["INV-0001", "INV-0002"]
    assert ids("differs") == ["INV-0002"] and ids("no_trial") == ["INV-0003"]
    assert reviewview.trial_cost_summary({"a": {"cost_usd": "0.1"}, "b": {"cost_usd": "0.25"}}) == {"trials": 2, "cost_usd": 0.35}


def test_a_trial_never_touches_results_csv(tmp_path, monkeypatch):
    from src import realtrial
    results = tmp_path / "results.csv"
    store.append_row(row(), results)
    before = results.read_bytes()
    saved = []
    real_save = realruns.save
    monkeypatch.setattr(realruns, "save", lambda iid, r, rep, **k: saved.append((iid, k)) or real_save(iid, r, rep, path=tmp_path / "real.csv", report_dir=tmp_path / "rr", **k))
    res = {"row": row(compliance_verdict="ALLOWED"), "report": "# r", "usage": NS(cost_usd=0.1, input_tokens=10, output_tokens=5)}
    monkeypatch.setattr(realtrial.pipeline, "run_item", lambda *a, **k: res)
    out = realtrial.run_trial({"Item_ID": "INV-0001"}, NS(opened_documents=[1, 2], m_norm="a", m_reason="b", m_verify="c"))
    assert out["row"]["compliance_verdict"] == "ALLOWED" and results.read_bytes() == before  # results.csv is untouched
    got = realruns.load(tmp_path / "real.csv")["INV-0001"]
    assert got["models"] == "a / b / c" and got["docs_opened"] == "2" and got["tokens"] == "15" and saved[0][0] == "INV-0001"
