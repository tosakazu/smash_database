import os
import re
import argparse
import sys
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Import the query functions we need from queries.py
# get_event_details_by_slug_query needs to be added
from scripts.common.queries import (
    get_event_sets_query, get_standings_query, get_seeds_query,
    get_phase_groups_query, get_event_details_by_tournament_query, get_tournament_event_list_query
)
# Import the utility functions we need from utils.py
from scripts.common.utils import (
    country_code2region, get_date_parts, get_event_directory,
    read_users_jsonl, read_set, read_tournaments_jsonl,
    write_json, extend_jsonl, write_jsonl,
    set_indent_num,
    fetch_data_with_retries, fetch_all_nodes,
    set_retry_parameters, set_api_parameters,
    FetchError, NoPhaseError,
)

REQUIRED_EVENT_FILES = ("attr.json", "matches.json", "standings.json", "seeds.json")
STANDINGS_PER_PAGE = 200
SEEDS_PER_PAGE = 200
SETS_PER_PAGE = 50

def event_files_complete(event_dir):
    return all(os.path.exists(os.path.join(event_dir, name)) for name in REQUIRED_EVENT_FILES)

# --- Functions reused from the original script ---
# Share download.py's functions so we get the same per-phase_group fetch logic as
# v2 (refetch) plus the rich schema (match_id / bracket_label / global_round).
from scripts.common.download import (
    fetch_all_sets as _fetch_all_sets_v2,
    download_all_set as _download_all_set_v2,
)

# Save the event's set data
def download_all_set(event_id, entrant2user, event_dir):
    """Save all sets of the event to matches.json using the v2 schema.
    entrant2user is accepted for compatibility but unused internally (write_matches_v2 rebuilds it).
    """
    _download_all_set_v2(event_id, entrant2user, event_dir)

def fetch_all_sets(event_id):
    """Fetch all sets of the event from the API using the v2 logic.
    Returns: list of (set_node, phase_info, pg_info) tuples.
    """
    try:
        return _fetch_all_sets_v2(event_id)
    except FetchError as e:
        print(f"Error fetching sets for event {event_id}: {e}")
        return None

def write_matches(all_nodes, entrant2user, event_dir):
    """Format the fetched set data and write it to matches.json"""
    json_data = {"data": []}
    processed_count = 0
    skipped_count = 0
    for node in all_nodes:
        # Skip if required information is missing
        if node['slots'] is None or len(node['slots']) != 2:
            skipped_count += 1
            continue
        slot0 = node['slots'][0]
        slot1 = node['slots'][1]
        if (slot0['entrant'] is None or slot1['entrant'] is None or
            slot0['standing'] is None or slot1['standing'] is None or
            slot0['standing']['stats'] is None or slot1['standing']['stats'] is None or
            slot0['standing']['stats']['score'] is None or slot1['standing']['stats']['score'] is None):
            skipped_count += 1
            continue

        # Check that the entrant IDs exist in the entrant2user mapping
        entrant0_id = slot0['entrant']['id']
        entrant1_id = slot1['entrant']['id']
        if entrant0_id not in entrant2user or entrant1_id not in entrant2user:
            # print(f"Skipping set {node.get('id', 'N/A')} due to missing entrant ID in mapping.")
            skipped_count += 1
            continue

        # Use 0 when the score is None
        score0 = slot0['standing']['stats']['score']['value'] if slot0['standing']['stats']['score']['value'] is not None else 0
        score1 = slot1['standing']['stats']['score']['value'] if slot1['standing']['stats']['score']['value'] is not None else 0

        # Determine winner and loser: prefer node.winnerId (entrant ID).
        # Comparing scores alone had a bug where score 0/0 (score unavailable due to cancel/DQ)
        # always misjudged slot1 as the winner (found via Melon Ojisan at Ike-Suma #4).
        winner_eid = node.get('winnerId')
        if winner_eid is not None and winner_eid in (entrant0_id, entrant1_id):
            winner_slot = slot0 if winner_eid == entrant0_id else slot1
        else:
            # winnerId unknown -> compare scores. A tie (e.g. 0-0) cannot be resolved, so skip.
            if score0 == score1:
                skipped_count += 1
                continue
            winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot == slot0 else slot0
        winner_score = score0 if winner_slot == slot0 else score1
        loser_score = score1 if winner_slot == slot0 else score0

        winner_entrant_id = winner_slot['entrant']['id']
        loser_entrant_id = loser_slot['entrant']['id']

        dq = (score0 < 0 or score1 < 0)
        # On start.gg a 0-0 score usually means unplayed or cancelled
        cancel = (score0 == 0 and score1 == 0 and not dq and winner_eid is None)

        # Process per-game details
        details = []
        if node.get('games'):
            for game in node['games']:
                if game is None: continue # skip if game is None
                winner_id_in_game = game.get('winnerId')
                selections_data = []
                if game.get('selections'):
                    for selection in game['selections']:
                         # Check that the required information is present
                        if (selection and selection.get('entrant') and selection['entrant'].get('id') and
                            selection.get('character') and selection['character'].get('id') and selection['character'].get('name')):
                            entrant_id_in_selection = selection['entrant']['id']
                            selections_data.append({
                                "user_id": entrant2user.get(entrant_id_in_selection), # safe access via .get()
                                "selection_id": selection.get('id'),
                                "character_id": selection['character']['id'],
                                "character_name": selection['character']['name']
                            })
                        else:
                            # print(f"Skipping selection due to missing data in game {game.get('id', 'N/A')}")
                            pass # skip incomplete selections

                details.append({
                    "game_id": game.get('id'),
                    "order_num": game.get('orderNum'),
                    "winner_id": entrant2user.get(winner_id_in_game) if winner_id_in_game else None, # safe access via .get()
                    "entrant1_score": game.get('entrant1Score'),
                    "entrant2_score": game.get('entrant2Score'),
                    "stage": game.get('stage', {}).get('name') if game.get('stage') else None, # safe access
                    "selections": selections_data
                })
        else:
            details = [] # empty list when there are no games

        # Process phase and wave information
        phase = None
        wave = None
        if node.get('phaseGroup'):
            phase = node['phaseGroup'].get('displayIdentifier')
            if node['phaseGroup'].get('wave'):
                wave = node['phaseGroup']['wave'].get('identifier')

        # Build the match record
        match_data = {
            "winner_id": entrant2user.get(winner_entrant_id), # safe access via .get()
            "loser_id": entrant2user.get(loser_entrant_id), # safe access via .get()
            "winner_score": winner_score,
            "loser_score": loser_score,
            "round_text": node.get('fullRoundText'),
            "round": node.get('round'),
            "phase": phase,
            "wave": wave,
            "dq": dq,
            "cancel": cancel,
            "state": node.get('state'), # COMPLETED, etc.
            "details": details
        }
        json_data["data"].append(match_data)
        processed_count += 1

    if processed_count > 0:
        write_json(json_data, f"{event_dir}/matches.json", with_version=True)
        print(f"Wrote {processed_count} matches to {event_dir}/matches.json. Skipped {skipped_count} incomplete sets.")
    else:
        print(f"No processable matches found after filtering. Skipped {skipped_count} incomplete sets.")


def write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, url, is_online, event_dir, end_timestamp=None):
    """Save the event's attributes as attr.json"""
    os.makedirs(event_dir, exist_ok=True) # create the directory if it does not exist
    json_data = {
        "event_id": event_id,
        "tournament_name": tournament_name,
        "event_name": event_name,
        "region": country_code2region(place.get("country_code")) if place.get("country_code") else None, # safe access
        "place": place, # dict with country_code, city, lat, lng, etc.
        "num_entrants": num_entrants,
        "offline": not is_online if is_online is not None else None, # handle is_online being None
        "url": url, # tournament URL
        "status": "completed", # assumes the event has finished
        "timestamp": timestamp, # event start timestamp
        "end_timestamp": end_timestamp, # tournament end timestamp (used by holiday detection and the refetch window)
    }
    write_json(json_data, f"{event_dir}/attr.json", with_version=True)
    print(f"Successfully wrote attr.json for event {event_id} to {event_dir}")

def download_standings(event_id, event_dir):
    """Fetch standings, save them as standings.json, and return user information"""
    standings_data = []
    user_data = []
    player_data = []
    entrant2user = {}

    query = get_standings_query()
    variables = {"eventId": event_id}
    keys = ["event", "standings"]
    try:
        standings_nodes = fetch_all_nodes(query, variables, keys, per_page=STANDINGS_PER_PAGE)
        if not standings_nodes:
             print(f"No standings data found for event {event_id}.")
             return [], [], {} # return empty results when there is no data
    except FetchError as e:
        print(f"Error fetching standings for event {event_id}: {e}")
        return [], [], {} # return empty results on error too

    placements_list = []
    processed_count = 0
    skipped_count = 0

    for node in standings_nodes:
        # Skip if required information is missing
        if (node is None or node.get('entrant') is None or
            node['entrant'].get('participants') is None or
            not node['entrant']['participants'] or # list must be non-empty
            node['entrant']['participants'][0].get('user') is None or
            node['entrant']['participants'][0].get('player') is None or
            node.get('placement') is None or node['entrant'].get('id') is None):
            # print(f"Skipping standing entry due to missing data: {node}")
            skipped_count += 1
            continue

        user = node['entrant']['participants'][0]['user']
        player = node['entrant']['participants'][0]['player']
        entrant_id = node['entrant']['id']
        user_id = user.get('id')
        placement = node['placement']

        if user_id is None:
            # print(f"Skipping standing entry due to missing user ID: {node}")
            skipped_count += 1
            continue

        user_data.append(user)
        player_data.append(player)
        entrant2user[entrant_id] = user_id
        placements_list.append((placement, user_id))
        processed_count += 1

    if not placements_list:
        print(f"No valid placements could be processed for event {event_id}. Skipped {skipped_count} entries.")
        return user_data, player_data, entrant2user # user info may still be returned

    placements_list.sort(key=lambda x: x[0]) # sort by placement
    placements_dicts = [
        {"placement": placement, "user_id": user_id}
        for placement, user_id in placements_list
        if user_id is not None # include only entries whose user_id is not None
    ]

    os.makedirs(event_dir, exist_ok=True)
    json_data = {"data": placements_dicts}
    write_json(json_data, f"{event_dir}/standings.json", with_version=True)
    print(f"Successfully wrote {len(placements_dicts)} standings to {event_dir}/standings.json. Processed {processed_count}, Skipped {skipped_count} entries.")

    return user_data, player_data, entrant2user

def download_seeds(event_id, user_data, player_data, entrant2user, event_dir):
    """Fetch seed data and save it as seeds.json"""
    try:
        phase_id = fetch_phase_id(event_id)
        if phase_id is None:
             # fetch_phase_id should have logged the error, so keep this message short
             print(f"Could not determine phase ID for event {event_id}. Skipping seed download.")
             return # seeds cannot be fetched without a phase_id
    except NoPhaseError as e:
        print(f"Skipping seed download for event {event_id}: {e}")
        return # also skip on NoPhaseError
    except FetchError as e:
        print(f"Error fetching phase ID for event {event_id}: {e}. Skipping seed download.")
        return # also skip on other FetchErrors

    query = get_seeds_query()
    variables = {"phaseId": phase_id}
    keys = ["phase", "seeds"]
    try:
        seeds_nodes = fetch_all_nodes(query, variables, keys, per_page=SEEDS_PER_PAGE)
        if not seeds_nodes:
            print(f"No seeds data found for phase {phase_id} in event {event_id}.")
            # Processing can continue without a seeds file, so do not create an empty one
            # To create an empty seeds.json, add the write here
            # write_json({"data": []}, f"{event_dir}/seeds.json", with_version=True)
            return # nothing to do when there is no data
    except FetchError as e:
        print(f"Error fetching seeds for event {event_id} (phase {phase_id}): {e}")
        return # stop on error

    seeds_list = []
    processed_count = 0
    skipped_count = 0

    for seed in seeds_nodes:
        # Skip if required information is missing
        if (seed is None or seed.get('entrant') is None or
            seed['entrant'].get('id') is None or
            seed.get('seedNum') is None):
            # print(f"Skipping seed entry due to missing data: {seed}")
            skipped_count += 1
            continue

        entrant_id = seed['entrant']['id']
        seed_num = seed['seedNum']

        # Check whether entrant_id exists in the entrant2user mapping
        user_id = entrant2user.get(entrant_id)

        # If not in entrant2user, try the participant data (e.g. seeded-only entrants missing from standings)
        if user_id is None:
            if (seed['entrant'].get('participants') and
                seed['entrant']['participants'][0].get('user') and
                seed['entrant']['participants'][0].get('player')):

                user = seed['entrant']['participants'][0]['user']
                player = seed['entrant']['participants'][0]['player']
                user_id = user.get('id')

                if user_id and entrant_id: # once we have both user_id and entrant_id
                    if user_id not in [u['id'] for u in user_data if u]: # add if not already in the list
                         user_data.append(user)
                         player_data.append(player)
                    entrant2user[entrant_id] = user_id # also add to the mapping
                    # print(f"Added user {user_id} from seed data.")
                else:
                    # print(f"Skipping seed entry {seed.get('id', 'N/A')} as user_id could not be determined even from participant data.")
                    skipped_count += 1
                    continue # skip if user_id cannot be determined
            else:
                # print(f"Skipping seed entry {seed.get('id', 'N/A')} as user_id is missing and participant data is incomplete.")
                skipped_count += 1
                continue # skip if participant data is missing too

        # user_id resolved; add to the list
        seeds_list.append((seed_num, user_id))
        processed_count += 1

    if not seeds_list:
        print(f"No valid seeds could be processed for event {event_id}. Skipped {skipped_count} entries.")
        return

    seeds_list.sort(key=lambda x: x[0]) # sort by seed number
    seeds_dicts = [
        {"seed_num": seed_num, "user_id": user_id}
        for seed_num, user_id in seeds_list
        if user_id is not None # include only entries whose user_id is not None
    ]

    os.makedirs(event_dir, exist_ok=True)
    json_data = {"data": seeds_dicts}
    write_json(json_data, f"{event_dir}/seeds.json", with_version=True)
    print(f"Successfully wrote {len(seeds_dicts)} seeds to {event_dir}/seeds.json. Processed {processed_count}, Skipped {skipped_count} entries.")


def extend_user_info(user_data, player_data, users, users_file_path, dirty_user_ids=None):
    """Append new users to users.jsonl; update existing users in memory when they changed.

    dirty_user_ids: set that receives the id of every existing user that was updated
        (the caller uses it to rewrite the whole file with write_jsonl at the end).
    """
    new_users = []
    updated_count = 0
    added_count = 0

    for user, player in zip(user_data, player_data):
        # Skip if user or player is None or has no ID
        if user is None or player is None or user.get('id') is None or player.get('id') is None:
            continue

        user_id = user['id']
        player_id = player['id']
        gamer_tag = player.get('gamerTag')
        prefix = player.get('prefix')
        # Default value when genderPronoun is None
        gender_pronoun = user.get('genderPronoun') if user.get('genderPronoun') is not None else "unknown"
        startgg_discriminator = user.get('discriminator')

        location = user.get('location') or {}
        country = location.get('country')
        addr_state = location.get('state')
        city = location.get('city')

        # Extract information from authorizations
        x_id = None
        x_name = None
        discord_id = None
        discord_name = None
        if user.get('authorizations'):
            for auth in user['authorizations']:
                if auth and auth.get('type'): # check that auth exists and has a type key
                    if auth['type'] == 'TWITTER':
                        x_id = auth.get('externalId')
                        x_name = auth.get('externalUsername')
                    elif auth['type'] == 'DISCORD':
                        discord_id = auth.get('externalId')
                        discord_name = auth.get('externalUsername')

        # Check whether the user already exists
        if user_id not in users:
            new_user_entry = {
                "user_id": user_id,
                "player_id": player_id,
                "gamer_tag": gamer_tag,
                "prefix": prefix,
                "gender_pronoun": gender_pronoun,
                "startgg_discriminator": startgg_discriminator,
                "country": country,
                "addr_state": addr_state,
                "city": city,
                "x_id": x_id,
                "x_name": x_name,
                "discord_id": discord_id,
                "discord_name": discord_name
            }
            users[user_id] = new_user_entry
            new_users.append(new_user_entry)
            added_count += 1
        else:
            # Detect changes for an existing user. Overwrite even when the API value is None
            # (= not fetched / deleted), i.e. trust the latest state on start.gg.
            existing = users[user_id]
            changed = False
            api_record = {
                "user_id": user_id,
                "player_id": player_id,
                "gamer_tag": gamer_tag,
                "prefix": prefix,
                "gender_pronoun": gender_pronoun,
                "startgg_discriminator": startgg_discriminator,
                "country": country,
                "addr_state": addr_state,
                "city": city,
                "x_id": x_id,
                "x_name": x_name,
                "discord_id": discord_id,
                "discord_name": discord_name,
            }
            for k, v in api_record.items():
                if existing.get(k) != v:
                    existing[k] = v
                    changed = True
            if changed:
                updated_count += 1
                if dirty_user_ids is not None:
                    dirty_user_ids.add(user_id)

    if new_users:
        extend_jsonl(new_users, users_file_path, with_version=True)
        print(f"Extended user info: Added {added_count} new users to {users_file_path}.")
    else:
        print("No new users to add.")
    if updated_count:
        print(f"Extended user info: Updated {updated_count} existing users in memory.")


def _flush_dirty_users(users, dirty_user_ids, users_file_path):
    """Rewrite the whole users.jsonl if any existing user changed.

    New users were already appended via extend_jsonl, so here we rewrite everything with write_jsonl.
    """
    if not dirty_user_ids:
        return
    print(f"[users] {len(dirty_user_ids)} existing users updated, rewriting {users_file_path}", flush=True)
    write_jsonl(list(users.values()), users_file_path, with_version=True)

def extend_tournament_info(new_tournament_info, tournament_file_path):
    """Append new tournament information to tournaments.jsonl"""
    # tournaments.jsonl is append-only, so simply append
    extend_jsonl([new_tournament_info], tournament_file_path, with_version=True)
    print(f"Extended tournament info for tournament ID {new_tournament_info.get('tournament_id')} to {tournament_file_path}.")


def fetch_phase_id(event_id):
    """Get the first phase ID for an event ID"""
    page = 1
    per_page = 10 # events usually have few phases, so 10 is plenty
    # Phase ID fetching is retried
    try:
        response_data = fetch_data_with_retries(
            get_phase_groups_query(),
            {"eventId": event_id, "page": page, "perPage": per_page}
        )
    except FetchError as e:
        # All retries inside fetch_data_with_retries failed
        print(f"Failed to fetch phase groups for event {event_id} after retries: {e}")
        raise # re-raise so the caller handles it

    # Validate the response data
    if (not response_data or "data" not in response_data or
        response_data["data"] is None or "event" not in response_data["data"] or
        response_data["data"]["event"] is None):
        raise FetchError(f"Invalid response structure when fetching phases for event {event_id}. Response: {response_data}")

    event_data = response_data["data"]["event"]

    # Check that phases exists, is non-empty, and the first element has an id
    if event_data.get("phases") and isinstance(event_data["phases"], list) and len(event_data["phases"]) > 0 and event_data["phases"][0].get("id"):
        return event_data["phases"][0]["id"]
    else:
        # Raise NoPhaseError when no phases are found
        raise NoPhaseError(f"No phases found for event {event_id}. Response data: {response_data}")


def write_done_event(event_id, file_path):
    """Append a processed event ID to the file"""
    try:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"{event_id}\n")
            f.flush() # flush the buffer so it is written immediately
    except IOError as e:
        print(f"Error writing to done events file {file_path}: {e}", file=sys.stderr)

# --- Newly added functions ---

# ── Target specification (start.gg URL or slug) ──
_SPEC_RE = re.compile(r'^(?:https?://)?(?:www\.)?(?:start\.gg|smash\.gg)?/?(?:tournament/)?([^/?#\s]+)(?:/event/([^/?#\s]+))?', re.IGNORECASE)


def parse_event_spec(spec: str):
    """--event value -> (tournament_slug, event_slug | None).

    Accepted forms (all refer to the same tournament/event):
      genesis-x2/ultimate-singles
      tournament/genesis-x2/event/ultimate-singles
      https://www.start.gg/tournament/genesis-x2/event/ultimate-singles/standings?page=2
      https://www.start.gg/tournament/genesis-x2/events        (no event = all events of the target game in the tournament)
      https://www.start.gg/tournament/genesis-x2
    """
    s = spec.strip()
    m = _SPEC_RE.match(s)
    if not m or not m.group(1):
        raise ValueError(f"cannot parse tournament: {spec!r}")
    t_slug, e_slug = m.group(1), m.group(2)
    if e_slug is None and '/' in s and not re.search(r'^(?:https?://|www\.|start\.gg|smash\.gg|tournament/)', s, re.IGNORECASE):
        # short form "genesis-x2/ultimate-singles"
        t_slug, e_slug = s.split('/', 1)
        e_slug = e_slug.split('/')[0] or None
    if t_slug in ('tournament', 'event', 'events', 'details', 'attendees', 'overview', 'standings', 'brackets') \
            or not re.fullmatch(r'[A-Za-z0-9_-]+', t_slug) or (e_slug and not re.fullmatch(r'[A-Za-z0-9_-]+', e_slug)):
        raise ValueError(f"tournament slug missing / cannot parse: {spec!r}")
    return t_slug, e_slug


def list_tournament_events(tournament_slug, game_id):
    """List event slugs of the given game in a tournament (for URLs without an event)."""
    resp = fetch_data_with_retries(get_tournament_event_list_query(), {"tournamentSlug": tournament_slug, "gameId": int(game_id)})
    t = ((resp or {}).get("data") or {}).get("tournament")
    if not t:
        raise FetchError(f"tournament not found: {tournament_slug}")
    out = []
    for ev in t.get("events") or []:
        slug = (ev.get("slug") or "").rsplit("/", 1)[-1]
        if slug:
            out.append(slug)
    return out


def fetch_event_details_by_slug(tournament_slug, event_slug):
    """Fetch event details from the tournament and event slugs"""
    query = get_event_details_by_tournament_query()
    variables = {"tournamentSlug": tournament_slug, "eventSlug": event_slug}
    try:
        response_data = fetch_data_with_retries(query, variables)
    except FetchError as e:
        print(f"Error fetching event details for {tournament_slug}/{event_slug}: {e}")
        return None # return None on error

    # Validate the response data
    if (not response_data or "data" not in response_data or
        response_data["data"] is None or "tournament" not in response_data["data"] or
        response_data["data"]["tournament"] is None):
        print(f"Invalid response structure for event details: {tournament_slug}/{event_slug}. Response: {response_data}")
        return None
    
    tournament_data = response_data["data"]["tournament"]
    
    # Check the events array
    if not tournament_data.get("events") or not tournament_data["events"]:
        print(f"No matching events found for {tournament_slug}/{event_slug}.")
        return None
    
    # Use the first event (there should be only one if the filter is set correctly)
    event_data = tournament_data["events"][0]
    
    # Basic check that the required information is present
    if not all(k in event_data for k in ["id", "name", "isOnline", "startAt"]):
         print(f"Missing essential keys in event data for {tournament_slug}/{event_slug}.")
         return None
    
    # Merge tournament and event information
    merged_data = {
        **event_data,
        "tournament": {
            "id": tournament_data.get("id"),
            "name": tournament_data.get("name"),
            "slug": tournament_data.get("slug"),
            "endAt": tournament_data.get("endAt"),   # end_timestamp in attr.json (used by holiday detection and the refetch window)
            "url": tournament_data.get("url"),
            "countryCode": tournament_data.get("countryCode"),
            "city": tournament_data.get("city"),
            "lat": tournament_data.get("lat"),
            "lng": tournament_data.get("lng"),
            "venueName": tournament_data.get("venueName"),
            "timezone": tournament_data.get("timezone"),
            "postalCode": tournament_data.get("postalCode"),
            "venueAddress": tournament_data.get("venueAddress"),
            "mapsPlaceId": tournament_data.get("mapsPlaceId")
        }
    }

    return merged_data # return the merged data


def download_specific_event(tournament_slug, event_slug, startgg_dir, done_file_path, users_file_path, tournament_file_path, users, tournaments, done_events, dirty_user_ids=None):
    """Download and save the data of a single specified event"""
    print(f"--- Processing event: {tournament_slug} / {event_slug} ---")

    # 1. Fetch event details
    event_data = fetch_event_details_by_slug(tournament_slug, event_slug)
    if not event_data:
        print(f"Could not fetch details for event {tournament_slug}/{event_slug}. Skipping.")
        return False # failure

    # 2. Extract the required information
    try:
        event_id = event_data["id"]
        event_name = event_data["name"]
        is_online = event_data["isOnline"]
        timestamp = event_data["startAt"] # start time
        tournament_info = event_data["tournament"]
        tournament_id = tournament_info["id"]
        tournament_name = tournament_info["name"]
        tournament_url = tournament_info["url"] # URL of the whole tournament

        # Location info (may be absent, so use .get())
        place = {
            "country_code": tournament_info.get("countryCode"),
            "city": tournament_info.get("city"),
            "lat": tournament_info.get("lat"),
            "lng": tournament_info.get("lng"),
            "venue_name": tournament_info.get("venueName"),
            "timezone": tournament_info.get("timezone"),
            "postal_code": tournament_info.get("postalCode"),
            "venue_address": tournament_info.get("venueAddress"),
            "maps_place_id": tournament_info.get("mapsPlaceId")
        }
        country_code = place.get("country_code", "") # used to build the directory path

    except KeyError as e:
        print(f"Missing expected key in event data for {tournament_slug}/{event_slug}: {e}. Skipping.")
        return False # failure

    # 4. Determine the directory for the event data
    year, month, day = get_date_parts(timestamp)
    event_dir = get_event_directory(startgg_dir, country_code, year, month, day, tournament_name, event_name)
    print(f"Data will be saved to: {event_dir}")
    os.makedirs(event_dir, exist_ok=True) # create the directory

    # 3. Check whether already processed (re-download if files are missing)
    if event_id in done_events and event_files_complete(event_dir):
        print(f"Event ID {event_id} ({tournament_name} - {event_name}) already processed. Skipping.")
        return True # already processed, treat as success
    if event_id in done_events:
        print(f"Event ID {event_id} is marked done but files are missing. Re-downloading.")

    # 5. Download and save the various data
    try:
        # 5a. Standings (also yields user info and the entrant->user mapping)
        user_data, player_data, entrant2user = download_standings(event_id, event_dir)
        if not entrant2user: # an empty entrant2user may make the following steps difficult
            print(f"Warning: No entrant-to-user mapping created for event {event_id}. Subsequent data might be incomplete.")
            # Whether to abort here depends on requirements
            # return False # to abort

        num_entrants = len(user_data) # number of entrants
        print(f"Found {num_entrants} entrants for event {event_id}.")

        # 5b. Seeds (run after standings; may update user info)
        download_seeds(event_id, user_data, player_data, entrant2user, event_dir)

        # 5c. Update user info (including users added from seeds)
        extend_user_info(user_data, player_data, users, users_file_path, dirty_user_ids=dirty_user_ids)

        # 5d. All sets (match results)
        download_all_set(event_id, entrant2user, event_dir)

        # 5e. Event attributes

        write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, tournament_url, is_online, event_dir,
                               end_timestamp=tournament_info.get("endAt"))

        # 6. Update tournaments.jsonl
        # Add the tournament if not recorded yet; otherwise add the event to it
        if tournament_id not in tournaments:
            tournaments[tournament_id] = {
                "tournament_id": tournament_id,
                "name": tournament_name,
                "events": []
            }
            # Append to the file as a new tournament
            tournament_entry = tournaments[tournament_id].copy() # make a copy
            tournament_entry["events"].append({
                 "event_id": event_id,
                 "event_name": event_name,
                 "path": event_dir
            })
            extend_tournament_info(tournament_entry, tournament_file_path)
        else:
            # Add the event to an existing tournament (not appended to the file here, since that requires rewriting the whole file)
            # extend_tournament_info from the original script only appends, so
            # adding an event to an existing tournament would require read -> update -> rewrite of the file.
            # For simplicity, only new tournaments are written to the file here.
            # Events added to existing tournaments are reflected in the in-memory dict.
            # If needed, add a step that writes the whole tournaments dict to the file after all events are processed.
            tournaments[tournament_id]["events"].append({
                 "event_id": event_id,
                 "event_name": event_name,
                 "path": event_dir
            })
            print(f"Event {event_id} added to existing tournament {tournament_id} in memory.")
            # Note: this change is not immediately reflected in tournaments.jsonl.

        # 7. Add to the done list and save
        done_events.add(event_id)
        write_done_event(event_id, done_file_path)
        print(f"--- Successfully processed event: {tournament_slug} / {event_slug} (ID: {event_id}) ---")
        return True # success

    except (FetchError, NoPhaseError) as e:
        print(f"An error occurred processing event {event_id}: {e}", file=sys.stderr)
        return False # failure
    except Exception as e: # unexpected error
        print(f"An unexpected error occurred processing event {event_id}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False # failure


# --- Main ---
def main():
    # Command-line arguments (adapted from the original script)
    parser = argparse.ArgumentParser(description="Download specific tournament event data from start.gg")
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha", help="API URL")
    parser.add_argument("--token", default=os.environ.get("STARTGG_TOKEN"),
                        help="start.gg API token (defaults to the STARTGG_TOKEN environment variable)")
    # finish_date is not needed for specific-event downloads; keep or remove depending on other uses
    # parser.add_argument("--finish-date", type=lambda s: datetime.strptime(s, '%Y-%m-%d'), default=datetime(2018, 1, 1), help="Finish date (not used for specific download)")
    parser.add_argument("--max-retries", type=int, default=10, help="Maximum number of retries for API requests") # slightly lower default
    parser.add_argument("--retry-delay", type=int, default=5, help="Delay between retries in seconds")
    parser.add_argument("--indent-num", type=int, default=2, help="Indentation level for JSON output")
    parser.add_argument("--startgg-dir", default="data/startgg", help="Data root (events are saved under <root>/<region>/events/...)")
    # The done list is per event
    parser.add_argument("--done-file-path", default=None, help="Path to the file recording completed event downloads")
    parser.add_argument("--users-file-path", default=None, help="Path to the file recording startgg user info")
    parser.add_argument("--tournament-file-path", default=None, help="Path to the file recording tournament info")
    # game_id and country_code are not directly needed for specific-event downloads
    # parser.add_argument("--game-id", default="1386", help="Game ID (not used for specific download)")
    # parser.add_argument("--country-code", default="", help="Country code (not used for specific download)")
    from scripts.common._cli import add_region_arg, resolve_index_paths
    parser.add_argument("--event", action="append", default=[], metavar="URL_OR_SLUG", required=True,
                        help="Event to import (repeatable). A start.gg URL (any tab of the tournament page) or "
                             "tournament-slug/event-slug. A URL without an event imports all events of the target game in the tournament")
    parser.add_argument("--game-id", default="1386", help="Game whose events are listed when the URL has no event (default 1386 = Smash Ultimate)")
    add_region_arg(parser)
    args = parser.parse_args()
    resolve_index_paths(parser, args, done_file_path="done_events.csv", users_file_path="users.jsonl", tournament_file_path="tournaments.jsonl")

    # Apply settings
    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    # Load existing data
    # Initialize with empty data if absent
    if not os.path.exists(os.path.dirname(args.done_file_path)):
        os.makedirs(os.path.dirname(args.done_file_path), exist_ok=True)
    if not os.path.exists(os.path.dirname(args.users_file_path)):
        os.makedirs(os.path.dirname(args.users_file_path), exist_ok=True)
    if not os.path.exists(os.path.dirname(args.tournament_file_path)):
        os.makedirs(os.path.dirname(args.tournament_file_path), exist_ok=True)

    done_events = read_set(args.done_file_path, as_int=True)
    users = read_users_jsonl(args.users_file_path)
    tournaments = read_tournaments_jsonl(args.tournament_file_path) # dict keyed by tournament_id
    print(f"Loaded {len(done_events)} completed event IDs.")
    print(f"Loaded {len(users)} users.")
    print(f"Loaded {len(tournaments)} tournaments.")

    # Download targets (--event: start.gg URL or slug; without an event, all events of the target game in the tournament)
    target_events = []
    for spec in args.event:
        try:
            t_slug, e_slug = parse_event_spec(spec)
        except ValueError as e:
            parser.error(str(e))
        if e_slug:
            target_events.append((t_slug, e_slug))
        else:
            slugs = list_tournament_events(t_slug, args.game_id)
            print(f"{t_slug}: {len(slugs)} events of the target game {slugs}")
            target_events.extend((t_slug, e) for e in slugs)
    if not target_events:
        parser.error("no events to import")

    # Process each event
    success_count = 0
    fail_count = 0
    dirty_user_ids = set()
    for t_slug, e_slug in target_events:
        success = download_specific_event(
            t_slug, e_slug,
            args.startgg_dir, args.done_file_path, args.users_file_path, args.tournament_file_path,
            users, tournaments, done_events,
            dirty_user_ids=dirty_user_ids,
        )
        if success:
            success_count += 1
        else:
            fail_count += 1

    _flush_dirty_users(users, dirty_user_ids, args.users_file_path)

    print("\n--- Download Summary ---")
    print(f"Successfully processed: {success_count} events")
    print(f"Failed or skipped: {fail_count} events")
    # Note: tournaments.jsonl is only appended to for new tournaments.
    # Events added to existing tournaments happen in memory only and are not written to the file.
    # If needed, add a final step that overwrites the file with the whole tournaments dict.
    # e.g. provide a helper in utils such as write_jsonl(tournaments.values(), args.tournament_file_path, with_version=True).

if __name__ == "__main__":
    main()
