"""clock — 「今」の唯一の取得口。

ダウンロード工程は now に依存する判定が多い (未終了大会のスキップ、終了直後の取り直し、7 日窓、upcoming の窓、
クラス大会の走査期間)。以前は datetime.now() / datetime.utcnow() / date.today() / time.time() が 6 箇所に散っていた。
テスト (tests/fetch/_cassette.py) はここを差し替えて記録時刻に固定する。本番では実時刻。
"""
from __future__ import annotations

import datetime as _dt
import time as _time

_override_ts: float | None = None


def set_now(ts: float | None) -> None:
    """テスト用: 固定する (None で解除)。"""
    global _override_ts
    _override_ts = None if ts is None else float(ts)


def now_ts() -> int:
    return int(_override_ts if _override_ts is not None else _time.time())


def now() -> _dt.datetime:
    """ローカル時刻 (naive)。本番ホストは TZ=Asia/Tokyo (deploy/paths.sh)。"""
    return _dt.datetime.fromtimestamp(now_ts())


def utcnow() -> _dt.datetime:
    return _dt.datetime.fromtimestamp(now_ts(), tz=_dt.timezone.utc).replace(tzinfo=None)


def today() -> _dt.date:
    return now().date()
