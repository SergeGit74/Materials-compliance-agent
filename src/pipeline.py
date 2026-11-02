"""Per-item pipeline (README section 3). Deterministic steps in code, model steps via `Models`."""
import os
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .models import Candidate, Compliance, Models, Normalized, Usage
from .specs import SpecRow, compare
from .units import Conversion, normalize_units, text_only_tokens

STEPS = ("load", "normalize", "discover", "fit", "compliance", "verify", "write")


class BudgetExceeded(Exception):
    pass


class Budget:
    """Hard per-item limits (8.6)."""

    def __init__(self, max_steps: int, max_tokens: int):
        self.max_steps, self.max_tokens = max_steps, max_tokens
        self.steps = 0
        self.usage = Usage()

    @classmethod
    def from_env(cls) -> "Budget":
        return cls(int(os.getenv("MAX_STEPS_PER_ITEM", "15")), int(os.getenv("MAX_TOKENS_PER_ITEM", "300000")))

    def step(self, usage: Usage | None = None) -> None:
        self.steps += 1
        if usage:
            self.usage.input_tokens += usage.input_tokens
            self.usage.output_tokens += usage.output_tokens
            self.usage.cost_usd += usage.cost_usd
        total = self.usage.input_tokens + self.usage.output_tokens
        if self.steps > self.max_steps or total > self.max_tokens:
            raise BudgetExceeded("budget exceeded")


def rule_files(rules_dir: Path = config.RULES_DIR) -> list[str]:
    """Rulebooks available to cite: files in rules/ except the folder's own README and INDEX, and, when INDEX.md exists, only
    those a person has confirmed there (a row marked PENDING is not citable)."""
    from .rules_index import citable_files
    return citable_files(rules_dir)


def build_search_query(n: Normalized) -> str:
    """Web discovery sees only product type and key specs (1.4). Nothing from the inventory row."""
    return " ".join(x for x in (n.product_type, n.specs_text) if x).strip()


def valid_rule_refs(refs: list[str], available: list[str]) -> list[str]:
    """Keep only `file#section` refs that point at a real file in rules/."""
    return [r for r in refs if "#" in r and r.split("#", 1)[0] in available and r.split("#", 1)[1]]


VERDICT_CAP = {"ALLOWED": 0.75, "NOT_ALLOWED": 0.75, "NEEDS_DOCUMENTS": 0.65, "UNKNOWN": 0.40}  # ALLOWED/NOT_ALLOWED stay below 0.8: site fault levels, approvals and exact stock models are never fully known


def confidence_breakdown(norm_conf: float, verdict: str, fit_status: str, n_candidates: int, verifier_rejected: bool) -> tuple[float, str]:
    """Confidence in the VERDICT, not in the normalization. Starts at the normalization confidence and is capped by how much
    evidence the verdict rests on: UNKNOWN at most 0.40, NEEDS_DOCUMENTS 0.65, ALLOWED 0.75. No sourced candidate, a NO_FIT
    or a verifier rejection of a core claim each lower it further. Returns the value and a plain-language reason for every step."""
    parts = [f"normalization {norm_conf:.2f}"]
    c = norm_conf
    cap = VERDICT_CAP.get(verdict, 0.40)
    if c > cap:
        c = cap
        parts.append(f"capped at {cap:.2f} because the verdict is {verdict} (the cap reflects how much evidence that verdict can rest on)")
    if n_candidates == 0:
        c *= 0.7
        parts.append("x0.7: no sourced producer candidate")
    if fit_status == "NO_FIT":
        c *= 0.7
        parts.append("x0.7: best candidate is NO_FIT")
    if verifier_rejected:
        c *= 0.8
        parts.append("x0.8: the verifier rejected a core claim")
    c = round(c, 2)
    return c, f"Confidence {c:.2f} = " + "; ".join(parts) + "."


def final_confidence(norm_conf: float, verdict: str, fit_status: str, n_candidates: int, verifier_rejected: bool) -> float:
    return confidence_breakdown(norm_conf, verdict, fit_status, n_candidates, verifier_rejected)[0]


def unique(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def enforce_verdict(verdict: str, rule_refs: list[str], supplier_urls: list[str]) -> tuple[str, str]:
    """ALLOWED needs a cited rule AND a supplier document (1.3). Otherwise downgrade."""
    if verdict not in config.VERDICTS:
        return "UNKNOWN", f"invalid verdict '{verdict}' replaced"
    if verdict == "ALLOWED" and not (rule_refs and supplier_urls):
        return "UNKNOWN", "ALLOWED downgraded: missing rule reference or supplier document"
    return verdict, ""


def fit_check(item_specs: str, candidates: list[Candidate]) -> tuple[str, str, list[tuple[str, str, list[SpecRow]]]]:
    """Step 4 in code. The overall status is the best status across candidates."""
    if not candidates:
        return "UNKNOWN", "No sourced producer candidate to compare against.", []
    order = ["FIT", "PARTIAL", "UNKNOWN", "NO_FIT"]
    tables, statuses = [], []
    for c in candidates:
        status, rows = compare(item_specs, c.specs_text)
        tables.append((c.producer, status, rows))
        statuses.append(status)
    best = min(statuses, key=order.index)
    notes = "; ".join(f"{p}: {s}" for p, s, _ in tables)
    unchecked = text_only_tokens(item_specs)
    if unchecked:
        notes += f". Not compared in code (non-numeric): {', '.join(unchecked)}"
    return best, notes, tables


def run_item(item: dict, models: Models, budget: Budget | None = None, progress=None) -> dict:
    """Process one item. `progress(step_name, budget)` is called as each step starts (for the app UI)."""
    budget = budget or Budget.from_env()
    say = progress or (lambda *_: None)
    verifier_rejected = False
    notes: list[str] = []
    category = item["Category"]
    open_questions: list[str] = []

    say("Load and validate", budget)
    # 1. load and validate: unit conversion in code, every conversion recorded
    conversions: list[Conversion] = []
    spec_text, c1 = normalize_units(item["Key_Specs"])
    desc_text, c2 = normalize_units(item["Internal_Description"])
    conversions = c1 + c2
    budget.step()

    n = Normalized(canonical_name=item["Internal_Description"], product_type=item["Internal_Description"],
                   category=category, specs_text=spec_text, confidence=0.0)
    candidates: list[Candidate] = []
    comp = Compliance()
    verdict, fit_status, fit_notes, fit_tables = "UNKNOWN", "UNKNOWN", "", []
    verifier_notes = ""
    search_method = getattr(models, "search_method", None) or ("none (mock)" if getattr(models, "name", "") == "mock" else "unspecified")
    rules_available = rule_files()

    try:
        # 2. normalize
        say("Normalize", budget)
        n, usage = models.normalize(item, spec_text)
        budget.step(usage)
        if n.too_vague:
            verdict = "UNKNOWN"
            notes.append(f"Row too vague to process: {n.vague_reason}")
            open_questions.append("Split or describe the row so one product is identified.")
        else:
            # 3. discover producers (minimal fields only)
            say("Discover producers", budget)
            query = build_search_query(n)
            candidates, usage = models.discover(query)
            budget.step(usage)
            candidates = [c for c in candidates if c.source_url.strip()]  # no source, no producer
            if not candidates:
                open_questions.append("No sourced producer candidates found.")

            # 4. fit check, in code
            say("Fit check", budget)
            fit_status, fit_notes, fit_tables = fit_check(n.specs_text, candidates)
            budget.step()

            # 5. compliance (Dubai, English rulebooks in rules/ only)
            say("Compliance check", budget)
            if not rules_available:
                notes.append("No rulebooks in rules/: Dubai English rule source missing.")
                open_questions.append("Add the English Dubai rulebooks to rules/ (see rules/README.md).")
            comp, usage = models.assess_compliance(n, candidates, rules_available)
            budget.step(usage)
            refs = valid_rule_refs(comp.rule_refs, rules_available)
            dropped = len(comp.rule_refs) - len(refs)
            if dropped:
                notes.append(f"{dropped} rule reference(s) dropped: not file#section from rules/.")
            comp.rule_refs = refs

            # 6. verify (separate pass)
            say("Verify", budget)
            ver, usage = models.verify(n, candidates, comp)
            budget.step(usage)
            verifier_notes = ver.notes
            if ver.rejected_claims:
                # Only a rejected CORE claim (the rule applies, the parameters meet it, the document states them) lowers ALLOWED.
                # A rejected side detail is removed from the conclusion and shown to the reviewer; the verdict stands.
                core = ver.core_rejected if ver.core_rejected is not None else ver.rejected_claims
                side = [c for c in ver.rejected_claims if c not in core]
                if ver.corrected_notes:
                    comp.notes = ver.corrected_notes
                if side:
                    notes.append("Verifier removed side detail(s) it could not support: " + " ; ".join(side))
                if core:
                    verifier_rejected = True
                    notes.append(f"Verifier rejected {len(core)} core claim(s): " + " ; ".join(core))
                    comp.verdict = "UNKNOWN" if comp.verdict == "ALLOWED" else comp.verdict

            verdict, why = enforce_verdict(comp.verdict, comp.rule_refs, comp.supplier_urls)
            if why:
                notes.append(why)
            if comp.notes:
                notes.append(comp.notes)
    except BudgetExceeded:
        verdict = "UNKNOWN"
        notes.append("budget exceeded")
        open_questions.append("Budget exceeded: confirm before continuing this item.")

    confidence, why_confidence = confidence_breakdown(n.confidence, verdict, fit_status, len(candidates), verifier_rejected)
    notes.append(why_confidence)
    safety = config.is_safety_critical(category, config.item_safety_text(item))
    if safety:
        notes.append("Safety-critical: a person must decide.")
    # a model-assigned category (e.g. for manual items) may itself be safety-critical
    if not safety and config.is_safety_critical(n.category, n.canonical_name):
        safety = True
        notes.append("Safety-critical: a person must decide.")
    if getattr(models, "name", "") == "mock":
        notes.append("MOCK RUN: no model or web call was made.")

    opened = list(getattr(models, "opened_documents", []) or [])
    if opened:
        notes.append(f"{len(opened)} source document(s) opened and read.")
    row = {
        "item_id": item["Item_ID"],
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "category": n.category or category,
        "normalized_name": n.canonical_name,
        "producer_candidates": unique(c.producer for c in candidates),
        "matched_producer": "",
        "alias_notes": "; ".join(c.alias_note for c in candidates if c.alias_note),
        "fit_status": fit_status,
        "fit_notes": fit_notes,
        "dubai_rule_refs": comp.rule_refs,
        "compliance_verdict": verdict,
        "confidence": f"{confidence:.2f}",
        "evidence_urls": [c.source_url for c in candidates] + comp.supplier_urls,
        "human_decision": "pending",  # only a person changes this (1.5)
        "notes": " | ".join(notes),
    }
    report = render_report(item, n, conversions, candidates, fit_status, fit_tables, comp, verifier_notes,
                           verdict, open_questions, budget, search_method, rules_available, notes, opened)
    say("Write results", budget)
    return {"row": row, "report": report, "safety": safety, "open_questions": open_questions,
            "candidates": [c.model_dump() for c in candidates], "normalized": n.model_dump(), "fit_status": fit_status,
            "opened_documents": list(getattr(models, "opened_documents", []) or []),
            "verifier_rejected": verifier_rejected, "budget_exceeded": "budget exceeded" in notes,
            "usage": budget.usage, "steps": budget.steps}


def skip_row(item: dict) -> dict:
    return {
        "item_id": item["Item_ID"],
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "category": item["Category"],
        "normalized_name": item["Internal_Description"],
        "fit_status": "UNKNOWN",
        "compliance_verdict": "UNKNOWN",
        "confidence": "0.00",
        "human_decision": "skipped",
        "notes": "Skipped by user; not processed.",
    }


def render_report(item, n, conversions, candidates, fit_status, fit_tables, comp, verifier_notes,
                  verdict, open_questions, budget, search_method, rules_available, notes, opened=()) -> str:
    L = [f"# {item['Item_ID']}: {n.canonical_name}", ""]
    L += ["## Original row", ""] + [f"- {k}: {v}" for k, v in item.items()] + [""]
    L += ["## Normalization", "", f"- Canonical name: {n.canonical_name}", f"- Category: {n.category}",
          f"- Specs after unit normalization: {n.specs_text or '(none)'}", f"- Confidence: {n.confidence:.2f}",
          "- Unit conversions:"]
    L += [f"  - {c}" for c in conversions] or ["  - none"]
    L += ["", "## Producer candidates and sources", ""]
    L += [f"- {c.producer} ({c.source_url})" + (f" - {c.alias_note}" if c.alias_note else "") for c in candidates] \
        or ["- none found"]
    L += [f"- Search method: {search_method}"]
    L += [f"- Document opened and read: {d['url']} ({d['kind']}, {d['chars']} characters, via {d.get('via', 'search result')})" for d in opened] or ["- No source document was opened; candidates rest on search snippets."]
    L += ["", "## Fit table", "", f"Overall: {fit_status}", ""]
    if fit_tables:
        L += ["| Candidate | Unit | Wanted | Found | Result |", "|---|---|---|---|---|"]
        for producer, status, rows in fit_tables:
            L += [f"| {producer} | {r.unit} | {r.wanted} | {r.found} | {r.result} |" for r in rows] \
                or [f"| {producer} | - | - | - | {status} |"]
    else:
        L += ["No candidate specs to compare."]
    L += ["", "## Rules checked", ""]
    L += [f"- {r}" for r in comp.rule_refs] or [f"- none cited (rules/ holds {len(rules_available)} rulebook file(s))"]
    L += ["", "## Verification notes", "", verifier_notes or "(not run)", "", "## Final verdict", "",
          f"**{verdict}** (human_decision: pending)", ""]
    if notes:
        L += [f"Notes: {' | '.join(notes)}", ""]
    L += ["## Open questions for the human", ""] + ([f"- {q}" for q in open_questions] or ["- none"])
    u = budget.usage
    L += ["", "## Run log", "", f"- Steps: {budget.steps}", f"- Tokens in/out: {u.input_tokens}/{u.output_tokens}",
          f"- Estimated cost: ${u.cost_usd:.2f}", ""]
    return "\n".join(L)
