# Data model

Everything lives under `data/startgg/` on a data branch (`data-<Region>`); `main` has no
data. Paths below are relative to the repository root; `<Region>` is `Japan`, `North_America`, ... as
returned by `country_code2region()` in `scripts/common/utils.py` (spaces replaced by `_`).

```
data/startgg/<Region>/              one directory per region
├── done.csv                        region index (four files, rewritten by the downloader)
├── done_events.csv
├── tournaments.jsonl
├── users.jsonl
├── users_curated.jsonl             generated: prefecture per player (scripts/common/curate.py)
├── curation/                       hand-maintained tables (see below)
│   ├── user_merges.json
│   ├── overseas_manual.json
│   └── awaiting_resume.json
└── events/YYYY/MM/DD/<Tournament>/<Event>/
    ├── attr.json
    ├── standings.json
    ├── seeds.json
    ├── matches.json
    ├── phases.json                 (Japan class separation, optional)
    ├── curated.json                (judgements derived from the files above; scripts/common/curate.py)
    └── class_phases/               (Japan class separation, optional)
        ├── <phase_id>.json
        └── <Letter>_virtual/{attr.json,standings.json,matches.json}
```

The date in the path is the tournament start date in UTC; `<Tournament>` and `<Event>`
are the start.gg names with spaces replaced by `_` (and `/` removed). If start.gg later
changes a tournament's date, the downloader moves the directory and updates
`tournaments.jsonl` (see [pipeline.md](pipeline.md), "date changes").

All JSON is UTF-8 with `ensure_ascii=False`, indent 2 unless noted, and carries a
`"version": "1.0"` field. Key order is fixed by the writers in `scripts/common/utils.py`.
Do not rewrite files with a different serializer.

## Region index

### `done.csv`

One start.gg tournament id per line. A tournament is appended once every event of it
has been downloaded and either has a champion or has passed the retry window
(see pipeline.md). The downloader skips listed tournaments unless a re-download rule
applies.

### `done_events.csv`

One event id per line. Written by `download_specific_event.py` (manual single-event
downloads); the nightly downloader does not read it.

### `tournaments.jsonl`

One JSON object per line, one per tournament:

```json
{"tournament_id": 729207, "name": "渋谷BeeSmash 35",
 "events": [{"event_id": 1255738, "event_name": "Singles",
             "path": "data/startgg/Japan/events/2024/11/17/渋谷BeeSmash_35/Singles"}],
 "version": "1.0"}
```

`path` is the event directory relative to the repository root. This file is the index
consumers use to enumerate events; the directory tree alone is not authoritative.
There is no timestamp field yet (open issue #13).

### `users.jsonl`

One JSON object per line, one per start.gg user seen in any standings or seeds:

| Key | Meaning |
|---|---|
| `user_id` | start.gg user id. The identifier used everywhere else (`standings`, `seeds`, `matches`) |
| `player_id` | start.gg player id |
| `gamer_tag`, `prefix` | tag and team prefix at the time of the last download |
| `gender_pronoun` | as set on start.gg, or `"unknown"` |
| `startgg_discriminator` | the short code shown after the tag on start.gg (`tag#code`) |
| `country`, `addr_state`, `city` | location as registered on start.gg (free text, may be `null`) |
| `x_id`, `x_name`, `discord_id`, `discord_name` | linked accounts, or `null` |
| `version` | `"1.0"` |

Rows are updated in place when a user reappears with new information. A player who
enters tournaments in two regions has a row in both regions' files; merge by `user_id`.

## Event files

### `attr.json`

```json
{
  "event_id": 1628310,
  "tournament_name": "Victoire #1",
  "event_name": "singles tournament",
  "region": "Japan",
  "place": {"country_code": "JP", "city": "北名古屋市", "lat": 35.24, "lng": 136.86,
            "venue_name": null, "timezone": "Asia/Tokyo", "postal_code": "481-0033",
            "venue_address": "...", "maps_place_id": "..."},
  "num_entrants": 48,
  "offline": true,
  "url": "/tournament/victoire-1",
  "labels": {},
  "status": "completed",
  "timestamp": 1786240800,
  "end_timestamp": 1786273200,
  "version": "1.0"
}
```

* `timestamp` / `end_timestamp` are the tournament `startAt` / `endAt` (Unix seconds).
  When `startAt` is more than 3 days away from the real set times (organisers sometimes
  enter the announcement date or a placeholder), the downloader replaces the pair by
  the first and last set time (`corrected_event_window` in `download.py`).
* `url` is the tournament path on start.gg (prefix `https://www.start.gg`).
* `labels` is free-form metadata. Real events currently have `{}`; virtual class events
  set `is_class_virtual` (below). Historical values produced by an LLM classifier were
  dropped.
* `status` is `"completed"` for every downloaded event.

### `standings.json`

```json
{"data": [{"placement": 1, "user_id": 1787719}, {"placement": 2, "user_id": 12345}], "version": "1.0"}
```

Sorted by placement. `user_id` is `null` for entrants without a start.gg user (teams,
guests). Ties share a placement value.

### `seeds.json`

```json
{"data": [{"seed_num": 1, "user_id": 1787719}], "version": "1.0"}
```

Seeds of the event's first phase, sorted by `seed_num`. May be empty for events run
without seeding.

### `matches.json`

Compact (single-line) JSON:

```json
{"data": [ ...sets... ], "bracket_capacity": 32, "dup_set_id": 0, "dup_match_key": 0}
```

* `bracket_capacity` is the size of the main bracket (number of slots) when known.
* `dup_set_id` / `dup_match_key` count duplicates removed when the sets were merged
  from several phase groups; non-zero values are diagnostics, not errors.

Each element of `data` is one set:

| Key | Meaning |
|---|---|
| `match_id` | start.gg set id |
| `winner_id`, `loser_id` | start.gg user ids (`null` when the entrant has no user) |
| `winner_score`, `loser_score` | game counts. A negative score on either side marks a DQ (and sets `dq`) |
| `round`, `round_text` | start.gg round number (negative = losers side) and label such as `"Winners Round 1"` |
| `phase`, `phase_id`, `phase_name`, `phase_order`, `phase_num_seeds`, `phase_bracket_type`, `phase_top_n` | the phase the set belongs to. `phase` is the phase group display identifier. `phase_bracket_type` is `DOUBLE_ELIMINATION`, `SINGLE_ELIMINATION`, `ROUND_ROBIN`, `SWISS`, ... |
| `bracket_label`, `winners_top`, `losers_top`, `global_round`, `global_top_x`, `global_bracket_label` | derived labels for "which stage of the tournament" (e.g. `"総当たり"`, `"Top 8"`). `null` when not derivable |
| `phase_group_id`, `phase_group_start_at`, `wave_id`, `wave`, `wave_start_at` | pool identification: the phase group (pool) and, for pooled events, the wave it was played in |
| `dq`, `cancel` | flags for DQ sets and cancelled sets. Consumers should ignore both |
| `state` | start.gg set state (`3` = completed) |
| `started_at`, `completed_at` | Unix seconds, may be `null` |
| `details` | per-game records (below). Empty when the organiser did not report games |

Per-game record inside `details`:

```json
{"game_id": 39867329, "order_num": 1, "winner_id": 2568072,
 "entrant1_score": null, "entrant2_score": 0, "stage": null,
 "selections": [{"user_id": 2568072, "selection_id": 59643563,
                 "character_id": 1405, "character_name": "Mr. Game & Watch"}]}
```

`selections` carry character picks per user; this is the source of character-usage
statistics.

## Class separation (Japan)

Many Japanese tournaments run "B class" / "C class" brackets for players eliminated
from the main bracket, as extra phases of the same start.gg event. Their sets are
therefore mixed into `matches.json`, and their placements do not appear in
`standings.json`. `scripts/Japan/update_class_data.py` separates them:

### `phases.json`

```json
{"event_id": 1628310, "event_name": "singles tournament", "has_class_phases": true,
 "phases": [{"id": 2291959, "name": "予選", "order": 1, "bracket_type": "ROUND_ROBIN",
             "num_seeds": 48, "is_class": false,
             "phase_groups": [{"id": 3318428, "display": "1"}]}, ...]}
```

`is_class` is set when the phase name matches the class pattern
(`[BCDE]クラス`, `B class`, ...).

### `class_phases/<phase_id>.json`

Raw standings of one class phase, per phase group, plus `played_user_ids` (users who
played at least one set in that group, used to exclude no-shows):

```json
{"event_id": 1628310, "phase_id": 2361991, "phase_name": "Bクラス", "phase_order": 3,
 "bracket_type": "DOUBLE_ELIMINATION", "num_seeds": 24,
 "phase_groups": [{"phase_group_id": 3410328, "display": "1",
                   "standings": [{"placement": 1, "entrant_id": 23967900,
                                  "entrant_name": "BJ999", "user_id": 1847578}, ...],
                   "played_user_ids": [1847578, ...]}]}
```

### `class_phases/<Letter>_virtual/`

One class letter materialised as an event of its own so that consumers can treat it
like any other tournament:

* `attr.json` — copy of the parent event's attributes with `event_name` suffixed
  (`"Singles Tournament / Bクラス"`), `labels.is_class_virtual = true`, and a negative
  `event_id` = `-(parent_event_id * 10 + ord(letter))`, which cannot collide with real ids.
* `standings.json` — a plain list `[{"placement", "user_id"}]` (no `data` wrapper),
  derived from the class phases: each player's deepest class phase and placement there.
* `matches.json` — the parent's sets filtered to the class phase groups, same format as
  above.

The spsp loader reads these virtual directories as regular tournaments.

## `curated.json`

Written by `scripts/common/curate.py` for every event directory (virtual class events
included). It holds the judgements that consumers used to re-derive from the raw files;
the rules are in `scripts/Japan/classify.py` and `classifier_version` records which rule
set produced the file. The file is a sidecar: raw files are never rewritten, and it is
rewritten only when its content changes (so its mtime is stable). When `classify.py`
changes, bump `CLASSIFIER_VERSION` and run `curate.py --region Japan --all`.

```json
{
  "classifier_version": 1,
  "event_id": 1628310, "tournament_name": "Victoire #1", "event_name": "singles tournament", "num_entrants": 48,
  "is_class_virtual": false, "parent_event_id": null, "class_letter": null,
  "results": {"n_standings": 48, "min_placement": 1, "has_de_phase": true, "has_gf_recorded": null},
  "is_1on1": true, "not_1on1_reason": null,
  "names": {"special_rules": false, "uchi": false, "non_serious": false, "restricted_tname": false,
            "restricted_ename": false, "lower_class": false, "pre": false, "smapa": false,
            "force_weekday": false, "smacomi": false},
  "calendar": {"date": "2026-08-09", "end_date": "2026-08-09", "is_weekend_real": true, "is_force_weekend_period": false},
  "class_bracket": {"all_phases_class": false, "phase_group_ids": [3410328]},
  "place": {"prefecture": "愛知県"}
}
```

| Key | Meaning |
|---|---|
| `is_1on1`, `not_1on1_reason` | whether the event is a singles 1-on-1 event (doubles, crews, squad strike, character-limited, casual, online and test events are excluded; an allow-list rescues known false positives). The reason names the rule that excluded it |
| `names.*` | tournament/event-name patterns: `special_rules` (random-character, crew, doubles, ...), `uchi` (private / invitational), `non_serious` (= either), `restricted_tname` / `restricted_ename` (entry-restricted series, matched on the tournament name and the event name separately so consumers can apply the "restriction belongs to the restricted sibling event" rule), `lower_class` (B/C class naming), `pre` (preliminary), `smapa`, `force_weekday` (series treated as weekday events even on weekends), `smacomi` (上野スマコミ; consumers apply the entrant-count threshold) |
| `calendar` | dates in JST; `is_weekend_real` = any day of the event is a weekend or Japanese holiday; `is_force_weekend_period` = the event overlaps Obon (8/13-15) or the year-end break (12/26-1/5) |
| `results` | facts from `standings.json` / `matches.json`: number of standings rows with a user, best placement present, whether any completed set belongs to a double-elimination phase, and the `has_gf_recorded` backfill flag |
| `class_bracket` | phase-group ids of the class brackets inside the event (empty when the whole event is a class bracket) |
| `place.prefecture` | prefecture (kanji) of the venue, from the address, the city, or the free-text address via the resolver in `scripts/Japan/prefecture.py`; `null` outside Japan or when unresolvable |

These are facts about the data. How they are combined (weekday handling for small
events, minimum entrant counts, level filters) is up to the consumer.

## `users_curated.jsonl`

Generated by `curate.py` from `users.jsonl` (never edited by hand). One line per player
whose start.gg country is Japan and who filled in a city:

```json
{"user_id": 1787719, "prefecture": "兵庫県", "prefecture_reason": "pref_kanji"}
```

`prefecture` is `null` when the free text cannot be resolved to exactly one prefecture;
`prefecture_reason` names the rule that matched (or why it failed: `ambig_muni`,
`unmatched`, ...). The resolver and its municipality master (総務省 codes, kanji / kana /
romaji) live in `scripts/Japan/prefecture.py` and `scripts/Japan/resources/`.

## `curation/` (hand-maintained)

Tables that people maintain. They are region data, so they live on the data branch and
are committed like any other data change. Nothing regenerates them.

| File | Contents | Consumers |
|---|---|---|
| `user_merges.json` | `{"merges": [{"old": <uid>, "new": <uid>, "note": "..."}]}` — duplicate start.gg accounts folded into the canonical account. Chains (A→B, B→C) are allowed; readers resolve them | spsp build (standings and sets are rewritten before learning); seed tool via `site/data/user_merges.json` |
| `overseas_manual.json` | `uids`: overseas players who have no country on start.gg; `jp_uids`: players registered abroad who are treated as Japanese (joke countries, foreigners living in Japan) | spsp `build_overseas_json.py` (overseas tag = country ≠ Japan among ranked players ∪ `uids` without country − `jp_uids` − long-term residents) |
| `awaiting_resume.json` | `{"events": [{"event_id", "name", "added", "note"}]}` — events interrupted (weather, venue) and resuming later. `download.py --awaiting-file` re-fetches them every run until a winner exists; the build uses partial results meanwhile | download.py, spsp build (`spsp/cli/awaiting_resume.py --list/--add/--prune` edits it) |

## Other outputs

* `upcoming.json` (from `fetch_upcoming.py`) is not stored in this repository. Shape:
  `{"generated_at", "country_code", "lookahead_days", "count", "tournaments": [{tournament_id, tournament_slug, tournament_name, start_at, end_at, is_online, city, venue_name, country_code, num_attendees, url, events: [{event_id, event_slug, event_name, num_entrants, type, start_at}]}]}`.
* `failed_events.log` (cwd of the downloader) lists events that failed after all
  retries, one per line, for the operator to re-run.
