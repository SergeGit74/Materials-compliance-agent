"""Evaluation (README Phase 3): run AFTER processing, never during.

This is the only module that opens the Eval_Notes sheet. It does so only once results.csv has
rows, and only after the Eval_Notes-free metrics are computed. Nothing in the pipeline imports it.

Eval_Notes holds free text (Challenge_Tags, What_a_good_run_does), so only the checks that can be
decided in code are scored automatically. The rest are listed next to the evaluator's notes for a
person to judge. Nothing here claims more than it measures.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from . import config, store

EVAL_SHEET = "Eval_Notes"
COST_RE = re.compile(r"Estimated cost: \$([0-9.]+)")
TOKENS_RE = re.compile(r"Tokens in/out: (\d+)/(\d+)")


def processed(rows: list[dict]) -> list[dict]:
    """Rows that were actually run (skipped items are not results)."""
    return [r for r in rows if r.get("human_decision") != "skipped"]


def run_costs(ids: list[str], items_dir: Path = config.ITEMS_DIR) -> dict[str, dict]:
    """Cost and tokens per item, read from the run log in each item's report."""
    out = {}
    for i in ids:
        p = Path(items_dir) / f"{i}.md"
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        c, t = COST_RE.search(text), TOKENS_RE.search(text)
        if c:
            out[i] = {"cost": float(c.group(1)), "tokens": int(t.group(1)) + int(t.group(2)) if t else 0}
    return out


def compute_metrics(rows: list[dict], costs: dict[str, dict]) -> dict:
    """Metrics that need no Eval_Notes."""
    done = processed(rows)
    n = len(done)
    verdicts: dict[str, int] = {}
    for r in done:
        verdicts[r["compliance_verdict"]] = verdicts.get(r["compliance_verdict"], 0) + 1
    allowed = [r for r in done if r["compliance_verdict"] == "ALLOWED"]
    # An ALLOWED needs a cited rule AND supplier evidence (1.3). Without both it is a false ALLOWED.
    structural_false = [r["item_id"] for r in allowed if not (r["dubai_rule_refs"].strip() and r["evidence_urls"].strip())]
    safety_not_pending = [
        r["item_id"] for r in done
        if config.is_safety_critical(r["category"], r["normalized_name"]) and r["human_decision"] != "pending"]
    cost_vals = [c["cost"] for c in costs.values()]
    return {
        "processed": n,
        "skipped": len(rows) - n,
        "verdicts": verdicts,
        "unknown_rate": (verdicts.get("UNKNOWN", 0) / n) if n else None,
        "false_allowed_structural": structural_false,
        "allowed_total": len(allowed),
        "allowed_ids_for_review": [r["item_id"] for r in allowed],
        "safety_critical_not_pending": safety_not_pending,
        "items_with_cost_log": len(cost_vals),
        "cost_per_item": (sum(cost_vals) / len(cost_vals)) if cost_vals else None,
        "total_cost": sum(cost_vals),
    }


def load_eval_notes(workbook: Path, only_ids: set[str]) -> dict[str, dict]:
    """Read Eval_Notes for the given processed items. Call only after processing."""
    import openpyxl

    wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    try:
        if EVAL_SHEET not in wb.sheetnames:
            raise ValueError(f"No '{EVAL_SHEET}' sheet in {Path(workbook).name}")
        rows = list(wb[EVAL_SHEET].iter_rows(values_only=True))
    finally:
        wb.close()
    head = next((i for i, r in enumerate(rows[:15]) if r and "Item_ID" in r), None)
    if head is None:
        raise ValueError("Could not find the Item_ID header row in Eval_Notes")
    cols = list(rows[head])
    out = {}
    for r in rows[head + 1:]:
        rec = dict(zip(cols, r))
        iid = str(rec.get("Item_ID") or "").strip()
        if iid in only_ids:
            out[iid] = {"tags": str(rec.get("Challenge_Tags") or ""), "guidance": str(rec.get("What_a_good_run_does") or "")}
    return out


def join_notes(rows: list[dict], notes: dict[str, dict]) -> dict:
    """Checks that combine results with Eval_Notes."""
    by_id = {r["item_id"]: r for r in processed(rows)}
    alias_ids = [i for i, n in notes.items() if "alias" in n["tags"].lower() and i in by_id]
    with_alias = [i for i in alias_ids if by_id[i]["matched_producer"].strip() or by_id[i]["alias_notes"].strip()]
    return {
        "alias_tagged_items": alias_ids,
        # Coverage, not accuracy: whether the run produced an alias answer. A person must judge if it is right.
        "alias_answered": with_alias,
        "alias_coverage": (len(with_alias) / len(alias_ids)) if alias_ids else None,
        "missing_eval_notes": sorted(set(by_id) - set(notes)),
    }


def render(metrics: dict, joined: dict | None, notes: dict[str, dict], rows: list[dict]) -> str:
    m = metrics
    pct = lambda x: "n/a" if x is None else f"{x:.0%}"
    L = ["# Evaluation", "",
         f"Processed items: {m['processed']} (skipped, not scored: {m['skipped']})",
         f"Verdict mix: {m['verdicts']}",
         f"UNKNOWN rate: {pct(m['unknown_rate'])}",
         f"False ALLOWED (structural, target 0): {len(m['false_allowed_structural'])} {m['false_allowed_structural']}",
         f"ALLOWED verdicts to read against the evaluator notes: {m['allowed_total']}",
         f"Safety-critical items not left pending (target 0): {len(m['safety_critical_not_pending'])} "
         f"{m['safety_critical_not_pending']}"]
    if m["cost_per_item"] is None:
        L.append("Cost per item: n/a (no item reports with a run log)")
    else:
        L.append(f"Cost per item: ${m['cost_per_item']:.2f} over {m['items_with_cost_log']} items "
                 f"(total ${m['total_cost']:.2f})")
    if joined is None:
        L += ["", "Eval_Notes not read."]
        return "\n".join(L) + "\n"
    L += ["", f"Alias items (tagged in Eval_Notes): {len(joined['alias_tagged_items'])}",
          f"Alias coverage (answered, not verified correct): {pct(joined['alias_coverage'])}",
          "Alias accuracy needs a person: compare matched_producer and alias_notes with the notes below."]
    if joined["missing_eval_notes"]:
        L.append(f"Processed items without an Eval_Notes row: {joined['missing_eval_notes']}")
    review = [r for r in processed(rows) if r["item_id"] in joined["alias_tagged_items"] or r["compliance_verdict"] == "ALLOWED"]
    if review:
        L += ["", "## To review by a person", ""]
        for r in review:
            n = notes.get(r["item_id"], {})
            L += [f"- {r['item_id']}: verdict {r['compliance_verdict']}, producer '{r['matched_producer']}', "
                  f"alias notes '{r['alias_notes']}'",
                  f"  tags: {n.get('tags', '')}", f"  good run: {n.get('guidance', '')}"]
    return "\n".join(L) + "\n"


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.evaluate", description="Evaluate results after processing.")
    p.add_argument("--results", type=Path, default=config.RESULTS_CSV)
    p.add_argument("--items-dir", type=Path, default=config.ITEMS_DIR)
    p.add_argument("--workbook", type=Path, default=config.DATA_DIR / "inventory_sample.xlsx")
    p.add_argument("--no-eval-notes", action="store_true", help="metrics that need no Eval_Notes only")
    p.add_argument("--json", action="store_true", help="print metrics as JSON")
    p.add_argument("--out", type=Path, default=None, help="also save the output to this file (UTF-8; not inside data/)")
    args = p.parse_args(argv)

    if args.out is not None and _inside(args.out, config.DATA_DIR):
        print("Refusing to write inside data/: that folder is read-only for this tool.", file=sys.stderr)
        return 3

    rows = store.read_rows(args.results)
    if not processed(rows):
        print("No processed items in results.csv. Evaluation runs after processing, not before.", file=sys.stderr)
        return 1
    metrics = compute_metrics(rows, run_costs([r["item_id"] for r in processed(rows)], args.items_dir))

    notes, joined = {}, None
    if not args.no_eval_notes:
        try:
            notes = load_eval_notes(args.workbook, {r["item_id"] for r in processed(rows)})
        except (OSError, ValueError) as e:
            print(f"Could not read Eval_Notes: {e}", file=sys.stderr)
            return 2
        joined = join_notes(rows, notes)
    if args.json:
        text = json.dumps({"metrics": metrics, "eval_notes": joined}, indent=2) + "\n"
    else:
        text = render(metrics, joined, notes, rows)
    print(text, end="")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Saved to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
