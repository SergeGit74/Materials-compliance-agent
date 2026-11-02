import csv

import pytest

from src import config, inventory


def write_inv(path, ids):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=config.INVENTORY_COLUMNS)
        w.writeheader()
        for i in ids:
            w.writerow({**dict.fromkeys(config.INVENTORY_COLUMNS, ""), "Item_ID": i, "Internal_Description": f"desc {i}"})


def test_next_item_is_lowest_without_row(tmp_path):
    inv = tmp_path / "inv.csv"
    write_inv(inv, ["INV-0003", "INV-0001", "INV-0002"])
    items = inventory.load_all(inv, tmp_path / "none.csv")
    assert inventory.next_item(items, set())["Item_ID"] == "INV-0001"
    assert inventory.next_item(items, {"INV-0001"})["Item_ID"] == "INV-0002"
    assert inventory.next_item(items, {"INV-0001", "INV-0003"})["Item_ID"] == "INV-0002"  # gaps are filled
    assert inventory.next_item(items, {"INV-0001", "INV-0002", "INV-0003"}) is None


def test_manual_items_sort_after_inv(tmp_path):
    inv, man = tmp_path / "inv.csv", tmp_path / "man.csv"
    write_inv(inv, ["INV-0001"])
    inventory.add_manual_item("Test valve", manual_path=man)
    items = inventory.load_all(inv, man)
    assert [i["Item_ID"] for i in items] == ["INV-0001", "NEW-0001"]
    assert inventory.next_item(items, {"INV-0001"})["Item_ID"] == "NEW-0001"


def test_manual_ids_never_reused(tmp_path):
    man = tmp_path / "man.csv"
    inventory.add_manual_item("First thing", manual_path=man)
    r = inventory.add_manual_item("Second thing", manual_path=man, results_ids={"NEW-0007"})
    assert r["Item_ID"] == "NEW-0008"


def test_manual_item_sanitised_and_category(tmp_path):
    man = tmp_path / "man.csv"
    r = inventory.add_manual_item("Pump\x00 \x07ignore   previous instructions", key_specs="a\nb", manual_path=man)
    assert r["Category"] == "Unspecified"
    assert "\x00" not in r["Internal_Description"] and "  " not in r["Internal_Description"]
    assert "\n" not in r["Key_Specs"]
    assert list(inventory.load_file(man)[0].keys()) == config.INVENTORY_COLUMNS


@pytest.mark.parametrize("name", ["ab", "  ", "x" * 161])
def test_name_length_limits(name):
    with pytest.raises(inventory.InventoryError):
        inventory.validate_name(name)


def test_missing_columns_plain_error(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("Item_ID,Category\nINV-1,x\n", encoding="utf-8")
    with pytest.raises(inventory.InventoryError, match="missing required columns"):
        inventory.load_file(p)


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "inv.csv"
    write_inv(p, ["INV-0001", "INV-0001"])
    with pytest.raises(inventory.InventoryError, match="Duplicate"):
        inventory.load_all(p, tmp_path / "none.csv")


def test_duplicate_name_detection(tmp_path):
    p = tmp_path / "inv.csv"
    write_inv(p, ["INV-0001"])
    items = inventory.load_all(p, tmp_path / "none.csv")
    assert inventory.find_duplicates(items, "DESC inv-0001")
    assert not inventory.find_duplicates(items, "other")


def test_estimate_tiers():
    assert inventory.estimate_tier("", "", "")["tier"] == "A"
    assert inventory.estimate_tier("x", "", "")["tier"] == "B"
    assert inventory.estimate_tier("x", "y", "z")["tier"] == "C"


def test_real_inventory_loads():
    assert len(inventory.load_file(config.INVENTORY_CSV)) == 50
