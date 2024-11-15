## Directory Structure

```
data
|_startgg
  |_players.json
  |_tournaments.csv
  |_event2path.csv
  |_done.csv
  |_events
    |_id2path.csv
    |_yyyy
      |_mm
        |_dd
          |_Region
            |_Tournament Name
              |_Event Name
                |_attr.json
                |_matches.json
                |_standings.json
                |_seeds.json
```

## File Schema

### done.csv

```
tournament_id1
tournament_id2
...
```

### tournaments.csv

```
tournament_id1 tournament_name
tournament_id2 tournament_name
...
```

### event2path.csv

```
event_id1 date1 path1
event_id2 date2 path2
....
```

### players.json

```
{
  - version: str
  - data: list
  {
      - user_id: str
      - gamer_tag: str
      - prefix: str
      - gender_pronoun: str
      - x_id: str
      - x_name: str
    - discord_id: str
    - discord_name: str
  }
}
```

### attr.json

```
- version: str
- event_id: str
- tournament_name: str
- event_name: str
- date: str
- region: str
- num_entrants: int
- offline: bool
- rule: str
```

#### rule

- gati_1on1
- squad_strike
- oma1
- oma5
- other
- unkown

The rule is estimated by chatgpt with startgg event description.

### matches.json

```
- version: str
- data: list

  {
  - winner_id: str
  - loser_id: str
  - winner_score: int
  - loser_score: int
  - round_text: str
  - round: int
  - pool: str
  - dq: bool
  - state: int
  - details: str
  }
```
  TODO: add pool and cancel

### standings.json

```
- version: str
- data: list
  [
    {
      - placement: int
      - user_id: str
    }
  ]
```

### seeds.json

```
- version: str
- data: list
  [
    {
      - seed_num: int
      - user_id: str
    }
  ]
```
