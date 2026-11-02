"""Deterministic unit conversion and spec parsing (README step 1). Every conversion is recorded."""
import re
from dataclasses import dataclass

HP_TO_KW = 0.7457  # mechanical horsepower
INCH_TO_MM = 25.4

# Not preceded by a digit or dot, so "0.6/1kV" is not read as the fraction 6/1.
# Also not preceded by "R" / "R-", so refrigerant names such as R410A are not read as 410 A.
_NUM = r"(?<![\d.])(?<!R)(?<!R-)(\d+/\d+|\d+(?:\.\d+)?)"


@dataclass(frozen=True)
class Conversion:
    original: str
    result: str
    note: str = ""

    def __str__(self) -> str:
        return f"{self.original} -> {self.result}" + (f" ({self.note})" if self.note else "")


def _num(s: str) -> float:
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b)
    return float(s)


def _fmt(x: float) -> str:
    return f"{x:.4g}"


_HP = re.compile(_NUM + r"\s*HP\b", re.I)
_INCH = re.compile(_NUM + r'\s*(?:inch(?:es)?|in\b|")(\s*(?:NPT|BSP|NPS)\b)?', re.I)
_SQMM = re.compile(_NUM + r"\s*(?:sqmm|sq\.?\s?mm|mm2|mm²)", re.I)


def normalize_units(text: str) -> tuple[str, list[Conversion]]:
    """Return text with HP, inch and sqmm rewritten to kW, mm and mm2, plus the conversion log."""
    log: list[Conversion] = []

    def hp(m):
        kw = _num(m.group(1)) * HP_TO_KW
        log.append(Conversion(m.group(0), f"{_fmt(kw)} kW", f"1 HP = {HP_TO_KW} kW"))
        return f"{_fmt(kw)} kW"

    def inch(m):
        if m.group(2):  # nominal pipe thread size, not a length
            log.append(Conversion(m.group(0).strip(), "not converted", "nominal thread size"))
            return m.group(0)
        mm = _num(m.group(1)) * INCH_TO_MM
        log.append(Conversion(m.group(0), f"{_fmt(mm)} mm", f"1 in = {INCH_TO_MM} mm"))
        return f"{_fmt(mm)} mm"

    def sqmm(m):
        log.append(Conversion(m.group(0), f"{m.group(1)} mm2", "unit spelling"))
        return f"{m.group(1)} mm2"

    text = _HP.sub(hp, text)
    text = _INCH.sub(inch, text)
    text = _SQMM.sub(sqmm, text)
    return text, log


_QTY = re.compile(
    _NUM + r"\s*(kV|kA|kW|mm2|mm|ml|kg|bar|MHz|Hz|hrs?|hour|VAC|MP|amp|V|A|W|m)\b")
_CANON = {"VAC": "V", "amp": "A", "hrs": "hr", "hour": "hr"}


def parse_quantities(text: str) -> dict[str, list[float]]:
    """Numeric values with a recognised unit, grouped by canonical unit. Expects normalised text."""
    out: dict[str, list[float]] = {}
    for m in _QTY.finditer(text):
        unit = _CANON.get(m.group(2), m.group(2))
        out.setdefault(unit, []).append(_num(m.group(1)))
    return out


def text_only_tokens(text: str) -> list[str]:
    """Comma-separated spec tokens with no parsable quantity: left for the model to judge."""
    return [t.strip() for t in text.split(",") if t.strip() and not _QTY.search(t)]
