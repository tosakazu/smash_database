"""naming (Japan) — 大会名から導くラベル。

シリーズ名 (`tournament_series`)、開催回 (`tournament_series_number`)、実績バッジ用のラベル
(`tournament_award_label` = シリーズ統合 / `tournament_individual_label` = 100 人以上の個別) を
大会名だけから決める。2026-09-09 に spsp (spsp/meta.py・spsp/common.py) から移した。判定結果は
derive.py が各イベントの derived.json に書き、spsp は読むだけ。

日本の大会名の表記ゆれ (「第N回」「#N」「其のN」「Vol.N」漢数字、ブランドのサブタイトル、
全角記号) を扱うので地域固有。他地域は自分の naming を持つ。
"""
from __future__ import annotations

import functools
import re


def __skipped(tag: str, detail: str) -> None:
    """spsp 側の skiplog に相当。ここでは黙って捨てる (derive.py が失敗を数える)。"""
    return None


# ── 既知シリーズの正規化マッピング ──
# tournament_series() の冒頭で先にチェックし、マッチしたら明示的なラベルを返す.
# (name_pattern, canonical_label) のリスト. expanded_label が指定されていれば
# 「拡大」キーワード入りは別ラベルとして区別する.
_KNOWN_SERIES = [
    # (regex, canonical, expanded_label_or_None)
    (re.compile(r'Weekly\s*Smash\s*Party|スマパ', re.IGNORECASE), 'スマパ', 'スマパ 拡大版'),
    (re.compile(r'(?:^|/|／)\s*(?:九龍|kowloon)', re.IGNORECASE), '九龍', None),  # 修飾語ありの場合は別扱い (半/全角)
    (re.compile(r'イツクシマ|ITSUKUSHIMA', re.IGNORECASE), 'イツクシマ', None),
    (re.compile(r'ジョウスマ|JOHSUMA|joh-?suma', re.IGNORECASE), 'ジョウスマ', None),
    (re.compile(r'UltCore', re.IGNORECASE), 'UltCore', None),
    (re.compile(r'(?:真[\s・·]*)?闘[龍竜]門|Shin\s*Toryumon', re.IGNORECASE), '真・闘龍門', None),
]
_EXPANDED_PAT = re.compile(r'拡大|スペシャル|special', re.IGNORECASE)


def _match_known_series(name: str) -> str | None:
    """既知シリーズマッピング. 該当しない場合 None.

    九龍系は修飾語 (LIMIT BREAK / withスマバト / 篝火 etc) があれば別シリーズ扱いに.
    イツクシマは「外伝」だけ別シリーズ扱い.
    ウメブラ/スマバトは SP suffix 省略. DELTA×西武撃 はデルブゲキに統合.
    """
    if not name: return None
    # 九龍特有: 修飾語ありの場合は別シリーズ
    if re.search(r'九龍|kowloon', name, re.IGNORECASE):
        if re.search(r'LIMIT\s*BREAK', name, re.IGNORECASE): return '九龍 LIMIT BREAK'
        if re.search(r'スマバト|SUMABATO', name, re.IGNORECASE): return '九龍×スマバト'
        if re.search(r'篝火|KAGARIBI', name, re.IGNORECASE): return '九龍×篝火'
        return '九龍'
    # イツクシマ: 外伝は別シリーズ
    if re.search(r'イツクシマ|ITSUKUSHIMA', name, re.IGNORECASE):
        if '外伝' in name: return 'イツクシマ外伝'
        return 'イツクシマ'
    # スマパ: カジュアル (下位クラス) / 拡大版 / 通常 を別シリーズ扱い.
    # community 側で 拡大版 → スマパ 統合 (カジュアルは community でも別維持).
    if re.search(r'Weekly\s*Smash\s*Party|スマパ', name, re.IGNORECASE):
        if re.search(r'カジュアル', name): return 'スマパカジュアル'
        if _EXPANDED_PAT.search(name): return 'スマパ 拡大版'
        return 'スマパ'
    # DELTA×西武撃 (合同) → デルブゲキ. Open / Rising バリアントは保持.
    if re.search(r'DELTA.*[×x✕✖].*西武撃|西武撃.*[×x✕✖].*DELTA', name, re.IGNORECASE):
        if re.search(r'\bOpen\b', name, re.IGNORECASE): return 'デルブゲキ Open'
        if re.search(r'\bRising\b', name, re.IGNORECASE): return 'デルブゲキ Rising'
        return 'デルブゲキ'
    # ウメブラ系 (SP suffix を省略). 「ウメブラがんばれ大会」は別 (matchしない).
    if re.search(r'ウメブラ\s*SP|Umebura\s*SP', name, re.IGNORECASE):
        return 'ウメブラ'
    # カリスマ系 (SP suffix を省略). 「カリスマSP11」等.
    if re.search(r'カリスマ\s*SP', name, re.IGNORECASE):
        return 'カリスマ'
    # JAPAN <num> は実名保持 (一回きりの大会). マエスマ'JAPAN は親シリーズで別マッチ.
    if re.fullmatch(r'\s*JAPAN\s+\d+\s*', name, re.IGNORECASE):
        return name.strip()
    # SUMABATO×CYCLOPS 合同 (× vs x の表記揺れ統合, スマバト generic より先にマッチ必須).
    if re.search(r'SUMABATO\s*[×x✕✖]\s*CYCLOPS', name, re.IGNORECASE):
        return 'SUMABATO×CYCLOPS'
    # スマバト系: SP suffix / アルティメット 全部メインに統合 (ユーザー指示).
    # 「九龍withスマバト」「DELTA×西武撃」「SUMABATO×CYCLOPS」等の合同は上で先にマッチ済.
    if re.search(r'スマバト|SUMABATO', name, re.IGNORECASE):
        return 'スマバト'
    # シリーズ名のみで判定できる残り
    if re.search(r'ジョウスマ|JOHSUMA|joh-?suma', name, re.IGNORECASE): return 'ジョウスマ'
    if re.search(r'UltCore', name, re.IGNORECASE): return 'UltCore'
    if re.search(r'(?:真[\s・·]*)?闘[龍竜]門|Shin\s*Toryumon', name, re.IGNORECASE): return '真・闘龍門'
    if re.search(r'グランドスラム|GrandSlam|Grand\s*Slam', name, re.IGNORECASE): return 'グラスラ'
    if re.search(r'ろえスマ', name): return 'ろえスマ'
    # 横浜大口スマブラ小規模対戦会 → 略称「横浜大口対戦会」
    if re.search(r'横浜大口', name): return '横浜大口対戦会'
    # 渋谷"達" シリーズ: 引用符正規化で剥がれる前にここで固定表記を返す.
    # ASCII/curly/full-width いずれの引用符 + 全/半角空白も許容.
    if re.search(r'渋谷\s*["\u2018\u2019\u201C\u201D\u2032\u2033\uFF02\uFF07]?\s*達', name):
        return '渋谷"達"'
    # 渋谷BeeSmash 系列: BIG / 通常 / Shibuya / Ueno / BeeSmash 単独 + typo (BeeSmah) も統合.
    if re.search(r'bee\s*sma', name, re.IGNORECASE):
        return '渋谷BeeSmash'
    # クロブラ 系列: 神奈川スマブラ対戦会クロブラ / 平日大会 / 祝日大会 / N周年記念大会 /
    #   LAN / 超対戦会 / Kurobra ... を統合. ただしチーム戦は別シリーズ扱い.
    if re.search(r'クロブラ|Kurobra', name, re.IGNORECASE):
        if re.search(r'チーム|Team', name, re.IGNORECASE):
            return 'クロブラチーム'
        return 'クロブラ'
    # 下克上系列: mini も統合.
    if re.search(r'下\s*[剋克]\s*上', name):
        return '下克上'
    # ── 以下、A: 純粋な表記揺れマージ (実績側でも同じシリーズ扱いで OK なもの) ──
    # りぷぶら系: SP suffix 省略 → 'りぷぶら'.
    if re.search(r'りぷぶら', name, re.IGNORECASE):
        return 'りぷぶら'
    # dragon valley smash tournament: case 統合 (D 大文字 vs d 小文字).
    if re.search(r'dragon\s+valley\s+smash', name, re.IGNORECASE):
        return 'dragon valley smash tournament'
    # oden dojo: case 統合.
    if re.search(r'oden\s+dojo', name, re.IGNORECASE):
        return 'oden dojo'
    # Luperón League Saga: case 統合 (saga vs Saga).
    if re.search(r'Luperón\s+League', name, re.IGNORECASE):
        return 'Luperón League Saga'
    # 横浜BaySmash 系列: BaySmash / YOKOHAMA BaySmash / Yokohama BaySmash / 横浜BaySmash BIG 全部統合.
    if re.search(r'bay\s*smash', name, re.IGNORECASE):
        return '横浜BaySmash'
    # Kamui 系列: case 統合 (Main "Kamui" + small "kamui"). mini は別扱いを `_EXPANDED_PAT` 同様
    # 維持したいが、実績的にも同じ系列とみなせるので統合する.
    if re.search(r'\bkamui\b', name, re.IGNORECASE):
        return 'Kamui'
    # たまスマ / tamasuma: Latin ↔ カタカナの表記揺れ. kyokkan/極冠 は collab で別シリーズ.
    if re.search(r'tamasuma|たまスマ', name, re.IGNORECASE):
        if re.search(r'kyokkan|極冠', name, re.IGNORECASE):
            return 'たまスマ×極冠'
        return 'たまスマ'
    # Smalab / SMALAB: case 統合.
    if re.search(r'\bsmalab\b', name, re.IGNORECASE):
        return 'Smalab'
    # ミコっち杯 / ミコッチ杯: っ vs ッ + ち vs チ の表記揺れ統合.
    if re.search(r'ミコ[っッ][ちチ]杯', name):
        return 'ミコっち杯'
    # UnLock / UnnnLock: typo (n の数違い) 統合.
    if re.search(r'\bUn+Lock\b', name, re.IGNORECASE):
        return 'UnLock'
    # IMPACT ARENA + typo IMAPCT (= 1 case): まとめる.
    if re.search(r'\bIM[AP]+CT\s*ARENA\b', name, re.IGNORECASE):
        return 'IMPACT ARENA'
    # Shibuya G.G.Smash 表記揺れ統合: "Shibuya G.G.Smash Weekly N" / "Shibuya GG.Smash N" 等.
    # G.G / GG / G G いずれの区切りも許容.
    if re.search(r'(?:Shibuya\s+)?G\.?\s*G\.?\s*Smash', name, re.IGNORECASE):
        return 'Shibuya G.G.Smash'
    # しのスマ系列: 平日しのスマ / しのスマFinal は通常回と統合. HEROES は別維持.
    if re.search(r'しのスマ', name):
        if 'HEROES' in name: return 'しのスマHEROES'
        return 'しのスマ'
    # LOVEスマ系列: 平日LOVEスマ / LOVEスマBO5 / LOVEスマKingRankCup / 1Day杯 等 全て LOVEスマ に統合.
    # (L.S.C.T = Love Smash Champion Tournament は major scale なので別維持).
    if re.search(r'LOVEスマ', name, re.IGNORECASE):
        return 'LOVEスマ'
    # SmashCruiseTournament: 8桁 YYYYMMDD prefix (例: 20240407SmashCruiseTournament) で日付別シリーズ
    # 判定されないよう統合.
    if re.search(r'SmashCruise', name, re.IGNORECASE):
        return 'SmashCruiseTournament'
    # A-Leg 系列: ローマ数字 + サブタイトル (-謹賀新年-, -The Golden Peak!-, -Duel of WINter!!- 等) を統合.
    if re.search(r'A-Leg', name, re.IGNORECASE):
        return 'A-Leg'
    # After Eight Smash 系列: #FINAL / expansion 等のバリアント全部統合.
    if re.search(r'After\s*Eight\s*Smash', name, re.IGNORECASE):
        return 'After Eight Smash'
    # DIVE 系列: Re:DIVE / DIVE Re:Boot / DIVE#FINAL / DIVE#Night / DIVE#X / DIVE理不尽 等 統合.
    # UnderGround は制限大会のため別維持.
    if re.match(r'^(?:Re:)?[\s\u3000]*DIVE', name):
        if re.search(r'Under\s*Ground', name, re.IGNORECASE): return 'DIVE UnderGround'
        return 'DIVE'
    # BIG LAGOON 系列: ASCII Roman (III) と Unicode Roman (Ⅱ) 両方のバリアント統合.
    if re.search(r'BIG\s*LAGOON', name, re.IGNORECASE):
        return 'BIG LAGOON'
    # Cafeteria Cup 系列: LUNCHTIME / Kings / Candy / Honey Pot 等のサブタイトル統合.
    if re.search(r'Cafeteria\s*Cup', name, re.IGNORECASE):
        return 'Cafeteria Cup'
    # Double G 系列: 2nd / 3rd Anniversary Tournament 含めて統合.
    if re.search(r'Double\s*G\b', name, re.IGNORECASE):
        return 'Double G'
    # GAME COMクウガ 系列: SSBU TOURNAMENT suffix の有無で分裂しないよう統合.
    if re.search(r'GAME\s*COM\s*クウガ', name, re.IGNORECASE):
        return 'GAME COMクウガ'
    # Generations of Tournament Mode 系列: ナンバリング + サブタイトル (Close to Home / Leftovers) 統合.
    if re.search(r'Generations\s+of\s+Tournament\s+Mode', name, re.IGNORECASE):
        return 'Generations of Tournament Mode'
    # INNOSUMA 系列: GRAND / OMISOKA / Championship / in 各地 等の variants 統合.
    # RAISE は制限大会のため別維持.
    if re.search(r'INNOSUMA', name, re.IGNORECASE):
        if re.search(r'RAISE', name, re.IGNORECASE): return 'INNOSUMA!! RAISE'
        return 'INNOSUMA!'
    # 火ノ鳥 系列: トナメ / 1周年記念 等の variants 統合.
    if re.search(r'火ノ鳥', name):
        return '火ノ鳥'
    # 暁 系列: "暁～礼～" / "～暁～麗" 等. 他ブランドの subtitle に「暁」が出るケース
    # (継王〜暁〜 / こくブラ ～暁前夜祭～ 等) は先頭文字で除外.
    if re.match(r'^[～〜~]?[\s\u3000]*暁', name):
        return '暁'
    # 美らブラ系列: 番号 / 開催地サフィックス (うるま市/那覇市/浦添市/八重瀬町/名護市/宜野湾市 等)
    # で別シリーズ判定されないよう、ビギナー杯 (制限) / 極 / 外伝 を除いて 美らブラ に統合.
    # SP suffix は省略 (= ウメブラ/カリスマ と同じポリシー).
    # 番号は 77, 77.5 等 decimal 含むので generic stripping に依存せずここで固定する.
    if re.search(r'美らブラ', name):
        if re.search(r'ビギナー(?:ズ)?\s*杯', name): return '美らブラ ビギナーズ杯'
        if re.search(r'極|Ultimate', name, re.IGNORECASE): return '美らブラ極'
        if re.search(r'外伝', name): return '美らブラ外伝'
        return '美らブラ'
    # MaesumaTOP (Latin) → マエスマTOP (= 同一ブランドの表記揺れ).
    if re.search(r'\b[Mm]aesuma\s*TOP\b', name):
        return 'マエスマTOP'
    # W-Zone Smash: Smash / smash の case 統合.
    if re.search(r'W[-\s]?Zone\s*smash', name, re.IGNORECASE):
        return 'W-Zone Smash'
    # Luperón League Saga: ó なし (Luperon) / 空白なし (LuperonSaga) も統合.
    if re.search(r'Lupe(?:ron|rón)', name, re.IGNORECASE):
        return 'Luperón League Saga'
    # SuperDry Kai / SuperDryKai: 空白あり/なしの表記揺れ.
    if re.search(r'SuperDry\s*Kai', name, re.IGNORECASE):
        return 'SuperDry Kai'
    # TENSUMA / TENsuma: case 統合 (Lowkey TENSUMA は community 側で扱う).
    if re.search(r'\bTENsuma\b', name, re.IGNORECASE):
        return 'TENSUMA'
    # WINNER! ブランド: 末尾 ! を保持 (= 通常の `!` 末尾ストリップを上書き).
    # WINNERS (S付き) は別シリーズなので除外.
    if re.search(r'\bWINNER\s*!', name, re.IGNORECASE):
        return 'WINNER!'
    # まめブラ系: SP suffix 省略 (= ウメブラSP→ウメブラ と同じポリシー).
    # 名前先頭が「まめブラ」のもののみ対象 (collab "マエスマoffline feat.銭スマ・まめブラ" は除外).
    if re.match(r'^[\s【]*まめブラ', name):
        return 'まめブラ'
    # 兵庫対戦会系: 兵庫(県)(スマブラ)(大)対戦会 のバリアント全部統合.
    if re.search(r'兵庫(?:県)?(?:スマブラ)?(?:大)?対戦会', name):
        return '兵庫対戦会'
    # 唐スマ / 唐津スマブラ対戦会: 略称・正式名どちらも統合.
    if re.search(r'^[\s【]*唐(?:スマ|津)', name):
        return '唐スマ'
    # 上野スマコミ系: 「上野スマコミ」「スマコミ」「Ueno Smash Ultimate Weekly-上野スマブラＳＰ平日大会」.
    if re.search(r'上野\s*Smash\s*Ultimate\s*Weekly|上野スマブラ', name, re.IGNORECASE):
        return '上野スマコミ'
    if re.search(r'上野スマコミ|^[\s【]*スマコミ', name):
        return '上野スマコミ'
    # 華勝武杯 系列: 単独大会 + Bo5 House XXXX 第N回華勝武杯前 (前夜祭) 含む (ユーザー指示).
    if re.search(r'華勝武', name):
        return '華勝武杯'
    # みどブラ 系列: 1on1 / SP / Revival / FINAL 全部統合. ただしチーム戦は別 (ユーザー指示).
    if re.search(r'みどブラ', name):
        if re.search(r'チーム|Team', name, re.IGNORECASE):
            return 'みどブラチーム'
        return 'みどブラ'
    return None


# 漢数字 → アラビア数字 (簡易、10 まで).
_KANJI_DIGIT_MAP = {
    '零': '0', '〇': '0', '一': '1', '二': '2', '三': '3', '四': '4',
    '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
    '壱': '1', '弐': '2', '参': '3', '肆': '4',
    '伍': '5', '陸': '6', '漆': '7', '捌': '8', '玖': '9', '拾': '10',
}

# 合成漢数字 (〜99) と小数点対応の変換。
#   「拾玖」=19 / 「二十三」=23 のように 十/拾 を位取りとして解釈する
#   (従来の1文字置換だと 拾玖 → "10"+"9" = 109 に化ける。西武撃 #拾玖﹅伍 型)。
#   十/拾 を含まない連は従来どおり1文字ずつ置換 (其の壱 → 1 等)。
_KANJI_NUM_CHARS = '零〇一二三四五六七八九十壱弐参肆伍陸漆捌玖拾'
_KANJI_NUM_RUN_RE = re.compile('[' + _KANJI_NUM_CHARS + ']+')
_KANJI_TENS_RE = re.compile(
    r'([零〇一二三四五六七八九壱弐参肆伍陸漆捌玖])?[十拾]'
    r'([零〇一二三四五六七八九壱弐参肆伍陸漆捌玖])?')


def _kanji_run_to_arabic(run: str) -> str:
    m = _KANJI_TENS_RE.fullmatch(run)
    if m:
        tens = int(_KANJI_DIGIT_MAP[m.group(1)]) if m.group(1) else 1
        units = int(_KANJI_DIGIT_MAP[m.group(2)]) if m.group(2) else 0
        return str(tens * 10 + units)
    return ''.join(_KANJI_DIGIT_MAP.get(c, c) for c in run)


def _kanji_numbers_to_arabic(s: str) -> str:
    """漢数字の連をアラビア数字へ変換し、数字に挟まれた ﹅/・/． を小数点 '.' に統一.
    例: #拾玖﹅伍 → #19.5, 十九・五 → 19.5"""
    s = _KANJI_NUM_RUN_RE.sub(lambda m: _kanji_run_to_arabic(m.group(0)), s)
    return re.sub(r'(?<=\d)[﹅・．](?=\d)', '.', s)


@functools.lru_cache(maxsize=None)   # 大会名だけの純関数。13 万回呼ばれるので (build 5 秒)
def tournament_series_number(name: str) -> int | float | None:
    """大会名から開催回数 (ナンバリング) を抽出. 取れない場合 None.

    対応パターン:
      - #N / ＃N / ♯N (小数対応: #19.5 / #拾玖﹅伍 → 19.5)
      - 第N回 / 第N幕 / 第N章 等 (漢数字 / 全角 / 半角)
      - 数字 + ordinal (1st, 2nd, Third 等)
      - Vol.N / Day N / Round N / シーズン N / その N
      - 末尾 / 区切り直前の数字 (ウメブラSP11, スマバトSP68 等)

    戻り値は整数なら int、小数ナンバリングのみ float。
    """
    if not name: return None
    s = name.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    # 漢数字 → アラビア (合成対応: 拾玖=19。﹅・． は小数点として '.' へ)
    s = _kanji_numbers_to_arabic(s)
    # English ordinals → number
    ord_map = {
        'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
        'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
        'eleventh': 11, 'twelfth': 12,
    }
    m = re.search(r'\b(' + '|'.join(ord_map.keys()) + r')\b', s, re.IGNORECASE)
    if m: return ord_map[m.group(1).lower()]
    # Numeric patterns (高優先度: 明示的なラベル付き)
    patterns = [
        r'[#＃♯]\s*(\d+(?:\.\d+)?)',
        r'第\s*(\d+)\s*[回幕章節期戦陣]',      # 陣 = 渋谷大乱 第一陣 (2026-08)
        r'(\d+)(?:st|nd|rd|th)\b',
        r'(?:[Vv]ol\.?|VOL\.?)\s*(\d+)',
        r'(?:[Dd]ay)\s*(\d+)',
        r'(?:[Rr]ound)\s*(\d+)',
        r'シーズン\s*(\d+)',
        r'[そ其]の\s*(\d+)',                 # その / 其の 両対応 (るゆぶらっ！其の壱 等)
        r'(\d+)\s*杯目',                      # N杯目 (スマわんこ 9杯目 等)
    ]
    def _num(txt):
        v = float(txt)
        return int(v) if v.is_integer() else v
    for p in patterns:
        m = re.search(p, s)
        if m:
            try: return _num(m.group(1))
            except (TypeError, ValueError): pass
    # Fallback: 「/」「／」前のセグメントの末尾数字 (ウメブラSP11 / UmeburaSP11 → 11).
    # ただし U/u が直前にある場合は除外 (U20 = Under 20 等).
    seg = re.split(r'[/／]', s, maxsplit=1)[0].rstrip()
    m = re.search(r'(?<![Uu])(?<=\D)(\d+(?:\.\d+)?)\s*[_\-‐－]*\s*$', seg)
    if m:
        try: return _num(m.group(1))
        except (TypeError, ValueError): pass
    return None


# 大文字小文字・記号ゆらぎで分裂した既知シリーズの正規表記 (casefold キー → 正式表記)。
_SERIES_CASE_CANON = {
    'impact': 'Impact',        # Impact#19/20 と impact#23
    '彩trial': '彩Trial',       # 彩Trial#1 と 彩trial#2/3
    'flyhigh': 'FlyHigh!',     # FlyHigh♯23 (=「!」抜き表記の回) を主流の FlyHigh! に統合
}


@functools.lru_cache(maxsize=None)   # 大会名だけの純関数。13 万回呼ばれるので (build 5 秒)
def tournament_series(name: str) -> str:
    """大会名からシリーズ名を抽出 (番号要素・サブタイトルを除去してシリーズ統合).

    既知シリーズ (スマパ、九龍、イツクシマ、ジョウスマ、UltCore、真・闘龍門) は
    先にマッチ. その他は汎用 stripping ロジックで処理.
    """
    if not name: return ''
    known = _match_known_series(name)
    if known is not None: return known

    # まず 第N回 prefix (漢数字対応、/ も含めて) を除去 — "第一回/幻月～..." → "幻月～..."
    # 「第N回(...)」のように直後に括弧で版/枝番が来るケースも吸収する (例: 第188回(8/5ver)横浜大口... → 横浜大口...).
    s = re.sub(r'^第\s*[一二三四五六七八九十百千万零壱弐参肆伍陸漆捌玖拾\d]+\s*回\s*(?:[(（][^)）]*[)）]\s*)?[/／]?\s*', '', name)
    # 先頭の 【...】 ラベル除去 — 「【第302回】スマACT」「【金曜】平日しのスマ」など
    s = re.sub(r'^\s*【[^】]*】\s*', '', s)
    # 連続空白を1つに正規化 (= "Tokyo  Nights" と "Tokyo Nights" の表記揺れ統合)
    s = re.sub(r'[\s　]{2,}', ' ', s)
    # 「平日」prefix を strip (= 平日X と X は同一シリーズ. 平日カミスマ / 平日いろスマ /
    # 平日極強 等. しのスマは _match_known_series で先取りされる).
    s = re.sub(r'^\s*平日', '', s)
    # 下位クラス prefix を strip (= "B class トーナメント マエスマTOP#1" → "マエスマTOP#1").
    # 下位クラスは独立シリーズにせず親シリーズと統合.
    s = re.sub(
        r'^[\s\u3000]*[BCDEＢＣＤＥ][\s\u3000_\-]*(?:class|クラス)[\s\u3000]*(?:トーナメント[\s\u3000]*)?',
        '', s, flags=re.IGNORECASE,
    )
    # 先頭の #N / ＃N / ♯N 数字プレフィックス + 空白 を除去 — 「#4 TRY DASH」「#2 やさしいスマッシュ」
    s = re.sub(r'^\s*[#＃♯]\s*\d+(?:\.\d+)?\s+', '', s)
    # 先頭の 「ブランド N」描述 形式: 「...」内側がブランド + 番号、外側が description.
    # 例: "「上スマ 1」上野スマブラSP平日大会" → "上スマ" (= 「」内から番号を除去)
    # 数字を含む「...」のみ対象 (= 「タイトル名」スタイルの引用符 prefix は誤剥がしを避ける).
    m = re.match(r'^\s*「([^」]*\d[^」]*)」', s)
    if m:
        inner = re.sub(r'\s*\d+(?:\.\d+)?\s*', '', m.group(1)).strip()
        if inner:
            s = inner
    # 日付プレフィックス除去 (smart `/` split より前にやる): 8/4, 11/13, 2024.5.20 など.
    s = re.sub(r'^\d+\s*日?\s*[\(（][月火水木金土日祝振休昼夜朝夕\s]+[\)）]\s*', '', s)
    s = re.sub(r'^\d+[\.\-\/]\d+(?:[\.\-\/]\d+)?\s*', '', s)
    # 先頭の (...) / [...] による prize / status マーカー除去 (英語 weekly 等で頻出).
    # 例: "($100) The Sundown Series #85" → "The Sundown Series #85"
    #     "[$10] Friday Night Doubles" → "Friday Night Doubles"
    #     "(Online) Tent IN A HOUSE #2" → "Tent IN A HOUSE #2"
    #     "[CANCELED] Down the Bayou Brawl" → "Down the Bayou Brawl"
    # 日本語タイトルではこのパターンは稀 (= 大抵 ()内に意味があり、本タイトルの一部) なので
    # ASCII / 記号で始まる名前のみに限定する (= 先頭文字が JP の場合は touch しない).
    if s and s[0] not in '\u3040\u3041' and not re.match(r'^[\u3040-\u9fff]', s):
        for _ in range(3):
            prev = s
            s = re.sub(r'^\s*[(（][\$\d\w][^)）]*[)）]\s*', '', s)
            s = re.sub(r'^\s*[\[【][\$\d\w][^\]】]*[\]】]\s*', '', s)
            if s == prev: break
    # JP/EN 二言語表記の `/` 区切りでの分割は、`/` の左右が「括弧外で完結している」場合のみ.
    # 例: 篝火#15/KAGARIBI#15 → 篝火#15 はOK. 一方 (8/5ver) のように括弧内の `/` で分割しない.
    # 括弧扱い: ()（）【】「」〔〕《》『』 全てカウント.
    paren_depth = 0
    split_pos = -1
    for i, ch in enumerate(s):
        if ch in '(（【「〔《『': paren_depth += 1
        elif ch in ')）】」〕》』': paren_depth = max(0, paren_depth - 1)
        elif ch == '/' and paren_depth == 0:
            split_pos = i; break
    if split_pos >= 0:
        s = s[:split_pos].strip()
    s = s.strip()
    # もう一度 第N回 prefix (split 後にも残る場合があるので)
    s = re.sub(r'^第\s*[一二三四五六七八九十百千万零壱弐参肆伍陸漆捌玖拾\d]+\s*回\s*', '', s)
    # 先頭の 【...】 もう一度
    s = re.sub(r'^\s*【[^】]*】\s*', '', s)
    # 日付プレフィックス除去: 11(火祝)上野スマコミ → 上野スマコミ。曜日 + 任意で「祝」「振休」「昼」「夜」「朝」等
    s = re.sub(r'^\d+\s*日?\s*[\(（][月火水木金土日祝振休昼夜朝夕\s]+[\)）]\s*', '', s)
    s = re.sub(r'^\d+[\.\-\/]\d+\s*', '', s)
    # サブタイトル除去 (引用符・括弧). 《...》『...』〔...〕 も同様に剥がす.
    s = re.sub(r'\s*["""][^"""]*["""]\s*$', '', s)
    s = re.sub(r'\s*[（(][^)）]*[)）]\s*$', '', s)
    s = re.sub(r'\s*[《〔『][^》〕』]*[》〕』]\s*$', '', s)
    # 引用符正規化 (シングル/ダブル、ASCII/curly/fullwidth、prime まで全て除去).
    # 例: 渋谷"達" / 渋谷"達" / 渋谷 "達" / 渋谷"達" を統一して 渋谷達 にする.
    s = re.sub(r"[\u0022\u0027\u2018\u2019\u201A\u201B\u201C\u201D\u201E\u201F\u2032\u2033\uFF02\uFF07]", '', s)
    # 引用符除去で残った余分なスペース (例: '渋谷 達' → '渋谷達') を collapse
    s = re.sub(r'(?<=[\u3040-\u9fff])\s+(?=[\u3040-\u9fff])', '', s)
    # まず "#N + 後続サブタイトル" を greedy で除去 (例: 渋谷"達" #79 shibuya -tatsu → 渋谷"達")
    # # (U+0023) / ＃ (U+FF03) / ♯ (U+266F MUSIC SHARP SIGN) すべて対応
    s = re.sub(r'\s*[#＃♯]\s*\d+(?:\.\d+)?.*$', '', s)
    # "JP + 数字 + 半角英字サブタイトル" → ASCII subtitle 部分を除去.
    # 例: 渋谷"達"36 shibuya -tatsu → 渋谷"達"
    # 直前が日本語の時のみ適用 (英語ブランド "1,2 Tuesdays" 等を誤分割しないため).
    s = re.sub(r'(?<=[\u3040-\u9fff])\d+(?:\.\d+)?\s*[A-Za-z\-][A-Za-z0-9\s\-]*\s*$', '', s)
    # 末尾の 【...】 ラベル除去 — 「船スマ32人規模トーナメント【船橋スマブラ対戦会】」など
    s = re.sub(r'[\s\u3000]*【[^】]*】[\s\u3000]*$', '', s)
    # 末尾の _N / Vol.N / " N" / 数字塊 / その N / Day N / Round N など (繰り返し)。
    # 〜...〜 wrap の内側に番号が残るケース (例: 「〜こくブラ Season39.75〜」) があるため、
    # tilde strip の後にもう一度呼べるようクロージャにしてある。
    def _strip_tail_numbering(s: str) -> str:
        for _ in range(3):
            prev = s
            s = re.sub(r'_\s*\d+(?:\.\d+)?\s*$', '', s)
            s = re.sub(r'[Vv]ol\.?\s*\d+\s*$', '', s)
            s = re.sub(r'\s*その\s*\d+\s*$', '', s)                                              # 大菊月 その5
            # 其の N / 其のN / そのN (= N が漢数字 / 算用数字 両対応): 例 るゆぶらっ！其の弐 → るゆぶらっ！
            s = re.sub(r'[\s\u3000]*[そ其]の[\s\u3000]*[一二三四五六七八九十壱弐参肆伍陸漆捌玖拾零\d]+[\s\u3000]*$', '', s)
            # N杯目: スマわんこ12杯目 → スマわんこ
            s = re.sub(r'[\s\u3000]*\d+[\s\u3000]*杯目[\s\u3000]*$', '', s)
            # 末尾の 第N回 / 第N陣 / 第N幕 等 (漢数字・算用数字とも)。
            # 例: 渋谷大乱 第一陣 → 渋谷大乱 (= 第二陣 と同じシリーズに揃える)
            s = re.sub(r'[\s\u3000]*第[\s\u3000]*'
                       r'[一二三四五六七八九十百千万零壱弐参肆伍陸漆捌玖拾\d]+'
                       r'[\s\u3000]*[回幕章節期戦陣][\s\u3000]*$', '', s)
            # N人規模(トーナメント): 船スマ32人規模トーナメント → 船スマ (= 定員も回数同様に除去)
            s = re.sub(r'[\s\u3000]*\d+[\s\u3000]*人規模(?:トーナメント)?[\s\u3000]*$', '', s)
            # クラス別トナメ suffix: こくブラ Season20 Aトナメ → こくブラ Season20
            # (= 下位クラス prefix と同方針でクラス別トナメも親シリーズに統合)
            s = re.sub(r'[\s\u3000]*[A-EＡ-Ｅ][\s\u3000]*(?:クラス)?トナメ[\s\u3000]*$', '', s)
            s = re.sub(r'\s*[Dd]ay\s*\d+\s*$', '', s)                                            # Day 2
            s = re.sub(r'\s*[Rr]ound\s*\d+\s*$', '', s)                                          # Round 3
            s = re.sub(r'\s*[Ss]eason\s*\d+(?:\.\d+)?\s*$', '', s)                               # Season 39.75
            s = re.sub(r'\s*シーズン\s*\d+(?:\.\d+)?\s*$', '', s)
            # 年度+季節 suffix: ELEVATE 2025冬 / 2026冬 → ELEVATE (年度・季節違いは同一シリーズ)
            s = re.sub(r'[\s\u3000]*20\d{2}[\s\u3000]*[春夏秋冬]\s*$', '', s)                                            # シーズン 5
            s = re.sub(r'\s*第[一二三四五六七八九十百千万零壱弐参肆伍陸漆捌玖拾\d]+\s*[幕回章節期戦話]\s*$', '', s)  # 第壱幕、第零幕
            # 英序数: First/Second/.../1st/2nd
            s = re.sub(r'\s+(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|Eleventh|Twelfth)\s*$', '', s, flags=re.IGNORECASE)
            s = re.sub(r'\s+\d+(?:st|nd|rd|th)\s*$', '', s, flags=re.IGNORECASE)
            s = re.sub(r'\s+\d+(?:\.\d+)?\s*$', '', s)
            # トレイル "数字 + _" や "数字 + -" は反復番号と判断 (例: カリスマSP17_ → カリスマSP)
            s = re.sub(r'\d+[_\-‐－]+\s*$', '', s)
            # トレイル数字 (直前が 非数字 かつ U/u でない場合のみ strip)
            # 例: ウメブラSP12 → ウメブラSP、カリスマSP17 → カリスマSP
            # 例外: マエスマU20 (U20 = Under 20 はそのまま)
            s = re.sub(r'(?<![Uu])(?<=\D)\d+(?:\.\d+)?\s*$', '', s).strip()
            if s == prev: break
        return s
    s = _strip_tail_numbering(s)
    # 〜...〜 / ~...~ ペア or 末尾 〜 (#N strip 後にやる必要があるので最後)
    # 末尾だけのペア strip (= "本タイトル 〜 サブタイトル 〜" 形式) は left 部分があるときのみ.
    # 全体 wrap (= "〜こくブラ in Tenjin〜") は別ロジックで leading / trailing をそれぞれ落とす.
    s_stripped = s.lstrip()
    if s_stripped and s_stripped[0] not in '～〜~':
        # 先頭が非 tilde の時のみ pair strip (末尾の suffix subtitle 想定)
        s = re.sub(r'[\s\u3000]*[～〜~][^～〜~]*?[～〜~][\s\u3000]*$', '', s)
    # 末尾の単独 〜 を strip
    s = re.sub(r'[\s\u3000]*[～〜~][\s\u3000]*$', '', s)
    # 先頭の単独 〜 (全体 wrap の場合) も strip
    s = re.sub(r'^[\s\u3000]*[～〜~][\s\u3000]*', '', s)
    # 〜...〜 wrap の内側に隠れていた末尾ナンバリングをもう一度除去
    # (例: 「〜こくブラ Season39〜」 → unwrap 後の「こくブラ Season39」をここで畳む)。
    s = _strip_tail_numbering(s)
    # 末尾の -ASCII subtitle- (ローマ字読みなど): 例 "錦祭-Kinsai-" → "錦祭". 完全除去.
    # ハイフン直前が「空白 or 日本語文字」のときのみ subtitle 区切りと判定.
    # ASCII 直前 (例: "A-Game", "X-Roads", "Pop-Con") は ハイフン付きブランド名と見なし維持する.
    # さらに、left 部分が全 ASCII (= 日本語を含まない) なら subtitle ではなくブランドの一部と
    # 判定する (例: "Jogibu -final lap-" は "Jogibu" 単独だとプレイヤー名と紛らわしいので保持).
    _m_subtitle = re.match(
        r'^(.+?)(?:[\s\u3000]+|(?<=[\u3040-\u9fff]))[-‐－][\s\u3000]*[A-Za-z][A-Za-z0-9\s\-\.]*[\s\u3000]*[-‐－]?[\s\u3000]*$',
        s,
    )
    if _m_subtitle and re.search(r'[\u3040-\u9fff]', _m_subtitle.group(1)):
        s = _m_subtitle.group(1).rstrip()
    # 末尾の -日本語- (短い日本語サブタイトル): 例 "こしスマ -改-" → "こしスマ 改". ハイフンを空白に.
    s = re.sub(r'[\s\u3000]*[-‐－][\s\u3000]*([^\s\u3000\-A-Za-z0-9][^\-]*?)[\s\u3000]*[-‐－][\s\u3000]*$', r' \1', s)
    # 末尾のダッシュ・記号. ASCII-only タイトル (= 日本語を含まない) では subtitle 区切りと
    # 区別できないので、剥がさない (例: "Jogibu -final lap-" の末尾 "-" は brand の一部).
    if re.search(r'[\u3040-\u9fff]', s):
        s = re.sub(r'\s*[-‐－〜～~]+\s*$', '', s)
    # "ブランド名! -サブタイトル-? [付帯]" 形式の末尾 strip.
    # 例: "WINNER! -WE5!- LFS" → "WINNER!" / "WINNER! -NEXT#3-" (= #3- strip 後 "WINNER! -NEXT") → "WINNER!"
    # `!` が brand 末尾にあり、その後 ` -` で始まる ASCII subtitle が続くケースを限定対象にする.
    s = re.sub(r'([!！])[\s\u3000]+-[A-Za-z0-9!！\s\-]+$', r'\1', s)
    # 末尾の感嘆符は brand identity の一部として保持するが、!/！ の数の表記揺れを統合する.
    # 例: "るゆぶらっ！" / "るゆぶらっ！！" / "るゆぶらっ ！" → 全て "るゆぶらっ！".
    # 半角/全角混在も最初の文字に統一 (= "スマパ！!" → "スマパ！", "Fly High!!" → "Fly High!").
    def _collapse_excl(_m):
        t = _m.group(0)
        for c in t:
            if c == '!' or c == '！':
                return c
        return ''
    s = re.sub(r'[\s\u3000]*[!！](?:[\s\u3000]*[!！])*[\s\u3000]*$', _collapse_excl, s)
    # ブランド表記正規化: "Fly High" → "FlyHigh" (空白あり/なし両方の表記揺れを統合)
    s = re.sub(r'^[Ff]ly\s+[Hh]igh\b', 'FlyHigh', s)
    # ローマ数字 (Unicode Ⅰ-Ⅻ / ⅰ-ⅻ U+2160..U+217B) を末尾シリーズ番号として除去.
    # 例: "ウメブラⅢ" → "ウメブラ", "雷影杯Ⅴ" → "雷影杯". ASCII 大文字の I/V/X はシリーズ名に
    # 含まれる可能性 (例 "BaySmash V") があるため Unicode 専用に限定する.
    s = re.sub(r'\s*[\u2160-\u217F]+\s*$', '', s)
    # 日本語 ↔ ASCII (英数字) 境界の空白を collapse: "下克上 mini" → "下克上mini"
    s = re.sub(r'(?<=[\u3040-\u9fff])\s+(?=[A-Za-z0-9])', '', s)
    s = re.sub(r'(?<=[A-Za-z0-9])\s+(?=[\u3040-\u9fff])', '', s)
    # 異字体・旧字体の統合 (シリーズ判定で別シリーズ扱いにならないように).
    # 剋 (U+524B) は 克 (U+514B) の異字体 — 下克上/下剋上 で表記揺れがある.
    s = s.translate(str.maketrans('剋', '克'))
    # 大文字小文字・末尾記号の表記揺れで分裂した既知シリーズの正規化
    # (主催者が途中で表記を変えたケース。casefold キーで引く)。
    s = s.strip()
    s = _SERIES_CASE_CANON.get(s.casefold(), s)
    return s or name


def _match_special_brand(name: str) -> str | None:
    """award label 用 (= シリーズ統合) の特殊 brand. None なら通常正規化に委ねる.
    対応:
      - スマパ系: 通常 / 拡大版 / カジュアル を分離.
      - クロブラ系: 「クロブラ」に統合 (周年記念等の variant も同じ).
    """
    if not name: return None
    if re.search(r'Weekly\s*Smash\s*Party|スマパ', name, re.IGNORECASE):
        if re.search(r'カジュアル', name): return 'スマパカジュアル'
        if re.search(r'拡大版|エキスパンド|Expanded|EXPANDED', name, re.IGNORECASE):
            return 'スマパ拡大版'
        return 'スマパ'
    if re.search(r'クロブラ|kurobra', name, re.IGNORECASE):
        return 'クロブラ'
    if re.search(r'グランドスラム|Grand\s*Slam', name, re.IGNORECASE):
        return 'グランドスラム'
    if re.search(r'真[・\s\.]?闘龍門|Shin\s*Toryumon', name, re.IGNORECASE):
        return '真・闘龍門'
    if re.search(r'りぷぶら|Ripubura', name, re.IGNORECASE):
        return 'りぷぶら'
    if re.search(r'篝火|Kagaribi', name, re.IGNORECASE):
        return '篝火'
    if re.search(r'イツクシマ|ITSUKUSHIMA|厳島', name, re.IGNORECASE):
        return 'イツクシマ'
    if re.search(r'ろえスマ', name):
        return 'ろえスマ！'
    # BeeSmash 系: BIG だけ別、その他全部 渋谷BeeSmash に統合 (= 表記揺れ吸収).
    # BeeSmah (typo), Beesmash (lowercase), ShibuyaBeeSmash, Ueno BeeSmash, BeeSmash 等を全部統合.
    if re.search(r'Bees?ma[hs]h?', name, re.IGNORECASE):
        if re.search(r'\bBIG\b', name, re.IGNORECASE):
            return '渋谷BeeSmash BIG'
        return '渋谷BeeSmash'
    # 横浜大口スマブラ小規模対戦会 → 横浜大口対戦会
    if re.search(r'横浜大口', name):
        return '横浜大口対戦会'
    return None


def _match_special_brand_individual(name: str) -> str | None:
    """individual label 用 (= 番号保持). None なら通常正規化に委ねる.
    base brand + #N (or 周年記念 等の suffix) を組み立てて返す.
    """
    if not name: return None
    base = _match_special_brand(name)
    if base is None: return None
    # スマパカジュアル / スマパ拡大版 は通常 number は付かないが、付くなら追加
    num = tournament_series_number(name)
    # 篝火 (= 第1回 篝火 は number 無しの可能性) → 篝火#1
    if base == '篝火' and num is None:
        num = 1
    # クロブラ系の周年記念サフィックス
    anniv_m = re.search(r'(\d+)\s*周年', name)
    parts = [base]
    if num is not None:
        parts.append(f'#{num}')
    if anniv_m and base == 'クロブラ':
        parts.append(f'({anniv_m.group(1)}周年記念)')
    # join with space when adding suffix
    if len(parts) == 1: return parts[0]
    out = parts[0]
    if len(parts) > 1: out += parts[1]  # base#N
    if len(parts) > 2: out += ' ' + ' '.join(parts[2:])  # plus 周年記念
    return out


def _normalize_brand_common(s: str) -> str:
    """共通の brand 正規化: 先頭 prefix / JP-EN split / 末尾 subtitle / 異字体 / !統合."""
    # 先頭の 第N回 (漢数字 / 算用数字) prefix を除去
    s = re.sub(r'^第\s*[一二三四五六七八九十百千万零壱弐参肆伍陸漆捌玖拾\d]+\s*回\s*(?:[(（][^)）]*[)）]\s*)?[/／]?\s*', '', s)
    # 先頭の 【...】 ラベル
    s = re.sub(r'^\s*【[^】]*】\s*', '', s)
    # 先頭の #N
    s = re.sub(r'^\s*[#＃♯]\s*\d+(?:\.\d+)?\s+', '', s)
    # 先頭日付 8 桁 (= 20250330 等)
    s = re.sub(r'^\d{8}\s*', '', s)
    # 日付プレフィックス (8/4, 11/13(土) 等)
    s = re.sub(r'^\d+\s*日?\s*[\(（][月火水木金土日祝振休昼夜朝夕\s]+[\)）]\s*', '', s)
    s = re.sub(r'^\d+[\.\-\/]\d+(?:[\.\-\/]\d+)?\s*', '', s)
    # 「BRAND N」prefix → BRAND 抽出 (例: 「上スマ18」上野スマブラSP平日大会 → 上スマ)
    m = re.match(r'^\s*「([^」]*\d[^」]*)」', s)
    if m:
        inner = re.sub(r'\s*\d+(?:\.\d+)?\s*', '', m.group(1)).strip()
        if inner:
            s = inner
    # JP/EN 区切り (/) — 括弧外のみ
    paren_depth = 0
    split_pos = -1
    for i, ch in enumerate(s):
        if ch in '(（【「〔《『': paren_depth += 1
        elif ch in ')）】」〕》』': paren_depth = max(0, paren_depth - 1)
        elif ch == '/' and paren_depth == 0:
            split_pos = i; break
    if split_pos >= 0:
        s = s[:split_pos].strip()
    # 末尾の 【...】 / (...) サブタイトル strip
    s = re.sub(r'\s*【[^】]*】\s*$', '', s)
    s = re.sub(r'\s*[（(][^)）]*[)）]\s*$', '', s)
    s = re.sub(r'\s*[《〔『][^》〕』]*[》〕』]\s*$', '', s)
    # 末尾 "Tournament" suffix (= ASCII brand に Tournament 付き → 削除).
    # 例: SmashCruiseTournament → SmashCruise.
    s = re.sub(r'(?<=[a-zA-Z])Tournament\s*$', '', s).rstrip()
    # JP brand + 数字 + JP descriptor → JP brand only.
    # 例: 船スマ48人規模トーナメント → 船スマ.
    m = re.match(r'^([\u3040-\u9fff]{2,8})\d+[\u3040-\u9fff]', s)
    if m:
        s = m.group(1)
    return s.strip()


def _strip_brand_subtitle(s: str) -> str:
    """ローマ字 subtitle (= 九龍-KOWLOON- / WINNER! -Next- / 錦祭-Kinsai-) を strip.
    JP + (space?) dash + ASCII もしくは ASCII + space + dash + ASCII を対象.
    """
    # Pattern 1: JP直後の dash + ASCII (e.g. 九龍-KOWLOON-, ジョウスマ -JOHSUMA, 錦祭-Kinsai-)
    s = re.sub(r'(?<=[\u3040-\u9fff])\s*[-‐－][\s\u3000]*[A-Za-z][A-Za-z0-9\s\-\.!]*[\s\u3000]*[-‐－]?[\s\u3000]*$', '', s).rstrip()
    # Pattern 2: ASCII + space + dash + ASCII (e.g. WINNER! -Next-). space 必須で A-Game 等を保護.
    s = re.sub(r'(?<=[A-Za-z0-9!])\s+[-‐－][\s\u3000]*[A-Za-z][A-Za-z0-9\s\-\.!]*[\s\u3000]*[-‐－]?[\s\u3000]*$', '', s).rstrip()
    return s


def _collapse_excl_marks(s: str) -> str:
    """末尾 !/！ の数 / 半全 / 空白を最初の 1 文字に collapse."""
    def _collapse(_m):
        t = _m.group(0)
        for c in t:
            if c == '!' or c == '！':
                return c
        return ''
    return re.sub(r'[\s\u3000]*[!！](?:[\s\u3000]*[!！])*[\s\u3000]*$', _collapse, s)


def _extract_quoted_brand_if_ascii(s: str) -> str:
    """ALL-ASCII で quote 内に sub-brand があれば抽出 (= NAGOYA SSBU TOURNAMENT "UltCore" → UltCore)."""
    if re.search(r'[\u3040-\u9fff]', s): return s  # JP含む場合は skip
    m = re.search(r'["「『\u201c\u201d\u2018\u2019\uff02]([A-Za-z][A-Za-z0-9]*)["」』\u201c\u201d\u2018\u2019\uff02]', s)
    if m: return m.group(1)
    return s


def tournament_award_label(name: str) -> str:
    """表彰バッチ用 (= シリーズ統合) のラベル抽出.

    Rules:
      - サブタイトル (= mini / 〜...〜 等) は **保持** (= 下克上mini は 下克上mini).
      - ローマ字 subtitle (= -KOWLOON-, -JOHSUMA, -Next- 等) は strip.
      - 末尾 SP suffix (= 大会名 SP) は strip (ウメブラSP → ウメブラ, スマバトSP → スマバト).
      - アポストロフィ ' は strip (= マエスマ' → マエスマ).
      - ナンバリング (#N / 第N回 / 其の N / N杯目 / Vol N / Day N / Round N / シーズン N /
        末尾ローマ数字 / 末尾連番) は strip.
      - ALL-ASCII で quote 内 sub-brand は抽出 (NAGOYA SSBU TOURNAMENT "UltCore" → UltCore).
      - _match_known_series は使わない.
      - 異字体統合 (剋→克).
      - !/！ の表記揺れ collapse.
    """
    if not name: return ''
    # スマパ / クロブラ等の特殊 brand は専用ハンドラに委譲
    special = _match_special_brand(name)
    if special is not None: return special
    s = _normalize_brand_common(name)
    # 末尾 numbering の繰り返し strip (subtitle は保持)
    for _ in range(3):
        prev = s
        s = re.sub(rf'\s*[#＃♯]\s*[\d{_KANJI_NUM_CHARS}].*$', '', s)
        s = re.sub(r'[\s\u3000]*[そ其]の[\s\u3000]*[一二三四五六七八九十壱弐参肆伍陸漆捌玖拾零\d]+[\s\u3000]*$', '', s)
        s = re.sub(r'[\s\u3000]*\d+[\s\u3000]*杯目[\s\u3000]*$', '', s)
        s = re.sub(r'\s*その\s*\d+\s*$', '', s)
        s = re.sub(r'[Vv]ol\.?\s*\d+\s*$', '', s)
        s = re.sub(r'\s*[Dd]ay\s*\d+\s*$', '', s)
        s = re.sub(r'\s*[Rr]ound\s*\d+\s*$', '', s)
        s = re.sub(r'\s*シーズン\s*\d+\s*$', '', s)
        s = re.sub(r'\s*第[一二三四五六七八九十百千万零壱弐参肆伍陸漆捌玖拾]+\s*[幕回章節期戦]\s*$', '', s)
        s = re.sub(r'\s+\d+(?:st|nd|rd|th)\s*$', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s+(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|Eleventh|Twelfth)\s*$', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*[\u2160-\u217F]+\s*$', '', s)
        s = re.sub(r'(?<![Uu])(?<=\D)\d+(?:\.\d+)?\s*$', '', s).rstrip()
        if s == prev: break
    s = _strip_brand_subtitle(s)
    # 末尾 SP suffix strip (= ウメブラSP → ウメブラ, スマバトSP → スマバト)
    s = re.sub(r'(?<=[\u3040-\u9fff])\s*SP\s*$', '', s).rstrip()
    # クォート系: curly / fullwidth → ASCII に統一 (= 渋谷"達" を保持しつつ表記揺れ吸収)
    s = re.sub(r'[\u201C\u201D\u201E\u201F\uFF02]', '"', s)
    s = re.sub(r"[\u2018\u2019\u201A\u201B\uFF07]", "'", s)
    # アポストロフィ ' / prime ′″ は strip (= マエスマ' → マエスマ)
    s = re.sub(r"['\u2032\u2033]+", '', s)
    # ASCII " の周りの空白を除去 (= 渋谷 "達" → 渋谷"達")
    s = re.sub(r'\s*"\s*', '"', s)
    # JP-JP 間の余分な空白を collapse (= 渋谷 達 → 渋谷達)
    s = re.sub(r'(?<=[\u3040-\u9fff])\s+(?=[\u3040-\u9fff])', '', s)
    # 日本語 / ASCII 境界の空白 collapse
    s = re.sub(r'(?<=[\u3040-\u9fff])\s+(?=[A-Za-z0-9])', '', s)
    s = re.sub(r'(?<=[A-Za-z0-9])\s+(?=[\u3040-\u9fff])', '', s)
    s = _collapse_excl_marks(s)
    s = _extract_quoted_brand_if_ascii(s)
    s = s.translate(str.maketrans('剋', '克'))
    return s.strip() or name


def tournament_individual_label(name: str) -> str:
    """≥100 nent 個別表彰用. award_label と同様の brand 正規化を行うが
    ナンバリング (#N / 末尾連番) は **保持** する. 表記揺れを # に統一.
    例: 篝火#15 → 篝火#15, ウメブラSP#10 → ウメブラ#10, JAPAN 24 → JAPAN 24,
        九龍-KOWLOON-#17 → 九龍#17, マエスマ'TOP#5 → マエスマTOP#5.
    """
    if not name: return ''
    special = _match_special_brand_individual(name)
    if special is not None: return special
    s = _normalize_brand_common(name)
    # 1. iteration number を取得 (= 後で再付与用)
    num = tournament_series_number(name)
    # 2. # / 末尾連番を一時除去 (= subtitle strip を阻害しないため)
    s_brand = re.sub(rf'\s*[#＃♯]\s*[\d{_KANJI_NUM_CHARS}].*$', '', s).rstrip()
    if num is not None:
        s_brand = re.sub(r'(?<![Uu])(?<=\D)\s*\d+(?:\.\d+)?\s*$', '', s_brand).rstrip()
    # 3. brand subtitle strip (-KOWLOON- / -Next- etc.)
    s_brand = _strip_brand_subtitle(s_brand)
    # 4. SP suffix strip (JP直後, ウメブラSP → ウメブラ)
    s_brand = re.sub(r'(?<=[\u3040-\u9fff])\s*SP\s*$', '', s_brand).rstrip()
    s_brand = re.sub(r'(?<=[\u3040-\u9fff])SP(?=\d|$)', '', s_brand).rstrip()
    # 5. クォート系の表記揺れ統合: curly/fullwidth → ASCII; apostrophe は strip; 達 を囲む " は保持.
    s_brand = re.sub(r'[\u201C\u201D\u201E\u201F\uFF02]', '"', s_brand)
    s_brand = re.sub(r"[\u2018\u2019\u201A\u201B\uFF07]", "'", s_brand)
    s_brand = re.sub(r"['\u2032\u2033]+", '', s_brand)
    s_brand = re.sub(r'\s*"\s*', '"', s_brand)
    # 6. JP-JP / JP-ASCII 境界空白 collapse
    s_brand = re.sub(r'(?<=[\u3040-\u9fff])\s+(?=[\u3040-\u9fff])', '', s_brand)
    s_brand = re.sub(r'(?<=[\u3040-\u9fff])\s+(?=[A-Za-z0-9])', '', s_brand)
    s_brand = re.sub(r'(?<=[A-Za-z0-9])\s+(?=[\u3040-\u9fff])', '', s_brand)
    s_brand = _collapse_excl_marks(s_brand)
    s_brand = _extract_quoted_brand_if_ascii(s_brand)
    s_brand = s_brand.translate(str.maketrans('剋', '克'))
    base = s_brand.strip() or name
    # 7. number 再付与: 元の表記スタイルに合わせる
    if num is not None:
        if re.search(rf'[#＃♯]\s*[\d{_KANJI_NUM_CHARS}]', name):
            return f'{base}#{num}'
        # ASCII 末尾 "BRAND 数字" 形式 (= JAPAN 24): space 区切り保持
        if re.search(r'[A-Z][A-Za-z]*\s+\d+(?:\.\d+)?\s*$', name):
            return f'{base} {num}'
        # それ以外: そのまま接続 (= ウメブラSP12 → ウメブラ12)
        return f'{base}{num}'
    return base


# ── 実際には開催されていない / テスト用の大会 (旧 spsp/cli/build_series_json.py) ──
# 大会名に【中止】【延期】等を含む場合はコミュニティ集計から除外する。
CANCELLED_PATTERN = re.compile(
    r'【\s*中止|【\s*延期|【\s*[Cc]ancel|\bcancelled\b|\bcanceled\b|POSTPONED',
    re.IGNORECASE,
)
# テスト / 検証 / 動作確認 等のページ。
TEST_PATTERN = re.compile(
    r'[Tt]est_|テスト|検証|動作確認|rust_invitational|^\s*Fasutofaito|^\./|^\s*スマッシュ\s*$',
)


def is_cancelled(name: str) -> bool:
    return bool(CANCELLED_PATTERN.search(name or ''))


def is_test_page(name: str) -> bool:
    return bool(TEST_PATTERN.search(name or ''))


def naming_labels(tname: str, ename: str) -> dict:
    """大会名から決まるラベル一式。derived.json の "naming" に入る。"""
    return {
        "series": tournament_series(tname),
        "series_number": tournament_series_number(tname),
        "award_label": tournament_award_label(tname),
        "individual_label": tournament_individual_label(tname),
        "cancelled": is_cancelled(tname) or is_cancelled(ename),
        "test_page": is_test_page(tname) or is_test_page(ename),
    }


# ── Community-level シリーズマージ ──
# tournament_series() は実績用 (= サブタイトル/Anniversary 等を区別) なので strict.
# ローカルランキングは「同じコミュニティ = 同じ参加者層」基準でより緩く統合する.
# (純粋な表記揺れマージは tournament_series() 側で既に処理済み)
_COMMUNITY_DIRECT_MERGES: dict[str, str] = {
    # スマパ 拡大版 は実績では別シリーズ (= 別の実績バッジ) だが、community 上は同じ参加者層なので統合.
    'スマパ 拡大版': 'スマパ',
    # プレ大会 → 本大会 (コラボシリーズではないもののみ). コラボ pre (Pre-DELTA = デルブゲキ系合同
    # クルーバトル併設) は別シリーズのまま.
    'Pre-篝火': '篝火',
    'プレ大会マエスマTOP': 'マエスマTOP',
    'プレローカルマエスマTOP': 'マエスマTOP',
    # サブタイトル・派生回をメインに統合 (community のみ, 実績は別).
    '#EVISUMA_duo': '#EVISUMA',
    '#EVISUMA_night': '#EVISUMA',
    'おばすまOFT': 'おばすま',
    'おばすまOST': 'おばすま',
    'さしすまQ.E.D.': 'さしすま',
    'まめブラSP': 'まめブラ',
    'growup': 'grow',
    'ミニ神威': 'Kamui',
    '神威Turning Point': 'Kamui',
    'ベルスマin四日市': 'ベルスマ',
    'ベルスマトナメ': 'ベルスマ',
    '兵庫大対戦会': '兵庫対戦会',
    '兵庫県スマブラ大対戦会': '兵庫対戦会',
    '兵庫県スマブラ対戦会': '兵庫対戦会',
    '兵庫県対戦会': '兵庫対戦会',
    '唐津スマブラ対戦会': '唐スマ',
    'スマコミ': '上野スマコミ',
    'Ueno Smash Ultimate Weekly-上野スマブラＳＰ平日大会': '上野スマコミ',
    'Lowkey TENSUMA': 'TENSUMA',
    # るゆぶらっ！: 其の壱 / 其の弐 を統合.
    'るゆぶらっ！其の壱': 'るゆぶらっ！',
    'るゆぶらっ！其の弐': 'るゆぶらっ！',
    # 九龍 LIMIT BREAK は制限大会のため、元シリーズ (九龍) とは community でも別維持.
    # スマバト/篝火 との合同は他ブランドとのコラボなので別維持.
    # 駒battle + 駒battle mini → 駒battle (駒スマは別).
    '駒battle mini': '駒battle',
    # 修羅ブラSP → 修羅ブラ (通常版の表記揺れ統合). 新風 は制限大会なので別維持.
    '修羅ブラSP': '修羅ブラ',
    # 新Impact(仮) → IMPACT ARENA (IMPACT 系列).
    '新Impact(仮)': 'IMPACT ARENA',
    # イツクシマ外伝 → イツクシマ (実績は別維持、community のみ統合).
    'イツクシマ外伝': 'イツクシマ',
    # ウメブラJapanMajor → ウメブラ (実績は別の特別大会、community は同一コミュニティ).
    'ウメブラJapanMajor': 'ウメブラ',
    # ウメブラがんばれ大会も同じ.
    'ウメブラがんばれ大会': 'ウメブラ',
}

# パターンマージ: 正規表現で strict series 名にヒットすれば community 名へ.
# 並び順優先 (= 最初にマッチしたものが勝つ). チーム除外などの先行ルールを上に置く.
_COMMUNITY_PATTERN_MERGES = [
    # チーム/Team 系列はメインと分離 (= クロブラ流ポリシー).
    (re.compile(r'^カリスマ\s*(?:チーム|Team)', re.I), 'カリスマチーム'),
    # メイン系列 (サブタイトル統合)
    (re.compile(r'^INNOSUMA', re.I), 'INNOSUMA'),
    (re.compile(r'^Double\s*G\b', re.I), 'Double G'),
    # WAVE Champions シリーズは Champions FINAL 含めて統合. WAVE 本体 (single) は別に維持.
    (re.compile(r'^WAVE\s*Champions', re.I), 'WAVE Champions'),
    # 彩 系列: 彩 / 彩Trial / 彩trial / 彩in池袋 全部統合.
    (re.compile(r'^彩(?:Trial|trial|in池袋|$)', re.I), '彩'),
    # ── 以下、サブタイトル/Anniversary/季節バージョン等の統合 (実績は別、community のみ) ──
    (re.compile(r'^A-Leg', re.I), 'A-Leg'),
    (re.compile(r'^After Eight Smash', re.I), 'After Eight Smash'),
    (re.compile(r'^Cafeteria Cup', re.I), 'Cafeteria Cup'),
    (re.compile(r'^Comic Con Okinawa', re.I), 'Comic Con Okinawa'),
    # DIVE UnderGround は制限大会のため別維持 (= negative lookahead で除外).
    (re.compile(r'^(?:Re:)?DIVE(?!\s*Under\s*Ground)', re.I), 'DIVE'),
    (re.compile(r'^ELEVATE', re.I), 'ELEVATE'),
    (re.compile(r'^GAME COMクウガ', re.I), 'GAME COMクウガ'),
    (re.compile(r'^Generations of Tournament Mode', re.I), 'Generations of Tournament Mode'),
    (re.compile(r'^Greca League', re.I), 'Greca League'),
    (re.compile(r'^HSTSP', re.I), 'HSTSP'),
    (re.compile(r'^Kadena', re.I), 'Kadena'),
    (re.compile(r'^MaKoTnLeague', re.I), 'MaKoTnLeague'),
    (re.compile(r'^Mind[Ss]et', re.I), 'Mindset'),
    (re.compile(r'^OEBスマブラSP', re.I), 'OEBスマブラSP'),
    (re.compile(r'^(?:SMP|Single Marine Program)', re.I), 'SMP'),
    (re.compile(r'SmashCruise', re.I), 'SmashCruiseTournament'),
    (re.compile(r'^Tokyo\s+Nights', re.I), 'Tokyo Nights'),
    (re.compile(r'^TOKYO SMASH', re.I), 'TOKYO SMASH'),
    (re.compile(r'^TRY DASH', re.I), 'TRY DASH'),
    # 日本語サブタイトル統合
    # くすブラ若葉 は制限大会のため別維持 (= negative lookahead で除外).
    (re.compile(r'くすブラ(?!\s*若葉)', re.I), 'くすブラ'),
    (re.compile(r'こしスマ', re.I), 'こしスマ'),
    (re.compile(r'^スマえもん', re.I), 'スマえもん'),
    (re.compile(r'^スマわんこ', re.I), 'スマわんこ'),
    (re.compile(r'^スマキャン', re.I), 'スマキャン'),
    (re.compile(r'^スマサー(?:連合)?合宿', re.I), 'スマサー合宿'),
    (re.compile(r'^バケスマSP', re.I), 'バケスマSP'),
    (re.compile(r'^マエスマHIT', re.I), 'マエスマHIT'),
    (re.compile(r'マエスマTOP$', re.I), 'マエスマTOP'),
    (re.compile(r'^ユニブラ', re.I), 'ユニブラ'),
    (re.compile(r'^ロンスマWKD', re.I), 'ロンスマWKD'),
    (re.compile(r'^兵庫(?:県?スマブラ)?(?:大)?対戦会', re.I), '兵庫対戦会'),
    (re.compile(r'^函武激', re.I), '函武激'),
    (re.compile(r'^夢スマ', re.I), '夢スマ'),
    (re.compile(r'^(?:平日)?極強', re.I), '極強'),
    (re.compile(r'^真・スマ', re.I), '真・スマのみや'),
    (re.compile(r'^知恵捨', re.I), '知恵捨'),
    (re.compile(r'^西条スマッシュ', re.I), '西条スマッシュ'),
    (re.compile(r'^船スマ', re.I), '船スマ'),
    (re.compile(r'^なにわSMASH', re.I), 'なにわSMASH'),
    (re.compile(r'^ももブラ', re.I), 'ももブラ'),
    (re.compile(r'^鹿児スマ', re.I), '鹿児スマ'),
    (re.compile(r'^ポチスマ', re.I), 'ポチスマ'),
    (re.compile(r'^Re:?肥後ブラ|^肥後ブラ', re.I), '肥後ブラSP'),
    (re.compile(r'^やさしいスマッシュ', re.I), 'やさしいスマッシュ'),
    (re.compile(r'^(?:Re:)?\s*IMPACT\s*(?:ARENA|MAJOR)?', re.I), 'IMPACT ARENA'),
    (re.compile(r'^カリスマ(?:SP|ぷち)?', re.I), 'カリスマ'),
    (re.compile(r'^スマ王', re.I), 'スマ王'),
    (re.compile(r'^Mjolner', re.I), 'Mjolner'),
    (re.compile(r'^BIG\s*LAGOON', re.I), 'BIG LAGOON'),
    (re.compile(r'^YASUブラ', re.I), 'YASUブラSP'),
    (re.compile(r'^Smash\s*Open', re.I), 'Smash Open'),
    (re.compile(r'^しのスマ|^平日しのスマ', re.I), 'しのスマ'),
    (re.compile(r'^LOVEスマ', re.I), 'LOVEスマ'),
    # 美らブラ ビギナーズ杯 は制限大会のため community でも別維持 (= negative lookahead).
    (re.compile(r'美らブラ(?!\s*ビギナー)', re.I), '美らブラ'),
    (re.compile(r'^カミスマ|^平日カミスマ|Kamisuma', re.I), 'カミスマ'),
    (re.compile(r'^いろスマ|^平日いろスマ', re.I), 'いろスマ'),
    (re.compile(r'こくブラ', re.I), 'こくブラ'),
]


def community_series(strict: str) -> str:
    """tournament_series() の結果からコミュニティレベルの集約名を返す."""
    if not strict:
        return strict
    if strict in _COMMUNITY_DIRECT_MERGES:
        return _COMMUNITY_DIRECT_MERGES[strict]
    for pat, target in _COMMUNITY_PATTERN_MERGES:
        if pat.search(strict):
            return target
    return strict


