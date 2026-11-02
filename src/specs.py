"""Deterministic spec comparison for the fit check (README step 4)."""
from dataclasses import dataclass

from .units import normalize_units, parse_quantities

TOLERANCE = 0.01  # relative


@dataclass
class SpecRow:
    unit: str
    wanted: list[float]
    found: list[float]
    result: str  # MATCH | MISMATCH | UNCONFIRMED


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b), 1e-9)


def compare(item_spec_text: str, candidate_spec_text: str) -> tuple[str, list[SpecRow]]:
    """Return (FIT | PARTIAL | NO_FIT | UNKNOWN, per-unit table).

    MATCH: every wanted value appears in the candidate. MISMATCH: the candidate states that unit
    but lacks a wanted value. UNCONFIRMED: the candidate does not state that unit at all.
    """
    want = parse_quantities(normalize_units(item_spec_text)[0])
    have = parse_quantities(normalize_units(candidate_spec_text)[0])
    rows: list[SpecRow] = []
    for unit, values in want.items():
        found = have.get(unit, [])
        if not found:
            result = "UNCONFIRMED"
        elif all(any(_close(v, f) for f in found) for v in values):
            result = "MATCH"
        else:
            result = "MISMATCH"
        rows.append(SpecRow(unit, values, found, result))
    results = {r.result for r in rows}
    if not rows or results == {"UNCONFIRMED"}:
        return "UNKNOWN", rows
    if "MISMATCH" in results:
        return "NO_FIT", rows
    if "UNCONFIRMED" in results:
        return "PARTIAL", rows
    return "FIT", rows
