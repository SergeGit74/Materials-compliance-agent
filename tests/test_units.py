import pytest

from src.specs import compare
from src.units import normalize_units, parse_quantities


def test_hp_fraction_to_kw():
    text, log = normalize_units("1/10 HP (75W), 230V")
    assert text.startswith("0.07457 kW")
    assert len(log) == 1 and "1/10 HP" in log[0].original


def test_inch_to_mm_and_log():
    text, log = normalize_units("250 mm / 10 inch dia")
    assert "254 mm" in text
    assert log[0].result == "254 mm"


def test_sqmm_spelling():
    text, log = normalize_units("4 core 185 sqmm copper")
    assert "185 mm2" in text and log


def test_thread_size_not_converted():
    text, log = normalize_units("K5.6, 1/2 in NPT")
    assert text == "K5.6, 1/2 in NPT"
    assert log[0].result == "not converted"


def test_parse_quantities_groups_by_unit():
    q = parse_quantities("3P, 250A, 36kA, 415VAC, 300 mm x 50 mm")
    assert q["A"] == [250] and q["kA"] == [36] and q["V"] == [415]
    assert q["mm"] == [300, 50] or q["mm"] == [50]  # "300 x" has no unit of its own


@pytest.mark.parametrize("item,cand,expected", [
    ("2.2 kW, 415V", "2.2kW 415 V", "FIT"),
    ("3 HP", "2.0 kW", "NO_FIT"),  # 3 HP = 2.24 kW
    ("40A, coil 230VAC", "40 A", "PARTIAL"),
    ("40A", "no numbers here", "UNKNOWN"),
    ("Type A only", "Type A", "UNKNOWN"),
])
def test_compare(item, cand, expected):
    assert compare(item, cand)[0] == expected


def test_compare_tolerance():
    assert compare("100 mm", "100.5 mm")[0] == "FIT"
    assert compare("100 mm", "102 mm")[0] == "NO_FIT"


def test_decimal_voltage_pair_not_read_as_fraction():
    # "0.6/1kV" is U0/U; the quantity attached to the unit is 1 kV, never 6/1
    assert parse_quantities("4 core 185 mm2 copper, 0.6/1kV") == {"mm2": [185.0], "kV": [1.0]}
    assert compare("0.6/1kV", "1 kV")[0] == "FIT"


def test_fraction_hp_still_works():
    assert normalize_units("1/10 HP")[0] == "0.07457 kW"


def test_refrigerant_name_not_read_as_amps():
    assert parse_quantities("R410A, 11.3 kg") == {"kg": [11.3]}
    assert parse_quantities("R-410A, 11.3 kg") == {"kg": [11.3]}
    assert parse_quantities("32A, 30mA") == {"A": [32.0]}
    assert compare("R410A, 11.3 kg", "11.34 kg")[0] == "FIT"
