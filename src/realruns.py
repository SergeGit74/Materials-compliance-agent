"""Real-mode trial runs kept next to, never inside, the main results (README 5 and 7).

results.csv holds the processed results that a person reviews. A trial run of the real model clients is written here instead:
results/real_runs.csv (the latest trial per item) and results/real_runs/INV-XXXX.md (its report). The Review screen shows them
as a second opinion next to the chat-run or mock result. Running a trial never changes results.csv or human_decision.

CLI:  python -m src.realtrial INV-0001 INV-0034      (spends API credits; asks you to type "spend")
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from . import config

REAL_CSV = config.RESULTS_DIR / "real_runs.csv"
REAL_DIR = config.RESULTS_DIR / "real_runs"
COLUMNS = ["item_id", "run_at", "verdict", "confidence", "fit_status", "rule_refs", "evidence_urls", "docs_opened", "cost_usd", "tokens",
           "seconds", "models", "source", "notes"]


def load(path: Path = REAL_CSV) -> dict[str, dict]:
    """Latest trial per item, keyed by item id."""
    if not Path(path).exists():
        return {}
    with Path(path).open(newline="", encoding="utf-8") as f:
        return {r["item_id"]: r for r in csv.DictReader(f)}


def save(item_id: str, row: dict, report: str, *, cost_usd: float, tokens: int, docs_opened: int, models: str, seconds: float = 0.0,
         source: str = "real-mode trial", path: Path = REAL_CSV, report_dir: Path = REAL_DIR) -> None:
    """Replace this item's trial row and write its report. Other items' rows are kept."""
    path = Path(path)
    rows = load(path)
    join = lambda v: ";".join(v) if isinstance(v, (list, tuple)) else (v or "")
    rows[item_id] = {
        "item_id": item_id, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": row["compliance_verdict"], "confidence": row["confidence"], "fit_status": row["fit_status"],
        "rule_refs": join(row["dubai_rule_refs"]), "evidence_urls": join(row["evidence_urls"]), "docs_opened": str(docs_opened),
        "cost_usd": f"{cost_usd:.4f}", "tokens": str(tokens), "seconds": f"{seconds:.0f}" if seconds else "", "models": models, "source": source, "notes": row["notes"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for iid in sorted(rows):
            w.writerow({c: rows[iid].get(c, "") for c in COLUMNS})
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    (Path(report_dir) / f"{item_id}.md").write_text(report, encoding="utf-8")


def compare(main_row: dict | None, trial: dict | None) -> str:
    """'' (no trial), 'same' (same verdict) or 'differs'."""
    if not trial or not main_row:
        return ""
    return "same" if trial["verdict"] == main_row["compliance_verdict"] else "differs"


def report_path(item_id: str, report_dir: Path = REAL_DIR) -> Path:
    return Path(report_dir) / f"{item_id}.md"
