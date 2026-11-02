"""Smoke test of the Check a new item screen in mock mode. It writes nothing: no save button is pressed."""
import pytest

from src import config

pytestmark = pytest.mark.skipif(bool(config.missing_env()), reason=".env is not set up on this machine")


def test_mock_check_shows_estimate_stages_result_and_the_save_prompt():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("../app.py", default_timeout=90).run()
    at.sidebar.radio[0].set_value("Check a new item").run()
    assert not at.exception
    assert not [b for b in at.button if b.label.startswith("Run")]  # nothing to run until something is typed
    at.text_input(key="n_name").set_value("circuit breaker, 3 pole, 160 A, 36 kA").run()
    est = {m.label: m.value for m in at.metric}
    assert est["Cost"] == "$0.00" and est["Mode"].startswith("Mock")  # the estimate is shown BEFORE the run
    [b for b in at.button if b.label.startswith("Run")][0].click().run()
    assert not at.exception
    subs = [s.value for s in at.subheader]
    assert any(s.startswith("Result for: circuit breaker") for s in subs) and "Next step: update the databases?" in subs
    buttons = {b.label: b.disabled for b in at.button}
    assert buttons["Save entry and result"] is True  # a mock result is only a placeholder
    assert buttons["Save the entry only"] is False and buttons["Discard"] is False
    [b for b in at.button if b.label == "Discard"][0].click().run()
    assert not at.exception and "Discarded" in " ".join(s.value for s in at.success)
