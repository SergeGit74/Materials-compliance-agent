import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

from src import clients, config, pipeline, rules_index
from src.models import Candidate, Compliance, MockModels, Normalized

ITEM = {"Item_ID": "INV-0001", "Category": "Electrical", "Internal_Description": "MCCB 250A 3 pole", "Part_Number": "NSX250-3P",
        "Manufacturer_Listed": "Schneider", "Equipment_Served": "LV Panel MDB-01", "Site_Location": "Site A - SECRET TOWER",
        "Qty": "4", "Unit": "pcs", "Key_Specs": "3P, 250A", "Last_Purchase_Date": "2024-03-12", "Stock_Status": "Active"}


def resp(payload, stop="end_turn", tin=1000, tout=200):
    return NS(content=[NS(type="text", text=json.dumps(payload))], stop_reason=stop, usage=NS(input_tokens=tin, output_tokens=tout))


class FakeAnthropic:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeSearch:
    def __init__(self, hits):
        self.hits, self.queries = hits, []

    def search(self, q):
        self.queries.append(q)
        return self.hits, 0.001


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("MODEL_NORMALIZE", "claude-haiku-5-5")
    monkeypatch.setenv("MODEL_REASON", "claude-sonnet-5-5")
    monkeypatch.setenv("MODEL_VERIFY", "claude-opus-5-5")
    monkeypatch.setattr(clients, "fetch_document", lambda url, http=None, limit=0, terms=None: {"url": url, "kind": "pdf", "text": "Datasheet text. " * 30, "raw": b"%PDF-1.4 x"})


def idx(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "dewa_test_v1.md").write_text("4.5 Switchgear\n\nLow voltage switchgear shall comply with BS EN 60947 for moulded case circuit breakers and accessories.\n", encoding="utf-8")
    return rules_index.RuleIndex(d, tmp_path / "c.json").load()


def err():
    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com", headers={"x-api-key": "SECRET-KEY"}))


def test_price_and_unknown_model_is_priced_high():
    assert clients.price_usd("claude-sonnet-5-5", 1_000_000, 1_000_000) == 12.0
    assert clients.price_usd("claude-haiku-5-5", 1000, 1000) == pytest.approx(0.0006)
    assert clients.price_usd("some-future-model", 1_000_000, 0) == 4.0


def test_normalize_sends_product_fields_only_and_uses_the_normalize_model():
    fake = FakeAnthropic(resp({"canonical_name": "MCCB", "product_type": "moulded case circuit breaker", "category": "Electrical",
                               "specs_text": "3P, 250 A", "confidence": 1.7, "too_vague": False, "vague_reason": ""}))
    m = clients.RealModels(anthropic_client=fake)
    n, usage = m.normalize(ITEM, "3P, 250 A")
    req = fake.requests[0]
    sent = json.dumps(req["messages"])
    assert req["model"] == "claude-haiku-5-5" and "SECRET TOWER" not in sent and "2024-03-12" not in sent and '"Qty"' not in sent
    assert req["output_config"]["format"]["type"] == "json_schema" and req["output_config"]["effort"] == "low"
    assert not {"temperature", "top_p", "top_k", "thinking", "tool_choice"} & set(req)  # rejected or removed on the current models
    assert n.confidence == 1.0 and usage.cost_usd == clients.price_usd("claude-haiku-5-5", 1000, 200)


def test_a_failed_call_never_invents_an_answer_or_leaks_the_key(tmp_path):
    m = clients.RealModels(anthropic_client=FakeAnthropic(err(), resp({"x": 1}, stop="refusal")), index=idx(tmp_path))
    n, _ = m.normalize(ITEM, "3P")
    assert n.too_vague and "APIConnectionError" in n.vague_reason and "SECRET-KEY" not in n.vague_reason
    n2 = Normalized(canonical_name="moulded case circuit breaker", product_type="mccb", category="Electrical", specs_text="BS EN 60947", confidence=0.5)
    comp, usage = m.assess_compliance(n2, [], [])  # the model call is refused
    assert comp.verdict == "UNKNOWN" and "refusal" in comp.notes and not comp.rule_refs


def test_discover_keeps_only_candidates_whose_url_was_in_the_results():
    hits = [{"title": "Datasheet", "url": "https://www.se.com/ds.pdf", "snippet": "NSX250"}, {"title": "Shop", "url": "http://insecure.example/x", "snippet": ""}]
    fake = FakeAnthropic(resp({"candidates": [
        {"producer": "Schneider Electric", "source_url": "https://www.se.com/ds.pdf", "specs_text": "3P, 250 A, 36 kA", "alias_note": ""},
        {"producer": "Invented Co", "source_url": "https://invented.example/p", "specs_text": "3P", "alias_note": ""}]}))
    search = FakeSearch(hits)
    m = clients.RealModels(anthropic_client=fake, search=search)
    m._maker = "Schneider"
    cands, usage = m.discover("moulded case circuit breaker 3P, 250 A")
    assert [c.producer for c in cands] == ["Schneider Electric"]
    assert search.queries == ["moulded case circuit breaker 3P, 250 A Schneider"] and "SECRET" not in json.dumps(fake.requests)
    assert usage.cost_usd > 0.001  # the search fee plus the model call


def test_discover_with_no_results_makes_no_model_call():
    fake = FakeAnthropic()
    cands, usage = clients.RealModels(anthropic_client=fake, search=FakeSearch([])).discover("anything")
    assert cands == [] and fake.requests == []


def test_compliance_drops_refs_and_urls_that_were_not_provided(tmp_path):
    index = idx(tmp_path)
    cand = Candidate(producer="Schneider", source_url="https://www.se.com/ds.pdf", specs_text="3P")
    fake = FakeAnthropic(resp({"rule_refs": ["dewa_test_v1.md#4.5-Switchgear", "dewa_test_v1.md#9.9", "other.pdf#1"], "verdict": "ALLOWED", "notes": "ok",
                               "supplier_urls": ["https://www.se.com/ds.pdf", "https://made-up.example/"]}))
    m = clients.RealModels(anthropic_client=fake, index=index)
    n = Normalized(canonical_name="moulded case circuit breaker", product_type="moulded case circuit breaker", category="Electrical", specs_text="BS EN 60947", confidence=0.8)
    cand.document_opened = True
    comp, _ = m.assess_compliance(n, [cand], ["dewa_test_v1.md"])
    assert comp.rule_refs == ["dewa_test_v1.md#4.5-Switchgear"] and comp.supplier_urls == ["https://www.se.com/ds.pdf"] and "dropped" in comp.notes
    sent = fake.requests[0]["messages"][0]["content"]
    assert "<data>" in sent and fake.requests[0]["model"] == "claude-sonnet-5-5" and fake.requests[0]["output_config"]["effort"] == "high"


def test_verify_uses_the_verify_model_and_reports_rejections(tmp_path):
    fake = FakeAnthropic(resp({"rejected_claims": [{"claim": "36 kA not stated", "affects_verdict": True}, {"claim": "Table G.10", "affects_verdict": False}],
                               "notes": "checked", "corrected_notes": "clean"}))
    m = clients.RealModels(anthropic_client=fake, index=idx(tmp_path))
    n = Normalized(canonical_name="MCCB", product_type="mccb", category="Electrical", confidence=0.8)
    v, _ = m.verify(n, [], Compliance(rule_refs=["dewa_test_v1.md#4.5-Switchgear"], verdict="ALLOWED"))
    assert v.rejected_claims == ["36 kA not stated", "Table G.10"] and v.core_rejected == ["36 kA not stated"] and v.corrected_notes == "clean"
    assert fake.requests[0]["model"] == "claude-opus-5-5" and "maker differs" in fake.requests[0]["system"]


def test_full_pipeline_with_fake_clients_downgrades_a_rejected_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "rule_files", lambda *a, **k: ["dewa_test_v1.md"])
    index = idx(tmp_path)
    hits = [{"title": "Datasheet", "url": "https://www.se.com/ds.pdf", "snippet": "NSX250"}]
    fake = FakeAnthropic(
        resp({"canonical_name": "MCCB 3P 250 A", "product_type": "moulded case circuit breaker", "category": "Electrical", "specs_text": "3P, 250 A",
              "confidence": 0.8, "too_vague": False, "vague_reason": ""}),
        resp({"candidates": [{"producer": "Schneider Electric", "source_url": "https://www.se.com/ds.pdf", "specs_text": "3P, 250 A", "alias_note": ""}]}),
        resp({"rule_refs": ["dewa_test_v1.md#4.5-Switchgear"], "verdict": "ALLOWED", "notes": "BS EN 60947", "supplier_urls": ["https://www.se.com/ds.pdf"]}),
        resp({"rejected_claims": [{"claim": "datasheet does not state BS EN 60947", "affects_verdict": True}], "notes": "x", "corrected_notes": ""}))
    m = clients.RealModels(anthropic_client=fake, search=FakeSearch(hits), index=index)
    m.search_method = "Serper (cached)"
    out = pipeline.run_item(ITEM, m, pipeline.Budget(15, 300000))
    assert out["row"]["compliance_verdict"] == "UNKNOWN" and out["verifier_rejected"]  # the verifier's rejection overrides ALLOWED
    assert out["usage"].cost_usd > 0 and "Serper" in out["report"]
    assert out["row"]["human_decision"] == "pending"


def test_serper_search_is_cached_on_disk(tmp_path):
    class Client:
        n = 0

        def search(self, q):
            Client.n += 1
            return [{"title": "t", "url": "https://a.example/x", "snippet": ""}]
    s = clients.SerperSearch(Client(), tmp_path)
    assert s.search("Query  One")[1] == clients.SERPER_USD_PER_QUERY
    assert s.search("query one")[1] == 0.0 and Client.n == 1


def test_factory_is_mock_by_default_and_real_needs_the_env(monkeypatch):
    assert isinstance(clients.make_models(), MockModels) and isinstance(clients.make_models(False), MockModels)
    monkeypatch.setattr(config, "missing_env", lambda: ["ANTHROPIC_API_KEY"])
    with pytest.raises(RuntimeError) as e:
        clients.make_models(True)
    assert "ANTHROPIC_API_KEY" in str(e.value)


def test_confidence_reflects_the_verdict_not_the_normalization():
    assert pipeline.final_confidence(0.93, "NEEDS_DOCUMENTS", "FIT", 3, True) == round(0.65 * 0.8, 2)
    assert pipeline.final_confidence(0.93, "UNKNOWN", "UNKNOWN", 0, False) == round(0.4 * 0.7, 2)
    assert pipeline.final_confidence(0.5, "ALLOWED", "FIT", 2, False) == 0.5  # never above what normalization supports
    assert pipeline.final_confidence(0.9, "ALLOWED", "NO_FIT", 2, False) == round(0.75 * 0.7, 2)
    assert pipeline.final_confidence(0.95, "ALLOWED", "FIT", 2, False) == 0.75  # the cap
    assert pipeline.unique(["a", "b", "a", "", "c", "b"]) == ["a", "b", "c"]


def test_discover_reads_documents_first_flags_them_and_limits_each_producer(tmp_path):
    hits = [{"title": "Shop page", "url": "https://shop.example/p", "snippet": ""},
            {"title": "Datasheet", "url": "https://www.se.com/ds.pdf", "snippet": ""},
            {"title": "More", "url": "https://www.se.com/a.pdf", "snippet": ""},
            {"title": "More", "url": "https://www.se.com/b.pdf", "snippet": ""}]
    assert [h["url"] for h in sorted(hits, key=clients.document_priority)][0].endswith(".pdf")
    cands = [{"producer": "Schneider Electric", "source_url": u, "specs_text": "3P, 250 A, IEC 60947-2", "alias_note": ""}
             for u in ("https://www.se.com/ds.pdf", "https://www.se.com/a.pdf", "https://www.se.com/b.pdf")]
    cands.append({"producer": "Shop", "source_url": "https://shop.example/p", "specs_text": "3P", "alias_note": ""})
    fake = FakeAnthropic(resp({"candidates": cands}))
    m = clients.RealModels(anthropic_client=fake, search=FakeSearch(hits), save_evidence=True, evidence_dir=tmp_path / "ev")
    m._item_id = "INV-0001"
    out, _ = m.discover("moulded case circuit breaker")
    assert [c.source_url for c in out].count("https://www.se.com/b.pdf") == 0 and len([c for c in out if c.producer == "Schneider Electric"]) == 2
    assert all(c.document_opened for c in out if c.source_url.endswith(".pdf"))
    assert len(m.opened_documents) == clients.MAX_DOCS and len(list((tmp_path / "ev").glob("INV-0001_real*.pdf"))) == clients.MAX_DOCS


def test_allowed_is_lowered_when_no_supplier_document_was_opened(tmp_path):
    index = idx(tmp_path)
    cand = Candidate(producer="Schneider", source_url="https://www.se.com/ds.pdf", specs_text="3P", document_opened=False)
    fake = FakeAnthropic(resp({"rule_refs": ["dewa_test_v1.md#4.5-Switchgear"], "verdict": "ALLOWED", "notes": "ok", "supplier_urls": ["https://www.se.com/ds.pdf"]}))
    m = clients.RealModels(anthropic_client=fake, index=index)
    n = Normalized(canonical_name="moulded case circuit breaker", product_type="moulded case circuit breaker", category="Electrical", specs_text="BS EN 60947", confidence=0.8)
    comp, _ = m.assess_compliance(n, [cand], [])
    assert comp.verdict == "NEEDS_DOCUMENTS" and comp.supplier_urls == [] and "opened" in comp.notes


PAGE = """<html><body><a href="/en/support">Support</a>
<a href="/product/download-pdf/LV431630?filename=ds.pdf">Download datasheet</a>
<a href="https://other-site.example/ds.pdf">Datasheet elsewhere</a>
<a href="https://www.se.com/en/certificates/type-test.pdf">Type test certificate</a>
<a href="/en/contact">Contact</a><a href="http://www.se.com/insecure.pdf">insecure</a></body></html>"""


def test_extract_links_keeps_datasheet_links_on_the_same_site_family_only():
    links = clients.extract_links(PAGE, "https://www.se.com/ae/en/product/X/")
    assert links[0].endswith("download-pdf/LV431630?filename=ds.pdf") and "https://www.se.com/en/certificates/type-test.pdf" in links
    assert not any("other-site" in x or "insecure" in x or "support" in x for x in links) and len(links) <= 2
    assert clients.registrable("eshop.se.com") == clients.registrable("www.se.com") == "se.com"


def test_discover_follows_a_datasheet_link_from_the_makers_page_and_may_cite_it(tmp_path, monkeypatch):
    pdf = {"url": "https://www.se.com/ds.pdf", "kind": "pdf", "text": "IEC 60947-2 datasheet " * 30, "raw": b"%PDF-1.4 x", "links": []}
    page = {"url": "https://www.se.com/p", "kind": "web page", "text": "Product page. " * 30, "raw": b"<html>", "links": ["https://www.se.com/ds.pdf"]}
    monkeypatch.setattr(clients, "fetch_document", lambda url, http=None, limit=0, terms=None: page if url.endswith("/p") else pdf)
    hits = [{"title": "Product", "url": "https://www.se.com/p", "snippet": ""}]
    fake = FakeAnthropic(resp({"candidates": [{"producer": "Schneider Electric", "source_url": "https://www.se.com/ds.pdf",
                                               "specs_text": "36 kA, IEC 60947-2", "alias_note": ""}]}))
    m = clients.RealModels(anthropic_client=fake, search=FakeSearch(hits), save_evidence=True, evidence_dir=tmp_path / "ev")
    m._item_id = "INV-0001"
    out, _ = m.discover("mccb")
    assert [c.source_url for c in out] == ["https://www.se.com/ds.pdf"] and out[0].document_opened  # not in the search results, but reached from a result page
    assert any("link on https://www.se.com/p" in d["via"] for d in m.opened_documents) and list((tmp_path / "ev").glob("INV-0001_real*.pdf"))


def test_query_expansion_adds_clause_style_searches_and_its_cost(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "x_v1.md").write_text("4.5.1 Main and Sub-Main Distribution Boards\n\nCircuit breakers and accessories installed in distribution boards shall comply with BS EN 60947 listed in Appendix 1.\n\n"
                               "7.2 Lighting\n\nLuminaires shall be fitted with lamps of the rated wattage marked on the fitting for the installation.\n", encoding="utf-8")
    index = rules_index.RuleIndex(d, tmp_path / "c.json").load()
    fake = FakeAnthropic(resp({"queries": ["distribution boards circuit breakers shall comply BS EN 60947", "switchgear standards appendix"]}, tin=300, tout=40))
    m = clients.RealModels(anthropic_client=fake, index=index, expand_queries=True)
    n = Normalized(canonical_name="MCCB", product_type="moulded case breaker", category="Electrical", specs_text="250 A", confidence=0.8)
    chunks = m._retrieve(n)
    assert any(c.section.startswith("4.5.1-") for c in chunks)
    assert fake.requests[0]["model"] == "claude-haiku-5-5" and "Product type: moulded case breaker" in fake.requests[0]["messages"][0]["content"]
    assert m._expansion_usage.cost_usd == clients.price_usd("claude-haiku-5-5", 300, 40)
    broken = clients.RealModels(anthropic_client=FakeAnthropic(err()), index=index, expand_queries=True)
    assert broken._retrieve(n) is not None and broken._expansion_usage.cost_usd == 0  # a failed expansion never stops the item


def two_clause_index(tmp_path):
    d = tmp_path / "rules2"
    d.mkdir()
    (d / "x_v1.md").write_text("4.5.1 Main and Sub-Main Distribution Boards\n\nThe switch gear, equipment and accessories shall generally comply with the standards specified in Appendix 1 as applicable.\n\n"
                               "7.2 Lighting\n\nLuminaires shall be fitted with lamps of the rated wattage marked on the fitting for the installation.\n", encoding="utf-8")
    return rules_index.RuleIndex(d, tmp_path / "c2.json").load()


def test_toc_numbers_map_back_to_clauses_and_respect_the_cap(tmp_path):
    index = two_clause_index(tmp_path)
    text, chunks = index.toc()
    assert text.splitlines()[0] == "## x_v1.md" and text.splitlines()[1].startswith("0 4.5.1-") and len(chunks) == 2
    short, kept = index.toc(max_chars=40)
    assert len(kept) < 2


def test_heading_selection_puts_the_models_picks_first_and_ignores_bad_numbers(tmp_path):
    index = two_clause_index(tmp_path)
    fake = FakeAnthropic(resp({"clauses": [0, 99, -1, 0]}, tin=500, tout=10))
    m = clients.RealModels(anthropic_client=fake, index=index, select_headings=True)
    n = Normalized(canonical_name="MCCB", product_type="moulded case circuit breaker", category="Electrical", specs_text="250 A", confidence=0.8)
    chunks = m._retrieve(n)
    assert chunks[0].section.startswith("4.5.1-") and m.toc_picks == [chunks[0].ref]
    req = fake.requests[0]
    assert req["model"] == "claude-haiku-5-5" and req["system"][0]["cache_control"] == {"type": "ephemeral"} and "0 4.5.1-" in req["system"][0]["text"]
    assert "Product type: moulded case circuit breaker" in req["messages"][0]["content"] and "250 A" in req["messages"][0]["content"]
    failed = clients.RealModels(anthropic_client=FakeAnthropic(err()), index=index, select_headings=True)
    assert failed._retrieve(n) is not None and failed.toc_picks == []  # a failed selection never stops the item


def test_cost_counts_prompt_cache_writes_and_reads():
    plain = clients.price_usd("claude-haiku-5-5", 1_000_000, 0)
    assert clients.price_usd("claude-haiku-5-5", 0, 0, cache_write=1_000_000) == pytest.approx(plain * 1.25)
    assert clients.price_usd("claude-haiku-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(plain * 0.1)
    r = NS(content=[NS(type="text", text="{}")], stop_reason="end_turn",
           usage=NS(input_tokens=100, output_tokens=10, cache_creation_input_tokens=5000, cache_read_input_tokens=0))
    m = clients.RealModels(anthropic_client=FakeAnthropic(r))
    _, usage = m._json("claude-haiku-5-5", "s", "u", {"type": "object"}, "low")
    assert usage.input_tokens == 5100 and usage.cost_usd == clients.price_usd("claude-haiku-5-5", 100, 10, 5000, 0)


def test_small_helper_calls_leave_room_for_adaptive_thinking(tmp_path):
    index = two_clause_index(tmp_path)
    fake = FakeAnthropic(resp({"queries": ["a b c d"]}), resp({"clauses": [0]}))
    m = clients.RealModels(anthropic_client=fake, index=index, expand_queries=True, select_headings=True)
    m._retrieve(Normalized(canonical_name="x", product_type="distribution board", category="Electrical", specs_text="", confidence=0.5))
    assert [r["max_tokens"] for r in fake.requests] == [3000, 3000]  # thinking tokens count: 600 ran out and the step silently returned nothing


def run_with_verifier(verifier_payload, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "rule_files", lambda *a, **k: ["dewa_test_v1.md"])
    hits = [{"title": "Datasheet", "url": "https://www.se.com/ds.pdf", "snippet": "NSX250"}]
    fake = FakeAnthropic(
        resp({"canonical_name": "MCCB 3P 250 A", "product_type": "moulded case circuit breaker", "category": "Electrical", "specs_text": "3P, 250 A",
              "confidence": 0.8, "too_vague": False, "vague_reason": ""}),
        resp({"candidates": [{"producer": "Other Maker", "source_url": "https://www.se.com/ds.pdf", "specs_text": "3P, 250 A, BS EN 60947", "alias_note": ""}]}),
        resp({"rule_refs": ["dewa_test_v1.md#4.5-Switchgear"], "verdict": "ALLOWED", "notes": "Complies with BS EN 60947. Table G.10 gives 6 kA.",
              "supplier_urls": ["https://www.se.com/ds.pdf"]}),
        resp(verifier_payload))
    m = clients.RealModels(anthropic_client=fake, search=FakeSearch(hits), index=idx(tmp_path))
    return pipeline.run_item(ITEM, m, pipeline.Budget(15, 300000))


def test_a_rejected_side_detail_does_not_lower_allowed(tmp_path, monkeypatch):
    out = run_with_verifier({"rejected_claims": [{"claim": "Table G.10 gives 6 kA", "affects_verdict": False}], "notes": "core supported",
                             "corrected_notes": "Complies with BS EN 60947."}, tmp_path, monkeypatch)
    row = out["row"]
    assert row["compliance_verdict"] == "ALLOWED" and not out["verifier_rejected"]  # a different maker with the same parameters is fine too
    assert "Table G.10 gives 6 kA" in row["notes"] and "Complies with BS EN 60947." in row["notes"] and "Table G.10 gives 6 kA." not in row["notes"].split("|")[-1]
    assert row["human_decision"] == "pending" and float(row["confidence"]) > 0.5


def test_a_rejected_core_claim_still_lowers_allowed(tmp_path, monkeypatch):
    out = run_with_verifier({"rejected_claims": [{"claim": "the datasheet states BS EN 60947", "affects_verdict": True}], "notes": "no",
                             "corrected_notes": ""}, tmp_path, monkeypatch)
    assert out["row"]["compliance_verdict"] == "UNKNOWN" and out["verifier_rejected"] and "core claim" in out["row"]["notes"]


def test_every_confidence_step_is_explained():
    v, why = pipeline.confidence_breakdown(0.93, "NEEDS_DOCUMENTS", "NO_FIT", 0, True)
    assert v == round(0.65 * 0.7 * 0.7 * 0.8, 2) and f"{v:.2f}" in why
    for part in ("normalization 0.93", "capped at 0.65", "NEEDS_DOCUMENTS", "no sourced producer", "NO_FIT", "verifier rejected a core claim"):
        assert part in why
    v2, why2 = pipeline.confidence_breakdown(0.5, "UNKNOWN", "FIT", 2, False)
    assert v2 == 0.4 and "capped at 0.40" in why2 and "x0.7" not in why2


def test_the_confidence_reason_is_kept_in_the_row_notes(tmp_path, monkeypatch):
    out = run_with_verifier({"rejected_claims": [], "notes": "ok", "corrected_notes": ""}, tmp_path, monkeypatch)
    assert out["row"]["compliance_verdict"] == "ALLOWED" and "capped at 0.75 because the verdict is ALLOWED" in out["row"]["notes"]
    assert f"Confidence {out['row']['confidence']} =" in out["row"]["notes"]


def opened_models(index, docs, verdict_payload):
    """RealModels with documents already opened, so only the compliance step runs."""
    fake = FakeAnthropic(resp(verdict_payload))
    m = clients.RealModels(anthropic_client=fake, index=index)
    m._opened_texts = [{"url": u, "kind": k, "text": "Datasheet text " * 40} for u, k in docs]
    return m, fake


NORM = Normalized(canonical_name="moulded case circuit breaker", product_type="moulded case circuit breaker", category="Electrical", specs_text="BS EN 60947", confidence=0.8)
OK = {"rule_refs": ["dewa_test_v1.md#4.5-Switchgear"], "verdict": "ALLOWED", "notes": "ok"}


def test_an_opened_maker_pdf_can_be_cited_even_if_the_model_made_no_candidate_for_it(tmp_path):
    m, fake = opened_models(idx(tmp_path), [("https://assets.legrand.com/pim/F01310EN-03.pdf", "pdf")],
                            {**OK, "supplier_urls": ["https://assets.legrand.com/pim/F01310EN-03.pdf"]})
    reseller = Candidate(producer="Legrand", source_url="https://shop.example/p", specs_text="32 A", document_opened=False, source_type="distributor")
    comp, _ = m.assess_compliance(NORM, [reseller], [])
    assert comp.verdict == "ALLOWED" and comp.supplier_urls == ["https://assets.legrand.com/pim/F01310EN-03.pdf"]
    sent = fake.requests[0]["messages"][0]["content"]
    assert "Opened documents" in sent and "assets.legrand.com" in sent and "maker datasheet" in fake.requests[0]["system"]


def test_allowed_needs_a_maker_document_not_only_a_retailer_page(tmp_path):
    shop = {**OK, "supplier_urls": ["https://www.amazon.ae/ABB-contactor/dp/B08"]}
    index = idx(tmp_path)
    m, _ = opened_models(index, [("https://www.amazon.ae/ABB-contactor/dp/B08", "web page")], shop)
    c = Candidate(producer="ABB", source_url="https://www.amazon.ae/ABB-contactor/dp/B08", specs_text="40 A", document_opened=True, source_type="marketplace")
    comp, _ = m.assess_compliance(NORM, [c], [])
    assert comp.verdict == "NEEDS_DOCUMENTS" and comp.supplier_urls == ["https://www.amazon.ae/ABB-contactor/dp/B08"]  # kept as a link, not as proof
    assert "retailer or marketplace" in comp.notes and "different maker with the same technical parameters is fine" in comp.notes
    # a reseller page that the model judged to be the maker's own page is accepted; so is a different maker with the same parameters
    m2, _ = opened_models(index, [("https://new.abb.com/products/ESB40", "web page")], {**OK, "supplier_urls": ["https://new.abb.com/products/ESB40"]})
    maker = Candidate(producer="ABB", source_url="https://new.abb.com/products/ESB40", specs_text="40 A", document_opened=True, source_type="maker")
    assert m2.assess_compliance(NORM, [maker], [])[0].verdict == "ALLOWED"
    # a datasheet PDF counts even on a distributor's server
    m3, _ = opened_models(index, [("https://pim.galco.com/ds?id=1", "pdf")], {**OK, "supplier_urls": ["https://pim.galco.com/ds?id=1"]})
    assert m3.assess_compliance(NORM, [], [])[0].verdict == "ALLOWED"


def test_verifier_sees_the_opened_flag_and_the_opened_documents(tmp_path):
    fake = FakeAnthropic(resp({"rejected_claims": [], "notes": "ok", "corrected_notes": ""}))
    m = clients.RealModels(anthropic_client=fake, index=idx(tmp_path))
    m._opened_texts = [{"url": "https://a.example/ds.pdf", "kind": "pdf", "text": "IEC 60947-2 " * 30}]
    cand = Candidate(producer="X", source_url="https://a.example/ds.pdf", specs_text="3P", document_opened=True, source_type="maker")
    m.verify(NORM, [cand], Compliance(rule_refs=[], verdict="ALLOWED"))
    sent = fake.requests[0]["messages"][0]["content"]
    assert '"document_opened_and_read": true' in sent and "IEC 60947-2" in sent and '"source_type": "maker"' in sent



def rerank_pool(n=40):
    return [rules_index.Chunk("x_v1.md", f"{i}.1-Item-{i}", 1, f"Clause {i} on breakers and switchgear.") for i in range(n)]


RERANK_PRODUCT = Normalized(canonical_name="moulded case circuit breaker", product_type="moulded case circuit breaker", category="Electrical", specs_text="250 A", confidence=0.8)


def test_haiku_orders_the_pool_ignores_bad_numbers_and_adds_its_cost():
    fake = FakeAnthropic(resp({"ranking": [37, 99, -1, 5, 37, 2]}, tin=4000, tout=30))
    seen = []
    m = clients.RealModels(anthropic_client=fake, rerank_with_haiku=True, say=seen.append)
    pool = rerank_pool()
    out, u = m._rerank(RERANK_PRODUCT, pool)
    req = fake.requests[0]
    assert req["model"] == "claude-haiku-5-5" and req["max_tokens"] == 3000
    assert "<data>" in req["messages"][0]["content"] and "[39] " in req["messages"][0]["content"]
    assert [c.section for c in out[:3]] == ["37.1-Item-37", "5.1-Item-5", "2.1-Item-2"]
    assert len(out) == 40 and len({c.ref for c in out}) == 40  # nothing lost or repeated; unranked clauses follow in their old order
    assert out[3].section == "0.1-Item-0" and u.cost_usd == clients.price_usd("claude-haiku-5-5", 4000, 30) and any("Ranking 40" in s for s in seen)


def test_a_failed_rerank_keeps_the_original_order_and_costs_nothing():
    m = clients.RealModels(anthropic_client=FakeAnthropic(err()), rerank_with_haiku=True)
    pool = rerank_pool()
    out, u = m._rerank(RERANK_PRODUCT, pool)
    assert out == pool and u.cost_usd == 0


def test_the_factory_ranks_with_haiku_and_leaves_voyage_off_unless_asked(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "SERPER_API_KEY"):
        monkeypatch.setenv(k, "x-not-a-real-key")
    monkeypatch.setenv("VOYAGE_EMBED_MODEL", "voyage-test")
    monkeypatch.delenv("USE_VOYAGE_RERANK", raising=False)
    m = clients.make_models(True)
    assert m.rerank_with_haiku is True and m.embedder is None
