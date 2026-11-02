from src import config, pipeline
from src.models import Candidate, Compliance, MockModels, Normalized, Usage, Verification

ITEM = {c: "" for c in config.INVENTORY_COLUMNS} | {
    "Item_ID": "INV-0001", "Category": "Electrical", "Internal_Description": "MCCB 3P 250A",
    "Key_Specs": "250A, 415V", "Site_Location": "Site A - Tower 1", "Qty": "4", "Equipment_Served": "MDB-01"}


def budget():
    return pipeline.Budget(15, 300000)


class Scripted(MockModels):
    """Mock with configurable answers, to drive the guards."""

    def __init__(self, candidates=(), compliance=None, verification=None, tokens=0, normalized=None):
        super().__init__()
        self._c, self._comp, self._ver = list(candidates), compliance, verification
        self._tok, self._norm = tokens, normalized
        self.queries = []

    def normalize(self, item, specs):
        n, _ = super().normalize(item, specs)
        return self._norm or n, Usage(input_tokens=self._tok)

    def discover(self, q):
        self.queries.append(q)
        return self._c, Usage()

    def assess_compliance(self, n, c, files):
        return self._comp or Compliance(), Usage()

    def verify(self, n, c, comp):
        return self._ver or Verification(), Usage()


def test_enforce_verdict():
    assert pipeline.enforce_verdict("ALLOWED", ["a.pdf#1"], ["http://x"])[0] == "ALLOWED"
    assert pipeline.enforce_verdict("ALLOWED", [], ["http://x"])[0] == "UNKNOWN"
    assert pipeline.enforce_verdict("ALLOWED", ["a.pdf#1"], [])[0] == "UNKNOWN"
    assert pipeline.enforce_verdict("MAYBE", [], [])[0] == "UNKNOWN"
    assert pipeline.enforce_verdict("NOT_ALLOWED", [], [])[0] == "NOT_ALLOWED"


def test_valid_rule_refs():
    assert pipeline.valid_rule_refs(["a.pdf#3.1", "b.pdf#1", "a.pdf", "a.pdf#"], ["a.pdf"]) == ["a.pdf#3.1"]


def test_mock_run_is_unknown_and_pending():
    row = pipeline.run_item(ITEM, MockModels(), budget())["row"]
    assert row["compliance_verdict"] == "UNKNOWN"
    assert row["human_decision"] == "pending"


def test_allowed_with_fake_rule_file_is_downgraded(monkeypatch):
    monkeypatch.setattr(pipeline, "rule_files", lambda *a: ["rule.pdf"])
    m = Scripted(compliance=Compliance(verdict="ALLOWED", rule_refs=["fake.pdf#1"], supplier_urls=["http://s"]))
    row = pipeline.run_item(ITEM, m, budget())["row"]
    assert row["compliance_verdict"] == "UNKNOWN"
    assert "downgraded" in row["notes"]


def test_allowed_with_evidence_kept(monkeypatch):
    monkeypatch.setattr(pipeline, "rule_files", lambda *a: ["rule.pdf"])
    m = Scripted(compliance=Compliance(verdict="ALLOWED", rule_refs=["rule.pdf#2"], supplier_urls=["http://s"]))
    assert pipeline.run_item(ITEM, m, budget())["row"]["compliance_verdict"] == "ALLOWED"


def test_verifier_rejection_blocks_allowed(monkeypatch):
    monkeypatch.setattr(pipeline, "rule_files", lambda *a: ["rule.pdf"])
    m = Scripted(compliance=Compliance(verdict="ALLOWED", rule_refs=["rule.pdf#2"], supplier_urls=["http://s"]),
                 verification=Verification(rejected_claims=["claim 1"]))
    assert pipeline.run_item(ITEM, m, budget())["row"]["compliance_verdict"] == "UNKNOWN"


def test_candidate_without_source_dropped():
    m = Scripted(candidates=[Candidate(producer="Ghost", source_url=" "),
                             Candidate(producer="Real", source_url="http://r")])
    assert pipeline.run_item(ITEM, m, budget())["row"]["producer_candidates"] == ["Real"]


def test_search_query_has_no_internal_data():
    m = Scripted()
    pipeline.run_item(ITEM, m, budget())
    for secret in ("Site A", "Tower", "MDB-01", "INV-0001"):
        assert secret not in m.queries[0]


def test_budget_exceeded_gives_unknown():
    row = pipeline.run_item(ITEM, Scripted(tokens=500), pipeline.Budget(15, 100))["row"]
    assert row["compliance_verdict"] == "UNKNOWN" and "budget exceeded" in row["notes"]
    row = pipeline.run_item(ITEM, Scripted(), pipeline.Budget(2, 100000))["row"]
    assert "budget exceeded" in row["notes"]


def test_vague_row_stops_at_normalization():
    n = Normalized(canonical_name="x", product_type="x", category="General", confidence=0.1,
                   too_vague=True, vague_reason="several items")
    m = Scripted(normalized=n)
    row = pipeline.run_item(ITEM, m, budget())["row"]
    assert row["compliance_verdict"] == "UNKNOWN" and m.queries == []


def test_safety_critical_pending():
    item = ITEM | {"Category": "Fire & Life Safety", "Internal_Description": "Sprinkler head"}
    out = pipeline.run_item(item, MockModels(), budget())
    assert out["safety"] and out["row"]["human_decision"] == "pending"


def test_report_has_required_sections():
    report = pipeline.run_item(ITEM, MockModels(), budget())["report"]
    for h in ("Original row", "Normalization", "Producer candidates", "Fit table", "Rules checked",
              "Verification notes", "Final verdict", "Open questions"):
        assert h in report


def test_rule_files_skips_readme_index(tmp_path):
    nl = chr(10)
    head = "| File | Issuer | Title | Version | Source | Added | Confirmed by |" + nl + "|---|---|---|---|---|---|---|" + nl
    (tmp_path / "INDEX.md").write_text(head + "| dm_x_v2024.pdf | dm | T | 2024 | u | 2026 | Jane |" + nl, encoding="utf-8")
    for n in ("README.md", "dm_x_v2024.pdf"):
        (tmp_path / n).write_text("x")
    assert pipeline.rule_files(tmp_path) == ["dm_x_v2024.pdf"]
    no_index = tmp_path / "other"
    no_index.mkdir()
    (no_index / "a_v1.pdf").write_text("x")
    assert pipeline.rule_files(no_index) == ["a_v1.pdf"]  # a folder with no INDEX.md at all is not gated (the tests rely on this)
    (no_index / "INDEX.md").write_text("no table yet")
    assert pipeline.rule_files(no_index) == []  # an INDEX.md that confirms nothing makes nothing citable


def test_only_files_confirmed_in_index_md_can_be_cited(tmp_path):
    nl = chr(10)
    head = "| File | Issuer | Title | Version | Source | Added | Confirmed by |" + nl + "|---|---|---|---|---|---|---|" + nl
    rows = "| a_v1.pdf | x | A | 2020 | u | 2026 | Jane |" + nl + "| b_v1.pdf | x | B | 2020 | u | 2026 | PENDING: added automatically |" + nl
    (tmp_path / "INDEX.md").write_text(head + rows, encoding="utf-8")
    for n in ("a_v1.pdf", "b_v1.pdf", "c_v1.pdf"):
        (tmp_path / n).write_text("x")
    assert pipeline.rule_files(tmp_path) == ["a_v1.pdf"]  # b is pending, c has no row


def test_scope_constants():
    assert config.RULES_LANGUAGE == "en" and config.RULES_JURISDICTION == "dubai"


def test_safety_check_uses_equipment_served_and_new_keywords():
    from src import config
    cladding = {"Internal_Description": "Aluminium composite panel 4mm FR core", "Equipment_Served": "Cladding"}
    fire_valve = {"Internal_Description": "Gate valve DN100 PN16 ductile", "Equipment_Served": "Fire/Domestic main"}
    plain_valve = {"Internal_Description": "Gate valve DN100 PN16 ductile", "Equipment_Served": "Chilled water"}
    assert config.is_safety_critical("Building Materials", config.item_safety_text(cladding))
    assert config.is_safety_critical("Plumbing & Pumps", config.item_safety_text(fire_valve))
    assert not config.is_safety_critical("Plumbing & Pumps", config.item_safety_text(plain_valve))
    assert config.is_safety_critical("Electrical", "Emergency light 3 h maintained")
    assert config.is_safety_critical("Building Materials", "Aluminium composite panel 4mm")
