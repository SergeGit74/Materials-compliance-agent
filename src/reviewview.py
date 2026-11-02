"""Logic behind the Review and Rulebooks screens of app.py. No Streamlit here, so it can be tested.

Reads results/results.csv, results/items/*.md, results/evidence/ and rules/INDEX.md. The only thing it writes is the
decision log results/decisions.csv (who decided what and when); the decision itself goes through store.set_decision.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config, realruns, store

EVIDENCE_DIR = config.RESULTS_DIR / "evidence"
DECISIONS_CSV = config.RESULTS_DIR / "decisions.csv"
DECISION_COLUMNS = ["item_id", "decision", "reviewer", "comment", "verdict_at_decision", "confidence_at_decision", "decided_at"]
VERDICT_ORDER = {"ALLOWED": 0, "NEEDS_DOCUMENTS": 1, "UNKNOWN": 2, "NOT_ALLOWED": 3}


# --- rulebooks ---------------------------------------------------------------------------------------------------
def read_index(path: Path = config.RULES_DIR / "INDEX.md") -> list[dict]:
    """Every row of rules/INDEX.md with all columns."""
    rows = []
    if not Path(path).exists():
        return rows
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and cells[0].endswith((".pdf", ".md", ".txt")):
            rows.append(dict(zip(["file", "issuer", "title", "version", "source_url", "date_added", "confirmed_by"], cells)))
    return rows


def rulebook_scope(entry: dict) -> str:
    """'Dubai' or 'Federal (adopted for Dubai)' from the INDEX row."""
    text = (entry.get("issuer", "") + " " + entry.get("confirmed_by", "")).lower()
    return "Federal (adopted for Dubai)" if "federal" in text else "Dubai"


def split_ref(ref: str) -> tuple[str, str]:
    """'file.pdf#4.5.1' -> ('file.pdf', '4.5.1')."""
    f, _, clause = ref.partition("#")
    return f.strip(), clause.strip()


def rules_cited(row: dict, index: list[dict] | None = None, rules_dir: Path = config.RULES_DIR) -> list[dict]:
    """One dict per cited rule: file, clause, title, scope, version, whether the file is in rules/."""
    by_file = {e["file"]: e for e in (index if index is not None else read_index())}
    out = []
    for ref in [r for r in row.get("dubai_rule_refs", "").split(";") if r.strip()]:
        f, clause = split_ref(ref)
        e = by_file.get(f, {})
        out.append({"file": f, "clause": clause, "title": e.get("title", "(not in INDEX.md)"), "scope": rulebook_scope(e) if e else "?",
                    "version": e.get("version", ""), "in_rules_folder": (Path(rules_dir) / f).exists()})
    return out


def citations_per_rulebook(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        for f in {split_ref(x)[0] for x in r.get("dubai_rule_refs", "").split(";") if x.strip()}:
            counts[f] = counts.get(f, 0) + 1
    return counts


# --- evidence ----------------------------------------------------------------------------------------------------
def evidence_files(item_id: str, evidence_dir: Path = EVIDENCE_DIR) -> list[Path]:
    d = Path(evidence_dir)
    return sorted(d.glob(f"{item_id}_*")) if d.exists() else []


def evidence_urls(row: dict) -> list[str]:
    return [u for u in row.get("evidence_urls", "").split(";") if u.strip()]


_MISSING = re.compile(r"\*\*(?:Missing documents(?: \(added\))?|Documents that would settle it)[^*]*\*\*:?\s*(.+)")


def missing_documents(item_id: str, items_dir: Path = config.ITEMS_DIR) -> list[str]:
    """The most recent 'missing documents' statement in the report; falls back to the open questions."""
    p = Path(items_dir) / f"{item_id}.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    found = _MISSING.findall(text)
    if found:
        return [found[-1].strip()]
    m = re.search(r"## Open questions for the human\s+(.*?)(?=\n## |\Z)", text, re.S)
    return [ln[2:].strip() for ln in (m.group(1).splitlines() if m else []) if ln.startswith("- ")]


# --- filtering and summary ---------------------------------------------------------------------------------------
def is_safety(row: dict, items_by_id: dict[str, dict]) -> bool:
    item = items_by_id.get(row["item_id"])
    text = config.item_safety_text(item) if item else row["normalized_name"]
    return config.is_safety_critical(row["category"], text)


def summary(rows: list[dict], items_by_id: dict[str, dict]) -> dict:
    done = [r for r in rows if r["human_decision"] != "skipped"]
    verdicts: dict[str, int] = {}
    for r in done:
        verdicts[r["compliance_verdict"]] = verdicts.get(r["compliance_verdict"], 0) + 1
    safety_pending = [r["item_id"] for r in done if is_safety(r, items_by_id) and r["human_decision"] == "pending"]
    return {"total": len(done), "verdicts": verdicts,
            "pending": sum(r["human_decision"] == "pending" for r in done),
            "approved": sum(r["human_decision"] == "approved" for r in done),
            "rejected": sum(r["human_decision"] == "rejected" for r in done),
            "safety_pending": safety_pending}


def filter_rows(rows: list[dict], items_by_id: dict[str, dict], verdicts=None, decisions=None, categories=None,
                safety_only: bool = False, query: str = "", real: dict | None = None, real_filter: str = "all") -> list[dict]:
    q = query.strip().lower()
    out = []
    for r in rows:
        if r["human_decision"] == "skipped":
            continue
        if verdicts and r["compliance_verdict"] not in verdicts:
            continue
        if decisions and r["human_decision"] not in decisions:
            continue
        if categories and r["category"] not in categories:
            continue
        if safety_only and not is_safety(r, items_by_id):
            continue
        if real_filter != "all":
            status = realruns.compare(r, (real or {}).get(r["item_id"]))
            if (real_filter == "has_trial" and not status) or (real_filter == "differs" and status != "differs") or (real_filter == "no_trial" and status):
                continue
        if q and q not in (r["item_id"] + " " + r["normalized_name"] + " " + r["matched_producer"] + " " + r["notes"]).lower():
            continue
        out.append(r)
    return out


def sort_rows(rows: list[dict], by: str) -> list[dict]:
    key = {"Item ID": lambda r: r["item_id"],
           "Verdict (best first)": lambda r: (VERDICT_ORDER.get(r["compliance_verdict"], 9), r["item_id"]),
           "Confidence (low first)": lambda r: (float(r["confidence"] or 0), r["item_id"]),
           "Confidence (high first)": lambda r: (-float(r["confidence"] or 0), r["item_id"])}[by]
    return sorted(rows, key=key)


# --- decisions ---------------------------------------------------------------------------------------------------
REAL_FILTERS = {"all": "All items", "has_trial": "Has a real-mode trial", "differs": "Real-mode verdict differs", "no_trial": "No real-mode trial"}


def real_cells(row: dict, trial: dict | None) -> tuple[str, str]:
    """('ALLOWED 0.75', 'same' | 'differs' | '') for the Review table."""
    if not trial:
        return "", ""
    return f"{trial['verdict']} {trial['confidence']}", realruns.compare(row, trial)


def trial_cost_summary(real: dict) -> dict:
    cost = sum(float(t.get("cost_usd") or 0) for t in real.values())
    return {"trials": len(real), "cost_usd": round(cost, 3)}


def needs_confirmation(row: dict, decision: str) -> str | None:
    """A message when approving something the system did not mark ALLOWED. The person must tick a box to go on."""
    if decision == "approved" and row["compliance_verdict"] != "ALLOWED":
        return (f"The verdict is {row['compliance_verdict']}, not ALLOWED. Approving means a person accepts this item "
                "without the evidence the system asks for.")
    return None


def _log(item_id: str, decision: str, reviewer: str, comment: str, verdict: str, confidence: str, log_path: Path = DECISIONS_CSV) -> None:
    """Append one line to the decision log: who did what, when, and on which verdict."""
    log_path = Path(log_path)
    new = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DECISION_COLUMNS)
        if new:
            w.writeheader()
        w.writerow({"item_id": item_id, "decision": decision, "reviewer": reviewer.strip(), "comment": comment.strip(),
                    "verdict_at_decision": verdict, "confidence_at_decision": confidence,
                    "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def record_decision(item_id: str, decision: str, reviewer: str, comment: str = "", results_csv: Path = config.RESULTS_CSV,
                    log_path: Path = DECISIONS_CSV) -> None:
    """Set the decision in results.csv and append who decided, when and on which verdict to decisions.csv."""
    if decision in ("approved", "rejected") and not reviewer.strip():
        raise ValueError("Enter your name before approving or rejecting.")
    row = next((r for r in store.read_rows(results_csv) if r["item_id"] == item_id), None)
    if row is None:
        raise ValueError(f"{item_id} has no row.")
    store.set_decision(item_id, decision, results_csv)
    log_path = Path(log_path)
    new = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DECISION_COLUMNS)
        if new:
            w.writeheader()
        w.writerow({"item_id": item_id, "decision": decision, "reviewer": reviewer.strip(), "comment": comment.strip(),
                    "verdict_at_decision": row["compliance_verdict"], "confidence_at_decision": row["confidence"],
                    "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def decision_history(item_id: str, log_path: Path = DECISIONS_CSV) -> list[dict]:
    if not Path(log_path).exists():
        return []
    with Path(log_path).open(newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["item_id"] == item_id]
