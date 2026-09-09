# -*- coding: utf-8 -*-
"""North America の判定ルール (derive.py が読む地域モジュール)。

日本 (scripts/Japan/classify.py) の写しではなく、**この地域で確かめられたものだけ**を書く。
日本固有の暦 (お盆・年末年始を休日扱いする) や大会名パターン (制限大会・下位クラス・プレ大会)
はここには無い。必要なら北米の運用担当者が足すこと。足したら CLASSIFIER_VERSION を上げて
`derive.py --region North_America --all` を回す (全イベントの derived.json が作り直される)。

いま derived.json に入るもの:
  is_1on1 / not_1on1_reason   ダブルス・チーム戦・amiibo などを名前で除外したか
  calendar                    開催日 (現地時間)、土日か、開催国の祝日か

まだ無いもの (足すときはこのファイルに):
  休日扱いする期間 (日本のお盆・年末年始に相当するもの。北米で何をそう見なすかは未定)、
  州・県ごとの祝日 (いまは国の祝日だけ)、大会名からのラベル (シリーズ名・制限大会など)、
  開催地の州、プレイヤーの居住地。
"""
from __future__ import annotations

import datetime as dt
import functools
import re

CLASSIFIER_VERSION = 2   # 2: 祝日 (US / CA / MX の国民の祝日) を週末扱いに追加   # 1: 初版 (1on1 判定と暦だけ)

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
# 国民の祝日。国ごとに違うので開催国 (attr.place.country_code) で引く。州・県の祝日は入れていない
# (Family Day やメキシコ各州の祝日など。必要になったら country_code だけでなく州も見ること)。
# 表記: ("fixed", 月, 日) / ("nth", 月, 曜日 0=月, n 番目。-1 = 最後) / ("easter", 復活祭からの日数)
DEFAULT_COUNTRY = "US"

HOLIDAY_RULES: dict[str, tuple] = {
    "US": (
        ("fixed", 1, 1),            # New Year's Day
        ("nth", 1, 0, 3),           # Martin Luther King Jr. Day
        ("nth", 2, 0, 3),           # Presidents' Day
        ("nth", 5, 0, -1),          # Memorial Day
        ("fixed", 6, 19),           # Juneteenth
        ("fixed", 7, 4),            # Independence Day
        ("nth", 9, 0, 1),           # Labor Day
        ("nth", 10, 0, 2),          # Columbus Day
        ("fixed", 11, 11),          # Veterans Day
        ("nth", 11, 3, 4),          # Thanksgiving
        ("fixed", 12, 25),          # Christmas Day
    ),
    "CA": (
        ("fixed", 1, 1),            # New Year's Day
        ("easter", -2),             # Good Friday
        ("victoria",),              # Victoria Day (5/25 の直前の月曜)
        ("fixed", 7, 1),            # Canada Day
        ("nth", 9, 0, 1),           # Labour Day
        ("fixed", 9, 30),           # National Day for Truth and Reconciliation
        ("nth", 10, 0, 2),          # Thanksgiving
        ("fixed", 11, 11),          # Remembrance Day
        ("fixed", 12, 25),          # Christmas Day
        ("fixed", 12, 26),          # Boxing Day
    ),
    "MX": (
        ("fixed", 1, 1),            # Año Nuevo
        ("nth", 2, 0, 1),           # Día de la Constitución
        ("nth", 3, 0, 3),           # Natalicio de Benito Juárez
        ("fixed", 5, 1),            # Día del Trabajo
        ("fixed", 9, 16),           # Día de la Independencia
        ("nth", 11, 0, 3),          # Revolución Mexicana
        ("fixed", 12, 25),          # Navidad
    ),
}
HOLIDAY_RULES["DO"] = HOLIDAY_RULES["MX"]   # 暫定: ドミニカ共和国の祝日表は未調査 (担当者が入れる)

# 土日に当たった祝日を前後の平日に振り替える国 (振替日も休みになる = 大会が組まれやすい)
OBSERVED_SHIFT_COUNTRIES = ("US", "CA")

# 上の規則で出ない日を足したいとき (単発の祝日・大型イベント週など) はここに実日付を書く
EXTRA_HOLIDAY_DATES: frozenset[dt.date] = frozenset()


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """その月の n 番目の <weekday> (n = -1 なら最後)。"""
    if n < 0:
        d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
        while d.weekday() != weekday:
            d -= dt.timedelta(days=1)
        return d
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(days=7 * (n - 1))


def _easter(year: int) -> dt.date:
    """西方教会の復活祭 (Anonymous Gregorian algorithm)。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return dt.date(year, month, day + 1)


@functools.lru_cache(maxsize=None)
def holidays_for(country_code: str | None, year: int) -> frozenset[dt.date]:
    """その国・その年の祝日 (振替日を含む)。表の無い国は既定 (US) を使う。"""
    rules = HOLIDAY_RULES.get((country_code or "").upper()) or HOLIDAY_RULES[DEFAULT_COUNTRY]
    days: set[dt.date] = set()
    for rule in rules:
        kind = rule[0]
        if kind == "fixed":
            days.add(dt.date(year, rule[1], rule[2]))
        elif kind == "nth":
            days.add(_nth_weekday(year, rule[1], rule[2], rule[3]))
        elif kind == "easter":
            days.add(_easter(year) + dt.timedelta(days=rule[1]))
        elif kind == "victoria":
            d = dt.date(year, 5, 25) - dt.timedelta(days=1)
            while d.weekday() != 0:
                d -= dt.timedelta(days=1)
            days.add(d)
    if (country_code or "").upper() in OBSERVED_SHIFT_COUNTRIES:
        for d in list(days):
            if d.weekday() == 5:
                days.add(d - dt.timedelta(days=1))    # 土曜 → 前日の金曜
            elif d.weekday() == 6:
                days.add(d + dt.timedelta(days=1))    # 日曜 → 翌日の月曜
    return frozenset(days | EXTRA_HOLIDAY_DATES)


def is_weekend_date(d: dt.date, country_code: str | None = None) -> bool:
    """土日、または開催国の祝日 (振替日を含む)。"""
    return d.weekday() >= 5 or d in holidays_for(country_code, d.year)


def is_weekend_range(start_d: dt.date, end_d: dt.date, country_code: str | None = None) -> bool:
    """開始日〜終了日のいずれかが週末なら True。15 日を超える長期イベントは開始日だけで判定
    (endAt がブラケット閉鎖まで含むことがあるため。Japan と同じ規約)。"""
    if end_d < start_d:
        end_d = start_d
    delta = (end_d - start_d).days
    if delta > 14:
        return is_weekend_date(start_d, country_code)
    return any(is_weekend_date(start_d + dt.timedelta(days=i), country_code) for i in range(delta + 1))


def _zone(place: dict | None):
    """イベントの現地時間。place.timezone があればそれ、無ければ地域の既定 TIMEZONE。"""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    name = (place or {}).get("timezone") or TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(TIMEZONE)


def calendar_flags(timestamp: int, end_timestamp: int | None, place: dict | None = None) -> dict:
    """開催日 (現地時間)、土日祝かどうか。祝日は開催国のもの。
    is_force_weekend_period (日本のお盆・年末年始) に当たるものは北米では未定義なので持たない。"""
    tz = _zone(place)
    cc = (place or {}).get("country_code")
    d = dt.datetime.fromtimestamp(int(timestamp), tz).date()
    end_ts = end_timestamp if end_timestamp is not None else timestamp
    end_d = dt.datetime.fromtimestamp(int(end_ts), tz).date()
    return {
        "date": d.isoformat(),
        "end_date": end_d.isoformat(),
        "timezone": str(tz),
        "country_code": cc,
        "is_weekend_real": is_weekend_range(d, end_d, cc),
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
