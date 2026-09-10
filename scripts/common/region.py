# -*- coding: utf-8 -*-
"""地域モジュール (scripts/<地域>/classify.py) の読み込みと、地域が宣言する機能の確認。

判定そのものは地域が持ち、共通スクリプトはここ経由でそれを借りる。無いものは推測しない
(既定値で代用しない) — 地域を足したときに黙って日本のルールが適用されるのを避けるため。
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace


def load_region_classifier(region: str):
    """scripts/<地域>/classify.py (CLASSIFIER_VERSION と classify_event を持つ)。無ければ止める。"""
    modname = f"scripts.{region.replace(' ', '_')}.classify"
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        raise SystemExit(f"地域 {region!r} の判定モジュール {modname} が無い: {e}")


# ── クラス bracket (大会の中に置かれる下位クラスの別ブラケット) ──
# 日本の B/C/D/E クラスが典型だが、名前も有無も地域による (北米の Amateur など)。
# 地域モジュールが次を宣言していればクラス bracket 対応とみなす:
#   CLASS_LETTERS                     クラスの識別子 (順序が仮想イベント ID の採番に使われる)
#   is_class_phase(name)              phase 名がクラス bracket か (phases.json の is_class)
#   is_unseparated_class_phase(name)  matches.json の phase 名から未分離のクラス戦を見つける
#   class_letter(name)                phase 名 → クラスの識別子 (無ければ None)
#   class_virtual_event_name(ev, l)   クラスを 1 大会として切り出すときのイベント名
CLASS_HOOKS = ("CLASS_LETTERS", "is_class_phase", "is_unseparated_class_phase",
               "class_letter", "class_virtual_event_name")


def class_support(clf) -> SimpleNamespace | None:
    """地域がクラス bracket を扱うなら各フックを、扱わないなら None。中途半端な宣言は止める。"""
    present = [name for name in CLASS_HOOKS if getattr(clf, name, None) is not None]
    if not present:
        return None
    missing = [name for name in CLASS_HOOKS if name not in present]
    if missing:
        raise SystemExit(f"{clf.__name__}: クラス bracket のフックが足りない ({', '.join(missing)})")
    return SimpleNamespace(**{name: getattr(clf, name) for name in CLASS_HOOKS})


def class_phase_group_ids(phases_data: dict | None, class_phase_files: list[dict]) -> tuple[bool, list[int]]:
    """(全 phase がクラスか, クラス bracket の phase_group_id 一覧)。地域に依らない (is_class は地域が付ける)。
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
