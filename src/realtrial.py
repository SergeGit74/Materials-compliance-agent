"""Run the real model clients on chosen items as a TRIAL: results go to results/real_runs.csv, never to results.csv.

    python -m src.realtrial INV-0001 INV-0034 [--confirm-spend]

Spends API credits (Anthropic, Serper, Voyage). One item at a time, in the order given. Asks you to type "spend" unless
--confirm-spend is passed. The planning figure from the 2026-11 trials is about $0.12 per item (range $0.09 to $0.18).
"""
from __future__ import annotations

import argparse
import sys
import time

from . import clients, config, inventory, pipeline, realruns


def run_trial(item: dict, models) -> dict:
    t0 = time.time()
    res = pipeline.run_item(item, models, pipeline.Budget.from_env())
    u = res["usage"]
    realruns.save(item["Item_ID"], res["row"], res["report"], cost_usd=u.cost_usd, tokens=u.input_tokens + u.output_tokens,
                  docs_opened=len(getattr(models, "opened_documents", [])), seconds=time.time() - t0,
                  models=f"{getattr(models, 'm_norm', '?')} / {getattr(models, 'm_reason', '?')} / {getattr(models, 'm_verify', '?')}")
    return {"row": res["row"], "cost": u.cost_usd, "seconds": time.time() - t0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.realtrial", description="Trial the real model clients on chosen items.")
    ap.add_argument("items", nargs="+", help="Item_IDs, for example INV-0001")
    ap.add_argument("--confirm-spend", action="store_true", help="skip the typed confirmation")
    args = ap.parse_args(argv)
    missing = config.missing_env()
    if missing:
        print("Missing or invalid .env settings: " + ", ".join(missing), file=sys.stderr)
        return 1
    inv = inventory.load_all()
    items = []
    for iid in args.items:
        it = inventory.find_item(inv, iid)
        if it is None:
            print(f"{iid} is not in the inventory.", file=sys.stderr)
            return 2
        items.append(it)
    est = 0.12 * len(items)
    print(f"REAL TRIAL of {len(items)} item(s): about ${est:.2f} (range ${0.09 * len(items):.2f} to ${0.18 * len(items):.2f}). results.csv is not changed.")
    if not args.confirm_spend and input('Type "spend" to continue: ').strip().lower() != "spend":
        print("Not confirmed. Nothing was run.")
        return 0
    total = 0.0
    for it in items:  # one item at a time; models are created per item, the rule index is shared
        out = run_trial(it, clients.make_models(real=True))
        r = out["row"]
        total += out["cost"]
        print(f"{it['Item_ID']}  {r['compliance_verdict']:16s} conf {r['confidence']}  ${out['cost']:.3f}  {out['seconds']:.0f}s")
    print(f"Total ${total:.3f}. Open the Review screen to compare.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
