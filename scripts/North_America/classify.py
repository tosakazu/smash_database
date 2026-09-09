# -*- coding: utf-8 -*-
"""North America の判定ルール (derive.py が読む地域モジュール)。

日本 (scripts/Japan/classify.py) の写しではなく、**この地域で確かめられたものだけ**を書く。
日本固有の暦 (お盆・年末年始を休日扱いする) や大会名パターン (制限大会・下位クラス・プレ大会)
はここには無い。必要なら北米の運用担当者が足すこと。足したら CLASSIFIER_VERSION を上げて
`derive.py --region North_America --all` を回す (全イベントの derived.json が作り直される)。

いま derived.json に入るもの:
  is_1on1 / not_1on1_reason   ダブルス・チーム戦・amiibo などを名前で除外したか
  calendar                    開催日 (現地時間) と週末かどうか

まだ無いもの (足すときはこのファイルに):
  祝日 (HOLIDAY_DATES が空なので土日だけが週末)、休日扱いする期間 (日本のお盆・年末年始に相当)、
  大会名からのラベル (シリーズ名・制限大会など)、開催地の州、プレイヤーの居住地。
"""
from __future__ import annotations

import datetime as dt
import re

CLASSIFIER_VERSION = 1   # 1: 初版 (1on1 判定と暦だけ)

# 北米は複数のタイムゾーンにまたがる。イベントの place.timezone があればそれを使い、
# 無いときだけこの既定を使う (プロセスの TZ もこの値で derive.py が設定する)。
TIMEZONE = "America/New_York"


def check_requirements() -> None:
    """この地域の判定に要る外部パッケージ。いまは無し (祝日表を入れるならここで確かめる)。"""
    return None


# ── 1on1 判定 (大会名・イベント名だけで決める) ──
# 除外語。英語のほか、カナダ (仏) とメキシコ (西) の表記も入れてある。
EXCLUDE_PATTERNS = (
    "doubles", "dubs", "2v2", "teams", "team event",
    "crew battle", "crews", "squad strike", "squadstrike",
    "amiibo", "ladder", "arcadian",          # arcadian = 上位者を除く別枠
    "dobles", "equipos",                     # es
    "doublette", "équipes", "equipes",       # fr
    "test", "testing",
)
# 2on2 / 3v3 / 5 vs 5 のような N 対 N 表記
EXCLUDE_REGEX = re.compile(r'(?<!\w)[2-9]\s*[-‐ ]?\s*(?:v|vs|on)\s*[-‐ ]?\s*[2-9](?!\w)', re.IGNORECASE)
# 除外語を含んでいても 1on1 として扱う大会 (見つかったら足す)
ALLOW_PATTERNS: tuple[str, ...] = ()


def is_1on1_event(attr: dict) -> tuple[bool, str | None]:
    """(1on1 として扱うか, 弾いた理由)。理由は "keyword:<語>" / "regex:NvN"。"""
    haystack_orig = (attr.get("event_name") or "") + " | " + (attr.get("tournament_name") or "")
    haystack = haystack_orig.lower()
    for kw in ALLOW_PATTERNS:
        if kw.lower() in haystack:
            return True, None
    for kw in EXCLUDE_PATTERNS:
        if kw.lower() in haystack:
            return False, f"keyword:{kw}"
    if EXCLUDE_REGEX.search(haystack_orig):
        return False, "regex:NvN"
    return True, None


# ── 暦 ──
# 祝日。北米は国 (US / CA / MX) ごとに違い、週末大会の扱いをどうするかも未定なので空にしてある。
# 入れるときは {(月, 日)} ではなく実日付の集合 (年ごとに変わる祝日があるため) を想定。
HOLIDAY_DATES: frozenset[dt.date] = frozenset()


def is_weekend_date(d: dt.date) -> bool:
    """土日、または HOLIDAY_DATES に入っている日。"""
    return d.weekday() >= 5 or d in HOLIDAY_DATES


def is_weekend_range(start_d: dt.date, end_d: dt.date) -> bool:
    """開始日〜終了日のいずれかが週末なら True。15 日を超える長期イベントは開始日だけで判定
    (endAt がブラケット閉鎖まで含むことがあるため。Japan と同じ規約)。"""
    if end_d < start_d:
        end_d = start_d
    delta = (end_d - start_d).days
    if delta > 14:
        return is_weekend_date(start_d)
    return any(is_weekend_date(start_d + dt.timedelta(days=i)) for i in range(delta + 1))


def _zone(place: dict | None):
    """イベントの現地時間。place.timezone があればそれ、無ければ地域の既定 TIMEZONE。"""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    name = (place or {}).get("timezone") or TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(TIMEZONE)


def calendar_flags(timestamp: int, end_timestamp: int | None, place: dict | None = None) -> dict:
    """開催日 (現地時間) と週末かどうか。is_force_weekend_period は日本固有なので持たない。"""
    tz = _zone(place)
    d = dt.datetime.fromtimestamp(int(timestamp), tz).date()
    end_ts = end_timestamp if end_timestamp is not None else timestamp
    end_d = dt.datetime.fromtimestamp(int(end_ts), tz).date()
    return {
        "date": d.isoformat(),
        "end_date": end_d.isoformat(),
        "timezone": str(tz),
        "is_weekend_real": is_weekend_range(d, end_d),
    }


# ── derive.py から呼ばれる入口 ──
def classify_event(base: dict, ctx) -> dict:
    """base (derive.py が作る共通部分) に北米の判定を足して返す。ctx: attr / tname / ename / phases / class_phase_files。"""
    ok, reason = is_1on1_event(ctx.attr)
    ts = ctx.attr.get("timestamp")
    out = dict(base)
    out.update({
        "is_1on1": ok,
        "not_1on1_reason": reason,
        "calendar": (calendar_flags(ts, ctx.attr.get("end_timestamp"), ctx.attr.get("place"))
                     if ts is not None else None),
    })
    return out


def classify_user(u: dict) -> dict | None:
    """users.jsonl の 1 行 → 導出したい項目。北米はまだ何も導出しない (None = users_derived.jsonl に行を書かない)。"""
    return None
