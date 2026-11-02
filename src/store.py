"""results.csv and per-item report writing (README section 4). UTF-8, append only except for redo."""
import csv
import os
import threading
from pathlib import Path

from . import config


# One lock for every write: the app can write from batch worker threads and the UI at once.
LOCK = threading.RLock()


def _ensure_header(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(config.RESULTS_COLUMNS)


def read_rows(path: Path = config.RESULTS_CSV) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def done_ids(path: Path = config.RESULTS_CSV) -> set[str]:
    return {r["item_id"] for r in read_rows(path)}


def _format(row: dict) -> dict:
    out = {}
    for col in config.RESULTS_COLUMNS:
        v = row.get(col, "")
        out[col] = ";".join(map(str, v)) if isinstance(v, (list, tuple)) else ("" if v is None else v)
    return out


def append_row(row: dict, path: Path = config.RESULTS_CSV) -> None:
    """Append one row. Refuses to add a second row for an item (use replace_row for redo)."""
    with LOCK:
        _append(row, path)


def _append(row: dict, path: Path) -> None:
    path = Path(path)
    _ensure_header(path)
    if row["item_id"] in done_ids(path):
        raise ValueError(f"{row['item_id']} already has a row; use redo to replace it.")
    with path.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=config.RESULTS_COLUMNS).writerow(_format(row))


def replace_row(row: dict, path: Path = config.RESULTS_CSV) -> None:
    """Redo: replace only this item's row; every other row is kept as it was."""
    with LOCK:
        _replace(row, path)


def _replace(row: dict, path: Path) -> None:
    path = Path(path)
    _ensure_header(path)
    rows = read_rows(path)
    if row["item_id"] not in {r["item_id"] for r in rows}:
        raise ValueError(f"{row['item_id']} has no row to replace.")
    new = _format(row)
    rows = [new if r["item_id"] == row["item_id"] else r for r in rows]
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=config.RESULTS_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def set_decision(item_id: str, decision: str, path: Path = config.RESULTS_CSV) -> None:
    """Review screen: a person sets approved, rejected or back to pending. Only that column changes."""
    if decision not in ("approved", "rejected", "pending"):
        raise ValueError(f"Invalid decision '{decision}'")
    with LOCK:
        path = Path(path)
        rows = read_rows(path)
        target = next((r for r in rows if r["item_id"] == item_id), None)
        if target is None:
            raise ValueError(f"{item_id} has no row.")
        target["human_decision"] = decision
        _replace(target, path)


def write_report(item_id: str, markdown: str, items_dir: Path = config.ITEMS_DIR) -> Path:
    items_dir = Path(items_dir)
    items_dir.mkdir(parents=True, exist_ok=True)
    path = items_dir / f"{item_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
