import httpx

from src import fetch_rules, intake

NL = chr(10)
HEAD = "| File | Issuer | Title | Version date | Source URL | Date added | Confirmed by |" + NL + "|---|---|---|---|---|---|---|" + NL
PDF = b"%PDF-1.4 test rulebook body"


def rules(tmp_path, rows):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "INDEX.md").write_text(HEAD + "".join(f"| {f} | dm | T {f} | 2020 | {u} | 2026 | Jane |" + NL for f, u in rows), encoding="utf-8")
    return d


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def ok(request):
    return httpx.Response(200, content=PDF)


def test_missing_files_are_downloaded_and_present_ones_are_left_alone(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://dmpmedia.dm.gov.ae/a.pdf"), ("b.pdf", "https://www.dewa.gov.ae/b.ashx")])
    (d / "b.pdf").write_bytes(b"%PDF-mine")
    res = {r["file"]: r["status"] for r in fetch_rules.fetch(d, http=client(ok), staging_dir=tmp_path / "st")}
    assert res == {"a.pdf": "installed", "b.pdf": "present"}
    assert (d / "a.pdf").read_bytes() == PDF and (d / "b.pdf").read_bytes() == b"%PDF-mine"  # an existing file is never overwritten


def test_dry_run_makes_no_request(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://dmpmedia.dm.gov.ae/a.pdf")])
    res = fetch_rules.fetch(d, dry_run=True)
    assert res[0]["status"] == "would download" and not (d / "a.pdf").exists()


def test_unknown_hosts_http_html_and_errors_are_refused_with_a_reason(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://random.example.com/a.pdf"), ("b.pdf", "http://www.dewa.gov.ae/b.pdf"),
                         ("c.pdf", "https://www.dewa.gov.ae/c.pdf"), ("d.pdf", "https://www.dewa.gov.ae/d.pdf")])

    def handler(request):
        return httpx.Response(404) if request.url.path.endswith("c.pdf") else httpx.Response(200, content=b"<html>moved</html>")
    res = {r["file"]: r for r in fetch_rules.fetch(d, http=client(handler), staging_dir=tmp_path / "st")}
    assert all(r["status"] == "failed" for r in res.values()) and not list(d.glob("*.pdf"))
    assert "issuer" in res["a.pdf"]["note"] and "https" in res["b.pdf"]["note"] and "404" in res["c.pdf"]["note"] and "not return a PDF" in res["d.pdf"]["note"]


def test_a_redirect_to_another_host_is_refused(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://www.dewa.gov.ae/a.pdf")])

    def handler(request):
        if request.url.host == "www.dewa.gov.ae":
            return httpx.Response(302, headers={"location": "https://evil.example.net/a.pdf"})
        return httpx.Response(200, content=PDF)
    res = fetch_rules.fetch(d, http=client(handler), staging_dir=tmp_path / "st")
    assert res[0]["status"] == "failed" and not (d / "a.pdf").exists()


def test_a_changed_edition_is_held_back_until_a_person_accepts_it(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://dmpmedia.dm.gov.ae/a.pdf")])
    (d / "a.pdf").write_bytes(b"%PDF-confirmed edition")
    sums = fetch_rules.write_checksums(d)
    assert sums["a.pdf"]["bytes"] == len(b"%PDF-confirmed edition") and (d / "CHECKSUMS.json").exists()
    (d / "a.pdf").unlink()
    held = fetch_rules.fetch(d, http=client(ok), staging_dir=tmp_path / "st")
    assert held[0]["status"] == "changed" and not (d / "a.pdf").exists() and (tmp_path / "st" / "a.pdf").read_bytes() == PDF
    taken = fetch_rules.fetch(d, http=client(ok), staging_dir=tmp_path / "st", accept_changed=True)
    assert taken[0]["status"] == "installed" and (d / "a.pdf").read_bytes() == PDF


def test_a_matching_checksum_installs_and_only_filters_rows(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://dmpmedia.dm.gov.ae/a.pdf"), ("b.pdf", "https://www.dewa.gov.ae/b.pdf")])
    (d / "a.pdf").write_bytes(PDF)
    (d / "b.pdf").write_bytes(b"%PDF-other")
    fetch_rules.write_checksums(d)
    (d / "a.pdf").unlink()
    (d / "b.pdf").unlink()
    res = fetch_rules.fetch(d, only="a.pdf", http=client(ok), staging_dir=tmp_path / "st")
    assert [(r["file"], r["status"]) for r in res] == [("a.pdf", "installed")] and not (d / "b.pdf").exists()
    assert intake.index_rows(d)[0]["file"] == "a.pdf"


def test_a_rulebook_listed_but_not_downloaded_is_reported_as_missing(tmp_path):
    from src import rules_index
    d = rules(tmp_path, [("a.pdf", "https://dmpmedia.dm.gov.ae/a.pdf")])
    idx = rules_index.RuleIndex(d, tmp_path / "c.json").load()
    assert [(r["file"], r["status"].split(":")[0]) for r in idx.report] == [("a.pdf", "missing")] and not idx.chunks


def test_app_has_no_bare_conditional_expression_statements():
    """A bare `st.x(...) if c else st.y(...)` line is shown on screen by Streamlit's magic as its text and object repr."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8"))
    bad = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.IfExp)]
    assert bad == [], f"bare conditional expression statements at lines {bad}"


def test_documents_needed_are_read_from_the_notes_one_per_numbered_entry():
    from src.pipeline import documents_needed
    notes = ("Rule applies. Documents needed: (1) a maker erection manual showing the 6 m build; (2) the maker's load rating, since the rule has classes at 1.2/2.4 kN/m2; "
             "(3) the contractor's design approved by the engineering office, and an inspection certificate before use. The excerpts do not name EN 1004.")
    got = documents_needed(notes)
    assert len(got) == 3 and got[0].startswith("a maker erection manual") and got[1].startswith("the maker's load rating") and got[2].endswith("before use")
    assert documents_needed("No documents are named here.") == []


def test_manual_item_keeps_the_chosen_category_and_defaults_to_unspecified(tmp_path):
    from src import inventory
    m = tmp_path / "m.csv"
    assert inventory.make_manual_item("Mobile aluminium scaffold tower", manual_path=m)["Category"] == "Unspecified"
    row = inventory.add_manual_item("Mobile aluminium scaffold tower", category="Safety & PPE", manual_path=m)
    assert row["Category"] == "Safety & PPE" and "Safety & PPE" in m.read_text(encoding="utf-8")


def test_documents_needed_covers_the_other_wordings_the_model_uses():
    from src.pipeline import documents_needed
    notes = ("Rule 8-11-2 limits tower height. Needed from the maker: a datasheet showing stabiliser base width, and an access ladder. Also needed: the safe working load against "
             "the duty classes (8-5), with a 4x safety factor. Platform width meets the minimum. Site-side documents: the scaffold design approved by the engineering office, "
             "third-party inspection certificate and tags (8-1, 8-10). The Unitech page is weaker evidence.")
    got = documents_needed(notes)
    assert len(got) == 3 and got[0].startswith("a datasheet") and got[1].startswith("the safe working load") and got[2].startswith("the scaffold design") and got[2].endswith("(8-1, 8-10)")


def test_update_in_place_copies_changes_deletes_removed_files_and_keeps_git(tmp_path):
    from src import export_clean
    built, repo = tmp_path / "built", tmp_path / "repo"
    (built / "src").mkdir(parents=True)
    (repo / ".git").mkdir(parents=True)
    (repo / "old").mkdir()
    (built / "a.md").write_text("new", encoding="utf-8")
    (built / "src" / "b.py").write_text("same", encoding="utf-8")
    (built / "c.md").write_text("added", encoding="utf-8")
    (repo / "a.md").write_text("old", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "b.py").write_text("same", encoding="utf-8")
    (repo / "gone.md").write_text("x", encoding="utf-8")
    (repo / "old" / "z.md").write_text("x", encoding="utf-8")
    (repo / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    rep = export_clean.sync(built, repo)
    assert rep == {"added": ["c.md"], "changed": ["a.md"], "removed": ["gone.md", "old/z.md"]}
    assert (repo / "a.md").read_text(encoding="utf-8") == "new" and not (repo / "old").exists() and (repo / ".git" / "HEAD").exists()
    import pytest
    with pytest.raises(ValueError, match="not a git repository"):
        export_clean.sync(built, tmp_path / "plain")


def test_a_mock_run_may_not_replace_an_existing_result(tmp_path, monkeypatch, capsys):
    from src import appcore, run, store
    from src.models import MockModels
    assert appcore.mock_would_overwrite(MockModels(), True) and not appcore.mock_would_overwrite(MockModels(), False)
    assert not appcore.mock_would_overwrite(object(), True)  # real models may replace a result (a redo)
    calls = []
    monkeypatch.setattr(run.pipeline, "run_item", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(store, "replace_row", lambda r: calls.append("replace"))
    run.process({"Item_ID": "INV-0001"}, MockModels(), redo=True)
    assert calls == [] and "not rerun" in capsys.readouterr().out
