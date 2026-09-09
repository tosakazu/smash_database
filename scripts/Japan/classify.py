"""classify (Japan) — 日本の大会・イベントの「データとしての判定」(名前パターン・暦・クラス bracket)。

大会名のフィルタ・祝日・クラス bracket は地域依存なので scripts/Japan に置く。共通の駆動部は scripts/common/derive.py で、
--region の地域モジュール (scripts/<地域>/classify.py) の classify_event() を呼ぶ。

spsp のビルドが読み込み時にやっていた判定 (data_loader._is_1on1_event、common.py の名前パターン、
tjpr_cascade/meta.py の暦判定、class bracket の phase_group 集合) を 2026-09-08 にここへ移した。
ここにあるのは「事実」の判定だけ: 名前が特定パターンに一致するか、開催期間に土日祝が含まれるか、
どの phase_group がクラス bracket か。それをランキングでどう扱うか (Lv 除外、平日係数、
同時開催の制限解除、参加者数の閾値) は spsp 側に残す。

関数は副作用を持たない。判定を変えたら CLASSIFIER_VERSION を上げ、derive.py --all で全件再処理する。
"""
from __future__ import annotations

import datetime as dt
import re

from scripts.Japan.naming import naming_labels
from scripts.Japan.prefecture import resolve as resolve_prefecture

CLASSIFIER_VERSION = 5   # 5: naming (シリーズ名・開催回・実績ラベル・中止/テスト検出) を追加 (2026-09-09)   # 4: 4: 制限大会に「<レート>未満/以下/以上 制限」「R/レート <数字> 以上」を追加 (2026-09-09)   # 3: 1on1 判定を名前だけで行う (labels.game_rule = 旧 LLM 分類への依存を撤廃)   # 2: place.prefecture (開催地の都道府県)

# ── 1on1 判定 (旧 spsp/data_loader.py) ──
# 明示的に弾く event/tournament name patterns. これら以外はデフォルト accept.
# (download.py が既に SSBU の game_id でフィルタ済 → 残ったものは SSBU 1on1 と推定)
EXCLUDE_PATTERNS = (
    # Doubles / team / multi-on-multi
    "doubles", "ダブルス", "ダブル",
    "team battle", "チーム戦", "チームバトル", "団体戦", "チームトーナメント", "team tournament",
    # 単独「チーム」「team」も基本チーム戦と判断 (グランドスラム_チーム / teamslam 等を捕捉)
    "チーム", "team",
    "2on2", "3on3", "4on4", "5on5", "6on6", "7on7", "8on8", "9on9",
    # Crew battle
    "crew", "クルー", "クルバト", "crew battle", "クルーバトル",
    # Squad strike / squad queue
    "squad", "スクワッド", "squad strike", "スカッドストライク", "ssqm", "ssdb",
    # おま5 / random / character lottery
    "おま5", "オマ5", "oma5", "おまかせ",
    "random select", "ランダムセレクト", "ガチャ",
    # Character-limited tournaments (ファルコン限定杯 等)
    "限定", "縛り", "専用", "only",
    # Casual / beginner / amateur 枠
    "casual", "カジュアル", "交流",
    "beginner", "初心者", "初級", "amateur",
    # Online (SPSP は offline only)
    "online", "オンライン",
    # Test 系のみ除外. 身内/招待制 (あらいぶ杯/帝国/もつカップ/合宿/同世代オフ等) は
    # 大会詳細データとしては残す (スコア計算からは NON_SERIOUS で除外される).
    "テスト", "検証", "test", "sample",
    # Items / variant rules
    "item on", "アイテム", "変則",
    # 変則ルールのサイドイベント (以前は attr.json の labels.game_rule = 旧 LLM 分類でだけ弾けていたもの)
    "スマッシュサバイバル", "amiibo", "でかい乱闘", "タックルマッハスタンプ",
)
# 「[N]vs[M]」「[N]v[M]」「[N]on[M]」のチーム戦パターン (Lv4 等の偽陽性を避けるため数字 prefix 必須 + word boundary 風)
# 区切りのハイフン・長音も許す ("2-on-2" / "3ｰon-3" 等)
EXCLUDE_REGEX = re.compile(r'(?<!\w)[2-9]\s*[-‐ー]?\s*(?:vs?|on|on\s|×|x)\s*[-‐ー]?\s*[2-9](?!\w)', re.IGNORECASE)
# 除外パターンに引っかかるが実際は通常 1on1 として救済する allow-list (チバスマ交流会 = 普通の 1on1)
ALLOW_PATTERNS = ("チバスマ",)


def is_1on1_event(attr: dict) -> tuple[bool, str | None]:
    """(1on1 として扱うか, 弾いた理由)。理由は "keyword:<kw>" / "regex:NvN"。

    判定は大会名・イベント名だけで行う。2026-02 以前のデータには LLM 分類由来の labels.game_rule が
    残っているが、新規取得では付かないので依存しない (2026-09-09 に撤廃。game_rule でしか弾けていなかった
    9 event は EXCLUDE_PATTERNS / EXCLUDE_REGEX に取り込んだ)。
    - ALLOW_PATTERNS に一致すれば True (除外より優先)
    - EXCLUDE_PATTERNS / EXCLUDE_REGEX に一致すれば False
    - それ以外は True
    """
    event_name_orig = attr.get("event_name") or ""
    tournament_name_orig = attr.get("tournament_name") or ""
    haystack_orig = event_name_orig + " | " + tournament_name_orig
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


# ── 名前パターン (旧 spsp/common.py) ──
SMAPA_PATTERN = re.compile(r'スマパ|Weekly Smash Party|Smash Party', re.IGNORECASE)

# プレ大会 (preliminary tournament)
PRE_PATTERN = re.compile(
    r'プレ(?:大会|トーナメント|トナメ|オフ|イベント|予選|戦|リリース|オープン|セッション|ローカル|シーズン)'
    r'|プレ(?=[一-鿿]|\s|[/／・\-‐　\(\)\[\]【】「」『』]|$)'
    r'|(?<![A-Za-z])[Pp]re[\-_]'
    r'|(?<![A-Za-z])[Pp]retournament(?![A-Za-z])'
    r'|(?<![A-Za-z])[Pp]re(?![A-Za-z])'
)
# 休日開催でも計算上は平日大会として扱うシリーズ
FORCE_WEEKDAY_PATTERN = re.compile(r'大菊月')
# 渋谷BeeSmash (weekly): 表記ゆれが多いので beesmash を大文字小文字無視でマッチし、BIG (月次の大型回) と上野/Ueno 開催回を除外
BEESMASH_PATTERN = re.compile(r'beesmash', re.IGNORECASE)
BEESMASH_EXCLUDE_PATTERN = re.compile(r'BIG|上野|Ueno', re.IGNORECASE)
# 上野スマコミ: 参加者数がこの値未満の回だけ平日扱い (判定は nent を持つ spsp 側)
SMACOMI_FORCE_WEEKDAY_MAX_NENT = 40
# 実質休日 (お盆・年末年始) は暦上平日でもこの参加者数以上なら休日扱い (期間判定は calendar_flags、合成は spsp 側)
FORCE_WEEKEND_MIN_NENT = 80


def is_force_weekday_name(tname: str, ename: str) -> bool:
    """名前だけで決まる実質平日ルール: 大菊月 / 渋谷BeeSmash (BIG・上野開催を除く)。
    上野スマコミ (nent < 40 の回) は is_smacomi_name と nent で spsp 側が決める。"""
    hay = f"{tname or ''} {ename or ''}"
    if FORCE_WEEKDAY_PATTERN.search(hay):
        return True
    if BEESMASH_PATTERN.search(hay) and not BEESMASH_EXCLUDE_PATTERN.search(hay):
        return True
    return False


def is_smacomi_name(tname: str, ename: str) -> bool:
    return 'スマコミ' in f"{tname or ''} {ename or ''}"


def is_force_weekday_tournament(tname: str, ename: str, nent) -> bool:
    """休日開催でも計算上は平日大会として扱う特別ルール (名前 + 参加者数)。大菊月 / 渋谷BeeSmash (BIG・上野除く) は常に、
    上野スマコミは nent < 40 の回のみ。derived.json には名前部分 (force_weekday, smacomi) だけを書き、nent との合成は読む側が行う。"""
    return is_force_weekday_name(tname, ename) or (is_smacomi_name(tname, ename) and (nent or 0) < SMACOMI_FORCE_WEEKDAY_MAX_NENT)


# 特殊ルール (= 通常 1on1 ガチではない大会フォーマット)
SPECIAL_RULES_PATTERN = re.compile(
    r'おま\s*[0-9０-９五]'                # おま5, おま6 ... (お任せキャラ縛り、半/全角数字)
    r'|クルー\s*(?:戦|バトル)|Crew\s*Battle|クルバト'
    r'|ダブルス|Doubles'
    r'|チーム戦|Team\s*Battle|団体戦'
    r'|[3-9]\s*on\s*[3-9]'                          # 3on3 / 5on5 等
    r'|ランダム\s*セレクト|random\s*select|ガチャ'
    r'|Smash\s*Factory'                             # 渋谷BeeSmash BIG 4 内 Smash Factory 等
    r'|女王杯|SSQM'                                  # 特殊ルール (= SSQM, 通常 1on1 とは別の遊び方)
    r'|キャラ\s*限定'
    r'|[ぁ-んァ-ヶ一-龯]+限定杯'                      # ファルコン限定杯 / マリオ限定杯 等
    , re.IGNORECASE
)
# 身内 / 招待制 / 練習会 (= 出場対象が限定された大会)
UCHI_PATTERN = re.compile(
    r'あらいぶ杯'
    r'|96\s*,?\s*97\s*年同世代オフ'
    r'|もつカップ'
    r'|帝国'                                         # 帝国オフ
    r'|合宿'                                         # スマサー合宿等
    r'|篝炎'                                          # 篝炎 in 炎メシハウス (= 身内, 篝火とは別)
    r'|炎メシハウス'
    r'|invitational'                                  # 招待制 (= RUST Invitational 等の英語名身内大会)
    , re.IGNORECASE
)
# 特殊ルール + 身内の和集合
NON_SERIOUS_PATTERN = re.compile(SPECIAL_RULES_PATTERN.pattern + '|' + UCHI_PATTERN.pattern, re.IGNORECASE)
# 制限大会 (出場資格が制限されている大会)
RESTRICTED_PATTERN = re.compile(
    r'雛囃子|灰神楽|鬼灯火'
    r'|西武撃[^/／]*[Rr]ising|[Rr]ising[^/／]*西武撃'
    r'|R\s*\d{3,4}\s*(?:未満|以下|以上|制限)'
    r'|レート\s*\d+\s*(?:未満|以下|以上|制限)'
    r'|\d{3,4}\s*(?:未満|以下|以上)\s*制限'          # R / レート の字が無い書き方 (例: 風雲「1700未満制限大会」)
    r'|ビギナー(?:ズ)?\s*杯'
    r'|Beginner'
    r'|VIP\s*未満|未\s*VIP'
    r'|修羅ブラ\s*新風'
    r'|九龍\s*LIMIT\s*BREAK'
    r'|くすブラ\s*若葉'
    r'|DIVE\s*Under\s*Ground'
    r'|INNOSUMA[!！\s]*RAISE',
    re.IGNORECASE,
)
# 下位クラスイベント (Bクラス / Cクラス / Bclass / B_Class / b_class 等)。セパレータは空白/アンダースコア/ハイフン
LOWER_CLASS_PATTERN = re.compile(
    r'[BCDEＢＣＤＥ]\s*クラス'
    r'|(?<![A-Za-z])[BCDE][\s_\-]*class(?![A-Za-z])'
    r'|スマパ[！!]?\s*[デ]?\s*カジュアル|スマパカジュアル',     # スマパ系カジュアルは下位クラス扱い (半/全角!)
    re.IGNORECASE,
)


def _hit(pat: re.Pattern, *names: str) -> bool:
    return any(bool(pat.search(n or '')) for n in names)


def name_flags(tname: str, ename: str) -> dict:
    """大会名・イベント名に対する名前パターンの判定 (すべて bool)。restricted は tname / ename を分けて持つ
    (同時開催の制限解除は spsp 側が判断する)。"""
    return {
        "special_rules": _hit(SPECIAL_RULES_PATTERN, tname, ename),
        "uchi": _hit(UCHI_PATTERN, tname, ename),
        "non_serious": _hit(NON_SERIOUS_PATTERN, tname, ename),
        "restricted_tname": bool(RESTRICTED_PATTERN.search(tname or '')),
        "restricted_ename": bool(RESTRICTED_PATTERN.search(ename or '')),
        "lower_class": _hit(LOWER_CLASS_PATTERN, tname, ename),
        "pre": _hit(PRE_PATTERN, tname, ename),
        "smapa": _hit(SMAPA_PATTERN, tname, ename),
        "force_weekday": is_force_weekday_name(tname, ename),
        "smacomi": is_smacomi_name(tname, ename),
    }


# ── 暦 (旧 spsp/common.py)。日付は TIMEZONE (= JST) で決める。derive.py がこの値でプロセスの TZ を設定する ──
TIMEZONE = "Asia/Tokyo"


def check_requirements() -> None:
    """この地域の判定に要る外部パッケージ (祝日表)。無ければ推測せず止める。"""
    try:
        import jpholiday  # noqa: F401
    except ImportError:
        raise RuntimeError("jpholiday が無い (pip install jpholiday) — 日本の祝日判定に要る")


def is_weekend_date(d: dt.date) -> bool:
    if d.weekday() >= 5:
        return True
    import jpholiday
    return jpholiday.is_holiday(d)


def is_weekend_range(start_d: dt.date, end_d: dt.date) -> bool:
    """開始日〜終了日のいずれかが土日祝なら True。15 日を超える長期イベント (endAt がブラケット閉鎖まで含む) は開始日だけで判定。"""
    if end_d < start_d:
        end_d = start_d
    delta = (end_d - start_d).days
    if delta > 14:
        return is_weekend_date(start_d)
    for i in range(delta + 1):
        if is_weekend_date(start_d + dt.timedelta(days=i)):
            return True
    return False


def is_force_weekend_date(d: dt.date) -> bool:
    """お盆 (8/13〜15) と年末年始 (12/26〜1/5): 祝日ではないが休みの人が多い期間。"""
    return ((d.month == 8 and 13 <= d.day <= 15)
            or (d.month == 12 and d.day >= 26)
            or (d.month == 1 and d.day <= 5))


def is_force_weekend_range(start_d: dt.date, end_d: dt.date) -> bool:
    """開始日〜終了日のいずれかがお盆/年末年始なら True (is_weekend_range と同じ走査規約)。"""
    if end_d < start_d:
        end_d = start_d
    delta = (end_d - start_d).days
    if delta > 14:
        return is_force_weekend_date(start_d)
    for i in range(delta + 1):
        if is_force_weekend_date(start_d + dt.timedelta(days=i)):
            return True
    return False


def calendar_flags(timestamp: int, end_timestamp: int | None) -> dict:
    d = dt.datetime.fromtimestamp(int(timestamp)).date()
    end_d = dt.datetime.fromtimestamp(int(end_timestamp if end_timestamp is not None else timestamp)).date()
    return {
        "date": d.isoformat(),
        "end_date": end_d.isoformat(),
        "is_weekend_real": is_weekend_range(d, end_d),
        "is_force_weekend_period": is_force_weekend_range(d, end_d),
    }


# ── クラス bracket (旧 spsp/data_loader._load_class_phase_group_ids) ──
def class_phase_group_ids(phases_data: dict | None, class_phase_files: list[dict]) -> tuple[bool, list[int]]:
    """(全 phase がクラスか, クラス bracket の phase_group_id 一覧)。
    全 phase が is_class の event は「event 自体がクラス大会」なので、その試合は本戦扱い (= 空リスト)。"""
    if not phases_data:
        return False, []
    phs = phases_data.get("phases") or []
    if phs and all(p.get("is_class") for p in phs):
        return True, []
    ids: set[int] = set()
    for cp in class_phase_files:
        for pg in (cp.get("phase_groups") or []):
            pgid = pg.get("phase_group_id")
            if pgid is not None:
                try:
                    ids.add(int(pgid))
                except (ValueError, TypeError):
                    pass
    return False, sorted(ids)


# ── 都道府県 (旧 spsp/cli/build_tournament_prefectures.py と build_player_prefectures.py) ──
PREFECTURES = ['北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県',
               '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県',
               '山梨県', '長野県', '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府',
               '兵庫県', '奈良県', '和歌山県', '鳥取県', '島根県', '岡山県', '広島県', '山口県', '徳島県',
               '香川県', '愛媛県', '高知県', '福岡県', '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県',
               '鹿児島県', '沖縄県']
_PREF_RE = re.compile('(' + '|'.join(PREFECTURES) + ')')


def place_prefecture(place: dict | None) -> str | None:
    """大会の開催地 (attr.json の place) → 都道府県 (漢字)。日本以外 (country_code が JP でも None でもない) は None。
    1. venue_address 中の都道府県表記を直接採用 (最も確実)、2. city を resolver で、3. venue_address 全文を resolver で。"""
    if not place:
        return None
    cc = place.get('country_code')
    if cc not in ('JP', None):
        return None
    va = place.get('venue_address') or ''
    m = _PREF_RE.search(va)
    if m:
        return m.group(1)
    city = place.get('city')
    if city:
        p, _ = resolve_prefecture(city)
        if p:
            return p
    if va:
        p, _ = resolve_prefecture(va)
        if p:
            return p
    return None


def classify_user(u: dict) -> dict | None:
    """users.jsonl の 1 行 → 居住地 (city) から都道府県。country が Japan で city がある人だけ対象 (それ以外は None = 行を書かない)。"""
    if u.get('country') != 'Japan':
        return None
    city = (u.get('city') or '').strip()
    if not city:
        return None
    pref, why = resolve_prefecture(city)
    return {"prefecture": pref, "prefecture_reason": why}


# ── derive.py から呼ばれる入口 ──
def classify_event(base: dict, ctx) -> dict:
    """base (derive.py が作る共通部分) に日本固有の判定を足して返す。ctx: attr / tname / ename / phases / class_phase_files。"""
    ok, reason = is_1on1_event(ctx.attr)
    ts = ctx.attr.get("timestamp")
    all_class, pg_ids = class_phase_group_ids(ctx.phases, ctx.class_phase_files)
    out = dict(base)
    out.update({
        "is_1on1": ok,
        "not_1on1_reason": reason,
        "names": name_flags(ctx.tname, ctx.ename),
        "calendar": calendar_flags(ts, ctx.attr.get("end_timestamp")) if ts is not None else None,
        "class_bracket": {"all_phases_class": all_class, "phase_group_ids": pg_ids},
        "place": {"prefecture": place_prefecture(ctx.attr.get("place"))},
        "naming": naming_labels(ctx.tname, ctx.ename),
    })
    return out
