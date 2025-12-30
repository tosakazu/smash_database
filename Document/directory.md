# Directory

## 全体構成（概観）
```
.
├── .github
│   └── workflows
│       ├── data_backfill.yml
│       ├── data_monthly_check.yml
│       └── data_update.yml
├── Document
│   ├── data_model.md
│   ├── directory.md
│   ├── fix.md
│   ├── flow.md
│   ├── githubAction.md
│   └── startgg_design.md
├── README.md
├── data
│   └── startgg
│       ├── cache
│       ├── check
│       ├── done.csv
│       ├── done_events.csv
│       ├── events
│       │   └── {Region}/{YYYY}/{MM}/{DD}/{Tournament}/{Event}
│       │       ├── attr.json
│       │       ├── matches.json
│       │       ├── seeds.json
│       │       └── standings.json
│       ├── tournaments.jsonl
│       └── users.jsonl
└── scripts
    ├── __init__.py
    ├── fetch
    │   ├── __init__.py
    │   ├── download.py
    │   ├── download_specific_event.py
    │   └── refresh_users.py
    ├── fix
    │   ├── __init__.py
    │   ├── check_events_in_tournaments.py
    │   ├── backfill_events.py
    │   ├── fix_missing_tournaments.py
    │   └── validate_data.py
    ├── test
    │   ├── __init__.py
    │   └── test_validate_data.py
    ├── event_analysis_prompt.txt
    ├── queries.py
    ├── storeJson.py
    └── utils.py
```

## 説明
- `Document/`: 仕様・運用・設計資料。
- `data/`: 取得済みデータや検証用データ。
- `scripts/`: 取得・検証・補完のスクリプト群。
