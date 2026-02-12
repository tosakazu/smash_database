# Data Model

## 保存全体像
- start.gg の取得結果は `data/startgg/` に集約。
- 各イベントは `attr.json` / `standings.json` / `seeds.json` / `matches.json` に分割保存。

## 管理ファイル

### `data/startgg/done.csv`
```csv
12345
67890
```
- 1行1大会ID（既取得の大会）

### `data/startgg/done_events.csv`
```csv
999
1000
```
- 1行1イベントID（個別取得済み）

## 大会索引

### `data/startgg/tournaments.jsonl`
```json
{"tournament_id": 12345, "name": "Example Tournament", "events": [{"event_id": 999, "event_name": "Ultimate Singles", "path": "data/startgg/events/Japan/2024/01/01/Example_Tournament/Ultimate_Singles"}], "version": "1.0"}
```

## ユーザー

### `data/startgg/users.jsonl`
```json
{"user_id": 111, "player_id": 222, "gamer_tag": "PlayerName", "prefix": "Team", "gender_pronoun": "he/him", "startgg_discriminator": "1234", "x_id": "1", "x_name": "user_x", "discord_id": "2", "discord_name": "user#0001", "version": "1.0"}
```

## イベント属性

### `data/startgg/events/.../attr.json`
```json
{
  "version": "1.0",
  "event_id": 999,
  "tournament_name": "Example Tournament",
  "event_name": "Ultimate Singles",
  "timestamp": 1710001000,
  "region": "Japan",
  "num_entrants": 128,
  "offline": true,
  "url": "https://www.start.gg/tournament/...",
  "place": {
    "country_code": "JP",
    "city": "Tokyo",
    "lat": 35.68,
    "lng": 139.76,
    "venue_name": "Example Hall",
    "timezone": "Asia/Tokyo",
    "postal_code": "100-0001",
    "venue_address": "Tokyo, Japan",
    "maps_place_id": "..."
  },
  "labels": {
    "registration_type": "full-open",
    "event_type": "main",
    "game_rule": "1on1"
  },
  "status": "completed"
}
```

## standings

### `data/startgg/events/.../standings.json`
```json
{
  "version": "1.0",
  "data": [
    {"placement": 1, "user_id": 111},
    {"placement": 2, "user_id": 112}
  ]
}
```

## seeds

### `data/startgg/events/.../seeds.json`
```json
{
  "version": "1.0",
  "data": [
    {"seed_num": 1, "user_id": 111},
    {"seed_num": 2, "user_id": 112}
  ]
}
```

## matches

### `data/startgg/events/.../matches.json`
```json
{
  "version": "1.0",
  "data": [
    {
      "winner_id": 111,
      "loser_id": 112,
      "winner_score": 2,
      "loser_score": 1,
      "round_text": "Winners Round 1",
      "round": 1,
      "phase": "A",
      "wave": "Wave 1",
      "dq": false,
      "cancel": false,
      "state": 3,
      "details": [
        {
          "game_id": 9999,
          "order_num": 1,
          "winner_id": 111,
          "entrant1_score": 1,
          "entrant2_score": 0,
          "stage": "Battlefield",
          "selections": [
            {
              "user_id": 111,
              "selection_id": 1,
              "character_id": 10,
              "character_name": "Mario"
            }
          ]
        }
      ]
    }
  ]
}
```

## 注意点
- doubles/crew などは user_id が取得できず `null` になる場合がある。
- `labels` は OpenAI による推定であり、正確性は保証されない。
