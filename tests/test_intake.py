import pytest

from src import clients, intake, pipeline, rules_index

NL = chr(10)
HEAD = "| File | Issuer | Title | Version date | Source URL | Date added | Confirmed by |" + NL + "|---|---|---|---|---|---|---|" + NL
CLAUSES = "4.5 Switchgear" + NL + NL + "Low voltage switchgear shall comply with BS EN 60947 for moulded case circuit breakers and accessories in the building." + NL


def rules_dir(tmp_path, rows=""):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "INDEX.md").write_text(HEAD + rows, encoding="utf-8")
    (d / "README.md").write_text("x", encoding="utf-8")
    return d


def download(tmp_path, name="found.md", text=CLAUSES):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_add_pending_names_the_file_adds_a_pending_row_and_refuses_copies(tmp_path):
    d = rules_dir(tmp_path)
    src = download(tmp_path)
    added = intake.add_pending(src, "https://www.dm.gov.ae/files/code.pdf", "Technical Guidelines for Confined Spaces Entry", rules_dir=d, today="2026-11-03")
    assert added["file"] == "dubai-municipality_technical-guidelines-for-confined-spaces-entry_v2026-11.md" and (d / added["file"]).exists()
    row = intake.index_rows(d)[0]
    assert row["confirmed_by"].startswith("PENDING") and row["source_url"] == "https://www.dm.gov.ae/files/code.pdf" and intake.pending(d)[0]["file"] == added["file"]
    with pytest.raises(ValueError, match="already in rules"):
        intake.add_pending(src, "https://www.dm.gov.ae/x.pdf", "Same bytes", rules_dir=d)
    other = download(tmp_path, "other.md", CLAUSES + "extra clause text that makes the file different from the first one")
    again = intake.add_pending(other, "https://www.dm.gov.ae/y.pdf", "Technical Guidelines for Confined Spaces Entry", rules_dir=d, today="2026-11-03")
    assert again["file"].endswith("_v2026-11-2.md")  # same title and month: a second file gets a suffix, nothing is overwritten
    with pytest.raises(ValueError, match="not a known Dubai issuer"):
        intake.add_pending(other, "https://random.example.com/a.pdf", "T", rules_dir=d)


def test_a_pending_file_is_not_indexed_or_citable_until_a_person_confirms(tmp_path):
    d = rules_dir(tmp_path)
    added = intake.add_pending(download(tmp_path), "https://www.dewa.gov.ae/a.pdf", "Switchgear rules", rules_dir=d)["file"]
    (d / "stray.md").write_text(CLAUSES + "stray", encoding="utf-8")  # a file nobody listed in INDEX.md
    idx = rules_index.RuleIndex(d, tmp_path / "c.json").load()
    status = {r["file"]: r["status"] for r in idx.report}
    assert status[added].startswith("waiting for a person") and status["stray.md"] == "not in INDEX.md" and not idx.chunks
    assert pipeline.rule_files(d) == []
    with pytest.raises(ValueError, match="name"):
        intake.confirm(added, "  ", rules_dir=d)
    sig_before = idx.signature()
    intake.confirm(added, "Jane Reviewer", rules_dir=d, today="2026-11-03")
    assert intake.index_rows(d)[0]["confirmed_by"].startswith("Jane Reviewer on 2026-11-03") and not intake.pending(d)
    idx2 = rules_index.RuleIndex(d, tmp_path / "c.json").load()
    assert idx2.signature() != sig_before and [r["status"] for r in idx2.report if r["file"] == added] == ["indexed"] and idx2.chunks
    assert pipeline.rule_files(d) == [added]
    with pytest.raises(ValueError, match="already confirmed"):
        intake.confirm(added, "Someone", rules_dir=d)


def test_a_rejected_pending_file_leaves_rules_and_the_index(tmp_path):
    d = rules_dir(tmp_path)
    added = intake.add_pending(download(tmp_path), "https://www.dm.gov.ae/a.pdf", "Wrong document", rules_dir=d)["file"]
    intake.remove(added, "Jane", rules_dir=d, rejected_dir=tmp_path / "rej")
    assert not (d / added).exists() and (tmp_path / "rej" / added).exists() and intake.index_rows(d) == []
    d2 = tmp_path / "x"
    d2.mkdir()
    (d2 / "INDEX.md").write_text(HEAD + "| a.md | dm | A | 2020 | u | 2026 | Jane |" + NL, encoding="utf-8")
    (d2 / "a.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Only a pending"):
        intake.remove("a.md", "Jane", rules_dir=d2)


def test_running_app_reloads_the_index_when_rules_change(tmp_path, monkeypatch):
    d = rules_dir(tmp_path, "| a_v1.md | dm | A | 2020 | u | 2026 | Jane |" + NL)
    (d / "a_v1.md").write_text(CLAUSES, encoding="utf-8")
    monkeypatch.setattr(clients, "RuleIndex", lambda: rules_index.RuleIndex(d, tmp_path / "c.json"))
    clients.reset_index()
    first = clients.current_index()
    assert clients.current_index() is first  # nothing changed: the loaded index is reused
    (d / "b_v1.md").write_text("7.2 Lighting" + NL + NL + "Luminaires shall carry the rated wattage on the fitting for installation purposes." + NL, encoding="utf-8")
    (d / "INDEX.md").write_text(HEAD + "| a_v1.md | dm | A | 2020 | u | 2026 | Jane |" + NL + "| b_v1.md | dm | B | 2020 | u | 2026 | Jane |" + NL, encoding="utf-8")
    second = clients.current_index()
    assert second is not first and {c.file for c in second.chunks} == {"a_v1.md", "b_v1.md"}
    clients.reset_index()


def test_candidates_are_dubai_readable_not_wrong_topic_and_not_copies(tmp_path):
    d = rules_dir(tmp_path, "| gb.pdf | dm | Green Building Regulations & Specifications | 2020 | https://own.example/x | 2026 | Jane |" + NL)
    (tmp_path / "g1.pdf").write_bytes(b"%PDF-1.4 x")

    def rec(url, quality="english text ok", **kw):
        return {"status": "document downloaded", "url": url, "path": "g1.pdf", "text_quality": quality, "title_in_search": "Fire Safety Guideline for Buildings", **kw}
    plan = {"rulebooks": [
        {"id": "N01", "title": "Fire", "download": {}, "more_downloads": [
            rec("https://www.dcd.gov.ae/a.pdf"),
            rec("https://www.dcd.gov.ae/b.pdf", quality="arabic"),
            rec("https://www.moi.gov.ae/c.pdf", jurisdiction="UAE federal"),
            rec("https://www.dm.gov.ae/d.pdf", relevance_note="Wrong topic: laundry"),
            rec("https://www.dm.gov.ae/e.pdf", title_in_search="Green Building Regulations and Specifications"),
            rec("https://www.dm.gov.ae/f.pdf", same_as_rules_copy=True)]}]}
    got = intake.candidates(plan, d)
    assert [c["url"] for c in got] == ["https://www.dcd.gov.ae/a.pdf"]
    res = intake.add_candidates(plan, dry_run=True, rules_dir=d)
    assert len(res["added"]) == 1 and not (d / "dubai-civil-defence_fire-safety-guideline-for-buildings_v2026-11.pdf").exists()  # dry run writes nothing


def test_long_documents_are_cut_by_relevance_not_just_at_the_start():
    pages = ["Title page: ComPacT NSX250F circuit breaker datasheet"] + [f"Page {i} about logistics and packaging only " * 40 for i in range(1, 30)]
    pages[17] = "Conformity: IEC 60947-2, rated Icu 36 kA at 415 V, certificate of conformity, type test " * 8
    text = chr(12).join(pages)
    cut = clients.select_pages(text, ["circuit", "breaker", "250", "IEC"], limit=3000)
    assert len(cut) <= 3000 and "ComPacT NSX250F" in cut and "IEC 60947-2" in cut  # the title page and the page that holds the standard
    assert clients.select_pages("short text", [], limit=3000) == "short text"
