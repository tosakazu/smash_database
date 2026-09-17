# -*- coding: utf-8 -*-
"""居住地の自由記述テキスト → 都道府県(漢字フル表記) リゾルバ.

start.gg の user.location.city は日本では自由記述で、都道府県・市区町村が
漢字 / ローマ字 / かな で混在する (state 欄は日本だと常に空)。これを正規化して
都道府県を 1 つに特定する。特定できない / 単一県に絞れない場合は None を返す。

データ: scripts/Japan/resources/municipal_master_with_wards.csv (2026-09-08 に spsp/prefecture_resolver.py から移動)
  (総務省 全国地方公共団体コード由来, 市区町村の漢字/かな/ローマ字 + 都道府県)
  出典: github.com/rooter-inc/governmental_statistics

resolve(text) -> (都道府県漢字 or None, 理由タグ)
  理由タグ: pref_kanji / muni_kanji / pref_kana / pref_romaji / muni_romaji /
            muni_kana / muni_kanji_core  (特定成功)
            ambig_jp(「日本」のみ) / ambig_region(関東等) / ambig_muni(重複地名) /
            unmatched / empty  (特定不可)
"""
import csv
import os
import re
import unicodedata
from collections import defaultdict

_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'municipal_master_with_wards.csv')

# 政令市/特別区の親市はローマ字を機械分割できないので明示 (例: 札幌市中央区=sapporoshichuuouku)
_POLITAN = {
    'sapporo': '北海道', 'sendai': '宮城県', 'saitama': '埼玉県', 'chiba': '千葉県',
    'yokohama': '神奈川県', 'kawasaki': '神奈川県', 'sagamihara': '神奈川県', 'niigata': '新潟県',
    'shizuoka': '静岡県', 'hamamatsu': '静岡県', 'nagoya': '愛知県', 'kyoto': '京都府',
    'osaka': '大阪府', 'sakai': '大阪府', 'kobe': '兵庫県', 'okayama': '岡山県',
    'hiroshima': '広島県', 'kitakyushu': '福岡県', 'fukuoka': '福岡県', 'kumamoto': '熊本県',
}
# 政令市のかな (CSV には行政区しか無いので親市のかなを補う。さっぽろ/はままつ 等)
_POLITAN_KANA = {
    'サッポロ': '北海道', 'センダイ': '宮城県', 'サイタマ': '埼玉県', 'チバ': '千葉県',
    'ヨコハマ': '神奈川県', 'カワサキ': '神奈川県', 'サガミハラ': '神奈川県', 'ニイガタ': '新潟県',
    'シズオカ': '静岡県', 'ハママツ': '静岡県', 'ナゴヤ': '愛知県', 'キョウト': '京都府',
    'オオサカ': '大阪府', 'サカイ': '大阪府', 'コウベ': '兵庫県', 'オカヤマ': '岡山県',
    'ヒロシマ': '広島県', 'キタキュウシュウ': '福岡県', 'フクオカ': '福岡県', 'クマモト': '熊本県',
}
# 都道府県のローマ字 (人が実際に書くヘボン式・長音省略形)
_PREF_ROM = {
    'hokkaido': '北海道', 'aomori': '青森県', 'iwate': '岩手県', 'miyagi': '宮城県', 'akita': '秋田県',
    'yamagata': '山形県', 'fukushima': '福島県', 'ibaraki': '茨城県', 'tochigi': '栃木県', 'gunma': '群馬県',
    'saitama': '埼玉県', 'chiba': '千葉県', 'tokyo': '東京都', 'kanagawa': '神奈川県', 'niigata': '新潟県',
    'toyama': '富山県', 'ishikawa': '石川県', 'fukui': '福井県', 'yamanashi': '山梨県', 'nagano': '長野県',
    'gifu': '岐阜県', 'shizuoka': '静岡県', 'aichi': '愛知県', 'mie': '三重県', 'shiga': '滋賀県',
    'kyoto': '京都府', 'osaka': '大阪府', 'hyogo': '兵庫県', 'nara': '奈良県', 'wakayama': '和歌山県',
    'tottori': '鳥取県', 'shimane': '島根県', 'okayama': '岡山県', 'hiroshima': '広島県', 'yamaguchi': '山口県',
    'tokushima': '徳島県', 'kagawa': '香川県', 'ehime': '愛媛県', 'kochi': '高知県', 'fukuoka': '福岡県',
    'saga': '佐賀県', 'nagasaki': '長崎県', 'kumamoto': '熊本県', 'oita': '大分県', 'miyazaki': '宮崎県',
    'kagoshima': '鹿児島県', 'okinawa': '沖縄県',
}
# 単一県に絞れない広域呼称 (訓令式/タイポ変種も含む)
_REGIONS = {
    '関東', '関西', '東海', '九州', '東北', '中部', '中国', '四国', '北陸', '近畿', '北関東',
    '南関東', '首都圏', '中京',
    'kanto', 'kanto', 'kansai', 'kannsai', 'tohoku', 'kyushu', 'kyusyu', 'chubu', 'chugoku',
    'tyugoku', 'shikoku', 'sikoku', 'tokai', 'kinki', 'hokuriku', 'kannto',
}
# 単一県に内包される地域呼称/湖など (substring 一致, 他に同名が無く曖昧でないもの)
_SUBREGION = {'湘南': '神奈川県', '琵琶湖': '滋賀県'}
_SUBREGION_ROM = {'shonan': '神奈川県', 'biwako': '滋賀県'}
# 地名の通称・歴史名・繁華街など (完全一致, 単一県に確定できるもの) + 明らかな誤字
_ALIAS = {
    '半蔵門': '東京都', '高円寺': '東京都', '成増': '東京都', '人形町': '東京都', '国領': '東京都',
    '江戸': '東京都', '溝ノ口': '神奈川県', '溝の口': '神奈川県', '幕張': '千葉県',
    '博多': '福岡県', '小倉': '福岡県', 'ナニワ': '大阪府', '浪速': '大阪府', '梅田': '大阪府',
    '大坂': '大阪府',
}
_ALIAS_ROM = {
    'edo': '東京都', 'makuhari': '千葉県', 'hakata': '福岡県', 'naniwa': '大阪府', 'umeda': '大阪府',
    # 明らかな誤字 (collapse 後の探索キーで完全一致, 単一県に確定できるもののみ)
    'koyto': '京都府', 'tokoy': '東京都', 'tyokyo': '東京都', 'tokio': '東京都',
    'sizioka': '静岡県', 'sizuok': '静岡県', 'ymanasi': '山梨県', 'gunmma': '群馬県', 'gumma': '群馬県',
    'yokohana': '神奈川県', 'kanaga': '神奈川県',
}
_ROMSUF = ('chou', 'machi', 'mura', 'son', 'gun', 'shi', 'ku')


def _kata2hira(s):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s)


def _clean(s):
    """小文字化・非英字除去のみ (長音/訓令式 collapse はしない)."""
    s = unicodedata.normalize('NFKC', s).lower().replace('ō', 'o').replace('ū', 'u')
    return re.sub(r'[^a-z]', '', s)


# 訓令式↔ヘボン式・長音の表記ゆれを 1 つの探索形に畳む (両辺に適用して一致させる)。
# 例: chiba/tiba -> tiba, shizuoka/sizuoka -> sizuoka, fukuoka/hukuoka -> hukuoka,
#     toukyou -> tokyo, oosaka -> osaka。
_COLLAPSE = [('oo', 'o'), ('ou', 'o'), ('uu', 'u'),
             ('shi', 'si'), ('chi', 'ti'), ('tsu', 'tu'),
             ('sha', 'sya'), ('shu', 'syu'), ('sho', 'syo'),
             ('cha', 'tya'), ('chu', 'tyu'), ('cho', 'tyo'),
             ('ji', 'zi'), ('ja', 'zya'), ('ju', 'zyu'), ('jo', 'zyo'),
             ('fu', 'hu')]


def _collapse(s):
    for a, b in _COLLAPSE:
        s = s.replace(a, b)
    return s


def _nrom(s):
    """ローマ字探索キー: clean + 長音/訓令式 collapse."""
    return _collapse(_clean(s))


_KANJI_OLD = {'條': '条', '﨑': '崎', '邊': '辺', '邉': '辺', '澤': '沢', '齋': '斎', '驒': '騨'}


def _nkan(s):
    # ヶ(U+30F6)/ヵ は地名で ケ/カ と揺れる (袖ヶ浦市=袖ケ浦市)。旧字も新字へ寄せる (四條畷=四条畷)。
    s = unicodedata.normalize('NFKC', s).strip().replace('ヶ', 'ケ').replace('ヵ', 'カ')
    for a, b in _KANJI_OLD.items():
        s = s.replace(a, b)
    return s


def _pick(slot):
    """[市区由来, 町村由来] の優先解決. 市区を優先, 同優先で衝突なら 'AMBIG'."""
    for v in slot:
        if v not in (None, 'AMBIG'):
            return v
    return 'AMBIG' if 'AMBIG' in slot else None


def _build():
    pref_core = {}       # 漢字コア(東京) -> 東京都
    pref_kana = {}       # かな(とうきょう) -> 東京都
    pref_rom = {_nrom(k): v for k, v in _PREF_ROM.items()}
    mk_full = {}         # 市区町村漢字フル -> 県
    mk_core = defaultdict(lambda: [None, None])    # 漢字コア -> [市区, 町村]
    mr_core = defaultdict(lambda: [None, None])    # ローマ字コア -> [市区, 町村]
    mkana_core = {}      # かなコア -> 県
    ward_prefs = defaultdict(set)   # 政令市の行政区名 -> {県}  (一意のものだけ後で mk_full へ)

    def addcore(d, key, pref, prio):
        if not key:
            return
        cur = d[key]
        if cur[prio] is None:
            cur[prio] = pref
        elif cur[prio] != pref:
            cur[prio] = 'AMBIG'

    with open(_CSV, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            pk = r['prefecture_kanji']
            ck = _nkan(r['city_kanji'])     # CSV も ヶ/ケ 揺れがある (茅ヶ崎市 vs 袖ケ浦市) ので正規化
            ckana = r['city_kana']
            crom = r['city_roman']
            core = pk[:-1] if (pk != '北海道' and pk[-1] in '都道府県') else pk
            pref_core[core] = pk
            for pkana in (r['prefecture_kana'], re.sub(r'(ケン|フ|ト|ドウ)$', '', r['prefecture_kana'])):
                pref_kana[pkana] = pk
                pref_kana[_kata2hira(pkana)] = pk
            mk_full[ck] = mk_full.get(ck) or pk
            mm = re.match(r'^(.+?市)(.+?区)$', ck)   # 政令市の行政区
            if mm:
                addcore(mk_core, mm.group(1), pk, 0)         # 札幌市
                addcore(mk_core, mm.group(1)[:-1], pk, 0)    # 札幌
                # 行政区名 (右京区/灘区 等) も低優先で登録。複数都市にある区(中区/北区/泉区
                # /青葉区)は dedup で AMBIG になり曖昧扱い、一意の区だけ特定される。
                addcore(mk_core, mm.group(2), pk, 1)
                ward_prefs[mm.group(2)].add(pk)
            else:
                prio = 0 if ck[-1] in '市区' else 1
                addcore(mk_core, re.sub(r'[市区町村]$', '', ck), pk, prio)
                rc = _clean(crom)          # 接尾辞除去は collapse 前の生ローマ字で行う
                for suf in _ROMSUF:
                    if rc.endswith(suf):
                        rc = rc[:-len(suf)]
                        break
                addcore(mr_core, _collapse(rc), pk, prio)
                kc = re.sub(r'(シ|ク|チョウ|マチ|ムラ|ソン|グン)$', '', ckana)
                if kc:
                    mkana_core[_kata2hira(kc)] = mkana_core.get(_kata2hira(kc)) or pk
    # 一意な政令市区名を mk_full に追加 (長い '港北区' が短い特別区 '北区' の部分一致に勝つ)。
    for w, ps in ward_prefs.items():
        if len(ps) == 1 and w not in mk_full:
            mk_full[w] = next(iter(ps))
    for k, v in _POLITAN.items():
        mr_core[_nrom(k)] = [v, None]
    for k, v in _POLITAN_KANA.items():                  # 政令市のかな (同名の町より政令市を優先=上書き)
        mkana_core[k] = v
        mkana_core[_kata2hira(k)] = v
    return {
        'pref_core': pref_core, 'pref_kana': pref_kana, 'pref_rom': pref_rom,
        'mk_full': mk_full, 'mk_core': dict(mk_core), 'mr_core': dict(mr_core),
        'mkana_core': mkana_core,
        'pk_sorted': sorted(pref_core, key=len, reverse=True),
        'mkf_sorted': sorted(mk_full, key=len, reverse=True),
        'mr_keys': sorted([k for k in mr_core if len(k) >= 4], key=len, reverse=True),
        'mkana_keys': sorted([k for k in mkana_core if k and len(k) >= 2], key=len, reverse=True),
        'mk_keys': sorted([k for k in mk_core if len(k) >= 1], key=len, reverse=True),
    }


_T = None


def resolve(text):
    """居住地テキスト -> (都道府県漢字 or None, 理由タグ)."""
    global _T
    if _T is None:
        _T = _build()
    if not text:
        return (None, 'empty')
    s = _nkan(text)
    sl = _nrom(text)
    sl_raw = _clean(text)        # JP/広域判定は collapse 前の生ローマ字で行う
    sh = _kata2hira(s)
    if not s:
        return (None, 'empty')
    if s in ('日本', 'にほん', 'ニホン') or sl_raw in ('japan', 'jp', 'nihon', 'nippon'):
        return (None, 'ambig_jp')
    if s in _REGIONS or sl_raw in _REGIONS:
        return (None, 'ambig_region')
    for sub, pref in _SUBREGION.items():               # 単一県内包の地域/湖 (湘南->神奈川, 琵琶湖->滋賀)
        if sub in s:
            return (pref, 'subregion')
    if sl in _SUBREGION_ROM or sl_raw in _SUBREGION_ROM:
        return (_SUBREGION_ROM.get(sl) or _SUBREGION_ROM[sl_raw], 'subregion')
    if s in _ALIAS:                                     # 通称/歴史名/繁華街 完全一致 (梅田->大阪, 博多->福岡)
        return (_ALIAS[s], 'alias')
    if sl in _ALIAS_ROM:                               # ローマ字通称 + 明らかな誤字 完全一致
        return (_ALIAS_ROM[sl], 'alias')
    for c in _T['pk_sorted']:                          # 都道府県 漢字 (神奈川藤沢市->神奈川)
        if c in s:
            return (_T['pref_core'][c], 'pref_kanji')
    for m in _T['mkf_sorted']:                         # 市区町村 漢字フル
        if m in s:
            return (_T['mk_full'][m], 'muni_kanji')
    for k in _T['pref_kana']:                           # 都道府県 かな
        if k and k in sh:
            return (_T['pref_kana'][k], 'pref_kana')
    for r in sorted(_T['pref_rom'], key=len, reverse=True):   # 都道府県 ローマ字
        if r in sl:
            return (_T['pref_rom'][r], 'pref_romaji')
    if sl in _T['mr_core']:                            # 市区町村 ローマ字 完全一致 (Ome->青梅, Tsu->津)
        v = _pick(_T['mr_core'][sl])
        if v == 'AMBIG':
            return (None, 'ambig_muni')
        if v:
            return (v, 'muni_romaji')
    for rc in _T['mr_keys']:                            # 市区町村 ローマ字コア (4字以上 部分一致)
        if rc in sl:
            v = _pick(_T['mr_core'][rc])
            if v == 'AMBIG':
                return (None, 'ambig_muni')
            if v:
                return (v, 'muni_romaji')
    for kc in _T['mkana_keys']:                         # 市区町村 かなコア
        if kc in sh:
            return (_T['mkana_core'][kc], 'muni_kana')
    for c in _T['mk_keys']:                             # 市区町村 漢字コア (1字は完全一致のみ)
        if (c in s) if len(c) >= 2 else (s == c):
            v = _pick(_T['mk_core'][c])
            if v == 'AMBIG':
                return (None, 'ambig_muni')
            if v:
                return (v, 'muni_kanji_core')
    return (None, 'unmatched')
