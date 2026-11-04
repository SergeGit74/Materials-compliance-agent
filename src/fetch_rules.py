"""Download the rulebook PDFs listed in rules/INDEX.md (for a copy of the repository that ships without the PDFs).

    python -m src.fetch_rules                 download every listed file that is missing from rules/
    python -m src.fetch_rules --dry-run       show what would be downloaded (no network)
    python -m src.fetch_rules --only fire     only rows whose file name or title contains the text
    python -m src.fetch_rules --write-checksums   record sha256 and size of the files now in rules/ (no network; run by the maintainer before an export)
    python -m src.fetch_rules --accept-changed    install a download even when it differs from the recorded checksum

Rules kept here:
- only https URLs on a Dubai or UAE issuer host (src/intake.py ISSUERS) are fetched;
- the file is saved under the name INDEX.md gives it, so the existing INDEX row and its confirmation apply;
- rules/CHECKSUMS.json holds the sha256 of the edition a person confirmed. A download that differs (the publisher replaced the file) is NOT installed:
  it goes to rules_staging/downloads/ and is reported, because the confirmation was for the other edition;
- a file already in rules/ is never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

from . import config, intake

MAX_BYTES = 200 * (1 << 20)
MAX_SECONDS = 300  # per file: a slow publisher server must not hold the app start for ever
CHECKSUMS = "CHECKSUMS.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_checksums(rules_dir: Path = config.RULES_DIR) -> dict:
    p = Path(rules_dir) / CHECKSUMS
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_checksums(rules_dir: Path = config.RULES_DIR) -> dict:
    """Record sha256 and size of every file in rules/ that INDEX.md lists. Run by the maintainer, offline."""
    rules_dir = Path(rules_dir)
    sums = {}
    for r in intake.index_rows(rules_dir):
        p = rules_dir / r["file"]
        if p.exists():
            sums[r["file"]] = {"sha256": sha256(p), "bytes": p.stat().st_size}
    (rules_dir / CHECKSUMS).write_text(json.dumps(sums, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sums


def _fetch(url: str, http: httpx.Client, max_bytes: int, max_seconds: float = MAX_SECONDS) -> tuple[bytes | None, str]:
    """The file's bytes, or None and the reason."""
    if not url.lower().startswith("https://"):
        return None, "only https URLs are fetched"
    if not intake.issuer_of(url):
        return None, "the host is not a known Dubai or UAE issuer; download this file by hand"
    data = bytearray()
    deadline = time.monotonic() + max_seconds
    try:
        with http.stream("GET", url) as r:
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}"
            if not intake.issuer_of(str(r.url)):
                return None, f"redirected to {r.url.host}, which is not a known issuer"
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > max_bytes:
                    return None, f"larger than {max_bytes // (1 << 20)} MB"
                if time.monotonic() > deadline:
                    return None, f"took longer than {int(max_seconds)} s"
    except httpx.HTTPError as e:
        return None, f"network error: {type(e).__name__}"
    if not bytes(data[:5]).startswith(b"%PDF"):
        return None, "the server did not return a PDF (the file may have moved); download it by hand"
    return bytes(data), ""


def fetch(rules_dir: Path = config.RULES_DIR, *, only: str = "", dry_run: bool = False, accept_changed: bool = False,
          http: httpx.Client | None = None, staging_dir: Path | None = None, max_bytes: int = MAX_BYTES,
          max_seconds: float = MAX_SECONDS, progress=None) -> list[dict]:
    """One result per INDEX row: status is present, would download, installed, changed (held back), or failed."""
    rules_dir = Path(rules_dir)
    staging_dir = Path(staging_dir) if staging_dir else config.ROOT / "rules_staging" / "downloads"
    sums = load_checksums(rules_dir)
    own = http is None and not dry_run
    if own:
        http = httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": "materials-compliance-agent/1.0"})
    out = []
    try:
        for r in intake.index_rows(rules_dir):
            if only and only.lower() not in (r["file"] + " " + r["title"]).lower():
                continue
            dest = rules_dir / r["file"]
            rec = {"file": r["file"], "url": r["source_url"], "status": "", "note": ""}
            out.append(rec)
            if dest.exists():
                rec["status"] = "present"
                continue
            if not r["source_url"].lower().startswith("http"):
                rec.update(status="failed", note="INDEX.md has no download URL for this file")
                continue
            if dry_run:
                rec["status"] = "would download"
                continue
            if progress:
                progress(f"downloading {r['file']}")
            data, why = _fetch(r["source_url"], http, max_bytes, max_seconds)
            if data is None:
                rec.update(status="failed", note=why)
                continue
            digest = hashlib.sha256(data).hexdigest()
            known = sums.get(r["file"], {}).get("sha256")
            if known and known != digest and not accept_changed:
                staging_dir.mkdir(parents=True, exist_ok=True)
                (staging_dir / r["file"]).write_bytes(data)
                rec.update(status="changed", note=f"differs from the confirmed edition; saved to {staging_dir.name}/{r['file']} instead. "
                                                  "A person must check it and confirm again (or rerun with --accept-changed).")
                continue
            dest.write_bytes(data)
            rec.update(status="installed", note="" if known else "no recorded checksum to compare with")
    finally:
        if own:
            http.close()
    return out


def _confirmed(row: dict) -> bool:
    return bool(row["confirmed_by"]) and not row["confirmed_by"].upper().startswith("PENDING")


def has_confirmed_pdf(rules_dir: Path = config.RULES_DIR) -> bool:
    """True when at least one PDF that INDEX.md lists as confirmed is already in rules/."""
    rules_dir = Path(rules_dir)
    return any(_confirmed(r) and r["file"].lower().endswith(".pdf") and (rules_dir / r["file"]).exists() for r in intake.index_rows(rules_dir))


def missing_confirmed(rules_dir: Path = config.RULES_DIR) -> list[str]:
    """Confirmed PDFs that INDEX.md lists with a download URL but that are not in rules/."""
    rules_dir = Path(rules_dir)
    return [r["file"] for r in intake.index_rows(rules_dir)
            if _confirmed(r) and r["file"].lower().endswith(".pdf") and r["source_url"].lower().startswith("http") and not (rules_dir / r["file"]).exists()]


def ensure_rules(rules_dir: Path = config.RULES_DIR, *, http: httpx.Client | None = None, staging_dir: Path | None = None,
                 max_bytes: int = MAX_BYTES, max_seconds: float = MAX_SECONDS, progress=print) -> dict:
    """Start of a hosted copy that ships without the PDFs: when a confirmed rulebook listed in INDEX.md is missing from rules/, run the same
    download as `python -m src.fetch_rules` (https, issuer hosts only, never overwrites, checksum rule unchanged). Files that are already there are
    left alone, so a restart retries only what is still missing. Progress lines go to `progress` (the server log by default). It never raises: the
    app must start even if every download fails. Returns {"ran", "installed", "problems": [{"file", "status", "note"}]}."""
    out = {"ran": False, "installed": 0, "problems": []}
    try:
        missing = missing_confirmed(rules_dir)
        if not missing:
            return out
        out["ran"] = True
        if progress:
            progress(f"rulebooks: {len(missing)} listed file(s) missing from rules/, fetching")
        for r in fetch(rules_dir, http=http, staging_dir=staging_dir, max_bytes=max_bytes, max_seconds=max_seconds, progress=progress):
            if r["status"] == "installed":
                out["installed"] += 1
            elif r["status"] in ("failed", "changed"):
                out["problems"].append({"file": r["file"], "status": r["status"], "note": r["note"]})
                if progress:
                    progress(f"rulebooks: {r['file']} {r['status']} ({r['note']})")
        if progress:
            progress(f"rulebooks: {out['installed']} installed, {len(out['problems'])} problem(s)")
    except Exception as e:  # nothing here may stop the app from starting
        out["problems"].append({"file": "(all)", "status": "failed", "note": f"{type(e).__name__} while fetching"})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download the rulebook PDFs listed in rules/INDEX.md that are missing from rules/.")
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded; no network")
    ap.add_argument("--only", default="", help="only rows whose file name or title contains this text")
    ap.add_argument("--write-checksums", action="store_true", help="record sha256 of the files now in rules/ (maintainer, no network)")
    ap.add_argument("--accept-changed", action="store_true", help="install a download even if it differs from the recorded checksum")
    a = ap.parse_args(argv)
    if a.write_checksums:
        sums = write_checksums()
        print(f"Wrote {len(sums)} checksum(s) to rules/{CHECKSUMS}.")
        return 0
    res = fetch(only=a.only, dry_run=a.dry_run, accept_changed=a.accept_changed)
    for r in res:
        print(f"{r['status']:15} {r['file']}" + (f"  ({r['note']})" if r["note"] else ""))
    bad = [r for r in res if r["status"] in ("failed", "changed")]
    got = sum(r["status"] == "installed" for r in res)
    print(f"{got} installed, {sum(r['status'] == 'present' for r in res)} already present, {len(bad)} need attention.")
    if got:
        print("Open the Rulebooks screen (or run the app) so the clause index is rebuilt.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
