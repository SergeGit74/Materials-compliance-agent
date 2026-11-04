"""Hosted deployment: secrets lookup order, demo passcode, real-mode caps, rulebook fetch on first start. No network, no API call."""
import hmac
import json
import threading

import httpx
import pytest
from dotenv import load_dotenv

from src import config, demo, fetch_rules, store
from src.batch import BatchRunner
from src.export_clean import ALWAYS_SKIP_FILES
from src.models import MockModels
from tests.test_batch import make_items, wait_for
from tests.test_fetch_rules import HEAD, NL, PDF, client, ok, rules

# ---------- secrets ----------


def test_secrets_fill_only_what_the_environment_leaves_empty(monkeypatch, capsys):
    env = {"ANTHROPIC_API_KEY": "from-env", "MODEL_REASON": "  "}
    secrets = {"ANTHROPIC_API_KEY": "from-secrets", "MODEL_REASON": "reason-model", "SERPER_API_KEY": "serp", "NOT_A_SETTING": "x"}
    applied = config.apply_secrets(secrets, env)
    assert env["ANTHROPIC_API_KEY"] == "from-env"  # the environment wins
    assert env["MODEL_REASON"] == "reason-model" and env["SERPER_API_KEY"] == "serp"  # secrets fill empty or missing names
    assert "NOT_A_SETTING" not in env  # only known setting names are copied
    assert sorted(applied) == ["MODEL_REASON", "SERPER_API_KEY"]  # names only, never values
    out = capsys.readouterr()
    assert "from-secrets" not in out.out + out.err and "serp" not in "".join(applied)


def test_dotenv_comes_last_and_a_missing_dotenv_is_not_an_error(tmp_path, monkeypatch):
    for n in ("MODEL_NORMALIZE", "MODEL_VERIFY", "SERPER_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("SERPER_API_KEY", "from-environment")
    config.apply_secrets({"MODEL_NORMALIZE": "from-secrets"})  # fills os.environ
    env_file = tmp_path / ".env"
    env_file.write_text("MODEL_NORMALIZE=from-dotenv\nMODEL_VERIFY=from-dotenv\nSERPER_API_KEY=from-dotenv\n", encoding="utf-8")
    load_dotenv(env_file)  # what config.missing_env() does: it never overrides a name that is already set
    import os
    assert os.environ["SERPER_API_KEY"] == "from-environment"  # 1. environment
    assert os.environ["MODEL_NORMALIZE"] == "from-secrets"  # 2. secrets beat .env
    assert os.environ["MODEL_VERIFY"] == "from-dotenv"  # 3. .env fills the rest
    assert load_dotenv(tmp_path / "absent.env") is False  # no .env file: not an error


def test_no_secrets_file_or_a_broken_secrets_object_is_not_an_error():
    class Broken:
        def get(self, name):
            raise RuntimeError("no secrets file")

    env = {}
    assert config.apply_secrets(Broken(), env) == [] and env == {}


def test_secrets_example_lists_names_only_and_the_real_file_is_ignored_and_never_exported():
    from pathlib import Path
    root = Path(config.ROOT)
    example = (root / ".streamlit" / "secrets.toml.example").read_text(encoding="utf-8")
    for n in config.SECRET_NAMES:
        assert n in example
    assert 'ANTHROPIC_API_KEY = ""' in example and "sk-" not in example
    ignored = [x.strip() for x in (root / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".streamlit/secrets.toml" in ignored and "rules/*.pdf" in ignored and ".env" in ignored
    assert "secrets.toml" in ALWAYS_SKIP_FILES  # src/export_clean.py never copies it


# ---------- passcode ----------


def test_passcode_is_compared_in_constant_time_and_never_empty(monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(demo.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert demo.check_passcode("open sesame", "open sesame") is True
    assert demo.check_passcode("open sesame!", "open sesame") is False
    assert demo.check_passcode("Open sesame", "open sesame") is False
    assert calls  # the comparison went through hmac.compare_digest
    assert demo.check_passcode("", "") is False and demo.check_passcode("anything", "") is False  # no passcode set: locked
    assert demo.check_passcode("pässwörd", "pässwörd") is True  # non-ASCII does not raise


def test_demo_mode_flag_and_cap_settings():
    assert demo.is_demo({"DEMO_MODE": "true"}) and demo.is_demo({"DEMO_MODE": " 1 "}) and not demo.is_demo({"DEMO_MODE": "false"}) and not demo.is_demo({})
    assert demo.caps({}) == (10, 30)
    assert demo.caps({"DEMO_MAX_REAL_ITEMS_PER_SESSION": "3", "DEMO_MAX_REAL_ITEMS_PER_DAY": "7"}) == (3, 7)
    assert demo.caps({"DEMO_MAX_REAL_ITEMS_PER_SESSION": "many", "DEMO_MAX_REAL_ITEMS_PER_DAY": "-4"}) == (10, 30)  # bad values fall back to the defaults


# ---------- spend caps ----------


def test_session_cap_stops_reservations_and_shows_the_remaining_count(tmp_path):
    lim = demo.DemoLimiter(3, 30, tmp_path / "c.json", today=lambda: "2026-11-03")
    assert lim.remaining() == 3
    assert [lim.reserve() for _ in range(4)] == [True, True, True, False]
    assert lim.remaining() == 0 and lim.session_used == 3 and lim.day_used() == 3
    assert "left: 0" in lim.summary()
    assert lim.reserve(0) is False


def test_day_cap_is_shared_by_sessions_and_resets_on_a_new_day(tmp_path):
    day = ["2026-11-03"]
    f = tmp_path / "c.json"
    a, b = (demo.DemoLimiter(10, 4, f, today=lambda: day[0]) for _ in range(2))  # two visitors
    assert a.reserve(3) is True
    assert b.remaining() == 1  # the day cap is app-wide
    assert b.reserve(2) is False and b.reserve(1) is True and a.reserve(1) is False
    day[0] = "2026-11-04"
    assert a.remaining() == 4  # a new day opens the day cap again (this session still has 7 of its own)
    assert demo.DemoLimiter(10, 4, f, today=lambda: day[0]).day_used() == 0


def test_a_damaged_counter_file_counts_as_full(tmp_path):
    f = tmp_path / "c.json"
    f.write_text("{not json", encoding="utf-8")
    lim = demo.DemoLimiter(10, 30, f)
    assert lim.remaining() == 0 and lim.reserve() is False


def test_reservations_from_many_threads_never_pass_the_cap(tmp_path):
    lim = demo.DemoLimiter(100, 5, tmp_path / "c.json")
    got = []
    ts = [threading.Thread(target=lambda: got.append(lim.reserve())) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(got) == 5 and lim.day_used() == 5
    assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["count"] == 5


def test_a_batch_stops_and_pauses_when_the_demo_cap_runs_out(tmp_path):
    lim = demo.DemoLimiter(2, 30, tmp_path / "c.json")
    r = BatchRunner(make_items(5), lambda: MockModels(), results_path=tmp_path / "r.csv", items_dir=tmp_path / "items", item_gate=lim.reserve)
    r.start()
    assert wait_for(lambda: r.snapshot()["state"] == "paused")
    s = r.snapshot()
    assert s["done"] == 2 and "cap" in s["pause_reason"] and lim.remaining() == 0  # exactly the allowed number of items ran
    assert len(store.read_rows(tmp_path / "r.csv")) == 2
    r.cancel_now()


# ---------- rulebooks on first start ----------


def test_nothing_is_fetched_when_every_confirmed_pdf_is_already_there(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://dmpmedia.dm.gov.ae/a.pdf"), ("b.pdf", "https://www.dewa.gov.ae/b.ashx")])
    (d / "a.pdf").write_bytes(b"%PDF-mine")
    (d / "b.pdf").write_bytes(b"%PDF-mine")
    seen = []
    info = fetch_rules.ensure_rules(d, http=client(lambda req: seen.append(req) or httpx.Response(200, content=PDF)), progress=None)
    assert info == {"ran": False, "installed": 0, "problems": []} and seen == []


def test_a_restart_retries_only_the_files_that_are_still_missing(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://dmpmedia.dm.gov.ae/a.pdf"), ("b.pdf", "https://www.dewa.gov.ae/b.ashx")])
    (d / "a.pdf").write_bytes(b"%PDF-mine")
    seen, lines = [], []
    info = fetch_rules.ensure_rules(d, http=client(lambda req: seen.append(req.url.host) or httpx.Response(200, content=PDF)), progress=lines.append)
    assert info["ran"] and info["installed"] == 1 and seen == ["www.dewa.gov.ae"]  # a.pdf is not fetched again and is never overwritten
    assert (d / "a.pdf").read_bytes() == b"%PDF-mine" and (d / "b.pdf").read_bytes() == PDF
    assert any("downloading b.pdf" in x for x in lines) and not any("a.pdf" in x for x in lines)


def test_the_reason_a_download_failed_is_reported_and_logged(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://www.dewa.gov.ae/a.ashx")])
    lines = []
    info = fetch_rules.ensure_rules(d, http=client(lambda req: httpx.Response(403)), progress=lines.append)
    assert info["problems"] == [{"file": "a.pdf", "status": "failed", "note": "HTTP 403"}]
    assert any("a.pdf failed (HTTP 403)" in x for x in lines)


def test_a_download_that_takes_too_long_is_given_up(tmp_path, monkeypatch):
    d = rules(tmp_path, [("a.pdf", "https://www.dewa.gov.ae/a.ashx")])
    clock = iter(range(0, 1000, 100))  # each reading of the clock is 100 s later
    monkeypatch.setattr(fetch_rules.time, "monotonic", lambda: next(clock))
    info = fetch_rules.ensure_rules(d, http=client(ok), max_seconds=50, progress=None)
    assert info["problems"] and "took longer than 50 s" in info["problems"][0]["note"] and not (d / "a.pdf").exists()


def test_first_start_downloads_the_listed_files_and_reports_the_ones_that_failed(tmp_path):
    d = rules(tmp_path, [("a.pdf", "https://dmpmedia.dm.gov.ae/a.pdf"), ("b.pdf", "https://random.example.com/b.pdf"),
                         ("c.pdf", "https://www.dewa.gov.ae/c.ashx")])
    (d / "CHECKSUMS.json").write_text("{}", encoding="utf-8")

    def handler(req):
        return httpx.Response(404) if req.url.path.endswith("c.ashx") else httpx.Response(200, content=PDF)

    info = fetch_rules.ensure_rules(d, http=client(handler), staging_dir=tmp_path / "st")
    assert info["ran"] and info["installed"] == 1 and (d / "a.pdf").read_bytes() == PDF
    assert {p["file"] for p in info["problems"]} == {"b.pdf", "c.pdf"} and all(p["note"] for p in info["problems"])


def test_a_pending_only_rulebook_does_not_count_and_a_failure_never_stops_the_app(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "INDEX.md").write_text(HEAD + "| p.pdf | dm | T | 2020 | https://dmpmedia.dm.gov.ae/p.pdf | 2026 | PENDING (check) |" + NL, encoding="utf-8")
    (d / "p.pdf").write_bytes(b"%PDF-pending")
    assert fetch_rules.has_confirmed_pdf(d) is False and fetch_rules.missing_confirmed(d) == []
    assert fetch_rules.ensure_rules(d, http=client(ok), progress=None)["ran"] is False  # only confirmed rows are fetched on start

    def boom(req):
        raise httpx.ConnectError("offline")

    d2 = rules(tmp_path / "x", [("c.pdf", "https://www.dewa.gov.ae/c.ashx")]) if (tmp_path / "x").mkdir() is None else None
    info = fetch_rules.ensure_rules(d2, http=client(boom), progress=None)
    assert info["ran"] and info["installed"] == 0 and "network error" in info["problems"][0]["note"]
    assert fetch_rules.ensure_rules(tmp_path / "no-such-folder", progress=None)["installed"] == 0  # a missing rules folder does not raise either


# ---------- the app in demo mode (mock models only) ----------

needs_env = pytest.mark.skipif(bool(config.missing_env()), reason=".env is not set up on this machine")


def demo_app(monkeypatch, **env):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("DEMO_MODE", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return AppTest.from_file("../app.py", default_timeout=120).run()


@needs_env
def test_demo_banner_default_reviewer_and_locked_real_switch(monkeypatch, tmp_path):
    from src import settings
    saved = settings.get_reviewer()
    at = demo_app(monkeypatch, DEMO_PASSCODE="test")
    assert not at.exception
    assert any("Demo on synthetic data. Saved decisions are not kept after the app restarts." in i.value for i in at.info)
    assert at.sidebar.text_input(key="reviewer").value == "Project owner" and at.sidebar.text_input(key="reviewer").disabled
    sw = at.sidebar.checkbox(key="use_real")
    assert sw.disabled  # locked until the passcode is entered
    assert settings.get_reviewer() == saved  # reviewer.local.json was not touched
    assert len(at.sidebar.radio[0].options) == 7  # no screen is hidden


@needs_env
def test_wrong_passcode_keeps_it_locked_and_the_right_one_unlocks_after_typing_spend(monkeypatch):
    at = demo_app(monkeypatch, DEMO_PASSCODE="test")
    at.sidebar.text_input(key="demo_code").set_value("nope")
    at.sidebar.text_input(key="demo_spend").set_value("spend")
    at.sidebar.button(key="demo_unlock").click().run()
    assert at.sidebar.checkbox(key="use_real").disabled  # wrong passcode
    at.sidebar.text_input(key="demo_code").set_value("test")
    at.sidebar.text_input(key="demo_spend").set_value("")
    at.sidebar.button(key="demo_unlock").click().run()
    assert at.sidebar.checkbox(key="use_real").disabled  # right passcode but no "spend" confirmation
    at.sidebar.text_input(key="demo_code").set_value("test")
    at.sidebar.text_input(key="demo_spend").set_value("spend")
    at.sidebar.button(key="demo_unlock").click().run()
    assert not at.sidebar.checkbox(key="use_real").disabled


@needs_env
def test_no_passcode_configured_means_real_models_stay_locked(monkeypatch):
    at = demo_app(monkeypatch, DEMO_PASSCODE="")
    assert at.sidebar.checkbox(key="use_real").disabled


@needs_env
def test_locked_demo_cannot_overwrite_a_result_even_if_the_switch_state_is_forced(monkeypatch):
    at = demo_app(monkeypatch, DEMO_PASSCODE="test")
    at.session_state["use_real"] = True  # forced; the switch is disabled, and the app must still treat the mode as mock
    at.run()
    box = [b for b in at.button if b.label == "Run this item"][0]
    assert box.disabled  # INV-0001 has a result, and a mock run may not replace it
    assert any("would replace this item's existing result" in w.value for w in at.warning)


@needs_env
def test_unlocked_demo_with_no_real_items_left_disables_every_run_button(monkeypatch):
    at = demo_app(monkeypatch, DEMO_PASSCODE="test", DEMO_MAX_REAL_ITEMS_PER_SESSION="0")
    at.sidebar.text_input(key="demo_code").set_value("test")
    at.sidebar.text_input(key="demo_spend").set_value("spend")
    at.sidebar.button(key="demo_unlock").click().run()
    at.sidebar.checkbox(key="use_real").check().run()
    assert not at.exception
    assert [b for b in at.button if b.label == "Run next item"][0].disabled
    assert any("Real-mode items left: 0" in c.value for c in at.sidebar.caption)
    assert any("limit for this demo is used up" in w.value for w in at.warning)
