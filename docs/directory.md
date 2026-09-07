# Directory

2026-09-07 からの構成。ブランチの分け方と運用は英語の `README.md` が正。

```
smash_db_tournament/            main の worktree (scripts と文書だけ。data/ は gitignore)
├── README.md
├── docs/
├── scripts/
│   ├── common/                 全地域共通: utils (API 層) / queries / clock / _cli / download / download_policy /
│   │   │                        fetch_upcoming / redownload_matches_v2 / storeJson
│   │   ├── manual/             手動ツール (パイプラインは使わない)
│   │   └── fix/                検証・補完ツール
│   ├── Japan/                  日本固有: 下位クラス (B/C クラス) の分離 (update_class_data と子 4 本、manual/)
│   └── test/                   単体テスト
└── data/                       data-Japan ブランチの worktree
    └── startgg/
        ├── Japan/              done.csv, done_events.csv, tournaments.jsonl, users.jsonl (索引)
        └── events/Japan/{YYYY}/{MM}/{DD}/{Tournament}/{Event}/
            ├── attr.json  standings.json  seeds.json  matches.json
            ├── phases.json  class_phases/  <X>_virtual/     (日本のクラス bracket 分離の出力)
```

- 他地域は `data-<地域>` ブランチに `startgg/<地域>/` と `startgg/events/<地域>/` を同じ形で持つ。
- 取得工程の回帰テスト (記録・再生) は spsp リポジトリの `tests/fetch/`。
