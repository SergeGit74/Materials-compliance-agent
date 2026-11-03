"""Export a clean copy of the project for GitHub: no secrets, no personal names, no files GitHub would reject.

    python -m src.export_clean OUT_DIR [--also "Another Name"] [--include-rules] [--include-evidence] [--update]

What it does
- copies the project into OUT_DIR (a new folder; it refuses to write into a non-empty one unless --update is given);
- with --update, OUT_DIR must be an existing git repository (it has a .git folder). The clean copy is built in a temporary folder and verified first;
  only if it passes, the repository folder is brought up to date: new and changed files are copied, files that are no longer in the export are deleted,
  and .git is never touched. Review the result with `git status`, then commit and push yourself or ask Claude to;
- never copies .env, .venv, caches, uploads, staged downloads, reviewer.local.json or any .git folder;
- replaces every reviewer name with "[reviewer]" in text files (csv, md, json, txt, py, html). The names come from the reviewer column of
  results/decisions.csv, the saved reviewer name and --also. Labels such as "Project owner (instruction in chat, ...)" are not personal names and stay;
- leaves out any file over 95 MB (GitHub rejects files over 100 MB) and lists it, so you can use Git LFS or leave it out;
- by default leaves out the rulebook PDFs and the saved evidence PDFs (large, and third-party documents); the INDEX.md of rules/ is kept;
- re-scans the output and fails if any name, a Windows home path or a key-like string is still there.
Nothing in the original project is changed.
"""
from __future__ import annotations

import argparse
import csv
import filecmp
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import config, settings

ALWAYS_SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "cache", "uploads", "downloads", "adopted_backup"}
ALWAYS_SKIP_FILES = {".env", "reviewer.local.json"}
TEXT_SUFFIXES = {".csv", ".md", ".json", ".txt", ".py", ".html", ".yaml", ".yml", ".toml", ".cfg"}
MAX_BYTES = 95 * 1024 * 1024
PLACEHOLDER = "[reviewer]"
HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+", re.I)
KEYLIKE = re.compile(r"\b(?:sk-ant-[\w-]{10,}|sk-[A-Za-z0-9]{20,}|pa-[A-Za-z0-9_-]{20,})\b")


def reviewer_names(decisions_csv: Path, also: list[str] | None = None, saved: str = "") -> list[str]:
    """Personal names to scrub: reviewers in the decision log, the saved name and --also. Generic role labels are kept."""
    names = set(n.strip() for n in (also or []) if n.strip())
    if saved.strip():
        names.add(saved.strip())
    if Path(decisions_csv).exists():
        with Path(decisions_csv).open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                n = (r.get("reviewer") or "").strip()
                if n and not n.lower().startswith("project owner"):
                    names.add(n)
    return sorted(names, key=len, reverse=True)


def scrub(text: str, names: list[str]) -> tuple[str, int]:
    """Replace every full name (case-insensitive) with the placeholder. Only the names given are removed: a first name on its own is not."""
    count = 0
    for n in names:
        text, k = re.subn(re.escape(n), PLACEHOLDER, text, flags=re.I)
        count += k
    return text, count


def export(src: Path, out: Path, names: list[str], include_rules: bool = False, include_evidence: bool = False) -> dict:
    src, out = Path(src), Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} is not empty. Choose a new folder.")
    if out.resolve().is_relative_to(src.resolve()):
        raise ValueError("The output folder must be outside the project.")
    report = {"copied": 0, "scrubbed_files": {}, "skipped_large": [], "skipped_by_rule": 0}
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        parts = set(rel.parts)
        if p.is_dir() or parts & ALWAYS_SKIP_DIRS or p.name in ALWAYS_SKIP_FILES:
            continue
        if rel.parts[0] == "rules" and p.suffix.lower() == ".pdf" and not include_rules:
            report["skipped_by_rule"] += 1
            continue
        if rel.parts[:2] == ("results", "evidence") and not include_evidence:
            report["skipped_by_rule"] += 1
            continue
        if rel.parts[0] == "rules_staging" and rel.parts[1:2] == ("downloads",):
            continue
        if p.stat().st_size > MAX_BYTES:
            report["skipped_large"].append(rel.as_posix())
            continue
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix.lower() in TEXT_SUFFIXES:
            text, n = scrub(p.read_text(encoding="utf-8", errors="replace"), names)
            dest.write_text(text, encoding="utf-8")
            if n:
                report["scrubbed_files"][rel.as_posix()] = n
        else:
            shutil.copy2(p, dest)
        report["copied"] += 1
    return report


def sync(built: Path, repo: Path) -> dict:
    """Bring the git repository folder `repo` up to date with the verified export `built`. .git is never touched."""
    built, repo = Path(built), Path(repo)
    if not (repo / ".git").exists():
        raise ValueError(f"{repo} is not a git repository (no .git folder). Use a new folder without --update.")
    rep = {"added": [], "changed": [], "removed": []}
    new = {p.relative_to(built).as_posix() for p in built.rglob("*") if p.is_file()}
    for rel in sorted(new):
        src, dest = built / rel, repo / rel
        if not dest.exists():
            rep["added"].append(rel)
        elif not filecmp.cmp(src, dest, shallow=False):
            rep["changed"].append(rel)
        else:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    for p in sorted(repo.rglob("*")):
        rel = p.relative_to(repo)
        if p.is_dir() or rel.parts[0] == ".git" or rel.as_posix() in new:
            continue
        p.unlink()
        rep["removed"].append(rel.as_posix())
    for d in sorted((p for p in repo.rglob("*") if p.is_dir() and p.relative_to(repo).parts[0] != ".git"), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    return rep


def verify(out: Path, names: list[str]) -> list[str]:
    """Problems left in the output: a name, a Windows home path, a key-like string or a .env file."""
    problems = []
    for p in sorted(Path(out).rglob("*")):
        if p.is_dir() or p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(out)
        for n in names:
            if re.search(re.escape(n), text, re.I):
                problems.append(f"{rel}: contains a reviewer name")
        if KEYLIKE.search(text):
            problems.append(f"{rel}: contains a key-like string")
        if HOME.search(text):
            problems.append(f"{rel}: contains a Windows home path")
    if (Path(out) / ".env").exists():
        problems.append(".env is in the output")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.export_clean", description="Export a clean copy of the project for GitHub.")
    ap.add_argument("out", type=Path)
    ap.add_argument("--also", action="append", default=[], help="another name to scrub (repeatable)")
    ap.add_argument("--include-rules", action="store_true", help="copy the rulebook PDFs (files over 95 MB are still left out)")
    ap.add_argument("--include-evidence", action="store_true", help="copy the saved evidence PDFs")
    ap.add_argument("--update", action="store_true", help="OUT is an existing git repository: update it in place (.git is kept; removed files are deleted)")
    args = ap.parse_args(argv)
    names = reviewer_names(config.RESULTS_DIR / "decisions.csv", args.also, settings.get_reviewer())
    print(f"Names to remove: {len(names)}" + ("" if names else " (none found: pass --also if a name appears in notes)"))
    target = args.out
    tmp = None
    try:
        if args.update:
            if not (args.out / ".git").exists():
                raise ValueError(f"{args.out} is not a git repository (no .git folder). Use a new folder without --update.")
            tmp = Path(tempfile.mkdtemp(prefix="export_clean_"))
            target = tmp / "export"
        rep = export(config.ROOT, target, names, args.include_rules, args.include_evidence)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"Copied {rep['copied']} files to {args.out}. Left out by rule (rulebook and evidence PDFs): {rep['skipped_by_rule']}.")
    for f, n in rep["scrubbed_files"].items():
        print(f"  scrubbed {n:3d} in {f}")
    for f in rep["skipped_large"]:
        print(f"  LEFT OUT (over 95 MB, GitHub would reject it): {f}")
    problems = verify(target, names)
    for pr in problems:
        print("  PROBLEM:", pr, file=sys.stderr)
    if tmp is not None:
        if not problems:
            sr = sync(target, args.out)
            print(f"Updated {args.out} in place: {len(sr['added'])} added, {len(sr['changed'])} changed, {len(sr['removed'])} removed. .git untouched. Run `git status` there.")
            for k in ("added", "changed", "removed"):
                for f in sr[k]:
                    print(f"  {k:8} {f}")
        else:
            print("Nothing in the repository folder was changed.", file=sys.stderr)
        shutil.rmtree(tmp, ignore_errors=True)
    print("Verification: " + ("FAILED" if problems else "no names, keys or home paths found in the output."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
