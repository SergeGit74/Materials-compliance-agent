import threading
import time

import pytest

from src import appcore, config, store
from src.batch import BatchRunner
from src.models import MockModels, Usage, Verification


def make_items(n, category="Electrical", desc="Cable"):
    return [{c: "" for c in config.INVENTORY_COLUMNS} | {
        "Item_ID": f"INV-{i:04d}", "Category": category, "Internal_Description": f"{desc} {i}",
        "Key_Specs": "10A"} for i in range(1, n + 1)]


class Costly(MockModels):
    def __init__(self, cost=0.0, gate=None, fail=False, reject=False, delay=0.0):
        super().__init__()
        self.cost, self.gate, self.fail, self.reject, self.delay = cost, gate, fail, reject, delay

    def normalize(self, item, specs):
        if self.gate:
            self.gate.wait(5)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        n, _ = super().normalize(item, specs)
        return n, Usage(input_tokens=10, cost_usd=self.cost)

    def verify(self, n, c, comp):
        return Verification(rejected_claims=["x"] if self.reject else []), Usage()


def runner(tmp_path, items, factory, **kw):
    return BatchRunner(items, factory, results_path=tmp_path / "r.csv", items_dir=tmp_path / "items", **kw)


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_runs_all_and_writes_rows_pending(tmp_path):
    r = runner(tmp_path, make_items(5), Costly, concurrency=3)
    r.start()
    r.join()
    assert wait_for(lambda: r.snapshot()["state"] == "done")
    rows = store.read_rows(tmp_path / "r.csv")
    assert sorted(x["item_id"] for x in rows) == [f"INV-{i:04d}" for i in range(1, 6)]
    assert all(x["human_decision"] == "pending" for x in rows)
    assert len(list((tmp_path / "items").glob("*.md"))) == 5


def test_resume_skips_items_with_rows(tmp_path):
    items = make_items(3)
    store.append_row({"item_id": "INV-0001", "human_decision": "pending"}, tmp_path / "r.csv")
    r = runner(tmp_path, items, Costly)
    r.start()
    r.join()
    assert wait_for(lambda: r.snapshot()["state"] == "done")
    assert r.finished == ["INV-0002", "INV-0003"]


def test_stop_after_each_forces_one_and_waits_for_continue(tmp_path):
    r = runner(tmp_path, make_items(4), Costly, concurrency=4, stop_after_each=True)
    r.start()
    assert wait_for(lambda: r.snapshot()["waiting_continue"])
    time.sleep(0.3)
    assert len(store.read_rows(tmp_path / "r.csv")) == 1  # nothing else started
    assert r.snapshot()["state"] == "waiting_continue"
    r.continue_()
    assert wait_for(lambda: len(store.read_rows(tmp_path / "r.csv")) == 2)
    assert wait_for(lambda: r.snapshot()["waiting_continue"])
    time.sleep(0.2)
    assert len(store.read_rows(tmp_path / "r.csv")) == 2
    r.stop_after_current()
    r.join()


def test_switch_on_mid_run_stops_after_in_progress(tmp_path):
    gate = threading.Event()
    r = runner(tmp_path, make_items(6), lambda: Costly(gate=gate), concurrency=2)
    r.start()
    assert wait_for(lambda: len(r.snapshot()["in_flight"]) == 2)
    r.set_stop_after_each(True)
    gate.set()
    assert wait_for(lambda: r.snapshot()["waiting_continue"])
    time.sleep(0.3)
    assert len(store.read_rows(tmp_path / "r.csv")) == 2  # the two in progress finished, no new item
    r.stop_after_current()
    r.join()


def test_stop_after_current_writes_in_flight_rows(tmp_path):
    gate = threading.Event()
    r = runner(tmp_path, make_items(6), lambda: Costly(gate=gate), concurrency=2)
    r.start()
    assert wait_for(lambda: len(r.snapshot()["in_flight"]) == 2)
    r.stop_after_current()
    gate.set()
    r.join()
    assert wait_for(lambda: r.snapshot()["state"] == "stopped")
    assert len(store.read_rows(tmp_path / "r.csv")) == 2


def test_cancel_now_discards_in_flight(tmp_path):
    gate = threading.Event()
    r = runner(tmp_path, make_items(4), lambda: Costly(gate=gate), concurrency=2)
    r.start()
    assert wait_for(lambda: len(r.snapshot()["in_flight"]) == 2)
    r.cancel_now()
    gate.set()
    r.join()
    assert wait_for(lambda: r.snapshot()["state"] == "cancelled")
    assert store.read_rows(tmp_path / "r.csv") == []  # aborted items get no row


def test_spend_cap_pauses_and_needs_higher_cap(tmp_path):
    r = runner(tmp_path, make_items(5), lambda: Costly(cost=0.5), spend_cap=1.0)
    r.start()
    assert wait_for(lambda: r.snapshot()["state"] == "paused")
    snap = r.snapshot()
    assert "spend cap" in snap["pause_reason"] and snap["cost"] >= 1.0
    n = len(store.read_rows(tmp_path / "r.csv"))
    time.sleep(0.2)
    assert len(store.read_rows(tmp_path / "r.csv")) == n  # nothing runs while paused
    with pytest.raises(ValueError):
        r.resume()
    r.resume(new_cap=100)
    r.join()
    assert wait_for(lambda: r.snapshot()["state"] == "done")
    assert len(store.read_rows(tmp_path / "r.csv")) == 5


def test_three_errors_in_a_row_pause(tmp_path):
    r = runner(tmp_path, make_items(6), lambda: Costly(fail=True))
    r.start()
    assert wait_for(lambda: r.snapshot()["state"] == "paused")
    assert "3 errors" in r.snapshot()["pause_reason"]
    assert len(r.snapshot()["failed"]) == 3
    assert store.read_rows(tmp_path / "r.csv") == []  # failed items get no row
    r.cancel_now()
    r.join()


def test_budget_exceeded_pauses(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_TOKENS_PER_ITEM", "5")
    r = runner(tmp_path, make_items(3), Costly)
    r.start()
    assert wait_for(lambda: r.snapshot()["state"] == "paused")
    assert "budget" in r.snapshot()["pause_reason"]
    r.cancel_now()
    r.join()


def test_verifier_rejection_rate_pauses(tmp_path):
    r = runner(tmp_path, make_items(8), lambda: Costly(reject=True))
    r.start()
    assert wait_for(lambda: r.snapshot()["state"] == "paused")
    assert "verifier rejected" in r.snapshot()["pause_reason"]
    r.cancel_now()
    r.join()


# ---- appcore ----

def test_estimate_and_cap():
    e = appcore.estimate(35, 1)
    assert e["cost_usd"] == (10.5, 21.0, 52.5)  # README: about $11 / $21 / $53
    assert appcore.estimate(35, 4)["minutes"][0] == pytest.approx(21.9, abs=0.1)
    assert appcore.default_cap(35) == 31.5


def test_eligible_excludes_safety_by_default():
    items = make_items(3) + make_items(2, "Fire & Life Safety", "Sprinkler")
    items[3]["Item_ID"], items[4]["Item_ID"] = "INV-0101", "INV-0102"
    ids = [i["Item_ID"] for i in appcore.eligible_for_batch(items, {"INV-0001"})]
    assert ids == ["INV-0002", "INV-0003"]
    ids = [i["Item_ID"] for i in appcore.eligible_for_batch(items, {"INV-0001"}, exclude_safety=False)]
    assert ids == ["INV-0002", "INV-0003", "INV-0101", "INV-0102"]
    assert len(appcore.eligible_for_batch(items, set(), max_items=2)) == 2


def test_set_decision_changes_only_that_column(tmp_path):
    p = tmp_path / "r.csv"
    store.append_row({"item_id": "A", "notes": "n", "human_decision": "pending"}, p)
    store.append_row({"item_id": "B", "notes": "m", "human_decision": "pending"}, p)
    store.set_decision("A", "approved", p)
    rows = store.read_rows(p)
    assert [(r["human_decision"], r["notes"]) for r in rows] == [("approved", "n"), ("pending", "m")]
    with pytest.raises(ValueError):
        store.set_decision("A", "skipped", p)
