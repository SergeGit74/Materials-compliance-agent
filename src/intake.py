"""Bring a found rulebook into rules/ and INDEX.md, with a person's confirmation as the gate that makes it citable.

The search (src/docsearch.py) downloads candidates into rules_staging/downloads/. This module can then:
  - add_pending(): copy a readable English Dubai-hosted file into rules/ under the naming convention and append an INDEX.md row whose
    "Confirmed by" cell says PENDING. The clause index and the citation check ignore a PENDING file, so adding it does not make it evidence;
  - confirm(): a named person replaces PENDING with their name and the date. Only then is the file indexed and citable;
  - remove(): a person rejects it; the file goes to rules_staging/rejected/ and the row is deleted.
Federal-tier downloads are not added automatically (README 1.2: only a federal code that a Dubai rulebook adopts by name counts).
"""
from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import config

INDEX_HEAD = "| File | Issuer | Title | Version date | Source URL | Date added | Confirmed by |\n|---|---|---|---|---|---|---|\n"
ISSUERS = {"dewa.gov.ae": "dewa", "dm.gov.ae": "dubai-municipality", "dcd.gov.ae": "dubai-civil-defence", "sira.gov.ae": "sira",
           "buildindubai.gov.ae": "dubai-municipality", "dlp.dubai.gov.ae": "dubai-legislation"}
SKIP_NOTES = ("wrong topic", "not a rulebook", "unusable", "not usable")


def issuer_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return next((v for k, v in ISSUERS.items() if host == k or host.endswith("." + k)), "")


def slug(text: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-") or "document"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def index_rows(rules_dir: Path = config.RULES_DIR) -> list[dict]:
    """INDEX.md rows with their cells, in file order."""
    p = Path(rules_dir) / "INDEX.md"
    rows = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 7 and cells[0].lower().endswith((".pdf", ".md", ".txt")):
                rows.append(dict(zip(["file", "issuer", "title", "version", "source_url", "date_added", "confirmed_by"], cells)))
    return rows


def pending(rules_dir: Path = config.RULES_DIR) -> list[dict]:
    return [r for r in index_rows(rules_dir) if not r["confirmed_by"] or r["confirmed_by"].upper().startswith("PENDING")]


def _clean(cell: str) -> str:
    return cell.replace("|", "/").replace("\n", " ").strip()


def add_pending(src: Path, url: str, title: str, *, rules_dir: Path = config.RULES_DIR, today: str | None = None, issuer: str = "",
                version: str = "") -> dict:
    """Copy a downloaded file into rules/ and append a PENDING row. Refuses duplicates (same bytes already in rules/) and unknown issuers."""
    src, rules_dir = Path(src), Path(rules_dir)
    day = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    issuer = issuer or issuer_of(url)
    if not issuer:
        raise ValueError(f"The host of {url} is not a known Dubai issuer. Add the file by hand after checking it.")
    digest = sha(src)
    for existing in rules_dir.iterdir():
        if existing.is_file() and existing.suffix.lower() in (".pdf", ".md", ".txt") and existing.stat().st_size == src.stat().st_size and sha(existing) == digest:
            raise ValueError(f"{src.name} is already in rules/ as {existing.name}.")
    version = version or day[:7]
    name = f"{issuer}_{slug(title)}_v{version}{src.suffix.lower()}"
    dest = rules_dir / name
    n = 2
    while dest.exists():
        dest = rules_dir / f"{issuer}_{slug(title)}_v{version}-{n}{src.suffix.lower()}"
        n += 1
    rules_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    row = (f"| {dest.name} | {issuer} | {_clean(title)[:140]} | {version} (retrieval month; check the document for its own date) | {_clean(url)} | {day} | "
           f"PENDING: added automatically from the search; not citable until a person confirms language, issuer, scope and edition |\n")
    idx = rules_dir / "INDEX.md"
    text = idx.read_text(encoding="utf-8") if idx.exists() else INDEX_HEAD
    idx.write_text(text.rstrip("\n") + "\n" + row, encoding="utf-8")
    return {"file": dest.name, "issuer": issuer, "title": title, "source_url": url}


def _rewrite_row(rules_dir: Path, file: str, replace) -> bool:
    p = Path(rules_dir) / "INDEX.md"
    lines = p.read_text(encoding="utf-8").splitlines()
    out, hit = [], False
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and cells[0] == file:
            hit = True
            new = replace(cells)
            if new is None:
                continue
            line = "| " + " | ".join(new) + " |"
        out.append(line)
    if hit:
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return hit


def confirm(file: str, reviewer: str, *, rules_dir: Path = config.RULES_DIR, today: str | None = None) -> None:
    """A named person confirms a pending rulebook; it becomes indexed and citable."""
    if not reviewer.strip():
        raise ValueError("Enter your name before confirming a rulebook.")
    day = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = next((r for r in index_rows(rules_dir) if r["file"] == file), None)
    if row is None:
        raise ValueError(f"{file} has no row in INDEX.md.")
    if row["confirmed_by"] and not row["confirmed_by"].upper().startswith("PENDING"):
        raise ValueError(f"{file} is already confirmed by {row['confirmed_by']}.")
    if not (Path(rules_dir) / file).exists():
        raise ValueError(f"{file} is not in rules/.")
    _rewrite_row(rules_dir, file, lambda c: c[:6] + [f"{_clean(reviewer)} on {day} (checked language, issuer, scope and edition)"] + c[7:])


def remove(file: str, reviewer: str, *, rules_dir: Path = config.RULES_DIR, rejected_dir: Path = config.ROOT / "rules_staging" / "rejected") -> None:
    """A named person rejects a rulebook that is pending: the file is moved out of rules/ and the INDEX row is deleted."""
    if not reviewer.strip():
        raise ValueError("Enter your name before removing a rulebook.")
    row = next((r for r in index_rows(rules_dir) if r["file"] == file), None)
    if row is None:
        raise ValueError(f"{file} has no row in INDEX.md.")
    if not (not row["confirmed_by"] or row["confirmed_by"].upper().startswith("PENDING")):
        raise ValueError("Only a pending rulebook can be removed here. Remove a confirmed one by hand.")
    src = Path(rules_dir) / file
    if src.exists():
        Path(rejected_dir).mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(Path(rejected_dir) / file))
    _rewrite_row(rules_dir, file, lambda c: None)


def guess_title(path: Path, fallback: str) -> str:
    """The document's own title from its first page (or its '# ' heading for Markdown); the search title or file name when that fails."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".pdf":
            from .docsearch import extract_text
            text = extract_text(path, pages=1) or ""
        else:
            text = path.read_text(encoding="utf-8", errors="replace")[:3000]
    except OSError:
        return fallback
    lines = [re.sub(r"\s+", " ", l).strip(" #|") for l in text.replace(chr(12), chr(10)).splitlines()]
    lines = [l for l in lines if len(l) >= 12 and not re.fullmatch(r"[\d\W]+", l) and "http" not in l.lower()]
    if not lines:
        return fallback
    key = re.compile(r"guideline|regulation|code of|code|law no|manual|standard|specification|guide|policy|circular", re.I)
    for i, l in enumerate(lines[:12]):
        if key.search(l):
            return (lines[i - 1] + " " + l)[:120] if i > 0 and len(l) < 30 else l[:120]
    return " ".join(lines[:2])[:120]


def looks_like_a_filename(title: str) -> bool:
    return " " not in title.strip() or "_20" in title


STOP = {"and", "the", "of", "for", "a", "an", "to", "in", "on", "with", "regulations", "guidelines", "code", "technical"}


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOP and len(w) > 2}


def looks_like_a_copy(title: str, existing_titles: list[str]) -> str:
    """Name of an existing INDEX title that shares at least 75 percent of this title's content words (a second copy of the same document)."""
    w = _words(title)
    if len(w) < 2:
        return ""
    return next((e for e in existing_titles if len(w & _words(e)) / len(w) >= 0.75), "")


def candidates(plan: dict, rules_dir: Path = config.RULES_DIR) -> list[dict]:
    """Staged downloads that could be added: readable English, Dubai-hosted, not flagged as wrong topic, not already in rules/."""
    out, seen = [], set()
    rows = index_rows(rules_dir)
    in_rules = {r["source_url"] for r in rows}
    titles = [r["title"] for r in rows]
    for rb in plan.get("rulebooks", []):
        for rec in [rb.get("download") or {}] + list(rb.get("more_downloads") or []):
            if rec.get("status") != "document downloaded" or rec.get("text_quality") != "english text ok":
                continue
            if rec.get("same_as_rules_copy") or rec.get("jurisdiction", "Dubai") != "Dubai" or rec["url"] in in_rules or rec["path"] in seen:
                continue
            note = (rec.get("relevance_note") or "").lower()
            if any(s in note for s in SKIP_NOTES) or not issuer_of(rec["url"]):
                continue
            title = rec.get("title") or rec.get("title_in_search") or Path(rec["path"]).stem
            if looks_like_a_filename(title):
                title = guess_title(config.ROOT / rec["path"], title)
            if looks_like_a_copy(title, titles):
                continue
            seen.add(rec["path"])
            out.append({"rulebook": rb["id"], "needed": rb["title"], "path": rec["path"], "url": rec["url"], "title": title, "note": rec.get("relevance_note", "")})
    return out


def add_candidates(plan: dict, only_ids: list[str] | None = None, *, rules_dir: Path = config.RULES_DIR, root: Path = config.ROOT, dry_run: bool = False) -> dict:
    """Add every eligible staged download (optionally only for some needed-rulebook ids) as PENDING. Returns what was added and skipped."""
    added, skipped = [], []
    for c in candidates(plan, rules_dir):
        if only_ids and c["rulebook"] not in only_ids:
            continue
        if dry_run:
            added.append(c)
            continue
        try:
            added.append(add_pending(Path(root) / c["path"], c["url"], c["title"], rules_dir=rules_dir) | {"rulebook": c["rulebook"]})
        except ValueError as e:
            skipped.append({"path": c["path"], "reason": str(e)})
    return {"added": added, "skipped": skipped}
