"""Batch mode engine (README 7.3). Started only by a person clicking Run batch in app.py.

Rules enforced here:
- every item's row and report are written as soon as it finishes, never at the end;
- items already in results.csv are skipped, so a stopped or crashed batch can resume;
- Stop after each item forces concurrency 1 and waits for Continue after every item;
- Stop after current item lets in-flight items finish; Cancel now discards them (no row);
- the batch pauses and asks on: 3 errors in a row, an item over its budget, the spend cap,
  or the verifier rejecting more than 30 percent of the last 10 items.
"""
import threading
import time
from collections import deque
from pathlib import Path

from . import config, pipeline, store

MAX_CONSECUTIVE_ERRORS = 3
REJECT_WINDOW = 10
REJECT_LIMIT = 0.30
REJECT_MIN_ITEMS = 5  # do not judge a rate from fewer items than this


class BatchRunner:
    def __init__(self, items: list[dict], models_factory, concurrency: int = 1, spend_cap: float = 0.0,
                 stop_after_each: bool = False, results_path: Path = config.RESULTS_CSV,
                 items_dir: Path = config.ITEMS_DIR, item_gate=None):
        self.item_gate = item_gate  # called once before each item starts; False pauses the batch (public-demo cap, src/demo.py)
        self.queue = deque(items)
        self.total = len(items)
        self.models_factory = models_factory
        self.concurrency = max(1, min(4, concurrency))
        self.spend_cap = spend_cap
        self.results_path, self.items_dir = Path(results_path), Path(items_dir)

        self._cond = threading.Condition()
        self._stop_after_each = stop_after_each
        self._stop = self._cancel = False
        self._waiting_continue = False
        self._pause_reason = ""
        self._in_flight: dict[int, str] = {}
        self._errors_in_row = 0
        self._recent_rejects: deque[bool] = deque(maxlen=REJECT_WINDOW)
        self.finished: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.last_finished = ""
        self.cost = 0.0
        self.tokens = 0
        self.started = self.ended = 0.0
        self.state = "idle"
        self._threads: list[threading.Thread] = []

    # ---- controls ----
    def start(self) -> None:
        with self._cond:
            if self.state != "idle":
                raise RuntimeError("Batch already started.")
            self.state = "running"
            self.started = time.time()
        workers = [threading.Thread(target=self._worker, args=(i,), daemon=True) for i in range(self.concurrency)]
        self._threads = workers
        for t in workers:
            t.start()
        threading.Thread(target=self._finish_when_done, daemon=True).start()

    def stop_after_current(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    def cancel_now(self) -> None:
        with self._cond:
            self._cancel = True
            self._cond.notify_all()

    def set_stop_after_each(self, on: bool) -> None:
        with self._cond:
            self._stop_after_each = on
            if not on:
                self._waiting_continue = False
            self._cond.notify_all()

    def continue_(self) -> None:
        with self._cond:
            self._waiting_continue = False
            self._cond.notify_all()

    def resume(self, new_cap: float | None = None) -> None:
        with self._cond:
            if "spend cap" in self._pause_reason:
                if new_cap is None or new_cap <= self.cost:
                    raise ValueError("Raise the spend cap above the cost so far to continue.")
                self.spend_cap = new_cap
            self._pause_reason = ""
            self._errors_in_row = 0
            self._recent_rejects.clear()
            self._cond.notify_all()

    def join(self, timeout: float | None = None) -> None:
        for t in self._threads:
            t.join(timeout)
        time.sleep(0.05)

    # ---- state ----
    def _effective_concurrency(self) -> int:
        return 1 if self._stop_after_each else self.concurrency

    def snapshot(self) -> dict:
        with self._cond:
            done = len(self.finished)
            end = self.ended or time.time()
            elapsed = (end - self.started) if self.started else 0.0
            remaining = len(self.queue) + len(self._in_flight)
            eta = (elapsed / done * remaining / max(1, self._effective_concurrency())) if done else None
            return {
                "state": self.state, "done": done, "total": self.total, "elapsed_s": elapsed,
                "eta_s": eta, "cost": self.cost, "cap": self.spend_cap, "tokens": self.tokens,
                "pause_reason": self._pause_reason, "waiting_continue": self._waiting_continue,
                "stop_after_each": self._stop_after_each, "last_finished": self.last_finished,
                "in_flight": list(self._in_flight.values()), "failed": list(self.failed),
                "stopping": self._stop and self.state in ("running", "paused", "waiting_continue"),
            }

    # ---- internals ----
    def _next_item(self) -> dict | None:
        done = store.done_ids(self.results_path)
        while self.queue:
            item = self.queue.popleft()
            if item["Item_ID"] not in done:  # resume: skip items that already have a row
                return item
        return None

    def _pause(self, reason: str) -> None:
        self._pause_reason = self._pause_reason or reason

    def _worker(self, idx: int) -> None:
        while True:
            with self._cond:
                while True:
                    if self._cancel or self._stop or not self.queue:
                        return
                    if self._pause_reason:
                        self.state = "paused"
                    elif self._waiting_continue:
                        self.state = "waiting_continue"
                    elif idx >= self._effective_concurrency():
                        self.state = "running"
                    elif self.item_gate is not None and not self.item_gate():
                        self._pause("real-run cap for this demo reached")
                        continue
                    else:
                        self.state = "running"
                        break
                    self._cond.wait(timeout=0.5)
                item = self._next_item()
                if item is None:
                    return
                self._in_flight[idx] = item["Item_ID"]
            error, result = None, None
            try:
                result = pipeline.run_item(item, self.models_factory(), pipeline.Budget.from_env())
            except Exception as e:  # one bad item must not kill the batch
                error = e
            with self._cond:
                self._in_flight.pop(idx, None)
                if self._cancel:  # aborted: no row, tokens lost
                    self._cond.notify_all()
                    return
                if error is not None:
                    self.failed.append((item["Item_ID"], f"{type(error).__name__}: {str(error)[:150]}"))
                    self._errors_in_row += 1
                    if self._errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                        self._pause(f"{MAX_CONSECUTIVE_ERRORS} errors in a row")
                else:
                    self._record(item, result)
                self._cond.notify_all()

    def _record(self, item: dict, result: dict) -> None:
        row = result["row"]
        store.append_row(row, self.results_path)  # written now, not at the end
        store.write_report(item["Item_ID"], result["report"], self.items_dir)
        self._errors_in_row = 0
        self.finished.append(item["Item_ID"])
        self.last_finished = item["Item_ID"]
        self.cost += result["usage"].cost_usd
        self.tokens += result["usage"].input_tokens + result["usage"].output_tokens
        self._recent_rejects.append(bool(result["verifier_rejected"]))
        if result["budget_exceeded"]:
            self._pause(f"{item['Item_ID']} exceeded its step or token budget")
        if self.spend_cap and self.cost >= self.spend_cap:
            self._pause(f"spend cap reached (${self.cost:.2f} of ${self.spend_cap:.2f})")
        n = len(self._recent_rejects)
        if n >= REJECT_MIN_ITEMS and sum(self._recent_rejects) / n > REJECT_LIMIT:
            self._pause(f"verifier rejected {sum(self._recent_rejects)} of the last {n} items")
        if self._stop_after_each:
            self._waiting_continue = True

    def _finish_when_done(self) -> None:
        for t in self._threads:
            t.join()
        with self._cond:
            self.ended = time.time()
            self.state = "cancelled" if self._cancel else "stopped" if (self._stop and self.queue) else "done"
            self._cond.notify_all()
