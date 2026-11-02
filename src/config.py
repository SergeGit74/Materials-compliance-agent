"""Project configuration: scope rules, category lists and .env loading (README sections 1, 8)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RULES_DIR = ROOT / "rules"
RESULTS_DIR = ROOT / "results"
ITEMS_DIR = RESULTS_DIR / "items"
CACHE_DIR = RESULTS_DIR / "cache"

INVENTORY_CSV = DATA_DIR / "inventory_sample.csv"
MANUAL_ITEMS_CSV = DATA_DIR / "manual_items.csv"
RESULTS_CSV = RESULTS_DIR / "results.csv"

# --- Scope rules (section 1.2): not configurable ---
RULES_LANGUAGE = "en"
RULES_JURISDICTION = "dubai"
RULES_EMIRATE = "Dubai"

# Anything outside this scope must yield UNKNOWN, never a guess.
OUT_OF_SCOPE_SOURCES = (
    "Arabic-language versions",
    "other emirates",
    "federal UAE rules",
    "rules not stored in rules/",
)

# --- Verdicts and statuses (section 3) ---
VERDICTS = ("ALLOWED", "NOT_ALLOWED", "NEEDS_DOCUMENTS", "UNKNOWN")
FIT_STATUSES = ("FIT", "PARTIAL", "NO_FIT", "UNKNOWN")
HUMAN_DECISIONS = ("pending", "approved", "rejected", "skipped")  # skipped: set by the CLI skip command

RESULTS_COLUMNS = (
    "item_id,processed_at,category,normalized_name,producer_candidates,matched_producer,"
    "alias_notes,fit_status,fit_notes,dubai_rule_refs,compliance_verdict,confidence,"
    "evidence_urls,human_decision,notes"
).split(",")

INVENTORY_COLUMNS = (
    "Item_ID,Category,Internal_Description,Part_Number,Manufacturer_Listed,Equipment_Served,"
    "Site_Location,Qty,Unit,Key_Specs,Last_Purchase_Date,Stock_Status"
).split(",")

# --- Categories ---
# Categories present in the sample inventory.
INVENTORY_CATEGORIES = (
    "Electrical", "Fire & Life Safety", "HVAC", "Plumbing & Pumps", "Building Materials",
    "Lifts & Escalators", "ELV & BMS", "Safety & PPE", "General",
)

# Safety-critical (section 1.5): human_decision stays "pending" whatever the verdict.
SAFETY_CRITICAL_CATEGORIES = (
    "Fire & Life Safety",
    "Lifts & Escalators",
)
SAFETY_CRITICAL_KEYWORDS = (
    "fire", "sprinkler", "smoke", "alarm", "extinguisher", "life safety",
    "lift", "elevator", "escalator",
    "emergency light", "structural", "cladding", "composite panel", "facade",
    "harness", "fall arrest", "work at height", "scaffold", "lanyard",
    "gas detector", "gas detection",
)

# --- Required .env variables (section 8.2) ---
REQUIRED_ENV = (
    "ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "MODEL_NORMALIZE", "MODEL_REASON", "MODEL_VERIFY",
    "VOYAGE_EMBED_MODEL", "RULES_LANGUAGE", "RULES_JURISDICTION",
    "MAX_STEPS_PER_ITEM", "MAX_TOKENS_PER_ITEM",
)
OPTIONAL_ENV = ("SERPER_API_KEY",)


def item_safety_text(item: dict) -> str:
    """Description plus the equipment it serves, so 'Fire/Domestic main' or 'Cladding' count."""
    return f"{item.get('Internal_Description', '')} {item.get('Equipment_Served', '')}".strip()


def is_safety_critical(category: str = "", text: str = "") -> bool:
    if category in SAFETY_CRITICAL_CATEGORIES:
        return True
    low = text.lower()
    return any(k in low for k in SAFETY_CRITICAL_KEYWORDS)


def missing_env() -> list[str]:
    """Names (never values) of required variables that are empty, plus scope violations."""
    load_dotenv(ROOT / ".env")
    missing = [n for n in REQUIRED_ENV if not os.getenv(n, "").strip()]
    if os.getenv("RULES_LANGUAGE", "").strip() != RULES_LANGUAGE:
        missing.append("RULES_LANGUAGE (must be en)")
    if os.getenv("RULES_JURISDICTION", "").strip() != RULES_JURISDICTION:
        missing.append("RULES_JURISDICTION (must be dubai)")
    return missing
