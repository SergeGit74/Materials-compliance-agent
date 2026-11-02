"""CLI: python -m src.run  (README section 5, Phase 2). One item per approval, then ask and stop."""
import argparse
import re
import sys

from . import config, inventory, pipeline, store
from .clients import make_models
from .models import MockModels

YES = {"y", "yes", "next", "ok"}


def short(item: dict, n: int = 60) -> str:
    d = item["Internal_Description"]
    return d if len(d) <= n else d[: n - 3] + "..."


def ask_permission(nxt: dict) -> tuple[str, str | None]:
    """The exact question from README 1.1. Returns (action, item_id). Any other reply asks again;
    end of input counts as stop."""
    question = f'Process next item {nxt["Item_ID"]} ("{short(nxt)}")? Reply: yes / skip / stop / redo INV-XXXX'
    while True:
        try:
            reply = input(question + "\n> ").strip()
        except EOFError:
            return "stop", None
        low = reply.lower()
        if low in YES:
            return "yes", None
        if low == "skip":
            return "skip", None
        if low == "stop":
            return "stop", None
        m = re.fullmatch(r"redo\s+([A-Za-z]+-\d+)", reply, re.I)
        if m:
            return "redo", m.group(1).upper()


def process(item: dict, models, redo: bool) -> None:
    result = pipeline.run_item(item, models)
    (store.replace_row if redo else store.append_row)(result["row"])
    path = store.write_report(item["Item_ID"], result["report"])
    row = result["row"]
    print(f"\n{item['Item_ID']}  {row['normalized_name']}")
    print(f"  producers : {row['producer_candidates'] and ', '.join(row['producer_candidates']) or 'none'}")
    print(f"  fit       : {row['fit_status']}")
    print(f"  verdict   : {row['compliance_verdict']}   confidence {row['confidence']}   human: {row['human_decision']}")
    if result["safety"]:
        print("  safety-critical: a person must make the final decision")
    for q in result["open_questions"]:
        print(f"  open      : {q}")
    print(f"  report    : {path}\n")


def run_loop(models, first: dict | None, redo_first: bool, items: list[dict]) -> int:
    current, redo = first, redo_first
    while True:
        if current is not None:
            process(current, models, redo)
        items = inventory.load_all()
        done = store.done_ids()
        nxt = inventory.next_item(items, done)
        while True:
            if nxt is None:
                print("No more unprocessed items.")
                return 0
            action, target = ask_permission(nxt)
            if action == "stop":
                return 0
            if action == "yes":
                current, redo = nxt, False
                break
            if action == "skip":
                store.append_row(pipeline.skip_row(nxt))
                print(f"Skipped {nxt['Item_ID']}.")
                nxt = inventory.next_item(items, store.done_ids())
                continue
            item = inventory.find_item(items, target)  # redo
            if item is None:
                print(f"{target} is not in the inventory.")
                continue
            current, redo = item, item["Item_ID"] in store.done_ids()
            break


def cmd_add(args, items: list[dict]) -> int:
    try:
        name = inventory.validate_name(args.add)
    except inventory.InventoryError as e:
        print(e)
        return 2
    est = inventory.estimate_tier(args.manufacturer, args.part_number, args.key_specs)
    print(f"Estimate (tier {est['tier']}, planning figures): {est['time']}, {est['cost']}, {est['tokens']} input tokens")
    if config.is_safety_critical("", name):
        print("Notice: this looks safety-critical, so a person must make the final decision.")
    if inventory.find_duplicates(items, name):
        if input("An item with this name already exists. Add anyway? [y/N] ").strip().lower() not in YES:
            print("Not added.")
            return 0
    if input("Add to queue? Nothing runs yet. [y/N] ").strip().lower() not in YES:
        print("Not added.")
        return 0
    row = inventory.add_manual_item(name, args.manufacturer, args.part_number, args.key_specs,
                                    args.equipment, results_ids=store.done_ids())
    print(f"Added {row['Item_ID']} to {config.MANUAL_ITEMS_CSV.name}. Run: python -m src.run --item {row['Item_ID']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.run", description="Process one inventory item at a time.")
    p.add_argument("--item", help="process this Item_ID (redo if it already has a row)")
    p.add_argument("--add", metavar="NAME", help="add a manual item (NEW-xxxx) to data/manual_items.csv")
    p.add_argument("--manufacturer", default="")
    p.add_argument("--part-number", default="")
    p.add_argument("--key-specs", default="")
    p.add_argument("--equipment", default="")
    p.add_argument("--real", action="store_true", help="use the real model clients (spends API credits; asks you to confirm)")
    args = p.parse_args(argv)

    missing = config.missing_env()  # names only, never values (8.6)
    if missing:
        print("Missing or invalid .env settings: " + ", ".join(missing), file=sys.stderr)
        return 1
    try:
        items = inventory.load_all()
    except inventory.InventoryError as e:
        print(e, file=sys.stderr)
        return 2
    if args.add:
        return cmd_add(args, items)

    if args.real:
        print("REAL MODE: model, search and embedding calls will spend API credits (planning figure about $0.60 per typical item).")
        if input('Type "spend" to continue: ').strip().lower() != "spend":
            print("Not confirmed. Nothing was run.")
            return 0
        try:
            models = make_models(real=True)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
    else:
        models = MockModels()
        print("MOCK MODE: no model or web calls are made.\n")
    done = store.done_ids()
    if args.item:
        item = inventory.find_item(items, args.item)
        if item is None:
            print(f"{args.item} is not in the inventory.", file=sys.stderr)
            return 2
        return run_loop(models, item, item["Item_ID"] in done, items)
    first = inventory.next_item(items, done)
    if first is None:
        print("No more unprocessed items.")
        return 0
    return run_loop(models, first, False, items)


if __name__ == "__main__":
    sys.exit(main())
