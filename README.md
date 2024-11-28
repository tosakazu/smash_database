## Directory Structure

```
data
|_startgg
  |_users.jsonl
  |_tournaments.jsonl
  |_done.csv
  |_events
    |_{Region Name}
      |_{yyyy}
        |_{mm}
          |_{dd}
            |_{Tournament Name}
              |_{Event Name}
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

### tournaments.jsonl

```
{
  - tournament_id: int
  - tournament_name: str
  - events: list
    [
      {
        - event_id: int
        - event_name: str
        - path: str
      }
    ]
  - version: str
}
```

### users.jsonl

```
{
  - user_id: int
  - player_id: int
  - gamer_tag: str
  - prefix: str
  - gender_pronoun: str
  - x_id: str
  - x_name: str
  - discord_id: str
  - discord_name: str
  - version: str
}
```

### attr.json

```
- version: str
- event_id: int
- tournament_name: str
- event_name: str
- timestamp: int
- region: str
- num_entrants: int
- offline: bool
- url: str
- place: dict
  {
    - country_code: str
    - city: str
    - lat: float
    - lng: float
    - venue_name: str
    - timezone: str
    - postal_code: str
    - venue_address: str
    - maps_place_id: str
  }
- labels: dict
  {
    - registration_type: str
    - event_type: str
    - game_rule: str
  }
```

#### labels

- registration_type: str
  - { full-open, full-invite, invite-open, restricted-open, close }
  - full-open: All players can register.
  - full-invite: Only invited players can register.
  - invite-open: Basically invited players can register, but some players can register without invitation.
  - restricted-open: Players can register with a certain condition.
  - close: Closed event.
- event_type: str
  - { main, sub, spectator }
- game_rule: str
  - { 1on1, doubles, random, squad-strike, oma-5, crew-battle }

labels are estimated by AI with startgg event description.  
The registration_type and event_type fields are filled in, but they aren't very reliable.  
The game_rule field is fairly trustworthy.  

### matches.json

```
- version: str
- data: list
  [
    {
      - winner_id: int
      - loser_id: int
      - winner_score: int
      - loser_score: int
      - round_text: str
      - round: int
      - phase: str
      - wave: str
      - dq: bool
      - cancel: bool
      - state: int
      - details: dict
        {
          game_id: int
          order_num: int
          winner_id: int
          entrant1_score: int
          entrant2_score: int
          stage: str
          selections: list
          [
            {
              user_id: int
              selection_id: int
              character_id: int
              character_name: str
            },
            {
              user_id: int
              selection_id: int
              character_id: int
              character_name: str
            }
          ]
        }
    }
  ]
```

Note that the system does not support data retrieval for doubles and crew battles, and even when this data exists in the database, the user_id is frequently not properly obtained.

### standings.json

```
- version: str
- data: list
  [
    {
      - placement: int
      - user_id: int
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
      - user_id: int
    }
  ]
```
