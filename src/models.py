"""Model-call layer. Phase 2 ships only a mock; real clients come later behind the same interface.

Each method receives only what that step is allowed to see. In particular `discover` gets the
normalized product type and key specs, never internal prices, stock, sites or asset codes (1.4).
"""
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Normalized(BaseModel):
    canonical_name: str
    product_type: str
    category: str
    specs_text: str = ""
    confidence: float = Field(ge=0, le=1)
    too_vague: bool = False
    vague_reason: str = ""


class Candidate(BaseModel):
    producer: str
    source_url: str  # a producer without a source must never be returned
    specs_text: str = ""
    alias_note: str = ""
    document_opened: bool = False  # True only when code fetched and read the page or PDF at source_url
    source_type: str = ""  # maker | standards_body | distributor | marketplace | other, as judged from the page


class Compliance(BaseModel):
    rule_refs: list[str] = []  # file#section, files from rules/ only
    verdict: str = "UNKNOWN"
    notes: str = ""
    supplier_urls: list[str] = []


class Verification(BaseModel):
    rejected_claims: list[str] = []  # every claim the verifier could not support
    core_rejected: list[str] | None = None  # the subset that decides the verdict; None means every rejected claim is treated as core
    corrected_notes: str = ""  # the compliance notes without the unsupported statements
    notes: str = ""


class Models(Protocol):
    name: str

    def normalize(self, item: dict, converted_specs: str) -> tuple[Normalized, Usage]: ...
    def discover(self, search_query: str) -> tuple[list[Candidate], Usage]: ...
    def assess_compliance(self, normalized: Normalized, candidates: list[Candidate],
                          rule_files: list[str]) -> tuple[Compliance, Usage]: ...
    def verify(self, normalized: Normalized, candidates: list[Candidate],
               compliance: Compliance) -> tuple[Verification, Usage]: ...


@dataclass
class MockModels:
    """Deterministic stand-in. Makes no network call and never produces an ALLOWED verdict."""
    name: str = "mock"
    calls: list[str] = field(default_factory=list)

    def normalize(self, item, converted_specs):
        self.calls.append("normalize")
        desc = item["Internal_Description"]
        vague = "assorted" in (desc + " " + item["Key_Specs"]).lower()
        category = item["Category"] if item["Category"] != "Unspecified" else "General"
        return Normalized(
            canonical_name=desc, product_type=desc, category=category, specs_text=converted_specs,
            confidence=0.2 if vague else 0.5, too_vague=vague,
            vague_reason="Several or unspecified items in one row (mock heuristic)." if vague else "",
        ), Usage()

    def discover(self, search_query):
        self.calls.append("discover")
        return [], Usage()  # mock: no web access, so no sourced producers

    def assess_compliance(self, normalized, candidates, rule_files):
        self.calls.append("assess_compliance")
        return Compliance(verdict="UNKNOWN", notes="Mock model: no rule retrieval performed."), Usage()

    def verify(self, normalized, candidates, compliance):
        self.calls.append("verify")
        return Verification(notes="Mock verifier: nothing to re-check."), Usage()
