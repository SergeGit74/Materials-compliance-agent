import pytest

from src import export_clean as ec
from src import settings


def make_project(root, name="Jane Q. Reviewer"):
    (root / "results" / "items").mkdir(parents=True)
    (root / "results" / "evidence").mkdir()
    (root / "results" / "cache").mkdir()
    (root / "results" / "adopted_backup").mkdir()
    (root / "rules").mkdir()
    (root / "rules_staging" / "downloads").mkdir(parents=True)
    (root / "src").mkdir()
    (root / ".env").write_text("ANTHROPIC_API_KEY=" + "sk-ant-" + "SECRET0000000000\n", encoding="utf-8")
    (root / "reviewer.local.json").write_text('{"name": "' + name + '"}', encoding="utf-8")
    (root / "results" / "decisions.csv").write_text(
        "item_id,decision,reviewer,comment,verdict_at_decision,confidence_at_decision,decided_at\n"
        f"INV-0001,approved,{name},ok,ALLOWED,0.75,2026-11-02\n"
        "INV-0020,adopted-real-mode,Project owner (instruction in chat, 2026-11-02),x,NOT_ALLOWED,0.75,2026-11-02\n", encoding="utf-8")
    (root / "results" / "results.csv").write_text(f"item_id,notes\nINV-0001,Adopted by {name.upper()} on 20261102\n", encoding="utf-8")
    (root / "results" / "items" / "INV-0001.md").write_text(f"# r\n\nAdopted real-mode result by {name}.\n", encoding="utf-8")
    (root / "results" / "evidence" / "a.pdf").write_bytes(b"%PDF-1.4 evidence")
    (root / "results" / "cache" / "c.json").write_text("{}", encoding="utf-8")
    (root / "results" / "adopted_backup" / "INV-0001_x.json").write_text(f'{{"adopted_by": "{name}"}}', encoding="utf-8")
    (root / "rules" / "INDEX.md").write_text("| a.pdf | dewa |\n", encoding="utf-8")
    (root / "rules" / "a.pdf").write_bytes(b"%PDF-1.4 rulebook")
    (root / "rules_staging" / "downloads" / "d.pdf").write_bytes(b"%PDF-1.4 staged")
    (root / "src" / "x.py").write_text("print('hello')\n", encoding="utf-8")
    return root


def test_names_come_from_the_log_the_saved_name_and_also_but_not_role_labels(tmp_path):
    root = make_project(tmp_path / "proj")
    names = ec.reviewer_names(root / "results" / "decisions.csv", also=["Second Person"], saved="Jane Q. Reviewer")
    assert names == ["Jane Q. Reviewer", "Second Person"]  # one entry per name; the 'Project owner' label is not a personal name


def test_export_removes_the_name_everywhere_and_never_copies_secrets(tmp_path):
    root = make_project(tmp_path / "proj")
    out = tmp_path / "export"
    names = ec.reviewer_names(root / "results" / "decisions.csv")
    rep = ec.export(root, out, names)
    text = lambda rel: (out / rel).read_text(encoding="utf-8")
    assert "[reviewer]" in text("results/decisions.csv") and "Jane" not in text("results/decisions.csv")
    assert "[reviewer]" in text("results/results.csv") and "JANE" not in text("results/results.csv")  # case-insensitive
    assert "Jane" not in text("results/items/INV-0001.md") and "Project owner (instruction in chat" in text("results/decisions.csv")
    assert not (out / ".env").exists() and not (out / "reviewer.local.json").exists()
    assert not (out / "results/cache").exists() and not (out / "results/adopted_backup").exists() and not (out / "rules_staging/downloads").exists()
    assert not (out / "results/evidence/a.pdf").exists() and not (out / "rules/a.pdf").exists() and (out / "rules/INDEX.md").exists()
    assert (out / "src/x.py").exists() and rep["scrubbed_files"]["results/decisions.csv"] == 1
    assert ec.verify(out, names) == []
    # the original is untouched
    assert "Jane Q. Reviewer" in (root / "results" / "decisions.csv").read_text(encoding="utf-8")


def test_options_and_limits(tmp_path, monkeypatch):
    root = make_project(tmp_path / "proj")
    big = root / "rules" / "big.pdf"
    big.write_bytes(b"%PDF-1.4 big" + b"x" * 6000)
    monkeypatch.setattr(ec, "MAX_BYTES", 4000)
    out = tmp_path / "export2"
    rep = ec.export(root, out, ["Jane Q. Reviewer"], include_rules=True, include_evidence=True)
    assert rep["skipped_large"] == ["rules/big.pdf"] and not (out / "rules/big.pdf").exists() and (out / "rules/a.pdf").exists()
    with pytest.raises(ValueError, match="not empty"):
        ec.export(root, out, [])
    with pytest.raises(ValueError, match="outside the project"):
        ec.export(root, root / "inside", [])


def test_verify_finds_what_the_scrub_missed(tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "a.md").write_text("note by Jane Q. Reviewer; key " + "sk-ant-" + "ABCDEFGHIJKLMNOP; " + "C:" + "\\Users\\jane\\proj\n", encoding="utf-8")
    (out / ".env").write_text("x", encoding="utf-8")
    found = " | ".join(ec.verify(out, ["Jane Q. Reviewer"]))
    assert "reviewer name" in found and "key-like" in found and "home path" in found and ".env" in found


def test_settings_roundtrip(tmp_path):
    f = tmp_path / "reviewer.local.json"
    assert settings.get_reviewer(f) == ""
    settings.set_reviewer("  A. Person ", f)
    assert settings.get_reviewer(f) == "A. Person"


def test_the_default_reviewer_is_a_role_label_that_the_export_keeps(tmp_path):
    assert settings.DEFAULT_REVIEWER == "Project owner"
    root = make_project(tmp_path / "proj", name="Project owner")
    (root / "results" / "decisions.csv").write_text(
        "item_id,decision,reviewer,comment,verdict_at_decision,confidence_at_decision,decided_at\nINV-0001,approved,Project owner,ok,ALLOWED,0.75,t\n", encoding="utf-8")
    assert ec.reviewer_names(root / "results" / "decisions.csv") == []  # nothing to scrub
    out = tmp_path / "e"
    ec.export(root, out, [])
    assert "Project owner" in (out / "results/decisions.csv").read_text(encoding="utf-8")
