# start.gg API 設計

## 概要
- start.gg の GraphQL API を利用して大会・イベント・結果データを取得し、`data/startgg/` 配下に保存する。
- 実装は `scripts/fetch/download.py` を中心に構成され、補助スクリプトで欠損補完や個別取得を行う。

## エンドポイントと認証
- エンドポイント: `https://api.start.gg/gql/alpha`
- 認証: `Authorization: Bearer <token>`
- 送信ボディ（例）
```json
{
  "query": "query TournamentsByGame($gameId: ID!, $perPage: Int!, $page: Int!) { ... }",
  "variables": {
    "gameId": 1386,
    "perPage": 5,
    "page": 1
  }
}
```

## 取得クエリと主なレスポンス例

### 大会一覧
- クエリ: `get_tournaments_by_game_query`
- 目的: 最新順で大会一覧を取得
- 主なレスポンス（抜粋）
```json
{
  "data": {
    "tournaments": {
      "nodes": [
        {
          "id": 12345,
          "name": "Example Tournament",
          "startAt": 1710000000,
          "endAt": 1710086400,
          "countryCode": "JP",
          "isOnline": false,
          "city": "Tokyo",
          "lat": 35.68,
          "lng": 139.76,
          "mapsPlaceId": "...",
          "postalCode": "100-0001",
          "venueAddress": "Tokyo, Japan",
          "venueName": "Example Hall",
          "timezone": "Asia/Tokyo",
          "url": "https://www.start.gg/tournament/..."
        }
      ],
      "pageInfo": {
        "totalPages": 10
      }
    }
  }
}
```

### 大会内イベント一覧
- クエリ: `get_tournament_events_query`
- 目的: 大会IDごとにイベントを取得
- 主なレスポンス（抜粋）
```json
{
  "data": {
    "tournament": {
      "events": [
        {
          "id": 999,
          "name": "Ultimate Singles",
          "startAt": 1710001000,
          "isOnline": false
        }
      ]
    }
  }
}
```

### standings（順位 + 参加者）
- クエリ: `get_standings_query`
- 目的: placements と participant 情報を取得
- 主なレスポンス（抜粋）
```json
{
  "data": {
    "event": {
      "standings": {
        "nodes": [
          {
            "placement": 1,
            "entrant": {
              "id": 555,
              "participants": [
                {
                  "user": {
                    "id": 111,
                    "genderPronoun": "he/him",
                    "discriminator": "1234",
                    "authorizations": [
                      {"type": "TWITTER", "externalId": "1", "externalUsername": "user_x"},
                      {"type": "DISCORD", "externalId": "2", "externalUsername": "user#0001"}
                    ]
                  },
                  "player": {
                    "id": 222,
                    "gamerTag": "PlayerName",
                    "prefix": "Team"
                  }
                }
              ]
            }
          }
        ]
      }
    }
  }
}
```

### seeds（シード）
- クエリ: `get_seeds_query`
- 目的: seedNum と participant 情報を取得
- 主なレスポンス（抜粋）
```json
{
  "data": {
    "phase": {
      "seeds": {
        "nodes": [
          {
            "id": 777,
            "seedNum": 1,
            "entrant": {
              "id": 555,
              "participants": [
                {
                  "user": {"id": 111},
                  "player": {"id": 222, "gamerTag": "PlayerName", "prefix": "Team"}
                }
              ]
            }
          }
        ]
      }
    }
  }
}
```

### sets（試合 + ゲーム詳細）
- クエリ: `get_event_sets_query`
- 目的: セット情報とゲーム詳細（キャラ選択など）を取得
- 主なレスポンス（抜粋）
```json
{
  "data": {
    "event": {
      "sets": {
        "nodes": [
          {
            "id": 888,
            "state": 3,
            "winnerId": 555,
            "round": 1,
            "fullRoundText": "Winners Round 1",
            "phaseGroup": {
              "displayIdentifier": "A",
              "wave": {"identifier": "Wave 1"}
            },
            "slots": [
              {
                "entrant": {"id": 555},
                "standing": {"stats": {"score": {"value": 2}}}
              },
              {
                "entrant": {"id": 556},
                "standing": {"stats": {"score": {"value": 1}}}
              }
            ],
            "games": [
              {
                "id": 9999,
                "orderNum": 1,
                "winnerId": 555,
                "entrant1Score": 1,
                "entrant2Score": 0,
                "stage": {"name": "Battlefield"},
                "selections": [
                  {
                    "id": 1,
                    "entrant": {"id": 555},
                    "character": {"id": 10, "name": "Mario"}
                  }
                ]
              }
            ]
          }
        ]
      }
    }
  }
}
```

## ページングとリトライ
- ページングは `fetch_all_nodes()` が担当。`page` を増やしながら `nodes` を全取得。
- API失敗時は `fetch_data_with_retries()` がリトライ。
  - 429 は待機時間を延長。
  - 5xx は指数的に待機時間を増加。

## 保存先
- 取得結果は `docs/data_model.md` に記載の形式で `data/startgg/` に保存される。
