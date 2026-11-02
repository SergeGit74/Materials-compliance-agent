import re
from pathlib import Path

import openpyxl

from src import evaluate, store


def row(i, verdict="UNKNOWN", **kw):
    r = {"item_id": i, "processed_at": "t", "category": "Electrical", "normalized_name": "thing",
         "compliance_verdict": verdict, "human_decision": "pending", "dubai_rule_refs": "",
         "evidence_urls": "", "matched_producer": "", "alias_notes": ""}
    r.update(kw)
    return r


def test_metrics_false_allowed_and_unknown_rate():
    rows = [row("A", "ALLOWED", dubai_rule_refs="r.pdf#1", evidence_urls="http://x"),
            row("B", "ALLOWED", dubai_rule_refs="r.pdf#1"),  # no supplier doc
            row("C", "UNKNOWN"), row("D", "UNKNOWN"),
            row("E", "UNKNOWN", human_decision="skipped")]
    m = evaluate.compute_metrics(rows, {})
    assert m["processed"] == 4 and m["skipped"] == 1
    assert m["false_allowed_structural"] == ["B"]
    assert m["unknown_rate"] == 0.5


def test_safety_critical_not_pending_flagged():
    rows = [row("A", category="Fire & Life Safety", human_decision="approved"),
            row("B", category="Fire & Life Safety")]
    assert evaluate.compute_metrics(rows, {})["safety_critical_not_pending"] == ["A"]


def test_cost_from_report_run_log(tmp_path):
    (tmp_path / "A.md").write_text("## Run log\n- Tokens in/out: 1000/200\n- Estimated cost: $0.40\n", encoding="utf-8")
    (tmp_path / "B.md").write_text("- Tokens in/out: 0/0\n- Estimated cost: $0.80\n", encoding="utf-8")
    costs = evaluate.run_costs(["A", "B", "C"], tmp_path)
    m = evaluate.compute_metrics([row("A"), row("B"), row("C")], costs)
    assert m["items_with_cost_log"] == 2 and abs(m["cost_per_item"] - 0.6) < 1e-9


def make_workbook(path):
    wb = openpyxl.Workbook()
    wb.active.title = "Inventory"
    ws = wb.create_sheet("Eval_Notes")
    ws.append(["FOR EVALUATORS ONLY."])
    ws.append([])
    ws.append(["Item_ID", "Challenge_Tags", "What_a_good_run_does"])
    ws.append(["A", "alias; rebrand", "finds the old name"])
    ws.append(["B", "vague", "stops early"])
    ws.append(["Z", "alias", "not processed"])
    wb.save(path)


def test_eval_notes_only_for_processed_ids(tmp_path):
    wb = tmp_path / "w.xlsx"
    make_workbook(wb)
    notes = evaluate.load_eval_notes(wb, {"A", "B"})
    assert set(notes) == {"A", "B"} and notes["A"]["guidance"] == "finds the old name"


def test_alias_coverage(tmp_path):
    wb = tmp_path / "w.xlsx"
    make_workbook(wb)
    rows = [row("A", matched_producer="Acme", alias_notes="formerly X"), row("B")]
    j = evaluate.join_notes(rows, evaluate.load_eval_notes(wb, {"A", "B"}))
    assert j["alias_tagged_items"] == ["A"] and j["alias_coverage"] == 1.0


def test_refuses_before_processing(tmp_path, capsys):
    rc = evaluate.main(["--results", str(tmp_path / "none.csv"), "--workbook", str(tmp_path / "nope.xlsx")])
    assert rc == 1  # exits before the workbook is opened


def test_end_to_end(tmp_path, capsys):
    wb, res = tmp_path / "w.xlsx", tmp_path / "results.csv"
    make_workbook(wb)
    store.append_row(row("A", matched_producer="Acme"), res)
    rc = evaluate.main(["--results", str(res), "--items-dir", str(tmp_path), "--workbook", str(wb)])
    out = capsys.readouterr().out
    assert rc == 0 and "UNKNOWN rate: 100%" in out and "finds the old name" in out


def test_only_evaluate_module_touches_eval_notes():
    src = Path(__file__).resolve().parent.parent / "src"
    for f in src.glob("*.py"):
        if f.name == "evaluate.py":
            continue
        code = re.sub(r'""".*?"""', "", f.read_text(encoding="utf-8"), flags=re.S)
        code = re.sub(r"#.*", "", code)
        assert "Eval_Notes" not in code.replace("Eval_Notes sheet", ""), f.name
    for f in src.glob("*.py"):
        if f.name != "evaluate.py":
            text = f.read_text(encoding="utf-8")
            assert not re.search(r"import evaluate|from \.evaluate|from src\.evaluate|from \. import .*evaluate", text), f.name


def test_pipeline_does_not_import_evaluate():
    import src.pipeline, src.run  # noqa: F401
    for mod in (src.pipeline, src.run):
        assert not hasattr(mod, "evaluate")


def test_out_saves_same_text_as_stdout(tmp_path, capsys):
    wb, res, out = tmp_path / "w.xlsx", tmp_path / "results.csv", tmp_path / "sub" / "eval.md"
    make_workbook(wb)
    store.append_row(row("A", matched_producer="Acme"), res)
    rc = evaluate.main(["--results", str(res), "--items-dir", str(tmp_path), "--workbook", str(wb), "--out", str(out)])
    cap = capsys.readouterr()
    assert rc == 0 and out.read_text(encoding="utf-8") == cap.out and "Saved to" in cap.err


def test_out_refuses_data_folder(tmp_path, capsys):
    from src import config
    res = tmp_path / "results.csv"
    store.append_row(row("A"), res)
    target = config.DATA_DIR / "evaluation.md"
    rc = evaluate.main(["--results", str(res), "--no-eval-notes", "--out", str(target)])
    assert rc == 3 and not target.exists()
