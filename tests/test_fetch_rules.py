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
