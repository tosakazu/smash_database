"""clock — the single source of "now".

Many download-pipeline decisions depend on now (skipping unfinished tournaments, re-fetching right after completion, the 7-day
window, the upcoming window, the class-tournament scan period). datetime.now() / datetime.utcnow() / date.today() / time.time() used to be scattered across 6 places.
Tests (tests/fetch/_cassette.py) override this to pin the recorded time. In production it is the real time.
"""
from __future__ import annotations

import datetime as _dt
import time as _time

_override_ts: float | None = None


def set_now(ts: float | None) -> None:
    """For tests: pin the time (None to unpin)."""
    global _override_ts
    _override_ts = None if ts is None else float(ts)


def now_ts() -> int:
    return int(_override_ts if _override_ts is not None else _time.time())


def now() -> _dt.datetime:
    """Local time (naive). The production host runs with TZ=Asia/Tokyo (deploy/paths.sh)."""
    return _dt.datetime.fromtimestamp(now_ts())


def utcnow() -> _dt.datetime:
    return _dt.datetime.fromtimestamp(now_ts(), tz=_dt.timezone.utc).replace(tzinfo=None)


def today() -> _dt.date:
    return now().date()
