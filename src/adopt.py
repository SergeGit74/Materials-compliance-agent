"""Adopt a real-mode trial result into the main results, one item at a time, by a named person (README 7).

What changes in results.csv: verdict, confidence, fit status, rule references, evidence links and notes. What stays: name, category,
producers, alias and fit notes. A human decision made on the old result is reset to pending, because it was made on different evidence.
The previous row and report are backed up first, and restore_adoption puts them back. Every step is logged in results/decisions.csv.
Adopting never approves anything: only a person sets approved or rejected.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config, realruns, store
from .reviewview import DECISIONS_CSV, _log, split_ref

ADOPT_BACKUP_DIR = config.RESULTS_DIR / "adopted_backup"


def problems(trial: dict, rule_files: list[str]) -> list[str]:
    """Why a trial result cannot be adopted. ALLOWED still needs a cited rule from rules/ AND a supplier link (README 1.3)."""
    out = []
    if trial["verdict"] not in config.VERDICTS:
        out.append(f"unknown verdict {trial['verdict']}")
    refs = [r for r in trial["rule_refs"].split(";") if r.strip()]
    urls = [u for u in trial["evidence_urls"].split(";") if u.strip()]
    bad = [r for r in refs if split_ref(r)[0] not in rule_files]
    if bad:
        out.append("rule reference(s) to files not in rules/: " + ", ".join(bad[:3]))
    if trial["verdict"] == "ALLOWED" and not (refs and urls):
        out.append("ALLOWED needs a cited rule and a supplier link")
    return out


def adopt_real_result(item_id: str, reviewer: str, comment: str = "", *, real: dict | None = None, rule_files: list[str] | None = None,
                      results_csv: Path = config.RESULTS_CSV, items_dir: Path = config.ITEMS_DIR, log_path: Path = DECISIONS_CSV,
                      backup_dir: Path = ADOPT_BACKUP_DIR, report_dir: Path = realruns.REAL_DIR) -> dict:
    if not reviewer.strip():
        raise ValueError("Enter your name before adopting a real-mode result.")
    trial = (real if real is not None else realruns.load()).get(item_id)
    if trial is None:
        raise ValueError(f"{item_id} has no real-mode trial.")
    row = next((r for r in store.read_rows(results_csv) if r["item_id"] == item_id), None)
    if row is None:
        raise ValueError(f"{item_id} has no row.")
    if rule_files is None:
        from . import pipeline
        rule_files = pipeline.rule_files()
    found = problems(trial, rule_files)
    if found:
        raise ValueError("Cannot adopt: " + "; ".join(found))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    day = stamp[:8]
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    report_file = Path(items_dir) / f"{item_id}.md"
    old_report = report_file.read_text(encoding="utf-8") if report_file.exists() else ""
    backup = backup_dir / f"{item_id}_{stamp}.json"
    backup.write_text(json.dumps({"row": row, "report": old_report, "adopted_by": reviewer.strip()}, indent=1), encoding="utf-8")

    reset = row["human_decision"] in ("approved", "rejected")
    new = dict(row)
    new.update({
        "compliance_verdict": trial["verdict"], "confidence": trial["confidence"], "fit_status": trial["fit_status"] or row["fit_status"],
        "dubai_rule_refs": trial["rule_refs"], "evidence_urls": trial["evidence_urls"],
        "notes": (trial["notes"] + f" | Adopted from the real-mode trial of {trial['run_at'][:10]} by {reviewer.strip()} on {day}; "
                  f"previous main result {row['compliance_verdict']} {row['confidence']}."),
    })
    if reset:
        new["human_decision"] = "pending"
    store.replace_row(new, results_csv)

    rp = realruns.report_path(item_id, report_dir)
    trial_report = rp.read_text(encoding="utf-8") if rp.exists() else "(real-mode report file not found)"
    reset_text = f"The earlier human decision ({row['human_decision']}) was reset to pending because it was made on the previous evidence. " if reset else ""
    section = (
        f"\n\n## Adopted real-mode result ({day}, {reviewer.strip()})\n\n"
        f"The main result was replaced by the real-mode trial of {trial['run_at'][:10]}: **{row['compliance_verdict']}** ({row['confidence']}) "
        f"became **{trial['verdict']}** ({trial['confidence']}). {reset_text}"
        f"The sections above are the previous report and are kept for the record. Comment: {comment.strip() or 'none'}.\n\n"
        f"### Real-mode trial report\n\n{trial_report}\n")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(old_report.rstrip("\n") + section, encoding="utf-8")
    _log(item_id, "adopted-real-mode", reviewer,
         f"{row['compliance_verdict']} {row['confidence']} -> {trial['verdict']} {trial['confidence']}. {comment}".strip(),
         trial["verdict"], trial["confidence"], log_path)
    return {"item_id": item_id, "from": (row["compliance_verdict"], row["confidence"]), "to": (trial["verdict"], trial["confidence"]),
            "decision_reset": reset, "backup": str(backup)}


def latest_backup(item_id: str, backup_dir: Path = ADOPT_BACKUP_DIR) -> Path | None:
    d = Path(backup_dir)
    files = sorted(f for f in d.glob(f"{item_id}_*.json") if ".restored." not in f.name) if d.exists() else []
    return files[-1] if files else None


def restore_adoption(item_id: str, reviewer: str, *, results_csv: Path = config.RESULTS_CSV, items_dir: Path = config.ITEMS_DIR,
                     log_path: Path = DECISIONS_CSV, backup_dir: Path = ADOPT_BACKUP_DIR) -> dict:
    """Put back the row and report saved before the latest adoption. The backup is kept, renamed so it is not used twice."""
    if not reviewer.strip():
        raise ValueError("Enter your name before undoing an adoption.")
    b = latest_backup(item_id, backup_dir)
    if b is None:
        raise ValueError(f"{item_id} has no adoption to undo.")
    saved = json.loads(b.read_text(encoding="utf-8"))
    current = next((r for r in store.read_rows(results_csv) if r["item_id"] == item_id), None)
    store.replace_row(saved["row"], results_csv)
    if saved["report"]:
        (Path(items_dir) / f"{item_id}.md").write_text(saved["report"], encoding="utf-8")
    b.rename(b.with_name(b.stem + ".restored.json"))
    _log(item_id, "restored-main-result", reviewer,
         f"undid the adoption: {current['compliance_verdict'] if current else '?'} -> {saved['row']['compliance_verdict']}",
         saved["row"]["compliance_verdict"], saved["row"]["confidence"], log_path)
    return {"item_id": item_id, "restored": (saved["row"]["compliance_verdict"], saved["row"]["confidence"])}
