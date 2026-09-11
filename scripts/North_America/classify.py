# -*- coding: utf-8 -*-
"""North America の判定ルール (derive.py が読む地域モジュール)。

日本 (scripts/Japan/classify.py) の写しではなく、**この地域で確かめられたものだけ**を書く。
日本固有の暦 (お盆・年末年始を休日扱いする) や大会名パターン (制限大会・下位クラス・プレ大会)
はここには無い。必要なら北米の運用担当者が足すこと。足したら CLASSIFIER_VERSION を上げて
`derive.py --region North_America --all` を回す (全イベントの derived.json が作り直される)。

いま derived.json に入るもの:
  is_1on1 / not_1on1_reason   ダブルス・チーム戦・amiibo などを名前で除外したか
  calendar                    開催日 (現地時間)、土日か、開催国の祝日か (US / CA / MX / DO)
  class_bracket               大会の中の下位クラス別ブラケット (Redemption / Amateur など) の phase_group
  names.lower_class           イベント自体が下位ブラケット (「Redemption Bracket」というイベント) か
  names.restricted            参加できる層が絞られた大会 (Arcadian = 地域 PR 入りの選手は出られない)。
                              日本の制限大会 (レート上限) に当たる。集計はするが扱いを変える

まだ無いもの (足すときはこのファイルに):
  休日扱いする期間 (日本のお盆・年末年始に相当するもの。北米で何をそう見なすかは未定)、
  州・県ごとの祝日 (Family Day など。attr.place に州が入っていないので、まず venue_address
  から州を取る仕組みが要る)、大会名からのラベル (シリーズ名・制限大会など)、
  開催地の州、プレイヤーの居住地。

クラス bracket の呼び名は米国の 1 週間ぶん (2026-09-05〜11、513 イベント) で確認した:
実際に多いのは **Redemption** (敗者救済ブラケット。phase としても、別イベント "Redemption
Bracket" としても現れる) で、Amateur / Novice は無かった (大型大会で使われる想定で残す)。
CLASS_LETTERS の並びは仮想イベント ID の採番に使うので、後から順序を変えないこと (足すときは末尾)。

確認しきれていない点 (担当者が現地の暦で検証すること):
  - DO の月曜寄せ (ley 139-97) は「火・水 → 前の月曜、木・金・土 → 次の月曜」で実装し、
    日曜に当たった場合は動かしていない。
  - DO の 8/16 (Restauración) は大統領就任の年は固定日として扱う説があるが、区別していない。
"""
from __future__ import annotations

import datetime as dt
import functools
import re

from scripts.common.region import class_phase_group_ids

CLASSIFIER_VERSION = 8   # 8: lower_class を大会名でも見る (日本と同じ。"Novice Knockout" のような大会全体が下位向けのもの)
                         # 7: Arcadian (PR 入り選手は出られない大会) を 1on1 の除外から外し names.restricted で印を付ける
                         # 6: Redemption (敗者救済ブラケット) をクラス bracket として扱い、names.lower_class を追加 (米国 1 週間ぶんの実データで確認)
                         # 5: is_offline (derive.py の共通部) を追加
                         #  # 4: クラス bracket (Amateur / Novice / B-E class) を扱う
                         # 3: DO の祝日表 (月曜寄せ) とメキシコの就任式、表の無い国は祝日を当てない (holidays に記録)
                         # 2: 祝日 (US / CA / MX) を週末扱いに追加   # 1: 初版 (1on1 判定と暦だけ)

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
    "amiibo", "ladder",
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
#      / ("movable", 月, 日) = 月曜に寄せる祝日 (DO) / ("transmission",) = メキシコの大統領就任式
# 表の無い国 (この地域に新しい国が入ったとき) は祝日を当てない。他国の表で代用しない。
DEFAULT_COUNTRY = "US"      # place.timezone が無いときの既定タイムゾーン用 (祝日の既定ではない)

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
        ("transmission",),          # 12/1 大統領就任式 (6 年ごと)
        ("fixed", 12, 25),          # Navidad
    ),
}
HOLIDAY_RULES["DO"] = (
    ("fixed", 1, 1),            # Año Nuevo
    ("movable", 1, 6),          # Día de Reyes
    ("fixed", 1, 21),           # Nuestra Señora de la Altagracia
    ("movable", 1, 26),         # Día de Duarte
    ("fixed", 2, 27),           # Día de la Independencia
    ("easter", -2),             # Viernes Santo
    ("easter", 60),             # Corpus Christi
    ("movable", 5, 1),          # Día del Trabajo
    ("movable", 8, 16),         # Día de la Restauración
    ("fixed", 9, 24),           # Nuestra Señora de las Mercedes
    ("movable", 11, 6),         # Día de la Constitución
    ("fixed", 12, 25),          # Navidad
)

# 土日に当たった祝日を前後の平日に振り替える国 (振替日も休みになる = 大会が組まれやすい)
OBSERVED_SHIFT_COUNTRIES = ("US", "CA")

# 12/1 が休日になる年 (メキシコの大統領就任式。6 年ごと、直近は 2024 年)
MX_TRANSMISSION_BASE_YEAR = 2024

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


def _movable_to_monday(d: dt.date) -> dt.date:
    """ドミニカ共和国 (ley 139-97): 火・水は前の月曜、木・金・土は次の月曜に移す。
    日曜のときの扱いは確認できていないので動かさない (要確認)。"""
    wd = d.weekday()
    if wd in (1, 2):                                  # 火・水
        return d - dt.timedelta(days=wd)
    if wd in (3, 4, 5):                               # 木・金・土
        return d + dt.timedelta(days=7 - wd)
    return d


def holidays_source(country_code: str | None) -> str | None:
    """その国の祝日表があるか (無ければ None = 祝日を当てない。他国の表で代用しない)。"""
    cc = (country_code or "").upper()
    return cc if cc in HOLIDAY_RULES else None


@functools.lru_cache(maxsize=None)
def holidays_for(country_code: str | None, year: int) -> frozenset[dt.date]:
    """その国・その年の祝日 (振替日を含む)。表の無い国は空 (推測しない)。"""
    cc = holidays_source(country_code)
    if cc is None:
        return frozenset(EXTRA_HOLIDAY_DATES)
    days: set[dt.date] = set()
    for rule in HOLIDAY_RULES[cc]:
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
        elif kind == "movable":
            days.add(_movable_to_monday(dt.date(year, rule[1], rule[2])))
        elif kind == "transmission":
            if (year - MX_TRANSMISSION_BASE_YEAR) % 6 == 0:
                days.add(dt.date(year, 12, 1))
    if cc in OBSERVED_SHIFT_COUNTRIES:
        for d in list(days):
            if d.weekday() == 5:
                days.add(d - dt.timedelta(days=1))    # 土曜 → 前日の金曜
            elif d.weekday() == 6:
                days.add(d + dt.timedelta(days=1))    # 日曜 → 翌日の月曜
    return frozenset(days | EXTRA_HOLIDAY_DATES)


def is_weekend_date(d: dt.date, country_code: str | None = None) -> bool:
    """土日、または開催国の祝日 (振替日を含む)。表の無い国は土日だけ。"""
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
        "holidays": holidays_source(cc),      # どの国の祝日表を当てたか (None = 当てていない)
        "is_weekend_real": is_weekend_range(d, end_d, cc),
    }


# ── クラス bracket (大会の中の下位クラス別ブラケット) ──
# 日本の B/C/D/E クラスに当たるもの。北米は Amateur / Novice 等の呼び名が多い (要確認)。
# CLASS_LETTERS の並び = 仮想イベント ID のずらし幅。後から順序を変えない (末尾に足す)。
CLASS_LETTERS = ("AMATEUR", "NOVICE", "BEGINNER", "B", "C", "D", "E", "REDEMPTION")

CLASS_PHASE_PATTERN = re.compile(
    r'(?<![A-Za-z])(?:amateur|amateurs|novice|beginner|redemption)(?![A-Za-z])'
    r'|(?<![A-Za-z])[BCDE][\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)
CLASS_LETTER_PATTERN = re.compile(
    r'(?<![A-Za-z])(amateur|novice|beginner|redemption)s?(?![A-Za-z])'
    r'|(?<![A-Za-z])([BCDE])[\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)

# イベント自体が下位ブラケットのもの (例: "Redemption Bracket" / "Ultimate Redemption" という別イベント)。
# 日本の names.lower_class (「Bクラス限定」など) に当たる。本戦の 1on1 とは別に扱いたいので印を付ける
LOWER_CLASS_EVENT_PATTERN = re.compile(
    r'(?<![A-Za-z])(?:redemption|amateur|amateurs|novice|beginner)(?![A-Za-z])'
    r'|(?<![A-Za-z])[BCDE][\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)


# 参加条件で層が絞られる大会。Arcadian = 地域の Power Ranking に載っている選手は出場不可 (上位勢抜きの大会)。
# 日本の「レート 1700 未満制限」に当たるので、1on1 から外すのではなく restricted の印を付ける
RESTRICTED_PATTERN = re.compile(r'(?<![A-Za-z])arcadian(?![A-Za-z])', re.IGNORECASE)


def name_flags(tname: str, ename: str) -> dict:
    """大会名・イベント名から決まるフラグ。restricted は日本と同じく大会名とイベント名を分けて持つ
    (同時開催の本戦を巻き込まないため)。プレ大会・特殊ルールなどはまだ未定義。"""
    return {
        "lower_class": bool(LOWER_CLASS_EVENT_PATTERN.search(tname or '') or LOWER_CLASS_EVENT_PATTERN.search(ename or '')),
        "restricted_tname": bool(RESTRICTED_PATTERN.search(tname or '')),
        "restricted_ename": bool(RESTRICTED_PATTERN.search(ename or '')),
    }


def is_class_phase(name: str | None) -> bool:
    """phase 名がクラス bracket か (phases.json の is_class)。"""
    return bool(CLASS_PHASE_PATTERN.search(name or ''))


def is_unseparated_class_phase(name: str | None) -> bool:
    """matches.json の phase 名がクラス戦か (phases.json 未分離の検出用)。北米は同じ判定でよい。"""
    return is_class_phase(name)


def class_letter(name: str | None) -> str | None:
    """phase 名 → クラスの識別子 (AMATEUR / NOVICE / BEGINNER / B / C / D / E)。"""
    if not name:
        return None
    m = CLASS_LETTER_PATTERN.search(name)
    if not m:
        return None
    return (m.group(1) or m.group(2)).upper()


def class_virtual_event_name(event_name: str, letter: str) -> str:
    """クラスを 1 大会として切り出すときのイベント名。"""
    label = f"{letter} class" if len(letter) == 1 else letter.title()
    return f"{event_name} / {label}"


# ── 開催予定 (upcoming) の注釈。まだディレクトリの無い大会に、名前と開始日だけで判定を付ける ──
def upcoming_flags(tournament_name: str, event_name: str, num_entrants: int, start_ts: int | None) -> dict:
    """開催予定 1 件に付ける判定。北米は 1on1 かどうかと暦だけ (大会名からのラベルがまだ無いため)。
    開催国が分からない (upcoming には place が無い) ので祝日は当てず、土日だけを見る。"""
    ok, reason = is_1on1_event({"tournament_name": tournament_name, "event_name": event_name})
    out = {"is_1on1": ok, "not_1on1_reason": reason}
    if start_ts:
        start = dt.datetime.fromtimestamp(int(start_ts)).date()
        out["is_weekend_real"] = start.weekday() >= 5
        out["is_weekend"] = out["is_weekend_real"]
    else:
        out["is_weekend"] = False       # 開始日不明は保守的に平日扱い
        out["is_weekend_real"] = False
    return out


# ── derive.py から呼ばれる入口 ──
def classify_event(base: dict, ctx) -> dict:
    """base (derive.py が作る共通部分) に北米の判定を足して返す。ctx: attr / tname / ename / phases / class_phase_files。"""
    ok, reason = is_1on1_event(ctx.attr)
    ts = ctx.attr.get("timestamp")
    all_class, pg_ids = class_phase_group_ids(ctx.phases, ctx.class_phase_files)
    out = dict(base)
    out.update({
        "is_1on1": ok,
        "not_1on1_reason": reason,
        "calendar": (calendar_flags(ts, ctx.attr.get("end_timestamp"), ctx.attr.get("place"))
                     if ts is not None else None),
        "names": name_flags(ctx.tname, ctx.ename),
        "class_bracket": {"all_phases_class": all_class, "phase_group_ids": pg_ids},
    })
    return out


def classify_user(u: dict) -> dict | None:
    """users.jsonl の 1 行 → 導出したい項目。北米はまだ何も導出しない (None = users_derived.jsonl に行を書かない)。"""
    return None
