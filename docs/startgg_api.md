# start.gg API usage

Everything that talks to start.gg goes through `scripts/common/utils.py`; the GraphQL
documents live in `scripts/common/queries.py`. Scripts never call `requests` directly,
which is what makes the record/replay tests possible.

## Endpoint and authentication

* Endpoint: `https://api.start.gg/gql/alpha` (override with `--url`).
* Header: `Authorization: Bearer <token>`. The token comes from the `STARTGG_TOKEN`
  environment variable (`--token` overrides it). Get one from the start.gg developer
  settings; a personal token is enough.
* Request body: `{"query": <document>, "variables": <JSON-encoded string>}`.
  Variables are sent as a JSON string, which the API accepts.

`setup_api(args)` in `_cli.py` configures the module-level URL, headers, retry count,
retry delay and page delay from the common CLI options
(`--token --url --max-retries --retry-delay`).

## Retries (`fetch_data_with_retries`)

One HTTP POST per call, retried up to `max_retries` times on any request error, HTTP
error or non-JSON body:

| Error | Wait before retry |
|---|---|
| HTTP 429 (rate limit) | `retry_delay × attempt` (linear back-off) |
| HTTP 5xx | `retry_delay × attempt` |
| anything else | `retry_delay` |

A random jitter of up to 10 % (at least 1 s) is added. When retries are exhausted a
`FetchError` is raised; the downloader logs the event to `failed_events.log` and moves
on. GraphQL-level errors (`"errors"` in the body) are returned to the caller, except
for query-complexity errors, which paging handles.

## Paging (`fetch_all_nodes`)

start.gg pages every list (`page` / `perPage`) and rejects queries whose "complexity"
exceeds a per-request budget. `fetch_all_nodes(query, variables, keys, per_page)`:

* walks `page = 1, 2, ...` until `pageInfo.totalPages` is reached and checks
  `pageInfo.total` against the number of nodes received, so truncated lists are detected;
* on a complexity error halves `perPage` (down to 2) and re-requests the same offset;
  after 30 consecutive successes at a reduced size it probes back upwards
  (AIMD-style), so a long fetch settles on the largest page size the API tolerates;
* sleeps `page_delay` seconds (default 2) between pages.

Set downloads (`redownload_matches_v2.fetch_phase_group_sets`) page per phase group
with 50 sets per page and include the `games` sub-selection, and sleep between phase
groups.

## Rate limits

start.gg allows roughly 80 requests per minute per token. The nightly run for Japan
(a two-week window, ~100–150 tournaments, most already downloaded) takes a few minutes;
a full re-download of a large event is dominated by the per-page delay. If you see many
429 lines, raise `--retry-delay` rather than lowering the page delay.

## Queries

All functions in `queries.py` return a document string; the pipeline uses:

| Function | Used by | Purpose |
|---|---|---|
| `get_tournaments_by_game_query(country_code, before_now, past)` | download.py, fetch_upcoming.py | tournaments of a game, filtered by country, sorted by `endAt` (past) or `startAt` (upcoming) |
| `get_tournament_events_query` | download.py | events of a tournament with their videogame tag |
| `get_standings_query` | download.py | standings with entrant → participant → user/player, including the user's location, discriminator and linked accounts |
| `get_seeds_query`, `get_phase_groups_query` | download.py | seeds of the first phase; phase groups of an event |
| `get_phase_group_sets_with_games_query` (and `_full`, `_minimal` variants) | redownload_matches_v2.py | sets of one phase group with slots, scores, round, wave and games |
| `get_event_phases_full_query`, `get_event_phases_named_query`, `get_phase_group_standings_query` | scripts/Japan/* | phase structure and per-phase-group standings for class separation |
| `get_event_details_by_tournament_query`, `get_event_details_by_id_query` | manual tools | event lookup by slug or id |
| `get_user_query`, `get_user_player_query` | refresh_users.py | refresh a user's profile |

Field names in the saved files are documented in [data_model.md](data_model.md).

## Changing a query

Adding a field to a query changes complexity and can trigger the halving above; it
also changes the response bodies, so the replay cassette in spsp `tests/fetch/fixtures/`
no longer matches and must be re-recorded (`dl_golden.py record`). Re-record with a
small window and commit the new cassette together with the query change.
