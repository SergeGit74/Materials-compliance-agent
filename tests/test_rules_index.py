from src import rules_index as ri


HEAD = "| File | Issuer | Title | Version | Source | Added | Confirmed by |" + chr(10) + "|---|---|---|---|---|---|---|" + chr(10)

RULES = """4.5 Switchgear

Low voltage switchgear and control gear assemblies of MDB and SMDB shall generally comply with the standards in Appendix 1, including BS EN 60947 for moulded case circuit breakers.

4.6 Motors

Every motor above 0.37 kW shall have control equipment with overload protection and a thermal overload relay in the starter.

4.5 Switchgear again

A later chapter reuses the number 4.5 for a different subject: luminaires and lighting fittings shall be marked with their rated wattage.
"""


def index(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "dewa_test_v1.md").write_text(RULES, encoding="utf-8")
    (d / "INDEX.md").write_text(HEAD + "| dewa_test_v1.md | dewa | T | 2020 | u | 2026 | Reviewer |" + chr(10), encoding="utf-8")
    (d / "README.md").write_text("x", encoding="utf-8")
    return ri.RuleIndex(d, tmp_path / "cache.json").load()


def test_chunks_carry_file_and_section_and_repeats_get_the_page(tmp_path):
    idx = index(tmp_path)
    refs = [c.ref for c in idx.chunks]
    assert "dewa_test_v1.md#4.5-Switchgear" in refs and "dewa_test_v1.md#4.6-Motors" in refs  # number plus a readable heading
    assert "dewa_test_v1.md#4.5-Switchgear-again" in refs and len(refs) == len(set(refs))
    assert not idx.skipped and all(c.file == "dewa_test_v1.md" for c in idx.chunks)  # INDEX.md and README.md are not rulebooks


def test_bm25_search_finds_the_clause_and_filters_by_file(tmp_path):
    idx = index(tmp_path)
    assert idx.search("moulded case circuit breaker BS EN 60947", k=1)[0].section == "4.5-Switchgear"
    assert idx.search("motor overload relay", k=1)[0].section == "4.6-Motors"
    assert idx.search("nonsense words here", k=3) == []
    assert idx.search("motor", files={"other.pdf"}) == []


def test_cache_is_reused_until_the_file_changes(tmp_path):
    index(tmp_path)
    first = (tmp_path / "cache.json").read_text(encoding="utf-8")
    ri.RuleIndex(tmp_path / "rules", tmp_path / "cache.json").load()
    assert (tmp_path / "cache.json").read_text(encoding="utf-8") == first
    new = RULES + "\n4.7 New\n\nA new clause about earthing conductors and bonding of metal parts in the installation.\n"
    (tmp_path / "rules" / "dewa_test_v1.md").write_text(new, encoding="utf-8")
    assert any(c.section.startswith("4.7-") for c in ri.RuleIndex(tmp_path / "rules", tmp_path / "cache.json").load().chunks)


def test_shifted_text_decoder_and_readability():
    assert ri.decode_shifted("6(,60,&") == "".join(chr(ord(c) + 29) for c in "6(,60,&")
    assert not ri.readable(["6(,60,&$1$/<6,6 " * 300])
    assert ri.readable(["the building shall be designed for the loads of and with " * 60])


class FakeEmbedder:
    model = "fake"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.0] if "overload" in t else [0.0, 1.0] for t in texts]


def test_embedding_rerank_uses_a_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "VEC_CACHE", tmp_path / "vec.json")
    idx = index(tmp_path)
    emb = FakeEmbedder()
    top = idx.search("overload motor starter", k=1, embedder=emb)
    assert top[0].section == "4.6-Motors" and emb.calls == 1
    idx.search("overload motor starter", k=1, embedder=emb)
    assert emb.calls == 1  # same texts: vectors came from the cache, nothing was embedded again


def test_embedding_failure_falls_back_to_keyword_ranking(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "VEC_CACHE", tmp_path / "vec2.json")

    class Broken:
        model = "broken"

        def embed(self, texts):
            raise RuntimeError("rate limited")

    idx = index(tmp_path)
    top = idx.search("motor overload relay", k=1, embedder=Broken())
    assert top[0].section == "4.6-Motors" and idx.embed_error == "RuntimeError"


def test_toc_drops_table_row_headings_but_keeps_files_without_dotted_numbers(tmp_path):
    d = tmp_path / "r3"
    d.mkdir()
    (d / "a_v1.md").write_text("4.5.1 Distribution boards\n\nThe switch gear and accessories shall comply with the standards in Appendix 1 as applicable here.\n\n"
                               "40 KA\n\nA table row about a fault rating that has no clause number and should not be offered to the model.\n", encoding="utf-8")
    (d / "b_v1.md").write_text("1 Cameras\n\nCameras shall be coloured and the resolution shall not be less than Full HD 1080p on the digital system.\n\n"
                               "2 Recorders\n\nRecording devices shall be digital and shall record at no less than 25 frames per second per camera.\n", encoding="utf-8")
    idx = ri.RuleIndex(d, tmp_path / "c3.json").load()
    text, chunks = idx.toc()
    sections = [c.section for c in chunks]
    assert any(s.startswith("4.5.1-") for s in sections) and not any(s.startswith("40-") for s in sections)
    assert any(s.startswith("1-Cameras") for s in sections)  # file b has no dotted headings, so all of its headings stay
    assert ri.is_clause_heading("D.5-Safety") and ri.is_clause_heading("K.3.6-Incoming@p693") and not ri.is_clause_heading("40-KA") and not ri.is_clause_heading("p2")
