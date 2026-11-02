"""Logic behind app.py that does not need Streamlit: estimates (7.4), batch selection (7.3), queue status."""
import os

from . import config

AED_PER_USD = 3.6725

# Planning figures from README 7.4 (not measurements): per item, low / typical / high.
COST_USD = (0.30, 0.60, 1.50)
MINUTES = (2.5, 3.5, 4.5)


def estimate(n_items: int, concurrency: int = 1) -> dict:
    """Time and cost for n items. Time divides by concurrency; cost does not."""
    c = max(1, concurrency)
    return {
        "cost_usd": tuple(round(n_items * x, 2) for x in COST_USD),
        "minutes": tuple(round(n_items * x / c, 1) for x in MINUTES),
    }


MEASURED_COST = (0.09, 0.14, 0.20)  # USD per item, low / typical / high, from the 50 real-mode trials of 2026-11
MEASURED_SECONDS = (50, 60, 80)  # per item, from the same trials


def manual_estimate(real: bool, trials: dict | None = None) -> dict:
    """Cost and time for ONE manual check. Real mode uses the recorded real-mode trials (10th percentile, median, 90th percentile of cost
    and of seconds when recorded); with fewer than 10 recorded values it uses the figures measured on 2026-11. Mock mode is free and instant."""
    if not real:
        return {"mode": "mock", "cost": (0.0, 0.0, 0.0), "seconds": (1, 2, 5),
                "basis": "Mock models: no web search and no AI call, so no cost. The result will be an UNKNOWN placeholder."}
    def spread(values, default):
        v = sorted(x for x in values if x > 0)
        if len(v) < 10:
            return default, False
        return (round(v[len(v) // 10], 3), round(v[len(v) // 2], 3), round(v[min(len(v) - 1, int(len(v) * 0.9))], 3)), True
    t = list((trials or {}).values())
    cost, c_ok = spread([float(x.get("cost_usd") or 0) for x in t], MEASURED_COST)
    secs, s_ok = spread([float(x.get("seconds") or 0) for x in t], MEASURED_SECONDS)
    n = len(t)
    return {"mode": "real", "cost": cost, "seconds": tuple(int(s) for s in secs),
            "basis": (f"Measured on {n} real-mode trials: cost {'from the trial records' if c_ok else 'from the 2026-11 measurements'}, "
                      f"time {'from the trial records' if s_ok else 'from the 2026-11 measurements (50 to 80 s)'}. A hard item can cost more. "
                      "The cost covers web search, reading datasheets, the AI models and the independent verifier.")}


def default_cap(n_items: int) -> float:
    """Default spend cap: 1.5 times the typical estimate."""
    return round(1.5 * n_items * COST_USD[1], 2)


def is_safety_item(item: dict) -> bool:
    return config.is_safety_critical(item["Category"], config.item_safety_text(item))


def status_of(item_id: str, rows_by_id: dict[str, dict]) -> str:
    row = rows_by_id.get(item_id)
    if row is None:
        return "waiting"
    return "skipped" if row.get("human_decision") == "skipped" else "processed"


def eligible_for_batch(items: list[dict], done_ids: set[str], exclude_safety: bool = True,
                       max_items: int | None = None) -> list[dict]:
    """Waiting items in Item_ID order. Safety-critical ones are left out unless the person turned that off."""
    out = [i for i in sorted(items, key=lambda r: r["Item_ID"])
           if i["Item_ID"] not in done_ids and not (exclude_safety and is_safety_item(i))]
    return out[:max_items] if max_items else out


def models_summary() -> dict:
    """Model names and search method for the confirmation dialog. Names only, never keys."""
    serper = bool(os.getenv("SERPER_API_KEY", "").strip())
    return {
        "normalize": os.getenv("MODEL_NORMALIZE", "(not set)"),
        "reason": os.getenv("MODEL_REASON", "(not set)"),
        "verify": os.getenv("MODEL_VERIFY", "(not set)"),
        "search": "Serper" if serper else "Anthropic web search tool",
    }
