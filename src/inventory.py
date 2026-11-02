"""Inventory loading, validation, next-item selection and manual items (README 1.1, 7.6)."""
import csv
import re
from pathlib import Path

import pandas as pd

from . import config


class InventoryError(Exception):
    """Plain-language problem with an input file."""


def _clean(value) -> str:
    return "" if value is None else str(value).strip()


def load_file(path: Path) -> list[dict]:
    """Read an inventory from .csv or .xlsx. Only the 'Inventory' sheet is ever read (never Eval_Notes)."""
    path = Path(path)
    if not path.exists():
        raise InventoryError(f"File not found: {path}")
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                columns = reader.fieldnames or []
                rows = list(reader)
        elif suffix in (".xlsx", ".xlsm"):
            df = pd.read_excel(path, sheet_name="Inventory", dtype=str, keep_default_na=False)
            columns = list(df.columns)
            rows = df.to_dict("records")
        else:
            raise InventoryError(f"Unsupported file type '{suffix}'. Use .csv or .xlsx.")
    except ValueError as e:  # missing sheet
        raise InventoryError(f"Could not read the 'Inventory' sheet in {path.name}: {e}") from e
    missing = [c for c in config.INVENTORY_COLUMNS if c not in columns]
    if missing:
        raise InventoryError(f"{path.name} is missing required columns: {', '.join(missing)}")
    return [{c: _clean(r.get(c)) for c in config.INVENTORY_COLUMNS} for r in rows]


def load_all(inventory_path: Path = config.INVENTORY_CSV,
             manual_path: Path = config.MANUAL_ITEMS_CSV) -> list[dict]:
    """Inventory plus manual items (if the file exists), sorted by Item_ID."""
    items = load_file(inventory_path)
    if Path(manual_path).exists():
        items += load_file(manual_path)
    ids = [i["Item_ID"] for i in items]
    if any(not i for i in ids):
        raise InventoryError("Some rows have an empty Item_ID.")
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise InventoryError(f"Duplicate Item_ID values: {', '.join(dupes)}")
    return sorted(items, key=lambda r: r["Item_ID"])


def next_item(items: list[dict], done_ids: set[str]) -> dict | None:
    """Lowest Item_ID with no row in results.csv."""
    for item in sorted(items, key=lambda r: r["Item_ID"]):
        if item["Item_ID"] not in done_ids:
            return item
    return None


def find_item(items: list[dict], item_id: str) -> dict | None:
    return next((i for i in items if i["Item_ID"].upper() == item_id.upper()), None)


# ---------- manual items (section 7.6) ----------

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
NAME_MIN, NAME_MAX = 3, 160
DETAIL_MAX = 200


def sanitize(text: str, max_len: int) -> str:
    """Strip control characters and collapse whitespace. The text is data, never instructions."""
    text = _CONTROL.sub(" ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:max_len]


def validate_name(name: str) -> str:
    clean = sanitize(name, NAME_MAX + 1)
    if len(clean) < NAME_MIN:
        raise InventoryError(f"The name must be at least {NAME_MIN} characters.")
    if len(clean) > NAME_MAX:
        raise InventoryError(f"The name must be at most {NAME_MAX} characters.")
    return clean


def find_duplicates(items: list[dict], name: str) -> list[dict]:
    key = name.lower()
    return [i for i in items if i["Internal_Description"].lower() == key]


def _next_manual_id(manual_path: Path, results_ids: set[str]) -> str:
    used = set(results_ids)
    if Path(manual_path).exists():
        used |= {r["Item_ID"] for r in load_file(manual_path)}
    nums = [int(m.group(1)) for i in used if (m := re.fullmatch(r"NEW-(\d+)", i))]
    return f"NEW-{(max(nums) if nums else 0) + 1:04d}"


def make_manual_item(name: str, manufacturer: str = "", part_number: str = "", key_specs: str = "",
                     equipment: str = "", manual_path: Path = config.MANUAL_ITEMS_CSV,
                     results_ids: set[str] = frozenset()) -> dict:
    """Build a business-user item with its next NEW- id WITHOUT writing it (the id is only reserved by save_manual_item)."""
    row = dict.fromkeys(config.INVENTORY_COLUMNS, "")
    row.update(
        Item_ID=_next_manual_id(manual_path, set(results_ids)),
        Category="Unspecified",
        Internal_Description=validate_name(name),
        Part_Number=sanitize(part_number, DETAIL_MAX),
        Manufacturer_Listed=sanitize(manufacturer, DETAIL_MAX),
        Equipment_Served=sanitize(equipment, DETAIL_MAX),
        Key_Specs=sanitize(key_specs, DETAIL_MAX),
    )
    return row


def save_manual_item(row: dict, manual_path: Path = config.MANUAL_ITEMS_CSV, results_ids: set[str] = frozenset()) -> dict:
    """Append a built manual item to manual_items.csv (the one file in data/ the app may write). Refuses if its id has been taken meanwhile."""
    if _next_manual_id(manual_path, set(results_ids)) != row["Item_ID"]:
        raise InventoryError(f"{row['Item_ID']} is no longer the next free id: another item was added. Run the check again.")
    manual_path = Path(manual_path)
    new_file = not manual_path.exists() or manual_path.stat().st_size == 0
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    with manual_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=config.INVENTORY_COLUMNS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    return row


def add_manual_item(name: str, manufacturer: str = "", part_number: str = "", key_specs: str = "",
                    equipment: str = "", manual_path: Path = config.MANUAL_ITEMS_CSV,
                    results_ids: set[str] = frozenset()) -> dict:
    """Append one business-user item to manual_items.csv. Only these fields are stored."""
    row = make_manual_item(name, manufacturer, part_number, key_specs, equipment, manual_path, results_ids)
    return save_manual_item(row, manual_path, results_ids)


def estimate_tier(manufacturer: str, part_number: str, key_specs: str) -> dict:
    """Planning figures from README 7.6 (not measurements)."""
    filled = sum(bool(x.strip()) for x in (manufacturer, part_number, key_specs))
    if filled == 0:
        return dict(tier="A", time="4 to 8 min", cost="$0.60 to $1.50, typical $1.00", tokens="about 325k")
    if filled == 3:
        return dict(tier="C", time="2 to 3.5 min", cost="$0.30 to $0.70, typical $0.40", tokens="about 130k")
    return dict(tier="B", time="2.5 to 4.5 min", cost="$0.40 to $1.00, typical $0.60", tokens="about 195k")
