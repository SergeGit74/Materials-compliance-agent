"""Local, per-computer settings that must never be published: the reviewer's name.

The file reviewer.local.json is listed in .gitignore and is skipped by src/export_clean.py. The name is typed in the app (or set with
`python -m src.settings --name "..."`) and only pre-fills the name field. Decisions, adoptions and notes still record it, which is why the
export tool scrubs it from every exported file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config

SETTINGS_FILE = config.ROOT / "reviewer.local.json"
DEFAULT_REVIEWER = "Project owner"  # a role label, not a personal name: it is kept in exports


def get_reviewer(path: Path = SETTINGS_FILE) -> str:
    try:
        return str(json.loads(Path(path).read_text(encoding="utf-8")).get("name", "")).strip()
    except (OSError, ValueError):
        return ""


def set_reviewer(name: str, path: Path = SETTINGS_FILE) -> None:
    Path(path).write_text(json.dumps({"name": name.strip()}, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.settings", description="Show or set the reviewer name kept on this computer only.")
    ap.add_argument("--name", help="set the reviewer name")
    args = ap.parse_args(argv)
    if args.name is not None:
        set_reviewer(args.name)
        print("Saved to reviewer.local.json (ignored by git and by the export tool).")
    else:
        print("Reviewer name is set." if get_reviewer() else "No reviewer name saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
