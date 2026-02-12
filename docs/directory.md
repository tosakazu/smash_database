# Directory

## 全体構成（概観）
```
.
├── Document
│   ├── data_model.md
│   ├── directory.md
│   ├── fix.md
│   ├── flow.md
│   ├── githubAction.md
│   └── startgg_design.md
├── README.md
├── data
│   ├── JJPR
│   │   └── check
│   │       └── JJPREvents.json
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
    ├── check_and_fill_missing.py
    ├── check_events_in_tournaments.py
    ├── check_jjpr_events.py
    ├── download.py
    ├── download_specific_event.py
    ├── event_analysis_prompt.txt
    ├── fill_missing_events.py
    ├── fix_missing_tournaments.py
    ├── queries.py
    ├── refresh_users.py
    ├── storeJson.py
    └── utils.py
```

## 説明
- `Document/`: 仕様・運用・設計資料。
- `data/`: 取得済みデータや検証用データ。
- `scripts/`: 取得・検証・補完のスクリプト群。
