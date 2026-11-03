"""Public-demo guard for a hosted copy of the app (DEMO_MODE=true): passcode for the real models and a cap on real-mode items.

Mock models stay the default. The passcode comes from the environment or Streamlit secrets (DEMO_PASSCODE), never from the repository, and is
compared in constant time. Real-mode items are counted per session and per day; the day count is a small file under results/cache/, which on
Streamlit Community Cloud is temporary disk, so it also resets when the app restarts. Nothing here needs Streamlit, so it can be tested.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

DEFAULT_SESSION_CAP = 10
DEFAULT_DAY_CAP = 30
MAX_PASSCODE_TRIES = 5
COUNTER = config.CACHE_DIR / "demo_counter.json"
_LOCK = threading.Lock()  # batch workers reserve from several threads


def is_demo(environ=None) -> bool:
    return str((environ if environ is not None else os.environ).get("DEMO_MODE", "")).strip().lower() in ("1", "true", "yes", "on")


def _cap(name: str, default: int, environ) -> int:
    try:
        v = int(str(environ.get(name, "")).strip())
    except ValueError:
        return default
    return v if v >= 0 else default


def caps(environ=None) -> tuple[int, int]:
    """(per session, per day) from DEMO_MAX_REAL_ITEMS_PER_SESSION and DEMO_MAX_REAL_ITEMS_PER_DAY; bad values fall back to 10 and 30."""
    env = os.environ if environ is None else environ
    return _cap("DEMO_MAX_REAL_ITEMS_PER_SESSION", DEFAULT_SESSION_CAP, env), _cap("DEMO_MAX_REAL_ITEMS_PER_DAY", DEFAULT_DAY_CAP, env)


def check_passcode(entered: str, expected: str) -> bool:
    """True only when a passcode is configured and the visitor typed exactly it. No passcode configured means real models stay locked."""
    if not expected or not entered:
        return False
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class DemoLimiter:
    """Counts real-mode items for one visitor session and for the whole app per day. reserve() is the only way to spend."""

    def __init__(self, session_cap: int, day_cap: int, path: Path = COUNTER, today=_today):
        self.session_cap, self.day_cap = session_cap, day_cap
        self.path, self.today = Path(path), today
        self.session_used = 0

    def day_used(self) -> int:
        """Items counted today. An unreadable counter file counts as full, so a damaged file can never open the cap."""
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return int(d["count"]) if d.get("date") == self.today() else 0
        except FileNotFoundError:
            return 0
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return self.day_cap

    def remaining(self) -> int:
        return max(0, min(self.session_cap - self.session_used, self.day_cap - self.day_used()))

    def reserve(self, n: int = 1) -> bool:
        """Take n real-mode items from both caps, or take nothing and return False."""
        with _LOCK:
            if n < 1 or self.remaining() < n:
                return False
            self.session_used += n
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"date": self.today(), "count": self.day_used() + n}), encoding="utf-8")
            tmp.replace(self.path)
            return True

    def summary(self) -> str:
        return (f"Real-mode items left: {self.remaining()} (this session {self.session_used} of {self.session_cap} used, "
                f"today {self.day_used()} of {self.day_cap} used)")
