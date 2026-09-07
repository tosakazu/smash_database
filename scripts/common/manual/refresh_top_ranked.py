"""順位(ensemble)上位 N 人のユーザー情報(市区町村等)だけを start.gg から更新する。

refresh_users.py の refresh_user_record を再利用し、対象を「ランキング上位 N 人」に限定する。

特徴:
  - 対象: --rank_source (= latest_tjpr_full.jsonl) を ranks.ensemble 昇順でソートした上位 N の user_id。
  - checkpoint (処理済 user_id を1行ずつ追記) により再開可能。
  - CPU 予算 (--cpu_budget 秒) を超えたら users.jsonl を書き戻して exit code 75 で終了する。
    ConoHa の CPU 制限(~300s/プロセス)対策。呼び出し側 shell ループが code 75 を見て再起動すれば続きから処理できる。
  - --flush_every 件ごとに users.jsonl を書き戻すので、SIGKILL されても進捗は概ね保存される。
  - 全対象が処理済になったら exit code 0。

使い方 (shell ループ例):
  while :; do
    python scripts/fetch/refresh_top_ranked.py --token "$TOK" --top 10000 ... ; rc=$?
    [ "$rc" = 0 ] && break        # 完了
    [ "$rc" = 75 ] || break       # 75 以外の異常は中断
  done
"""
import argparse
import json
import os
import resource
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.common.manual.refresh_users import refresh_user_record, UserNotFoundError
from scripts.common.utils import (
    read_users_jsonl,
    write_jsonl,
    set_indent_num,
    set_retry_parameters,
    set_api_parameters,
    FetchError,
)


def _cpu_seconds():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def _load_top_ids(rank_source, top_n):
    """rank_source を ensemble 昇順でソートし、上位 top_n の user_id(int) を返す。"""
    recs = []
    with open(rank_source, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            uid = d.get("user_id")
            rank = (d.get("ranks") or {}).get("ensemble")
            if uid is not None and rank:
                recs.append((rank, int(uid)))
    recs.sort(key=lambda x: x[0])
    return [uid for _, uid in recs[:top_n]]


def _load_done(checkpoint_path):
    done = set()
    if checkpoint_path and os.path.exists(checkpoint_path):
        with open(checkpoint_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        done.add(int(line))
                    except ValueError:
                        pass
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--token", required=True)
    ap.add_argument("--url", default="https://api.start.gg/gql/alpha")
    ap.add_argument("--users_file_path", default="data/startgg/users.jsonl")
    ap.add_argument("--rank_source", required=True,
                    help="latest_tjpr_full.jsonl (user_id + ranks.ensemble)")
    ap.add_argument("--top", type=int, default=10000)
    ap.add_argument("--checkpoint_path", required=True,
                    help="処理済 user_id を追記して再開に使う")
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--user_retries", type=int, default=5)
    ap.add_argument("--max_retries", type=int, default=10)
    ap.add_argument("--retry_delay", type=int, default=5)
    ap.add_argument("--indent_num", type=int, default=2)
    ap.add_argument("--flush_every", type=int, default=200,
                    help="この件数ごとに users.jsonl を書き戻す")
    ap.add_argument("--cpu_budget", type=float, default=200.0,
                    help="この CPU 秒を超えたら flush して exit 75 (再起動用)")
    ap.add_argument("--pause_every", type=int, default=200)
    ap.add_argument("--pause_seconds", type=float, default=20.0)
    ap.add_argument("--progress_interval", type=int, default=25)
    args = ap.parse_args()

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    users = read_users_jsonl(args.users_file_path)          # {int user_id: record}
    user_order = list(users.keys())
    top_ids = _load_top_ids(args.rank_source, args.top)
    done = _load_done(args.checkpoint_path)

    targets = [uid for uid in top_ids if uid in users and uid not in done]
    total_targets = len([uid for uid in top_ids if uid in users])
    print(f"[top_ranked] 上位{args.top} のうち users.jsonl 存在={total_targets}, "
          f"処理済={len(done)}, 今回対象={len(targets)}")
    if not targets:
        print("[top_ranked] 残りなし。完了。")
        return 0

    ck = open(args.checkpoint_path, "a", encoding="utf-8")
    processed = 0
    failures = 0
    consecutive_rate_limits = 0
    pending = []  # users.jsonl にまだ flush していない更新済 uid

    def flush_users():
        # 必ず「users.jsonl を書く → checkpoint に uid を記録」の順。
        # こうすれば SIGKILL されても checkpoint は永続済みの uid しか含まない。
        if not pending:
            return
        write_jsonl([users[uid] for uid in user_order], args.users_file_path,
                    with_version=True)
        for u in pending:
            ck.write(f"{u}\n")
        ck.flush()
        pending.clear()

    try:
        for i, uid in enumerate(targets, start=1):
            record = users[uid]
            attempt = 0
            ok = False
            refreshed = record
            while attempt < args.user_retries:
                attempt += 1
                try:
                    refreshed = refresh_user_record(record, args.sleep)
                    consecutive_rate_limits = 0
                    ok = True
                    break
                except UserNotFoundError as e:
                    print(f"  info: {e} 既存データ維持", file=sys.stderr)
                    ok = True
                    refreshed = record
                    break
                except FetchError as e:
                    if "Too Many Requests" in str(e):
                        consecutive_rate_limits += 1
                        backoff = max(args.retry_delay * consecutive_rate_limits,
                                      args.sleep * 5, 10)
                        print(f"  rate limit uid={uid} ({attempt}/{args.user_retries}) "
                              f"sleep {backoff:.1f}s", file=sys.stderr)
                        time.sleep(backoff)
                        continue
                    print(f"  warn uid={uid}: {e}", file=sys.stderr)
                    break
                except Exception as e:
                    print(f"  fail uid={uid}: {e}", file=sys.stderr)
                    break

            if not ok:
                failures += 1
                # 失敗は checkpoint に書かない (次回再試行)
                continue

            users[uid] = refreshed
            pending.append(uid)
            processed += 1

            if args.progress_interval and processed % args.progress_interval == 0:
                print(f"[top_ranked] {processed}/{len(targets)} 件 "
                      f"(CPU {_cpu_seconds():.0f}s)")

            if args.flush_every and len(pending) >= args.flush_every:
                flush_users()

            if args.pause_every and processed % args.pause_every == 0:
                time.sleep(args.pause_seconds)

            if _cpu_seconds() >= args.cpu_budget:
                flush_users()
                remaining = len(targets) - i
                print(f"[top_ranked] CPU 予算({args.cpu_budget}s)到達。flush して中断。"
                      f"今回処理={processed}, 残り≈{remaining}。再起動で続行可。")
                return 75

        flush_users()
        print(f"[top_ranked] 完了。処理={processed}, 失敗={failures}。")
        return 0
    finally:
        ck.close()


if __name__ == "__main__":
    sys.exit(main())
