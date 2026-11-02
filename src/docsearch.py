"""Find and fetch the rulebooks the inventory items would need (README 1.2: Dubai-issued, English only).

Three steps, kept apart on purpose:
  1. init      builds rules_staging/needed_rulebooks.json from the items and results. No network.
  2. search    asks Serper for candidate URLs. PAID (credits). Needs --yes and is never called by tests or by init.
  3. download  fetches PDFs and HTML pages over https from allowlisted domains into rules_staging/downloads/ (HTML is saved as
               Markdown text). Free, no API credits.

Nothing here writes to rules/ or data/. A downloaded file is only a candidate: a person checks it, copies it into
rules/ and adds a row to rules/INDEX.md (with 'Confirmed by'). Until then it is not compliance evidence.
Web content is untrusted data: only titles, links and short snippets are kept, and no instruction in them is followed.
Search queries are fixed text in this file (regulation topics). They never contain site names, asset codes, prices or stock.
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
from html.parser import HTMLParser
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from . import config, inventory, store

STAGING_DIR = config.ROOT / "rules_staging"
DOWNLOAD_DIR = STAGING_DIR / "downloads"
PLAN_PATH = STAGING_DIR / "needed_rulebooks.json"
INDEX_MD = config.RULES_DIR / "INDEX.md"

# Hosts the downloader may fetch from. Dubai issuers are tried first. Federal UAE hosts are a fallback tier (see run_downloads):
# those files are fetched into rules_staging/ labelled "UAE federal" and do NOT count as evidence under README 1.2 unless the
# project owner changes that rule. Abu Dhabi, Sharjah and other emirates' hosts are not on either list.
DEFAULT_ALLOWED_DOMAINS = ["dewa.gov.ae", "dm.gov.ae", "dcd.gov.ae", "sira.gov.ae", "buildindubai.gov.ae", "dlp.dubai.gov.ae"]
DEFAULT_FEDERAL_DOMAINS = ["u.ae", "moei.gov.ae", "moi.gov.ae", "moiat.gov.ae", "mohre.gov.ae", "uaelegislation.gov.ae", "cabinet.gov.ae"]
FEDERAL_NOTE = ("UAE FEDERAL source, not Dubai-issued: does not count as evidence under README 1.2 unless the project owner "
                "amends that rule. Fetched only because no Dubai document was downloaded (or --federal was asked for).")
MAX_BYTES = 200 * 1024 * 1024  # the UAE Fire and Life Safety Code PDF is 145 MB
MIN_HTML_CHARS = 1500  # a page with less text is a login wall, an error page or a menu, not a rulebook
MAX_DOWNLOAD_TRIES = 3

# Serper billing, from third-party sources (not Serper's own page, which was unreachable): 1 credit for up to 10
# results per query, 2 credits for 11 to 100 results; $50 buys 50,000 credits; new accounts get 2,500 free credits.
CREDITS_PER_QUERY = 1
USD_PER_CREDIT = 0.001
FREE_CREDITS = 2500
RESULTS_PER_QUERY = 10

SCOPE = {"language": "en", "jurisdiction": "Dubai",
         "note": "Federal UAE, other-emirate and Arabic documents do not count as evidence (README 1.2)."}

# Second search round. Same rules as the first: fixed regulation-topic text, never site names, asset codes, part numbers, prices or stock.
# Not limited to PDF: web pages count too. Queries use site: to stay on the issuers on the allowlists. Dubai issuers first; a federal
# query is added only where Dubai may publish nothing, and its results are fetched only as a fallback (see run_downloads).
ROUND2_QUERIES: dict[str, list[str]] = {
    "N01": ["site:dcd.gov.ae fire and life safety requirements for buildings",
            "site:dm.gov.ae fire safety requirements buildings guideline",
            "UAE fire and life safety code of practice site:moi.gov.ae",
            "UAE fire and life safety code site:u.ae"],
    "N02": ["site:dm.gov.ae elevators escalators technical guideline",
            "site:dlp.dubai.gov.ae lifts elevators escalators regulation",
            "lifts elevators escalators safety requirements site:u.ae"],
    "N03": ["site:dm.gov.ae plumbing drainage regulations technical guideline",
            "site:buildindubai.gov.ae plumbing sanitary drainage requirements"],
    "N04": ["site:dewa.gov.ae water meter specification approved",
            "site:dewa.gov.ae regulations for water installations plumbing"],
    "N06": ["site:dm.gov.ae personal protective equipment occupational safety guideline",
            "site:dm.gov.ae work at height safety guideline",
            "occupational safety and health personal protective equipment requirements site:mohre.gov.ae",
            "site:dm.gov.ae technical guideline safety helmets head protection",
            "site:dm.gov.ae technical guideline personal protective equipment selection and use",
            "site:dm.gov.ae technical guideline safety harness lanyard fall protection",
            "site:dm.gov.ae technical guideline gas detection atmospheric testing confined space",
            "site:dm.gov.ae Code of Construction Safety Practice technical guidelines"],
    "N08": ["site:dm.gov.ae refrigerant HVAC gas requirements",
            "ozone depleting substances refrigerants regulation site:moei.gov.ae"],
    "N09": ["site:dm.gov.ae building materials approval requirements",
            "site:dm.gov.ae construction materials product approval waterproofing"],
    "N10": ["site:dcd.gov.ae external cladding fire safety requirements",
            "site:dm.gov.ae external wall cladding regulation"],
    "N11": ["site:dewa.gov.ae approved materials list electrical equipment",
            "site:dewa.gov.ae material approval type test requirements"],
    "N13": ["site:dm.gov.ae hazardous chemicals storage guideline",
            "hazardous chemicals storage handling regulation site:moei.gov.ae"],
}
# Rulebooks already promoted into rules/ (file name). They are not searched for again.
PROMOTED: dict[str, str] = {
    "N05": "dubai-municipality_green-building-regulations-and-specifications_v2026-11.pdf",
    "N07": "sira_security-systems-technical-specifications_v2022-02.pdf",
}

# --- What each rulebook is, why it is needed and which queries look for it ---------------------------------------
# status_in_rules: present_ok | present_unreadable | missing
RULEBOOKS: list[dict] = [
    dict(id="R01", title="DEWA Regulations for Electrical Installations 2017", file="dewa_electricity-installations-regulations_v2022-09.pdf",
         status_in_rules="present_ok", issuer_hint="DEWA", why="Already in rules/. Covers electrical installations only.", queries=[]),
    dict(id="R02", title="Dubai Municipality structural codes and regulations guideline (English edition)",
         file="dubai-municipality_structural-codes-and-regulations_v2026-04.pdf", status_in_rules="present_unreadable",
         issuer_hint="Dubai Municipality",
         why="The copy in rules/ has an unreadable (Arabic) text layer, so it cannot be used. An English edition is needed for rebar, facade and structural items.",
         queries=["Dubai Municipality structural codes and regulations guidelines English pdf",
                  "Dubai structural design code reinforcement requirements English pdf"]),
    dict(id="R03", title="Dubai Seismic Design Code (clean English copy)", file="dubai-municipality_seismic-design-code_v2026-04.pdf",
         status_in_rules="present_unreadable", issuer_hint="Dubai Municipality",
         why="The copy in rules/ loses digits and spaces in its text layer, so clause numbers, factors and the tall-building height cannot be read.",
         queries=["Dubai seismic design code for buildings English pdf Dubai Municipality 2009"]),
    dict(id="R04", title="Dubai Wind Code", file="dubai-municipality_dubaiwindcode6-2_v2026-04.pdf", status_in_rules="present_ok",
         issuer_hint="Dubai Municipality", why="Already in rules/. Readable. Used only for wind loads.", queries=[]),
    dict(id="N01", title="Dubai fire and life safety requirements (fire protection systems, escape lighting, fire stopping)",
         issuer_hint="Dubai Civil Defence (hint, unverified)", status_in_rules="missing",
         why="Sprinklers, detectors, call points, extinguishers, hose reels, fire doors, dampers, fire boards, firestop sealant and escape lighting all depend on it. None of it is in rules/.",
         scope_risk="The national UAE fire code is federal and out of scope; only a Dubai-issued English document counts.",
         queries=["Dubai Civil Defence fire and life safety requirements English pdf",
                  "Dubai Civil Defence fire protection systems approved products requirements pdf",
                  "Dubai fire code building fire and life safety English"]),
    dict(id="N02", title="Dubai lift and escalator safety, inspection and spare parts requirements",
         issuer_hint="issuer to be confirmed by the search", status_in_rules="missing",
         why="Lift and escalator parts (rollers, governor rope, control boards, handrails) have no governing rule in rules/.",
         queries=["Dubai lifts and escalators safety requirements inspection English pdf",
                  "Dubai elevator maintenance spare parts regulation English pdf",
                  "Dubai elevator escalator code of practice pdf"]),
    dict(id="N03", title="Dubai building plumbing, drainage and water supply regulations",
         issuer_hint="Dubai Municipality (hint)", status_in_rules="missing",
         why="Pipes, valves, pumps, seals, gauges and sump pumps have no governing rule in rules/.",
         queries=["Dubai Municipality plumbing drainage regulations English pdf",
                  "Dubai building plumbing code water supply drainage requirements pdf",
                  "Dubai Municipality sanitary drainage pipe materials approved pdf"]),
    dict(id="N04", title="DEWA water services regulations and water meter / material specifications",
         issuer_hint="DEWA (hint)", status_in_rules="missing",
         why="Water meters, valves and potable pipes may need DEWA water approvals. rules/ holds only the electrical regulations.",
         queries=["DEWA regulations for water installations English pdf", "DEWA water meter technical specification pdf"]),
    dict(id="N05", title="Dubai Green Building Regulations and Specifications",
         issuer_hint="Dubai Municipality and DEWA (named in DEWA regulations section 12.1)", status_in_rules="missing",
         why="DEWA 12.1 points to these regulations for HVAC, lighting and insulation items but they are not in rules/. This is the one missing source the rulebook itself names.",
         queries=["Dubai Green Building Regulations and Specifications English pdf",
                  "Dubai Municipality green building regulations HVAC duct insulation requirements pdf"]),
    dict(id="N06", title="Dubai occupational safety and health: PPE, work at height, confined space and gas detection",
         issuer_hint="issuer to be confirmed by the search", status_in_rules="missing",
         why="Helmets, harnesses and gas detectors have no governing rule in rules/.",
         queries=["Dubai occupational safety and health personal protective equipment requirements English pdf",
                  "Dubai work at height confined space safety requirements pdf"]),
    dict(id="N07", title="Dubai security systems (CCTV and access control) requirements",
         issuer_hint="Security Industry Regulatory Agency (hint, unverified)", status_in_rules="missing",
         why="CCTV cameras and access control readers have no governing rule in rules/.",
         queries=["Dubai security systems CCTV requirements regulation English pdf",
                  "Dubai access control CCTV technical guidelines pdf"]),
    dict(id="N08", title="Dubai refrigerant and HVAC gas handling requirements",
         issuer_hint="issuer to be confirmed by the search", status_in_rules="missing",
         scope_risk="Refrigerant control is likely federal (out of scope); only a Dubai-issued English document counts.",
         why="Refrigerant and compressor oil items have no governing rule in rules/.",
         queries=["Dubai refrigerant handling requirements ozone depleting substances English pdf",
                  "Dubai HVAC refrigerant gas regulation pdf"]),
    dict(id="N09", title="Dubai building materials approval and quality requirements (waterproofing, adhesives, boards, sealants, steel)",
         issuer_hint="Dubai Municipality (hint)", status_in_rules="missing",
         why="Finishing and structural materials have no governing product rule in rules/.",
         queries=["Dubai Municipality building materials approval requirements English pdf",
                  "Dubai construction materials quality control requirements waterproofing tile adhesive pdf",
                  "Dubai Municipality approved construction materials list pdf"]),
    dict(id="N10", title="Dubai external wall cladding fire performance requirements",
         issuer_hint="Dubai Civil Defence / Dubai Municipality (hint, unverified)", status_in_rules="missing",
         why="The core and reaction to fire of cladding panels (INV-0044) has no governing rule in rules/.",
         queries=["Dubai external wall cladding fire safety requirements English pdf", "Dubai combustible cladding regulation pdf"]),
    dict(id="N11", title="DEWA product approval requirements for electrical equipment",
         issuer_hint="DEWA (hint)", status_in_rules="missing",
         why="rules/ names no approved-product list for breakers, cables or trays; if DEWA has one it would turn several NEEDS_DOCUMENTS into ALLOWED.",
         queries=["DEWA approved materials product approval requirements electrical equipment pdf",
                  "DEWA type approval circuit breakers cables approved list pdf"]),
    dict(id="N12", title="Referenced BS EN / IEC standards (BS EN 60947, BS 5467, BS 5266, BS EN 61439 and similar)",
         issuer_hint="BSI / IEC", status_in_rules="missing", paywalled=True,
         why="The DEWA regulations cite these standards. They are paid documents, so no automated search or download is tried.",
         queries=[]),
    dict(id="N13", title="Dubai hazardous substances and chemical storage requirements",
         issuer_hint="Dubai Municipality / Dubai Civil Defence (hint, unverified)", status_in_rules="missing",
         why="Transformer oil, compressor oil, refrigerant and sealants may carry storage rules outside rules/.",
         queries=["Dubai hazardous substances chemical storage handling regulation English pdf",
                  "Dubai Municipality storage of flammable liquids transformer oil requirements pdf"]),
]

# Item -> rulebook ids. [] means no specific rulebook is expected for that spare part or service item.
ITEM_MAP: dict[str, list[str]] = {
    "INV-0001": ["R01", "N11", "N12"], "INV-0002": ["R01", "N11", "N12"], "INV-0003": ["R01", "N11", "N12"], "INV-0004": ["R01", "R03", "N11"],
    "INV-0005": ["R01", "N05"], "INV-0006": ["R01", "N12"], "INV-0007": ["R01", "R03", "N01", "N12"], "INV-0008": ["R01", "N13"],
    "INV-0009": ["R01", "N05"], "INV-0010": ["N08", "N13"], "INV-0011": [], "INV-0012": ["N08", "N13"],
    "INV-0013": ["R01", "R03", "N05"], "INV-0014": ["R01", "N05"], "INV-0015": [], "INV-0016": ["R03", "N01"],
    "INV-0017": ["N01"], "INV-0018": ["N01"], "INV-0019": ["R03", "N01"], "INV-0020": ["R03", "N01"],
    "INV-0021": ["N01"], "INV-0022": ["N01"], "INV-0023": ["R03", "N01"], "INV-0024": ["N03"],
    "INV-0025": ["N03"], "INV-0026": ["N01", "N03", "N04"], "INV-0027": ["R03", "N03", "N04"], "INV-0028": ["R01", "N04"],
    "INV-0029": ["R01", "N03"], "INV-0030": ["R03", "N02"], "INV-0031": ["R03", "N02"], "INV-0032": ["R03", "N02"],
    "INV-0033": ["R03", "N02"], "INV-0034": ["N07"], "INV-0035": [], "INV-0036": ["R01", "N05"],
    "INV-0037": ["N07"], "INV-0038": ["R01", "N09"], "INV-0039": ["N09"], "INV-0040": ["R02", "R03", "N09"],
    "INV-0041": ["R03", "N01", "N09"], "INV-0042": ["R01", "N01", "N13"], "INV-0043": ["R02", "R03", "N09"],
    "INV-0044": ["R02", "R03", "N01", "N09", "N10"], "INV-0045": ["N06"], "INV-0046": ["N06"], "INV-0047": ["N06"],
    "INV-0048": ["R01", "N11", "N12"], "INV-0049": ["R03", "N03"], "INV-0050": [],
}


# --- helpers ---------------------------------------------------------------------------------------------------
def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_index(path: Path = INDEX_MD) -> dict[str, dict]:
    """File name -> {title, source_url} from the rules/INDEX.md table."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0].endswith(".pdf"):
            out[cells[0]] = {"title": cells[2], "source_url": cells[4]}
    return out


def kind_of(url: str) -> str:
    """'pdf' or 'web page', guessed from the URL (the real type is known only after the fetch)."""
    return "pdf" if re.search(r"\.pdf|\.ashx|/download|/doclib|/media/|/uploads/", url.lower()) else "web page"


def domain_ok(host: str | None, allowed: list[str]) -> bool:
    host = (host or "").lower()
    return any(host == d or host.endswith("." + d) for d in allowed)


def jurisdiction_of(host: str | None, plan: dict) -> str | None:
    """'Dubai', 'UAE federal' or None (host on neither list)."""
    if domain_ok(host, plan["allowed_domains"]):
        return "Dubai"
    if domain_ok(host, plan.get("federal_domains", [])):
        return "UAE federal"
    return None


def text_quality(text: str | None) -> str:
    """Is the text layer usable as English evidence? Catches Arabic copies and shifted-glyph copies."""
    if text is None:
        return "not checked (pdftotext not available)"
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 200:
        return "empty or image-only"
    letters = [c for c in chars if c.isalpha()]
    if letters and sum("؀" <= c <= "ۿ" for c in letters) / len(letters) > 0.3:
        return "arabic"
    if len(letters) / len(chars) < 0.5:
        return "unreadable text layer"
    words = re.findall(r"[a-z]{2,}", text.lower())
    common = {"the", "and", "of", "shall", "to", "in", "be", "for", "is", "a", "with", "or"}
    if not words or sum(w in common for w in words) / len(words) < 0.08:
        return "unreadable text layer"
    return "english text ok"


class _TextExtractor(HTMLParser):
    """Page text as Markdown-ish lines. Drops scripts, styles, menus, forms and comments (web content is untrusted data)."""
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "footer", "form", "button", "select", "head"}
    BLOCK = {"p", "div", "section", "article", "br", "tr", "ul", "ol", "table", "main", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif not self.skip:
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self.out.append("\n\n" + "#" * int(tag[1]) + " ")
            elif tag == "li":
                self.out.append("\n- ")
            elif tag in {"td", "th"}:
                self.out.append(" | ")
            elif tag in self.BLOCK:
                self.out.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif not self.skip and tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_to_markdown(raw: str) -> tuple[str, str]:
    """(title, text). Text only: no scripts, styles or links are kept."""
    ex = _TextExtractor()
    ex.feed(raw)
    text = htmllib.unescape("".join(ex.out))
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.split("\n")]
    out, blank = [], 0
    for ln in lines:
        blank = blank + 1 if not ln else 0
        if blank <= 1:
            out.append(ln)
    return " ".join(ex.title.split()), "\n".join(out).strip()


def looks_like_html(data: bytes, content_type: str) -> bool:
    head = data[:2048].lstrip().lower()
    return "html" in content_type.lower() or head.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def extract_text(pdf: Path, pages: int = 5) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-l", str(pages), "-layout", str(pdf), "-"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.decode("utf-8", errors="replace")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manual(reason: str) -> dict:
    return {"status": "manual_download_only", "reason": reason, "checked_at": now()}


# --- plan ------------------------------------------------------------------------------------------------------
def build_plan(previous: dict | None = None) -> dict:
    """Plan for all items. Keeps download and search results already recorded in a previous plan."""
    index = read_index()
    prev_rb = {r["id"]: r for r in (previous or {}).get("rulebooks", [])}
    results = {r["item_id"]: r for r in store.read_rows()}
    items = inventory.load_all()
    rulebooks = []
    for spec in RULEBOOKS:
        rb = json.loads(json.dumps(spec))  # deep copy
        rb.setdefault("scope_risk", None)
        rb.setdefault("paywalled", False)
        rb["language_required"], rb["jurisdiction"] = "en", "Dubai"
        rb["items"] = sorted(i for i, ids in ITEM_MAP.items() if rb["id"] in ids)
        known = index.get(rb.get("file", ""), {})
        rb["source_url"] = known.get("source_url") or None
        rb["source_url_origin"] = "rules/INDEX.md" if rb["source_url"] else None
        old = prev_rb.get(rb["id"], {})
        for k in ("searched", "candidates", "download", "download_federal"):
            if k in old:
                rb[k] = old[k]
        if "download" not in rb:
            if rb["paywalled"]:
                rb["download"] = _manual("paywalled standards: buy them from the standards body; no automated download")
            elif rb["source_url"]:
                rb["download"] = {"status": "not_attempted", "reason": "verified source URL from rules/INDEX.md; run: python -m src.docsearch download", "checked_at": now()}
            else:
                rb["download"] = _manual("no verified source URL yet; the automated search has not been run. If the search finds an allowlisted PDF it will be fetched, otherwise this stays manual")
        was = old.get("search", {}).get("status", "not_run")
        first = list(rb.pop("queries"))
        qs = first + [q for q in ROUND2_QUERIES.get(rb["id"], []) if q not in first]
        rb["search"] = {"queries": qs, "status": "not_needed" if rb["status_in_rules"] == "present_ok" else ("n/a" if rb["paywalled"] else was)}
        promoted = PROMOTED.get(rb["id"])
        rb["promoted_as"] = promoted if promoted and (config.RULES_DIR / promoted).exists() else None
        rulebooks.append(rb)
    item_rows = []
    for it in items:
        iid = it["Item_ID"]
        res = results.get(iid, {})
        ids = ITEM_MAP.get(iid, [])
        item_rows.append({
            "item_id": iid, "description": it["Internal_Description"], "category": it["Category"],
            "safety_critical": config.is_safety_critical(it["Category"], config.item_safety_text(it)),
            "verdict_now": res.get("compliance_verdict"),
            "rule_refs_cited": [x for x in (res.get("dubai_rule_refs") or "").split(";") if x],
            "needed_rulebooks": ids,
            "note": None if ids else "No specific rulebook expected: spare part, generic ICT item or an unidentifiable lot.",
        })
    plan = {
        "version": 1, "generated_at": now(), "scope": SCOPE,
        "how_to_promote": ("A downloaded file stays in rules_staging/downloads/. A person checks language, jurisdiction and edition, copies it into rules/, "
                           "and adds a row to rules/INDEX.md with the 'Confirmed by' column filled. Only then does it count as evidence."),
        # lists are merged with the defaults so a plan made before a default was added picks it up
        "allowed_domains": sorted(set((previous or {}).get("allowed_domains", [])) | set(DEFAULT_ALLOWED_DOMAINS)),
        "federal_domains": sorted(set((previous or {}).get("federal_domains", [])) | set(DEFAULT_FEDERAL_DOMAINS)),
        "search": {"provider": "serper", "results_per_query": RESULTS_PER_QUERY, "status": (previous or {}).get("search", {}).get("status", "not_run")},
        "rulebooks": rulebooks, "items": item_rows,
    }
    plan["search"]["estimate"] = estimate_cost(plan)
    return plan


def load_plan(path: Path = PLAN_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_plan(plan: dict, path: Path = PLAN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pending_queries(plan: dict) -> list[tuple[str, str]]:
    out = []
    for rb in plan["rulebooks"]:
        if rb.get("paywalled") or rb["status_in_rules"] == "present_ok":
            continue
        if rb.get("promoted_as"):
            continue
        dl = rb.get("download", {})
        if dl.get("status") == "document downloaded" and rb["status_in_rules"] == "missing" and not dl.get("relevance_note"):
            continue  # a downloaded file with a relevance note is a weak match, so the search for a better source goes on
        done = rb.get("searched", {})
        out += [(rb["id"], q) for q in rb["search"]["queries"] if q not in done]
    return out


def estimate_cost(plan: dict, results_per_query: int | None = None) -> dict:
    """Cost of the searches still to run. Downloads are plain HTTP and cost no API credits. No LLM is used."""
    n = len(pending_queries(plan))
    per = CREDITS_PER_QUERY if (results_per_query or RESULTS_PER_QUERY) <= 10 else 2
    credits = n * per
    worst = n * 2
    return {"queries": n, "credits_per_query": per, "credits": credits, "usd": round(credits * USD_PER_CREDIT, 3),
            "worst_case_credits": worst, "worst_case_usd": round(worst * USD_PER_CREDIT, 3),
            "free_credits_on_new_account": FREE_CREDITS, "covered_by_free_credits": worst <= FREE_CREDITS,
            "llm_cost": "none: this code makes no model call",
            "basis": "Serper: 1 credit per query up to 10 results, 2 credits for 11 to 100; $50 per 50,000 credits. Third-party figures, not confirmed on Serper's site."}


# --- search (PAID) ---------------------------------------------------------------------------------------------
class SerperClient:
    URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self._key, self._http = api_key, http or httpx.Client(timeout=30)

    def search(self, query: str, num: int = RESULTS_PER_QUERY) -> list[dict]:
        r = self._http.post(self.URL, headers={"X-API-KEY": self._key, "Content-Type": "application/json"},
                            json={"q": query, "num": num, "gl": "ae", "hl": "en"})
        r.raise_for_status()
        return [{"title": str(o.get("title", ""))[:200], "url": str(o.get("link", "")), "snippet": str(o.get("snippet", ""))[:300]}
                for o in r.json().get("organic", [])]


def client_from_env() -> SerperClient:
    load_dotenv(config.ROOT / ".env")
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        raise RuntimeError("SERPER_API_KEY is not set in .env")  # names only, never values
    return SerperClient(key)


# What a failed search call usually means (HTTP status from the Serper API; Serper's own docs were not checked).
SEARCH_ERRORS = {400: "bad request: the query text or parameters were rejected", 401: "the API key was rejected or is missing", 403: "the key is not allowed to use this endpoint",
                 402: "out of credits: top up the account", 429: "rate limit or credits used up: wait and retry", 500: "search service error: retry later", 503: "search service unavailable: retry later"}


def run_search(plan: dict, client, *, confirm: bool, max_queries: int, budget_credits: int) -> dict:
    """Run pending queries. Refuses without confirm. Stops at max_queries or the credit budget."""
    if not confirm:
        raise PermissionError("Search spends API credits. Pass confirm=True (CLI: --yes) after reading the estimate.")
    done = spent = 0
    allowed = plan["allowed_domains"]
    for rb_id, q in pending_queries(plan):
        if done >= max_queries or spent + CREDITS_PER_QUERY > budget_credits:
            break
        rb = next(r for r in plan["rulebooks"] if r["id"] == rb_id)
        try:
            hits = client.search(q)
        except httpx.HTTPStatusError as e:  # the message carries the status code only, never the key or the request headers
            plan["search"]["last_error"] = {"at": now(), "query": q, "status": e.response.status_code, "meaning": SEARCH_ERRORS.get(e.response.status_code, "unexpected response from the search API")}
            break
        except httpx.HTTPError as e:
            plan["search"]["last_error"] = {"at": now(), "query": q, "status": None, "meaning": f"network problem: {type(e).__name__}"}
            break
        done, spent = done + 1, spent + CREDITS_PER_QUERY
        rb.setdefault("searched", {})[q] = {"at": now(), "hits": len(hits)}
        cands = rb.setdefault("candidates", [])
        for h in hits:
            host = urlparse(h["url"]).hostname
            if h["url"] and all(c["url"] != h["url"] for c in cands):
                cands.append({**h, "domain": host, "allowlisted": domain_ok(host, allowed),
                              "federal_listed": domain_ok(host, plan.get("federal_domains", [])),
                              "tier": jurisdiction_of(host, plan) or "other (not fetched)", "kind": kind_of(h["url"]), "query": q})
        rb["search"]["status"] = "run"
    plan["search"]["status"] = "run" if not pending_queries(plan) else "partly_run"
    plan["search"]["last_run"] = {"at": now(), "queries": done, "credits_spent": spent}
    plan["search"]["estimate"] = estimate_cost(plan)
    return plan


# --- download (free) -------------------------------------------------------------------------------------------
def download(url: str, allowed: list[str], dest_dir: Path = DOWNLOAD_DIR, http: httpx.Client | None = None,
             max_bytes: int = MAX_BYTES) -> dict:
    """Fetch one PDF. Returns a download record: status 'document downloaded' or 'manual_download_only' with a reason."""
    p = urlparse(url)
    if p.scheme != "https":
        return _manual("only https URLs are fetched")
    if not domain_ok(p.hostname, allowed):
        return _manual(f"domain {p.hostname} is not on the allowlist; review it, add it to allowed_domains, or download by hand")
    own = http is None
    http = http or httpx.Client(follow_redirects=True, timeout=60, headers={"User-Agent": "materials-compliance-agent/1.0"})
    data = bytearray()
    ctype = ""
    try:
        with http.stream("GET", url) as r:
            ctype = r.headers.get("content-type", "")
            if r.status_code != 200:
                return _manual(f"HTTP {r.status_code}")
            if not domain_ok(r.url.host, allowed):
                return _manual(f"redirected to {r.url.host}, which is not on the allowlist")
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > max_bytes:
                    return _manual(f"file is larger than {max_bytes // (1 << 20)} MB")
    except httpx.HTTPError as e:
        return _manual(f"network error: {type(e).__name__}")
    finally:
        if own:
            http.close()
    if not bytes(data[:5]).startswith(b"%PDF"):
        if not looks_like_html(bytes(data), ctype):
            return _manual("response is neither a PDF nor an HTML page")
        if p.path.lower().endswith(".pdf"):
            return _manual("the URL ends in .pdf but the server returned an HTML page (moved or removed file)")
        return _save_html(url, bytes(data), dest_dir)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(p.path).name or "document.pdf")
    name = name if name.lower().endswith(".pdf") else name + ".pdf"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    dest.write_bytes(bytes(data))
    quality = text_quality(extract_text(dest))
    if quality == "empty or image-only":  # a long document can open with cover images; look at the whole text layer before judging
        quality = text_quality(extract_text(dest, pages=5000))
    note = "document downloaded"
    if quality != "english text ok":
        note += f"; NOT usable as evidence yet: text layer is {quality}"
    return {"status": "document downloaded", "note": note, "url": url, "path": str(dest.relative_to(config.ROOT)) if dest.is_relative_to(config.ROOT) else str(dest),
            "bytes": len(data), "sha256": sha256(dest), "text_quality": quality, "downloaded_at": now()}


def _try(urls: list[str], allowed: list[str], dest_dir: Path, http, jurisdiction: str) -> dict | None:
    result = None
    for u in _document_first(urls)[:MAX_DOWNLOAD_TRIES]:
        result = download(u, allowed, dest_dir, http)
        if result["status"] == "document downloaded":
            result["jurisdiction"] = jurisdiction
            if jurisdiction != "Dubai":
                result["note"] += "; " + FEDERAL_NOTE
            break
    return result


def _save_html(url: str, data: bytes, dest_dir: Path) -> dict:
    """Keep the readable text of an HTML page as Markdown, with its source on top. Rejects pages with too little text."""
    raw = data.decode("utf-8", errors="replace")
    title, text = html_to_markdown(raw)
    quality = text_quality(text)
    if len(text) < MIN_HTML_CHARS:
        return _manual(f"HTML page has too little text ({len(text)} characters): likely a menu, login wall or error page")
    p = urlparse(url)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (p.hostname or "") + p.path).strip("_")[:120] or "page"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (slug + ".md")
    header = (f"<!-- Source: {url} | Retrieved: {now()} | Converted from HTML to text by src/docsearch.py: scripts, menus and links are dropped. "
              f"Web content is untrusted data. A person must check it before it goes to rules/. -->\n\n# {title or slug}\n\n")
    dest.write_text(header + text + "\n", encoding="utf-8")
    note = "document downloaded (HTML page saved as Markdown)"
    if quality != "english text ok":
        note += f"; NOT usable as evidence yet: text is {quality}"
    return {"status": "document downloaded", "note": note, "format": "html-to-markdown", "title": title, "url": url,
            "path": str(dest.relative_to(config.ROOT)) if dest.is_relative_to(config.ROOT) else str(dest),
            "bytes": dest.stat().st_size, "sha256": sha256(dest), "text_quality": quality, "downloaded_at": now()}


def _document_first(urls: list[str]) -> list[str]:
    """PDF-like links before plain pages, so a menu page does not use up the tries."""
    return sorted(urls, key=lambda u: 0 if re.search(r"\.pdf|\.ashx|/download|/doclib|/media/", u.lower()) else 1)


def run_downloads(plan: dict, only: str | None = None, http: httpx.Client | None = None, dest_dir: Path = DOWNLOAD_DIR,
                  federal: bool = False) -> dict:
    """Dubai tier first. The federal tier runs for a rulebook only if the Dubai tier produced no document, or federal=True."""
    dubai, fed = plan["allowed_domains"], plan.get("federal_domains", [])
    for rb in plan["rulebooks"]:
        if only and rb["id"] != only:
            continue
        if rb.get("paywalled"):
            continue
        cands = rb.get("candidates", [])
        if rb["download"].get("status") != "document downloaded":
            urls = [rb["source_url"]] if rb.get("source_url") else []
            urls += [c["url"] for c in cands if domain_ok(c.get("domain"), dubai)]
            if urls:
                rb["download"] = _try(urls, dubai, dest_dir, http, "Dubai")
                # same bytes as the copy already in rules/?
                result = rb["download"]
                local = config.RULES_DIR / rb["file"] if rb.get("file") else None
                if result["status"] == "document downloaded" and local is not None and local.exists():
                    same = sha256(local) == result["sha256"]
                    result["same_as_rules_copy"] = same
                    result["note"] += "; identical to the copy in rules/" if same else "; differs from the copy in rules/"
        if rb["download"].get("status") == "document downloaded" and not federal:
            continue
        if rb.get("download_federal", {}).get("status") == "document downloaded":
            continue
        furls = [c["url"] for c in cands if domain_ok(c.get("domain"), fed)]
        if furls:
            rb["download_federal"] = _try(furls, dubai + fed, dest_dir, http, "UAE federal")
    return plan


MAX_MORE_DUBAI = 4
MAX_MORE_FEDERAL = 3


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in {"site", "dubai", "english", "requirements", "guideline", "regulation", "regulations"}}


def relevance_score(rb: dict, cand: dict) -> int:
    """Crude ranking of a search hit for a rulebook: words shared with the rulebook title and queries, plus a bonus for document links."""
    want = _words(rb["title"] + " " + " ".join(rb["search"]["queries"]))
    got = _words(cand.get("title", "") + " " + cand.get("snippet", "") + " " + cand["url"])
    return 2 * len(want & got) + (2 if cand.get("kind") == "pdf" else 0)


def run_more_downloads(plan: dict, only: str | None = None, http: httpx.Client | None = None, dest_dir: Path = DOWNLOAD_DIR) -> dict:
    """Second-round fetch for rulebooks that are missing or only weakly matched: PDFs and web pages, best-scoring Dubai hits first.
    Federal hits are tried only if no Dubai hit gave readable English text. Results go to rb['more_downloads'] and never replace rb['download']."""
    dubai, fed = plan["allowed_domains"], plan.get("federal_domains", [])
    for rb in plan["rulebooks"]:
        if (only and rb["id"] != only) or rb.get("paywalled") or rb.get("promoted_as"):
            continue
        cands = [c for c in rb.get("candidates", []) if c.get("query") in rb["search"]["queries"]]
        seen = {r.get("url") for r in rb.get("more_downloads", [])} | {rb.get("download", {}).get("url")}
        done = rb.setdefault("more_downloads", [])

        def tier(domains, limit, jurisdiction):
            pool = sorted((c for c in cands if domain_ok(c.get("domain"), domains) and c["url"] not in seen), key=lambda c: -relevance_score(rb, c))
            for c in pool[:limit]:
                seen.add(c["url"])
                res = download(c["url"], dubai + fed, dest_dir, http)
                res.setdefault("url", c["url"])
                res.update({"jurisdiction": jurisdiction, "title_in_search": c.get("title", ""), "kind_guess": c.get("kind")})
                if jurisdiction != "Dubai" and res["status"] == "document downloaded":
                    res["note"] += "; " + FEDERAL_NOTE
                done.append(res)

        tier(dubai, MAX_MORE_DUBAI, "Dubai")
        if not any(r["status"] == "document downloaded" and r.get("text_quality") == "english text ok" and r["jurisdiction"] == "Dubai" for r in done):
            tier(fed, MAX_MORE_FEDERAL, "UAE federal")
    return plan


def summary(plan: dict) -> str:
    L = [f"Plan: {len(plan['rulebooks'])} rulebooks, {len(plan['items'])} items, search {plan['search']['status']}"]
    for rb in plan["rulebooks"]:
        d = rb["download"]
        L.append(f"  {rb['id']} [{rb['status_in_rules']}] items={len(rb['items'])} download={d['status']}"
                 + (f" ({d.get('note') or d.get('reason')})" if d.get("note") or d.get("reason") else ""))
        for m in rb.get("more_downloads", []):
            L.append(f"      more [{m.get('jurisdiction')}]: {m['status']} {m.get('text_quality') or ''} {m.get('title') or m.get('title_in_search', '')[:60]} ({(m.get('reason') or '')[:70]})")
        f = rb.get("download_federal")
        if f:
            L.append(f"      federal tier: {f['status']} ({f.get('note') or f.get('reason')})")
    e = plan["search"]["estimate"]
    L.append(f"Search still to run: {e['queries']} queries, about {e['credits']} credits (${e['usd']}), worst case {e['worst_case_credits']} credits (${e['worst_case_usd']}).")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.docsearch", description="Plan, search for and download the rulebooks the items need.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="build rules_staging/needed_rulebooks.json (no network)")
    sub.add_parser("estimate", help="print the cost of the searches still to run (no network)")
    sub.add_parser("status", help="print the plan summary")
    s = sub.add_parser("search", help="PAID: run the pending searches")
    s.add_argument("--yes", action="store_true", help="confirm that API credits will be spent")
    s.add_argument("--max-queries", type=int, default=40)
    s.add_argument("--budget-credits", type=int, default=40)
    d = sub.add_parser("download", help="fetch PDFs from known or allowlisted URLs (free)")
    d.add_argument("--id", default=None, help="only this rulebook id")
    dm = sub.add_parser("download-more", help="second-round fetch of search hits (PDF and web pages) for missing or weakly matched rulebooks")
    dm.add_argument("--id", default=None)
    d.add_argument("--federal", action="store_true", help="also fetch federal-tier candidates where a Dubai file was already downloaded")
    for sp in (d, dm):
        sp.add_argument("--add-to-rules", action="store_true",
                        help="afterwards add readable English Dubai downloads to rules/ and INDEX.md as PENDING (a person must confirm them)")
    ip = sub.add_parser("intake", help="add readable English Dubai downloads to rules/ with a PENDING row in INDEX.md (no network)")
    ip.add_argument("--id", action="append", default=None, help="only for this needed-rulebook id (repeatable)")
    ip.add_argument("--dry-run", action="store_true", help="list what would be added")
    args = p.parse_args(argv)

    if args.cmd == "init":
        plan = build_plan(load_plan() if PLAN_PATH.exists() else None)
        save_plan(plan)
        print(summary(plan))
        return 0
    plan = load_plan()
    if args.cmd == "estimate":
        print(json.dumps(estimate_cost(plan), indent=2))
        return 0
    if args.cmd == "status":
        print(summary(plan))
        return 0
    if args.cmd == "search":
        est = estimate_cost(plan)
        print(f"About to run up to {min(est['queries'], args.max_queries)} queries: about {est['credits']} credits (${est['usd']}), worst case {est['worst_case_credits']}.")
        if not args.yes:
            print("Not run. Re-run with --yes to spend the credits.", file=sys.stderr)
            return 2
        plan = run_search(plan, client_from_env(), confirm=True, max_queries=args.max_queries, budget_credits=args.budget_credits)
    elif args.cmd == "download-more":
        plan = run_more_downloads(plan, args.id)
    elif args.cmd == "download":
        plan = run_downloads(plan, args.id, federal=args.federal)
    if args.cmd != "intake":
        save_plan(plan)
        print(summary(plan))
    if args.cmd == "intake" or getattr(args, "add_to_rules", False):
        from . import intake  # lives in its own module: this one never writes to rules/
        res = intake.add_candidates(plan, getattr(args, "id", None) if args.cmd == "intake" else ([args.id] if getattr(args, "id", None) else None),
                                    dry_run=getattr(args, "dry_run", False))
        verb = "Would add" if getattr(args, "dry_run", False) else "Added"
        for a in res["added"]:
            print(f"  {verb} {a.get('file') or a['path']}  <- {(a.get('url') or a.get('source_url', ''))[:90]}")
        for s in res["skipped"]:
            print(f"  skipped {s['path']}: {s['reason']}")
        print(f"{verb} {len(res['added'])} file(s) to rules/ as PENDING in INDEX.md. They are not citable until a person confirms them (Rulebooks screen).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
