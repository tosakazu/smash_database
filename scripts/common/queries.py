from scripts.common import clock


def get_event_sets_query():
    return """query EventSets($eventId: ID!, $page: Int!, $perPage: Int!) {
      event(id: $eventId) {
        id
        name
        sets(
          page: $page
          perPage: $perPage
          sortType: STANDARD
        ) {
          pageInfo { total totalPages }
          nodes {
            id
            state
            winnerId
            round
            fullRoundText
            startedAt
            completedAt
            phaseGroup {
              id
              displayIdentifier
              startAt
              wave {
                id
                identifier
                startAt
              }
            }
            slots {
              id
              entrant {
                id
                participants {
                  user {
                    id
                  }
                }
              }
              standing {
                stats {
                  score {
                    label
                    value
                  }
                }
              }
            }
            games {
              id
              orderNum
              winnerId
              entrant1Score
              entrant2Score
              stage {
                id
                name
              }
              selections {
                id
                entrant {
                  id
                  participants {
                    user {
                      id
                    }
                  }
                }
                character {
                  id
                  name
                }
              }
            }
          }
        }
      }
    }"""

def get_standings_query():
    return """query EventStandings($eventId: ID!, $page: Int!, $perPage: Int!) {
      event(id: $eventId) {
        standings(query: {page: $page, perPage: $perPage}) {
          pageInfo { total totalPages }
          nodes {
            placement
            entrant {
              id
              name
              participants {
                user {
                  id
                  genderPronoun
                  discriminator
                  location {
                    country
                    state
                    city
                  }
                  authorizations(types: [TWITTER, DISCORD]) {
                    externalId
                    externalUsername
                    type
                  }
                }
                player {
                  id
                  gamerTag
                  prefix
                }
              }
            }
          }
        }
      }
    }"""

def get_seeds_query():
    return """query PhaseSeeds($phaseId: ID!, $page: Int!, $perPage: Int!) {
      phase(id: $phaseId) {
        id
        seeds(query: {
          page: $page
          perPage: $perPage
        }) {
          pageInfo {
            total
            totalPages
          }
          nodes {
            id
            seedNum
            entrant {
              id
              participants {
                user {
                  id
                  genderPronoun
                  discriminator
                  location {
                    country
                    state
                    city
                  }
                  authorizations(types: [TWITTER, DISCORD]) {
                    externalId
                    externalUsername
                    type
                  }
                }
                player {
                  id
                  gamerTag
                  prefix
                }
              }
            }
          }
        }
      }
    }"""

def get_user_query():
    return """query UserDetails($userId: ID!) {
      user(id: $userId) {
        id
        genderPronoun
        discriminator
        location {
          country
          state
          city
        }
        authorizations(types: [TWITTER, DISCORD]) {
          externalId
          externalUsername
          type
        }
      }
    }"""

def get_user_player_query():
    return """query UserAndPlayer($userId: ID!, $playerId: ID!) {
      user(id: $userId) {
        id
        genderPronoun
        discriminator
        location {
          country
          state
          city
        }
        authorizations(types: [TWITTER, DISCORD]) {
          externalId
          externalUsername
          type
        }
      }
      player(id: $playerId) {
        id
        gamerTag
        prefix
      }
    }"""

def get_tournament_events_query():
    # Drop the videogameId filter and fetch each event's videogame.id so that
    # download.py decides "SSBU tag or lower-class bracket name".
    # (On start.gg, B-class side events are sometimes registered without videogameId.)
    # $gameId is accepted for caller compatibility but unused in the query.
    return """query TournamentEvents($tournamentId: ID!) {
      tournament(id: $tournamentId) {
        id
        name
        events {
          id
          name
          startAt
          isOnline
          videogame {
            id
          }
        }
      }
    }"""

def get_phase_groups_query():
    return """query PhaseGroupsByEvent($eventId: ID!, $page: Int!, $perPage: Int!) {
      event(id: $eventId) {
        phases {
          id
          phaseGroups(query: {page: $page, perPage: $perPage}) {
            pageInfo {
              total
            }
            nodes {
              id
              displayIdentifier
            }
          }
        }
      }
    }"""


def get_event_phases_full_query():
    """List the event's phases (including phase_groups, with metadata). Fetches num_seeds, bracket_type, name of each phase,
    plus id, displayIdentifier, wave of each phase_group."""
    return """query EventPhasesFull($eventId: ID!) {
      event(id: $eventId) {
        id
        phases {
          id
          name
          numSeeds
          bracketType
          phaseOrder
          phaseGroups(query: {page: 1, perPage: 500}) {
            nodes {
              id
              displayIdentifier
              startAt
              wave {
                id
                identifier
                startAt
              }
            }
          }
        }
      }
    }"""


def get_phase_group_sets_full_query():
    """Fetch the sets in a phase group (games field excluded to keep complexity down).
    page/perPage selectable. 224 sets x 100 perPage stays under complexity 1000.
    games (character/stage selection history) is not needed for ranking, so it is excluded."""
    return """query PhaseGroupSetsFull($phaseGroupId: ID!, $page: Int!, $perPage: Int!) {
      phaseGroup(id: $phaseGroupId) {
        id
        sets(page: $page, perPage: $perPage, sortType: STANDARD) {
          pageInfo { total totalPages }
          nodes {
            id
            state
            winnerId
            round
            fullRoundText
            startedAt
            completedAt
            slots {
              id
              entrant {
                id
                participants {
                  user { id }
                }
              }
              standing {
                stats {
                  score { label value }
                }
              }
            }
          }
        }
      }
    }"""


def get_event_sets_full_query():
    """Fetch sets directly under the event with full fields (= workaround for the silent-partial bug where
    phase_group iteration returns empty responses under throttling. event.sets is low-complexity and robustly returns everything).
    Each set includes phaseGroup / phase info so tuples for write_matches_v2 can be built."""
    return """query EventSetsFull($eventId: ID!, $page: Int!, $perPage: Int!) {
      event(id: $eventId) {
        id
        sets(page: $page, perPage: $perPage, sortType: STANDARD) {
          pageInfo { total totalPages }
          nodes {
            id
            state
            winnerId
            round
            fullRoundText
            startedAt
            completedAt
            slots {
              id
              entrant {
                id
                participants { user { id } }
              }
              standing {
                stats {
                  score { label value }
                }
              }
            }
            phaseGroup {
              id
              displayIdentifier
              startAt
              wave { id identifier startAt }
              phase { id name numSeeds bracketType phaseOrder }
            }
          }
        }
      }
    }"""


def get_phase_group_sets_with_games_query():
    """Fetch the sets of a phase group with games (character/stage selection history).
    Combined complexity is high, so call with perPage kept to 5-10.
    For generating the sidecar character_games.json."""
    return """query PhaseGroupSetsWithGames($phaseGroupId: ID!, $page: Int!, $perPage: Int!) {
      phaseGroup(id: $phaseGroupId) {
        id
        sets(page: $page, perPage: $perPage, sortType: STANDARD) {
          pageInfo { total totalPages }
          nodes {
            id
            state
            winnerId
            round
            fullRoundText
            startedAt
            completedAt
            slots {
              id
              entrant {
                id
                participants { user { id } }
              }
            }
            games {
              id
              orderNum
              winnerId
              entrant1Score
              entrant2Score
              stage { id name }
              selections {
                id
                entrant {
                  id
                  participants { user { id } }
                }
                character { id name }
              }
            }
          }
        }
      }
    }"""


def get_phase_group_sets_full_with_games_query():
    """Fetch the sets of a phase group with both scores and games (character/stage selection history).

    Merges get_phase_group_sets_full_query (scores, no games) and
    get_phase_group_sets_with_games_query (games, no scores).
    Match results (standing.stats.score) and character details come in one pass, so
    the periodic update no longer has to hit the API twice for download and character fetch.

    Complexity is high because games are included. Keep perPage around 4-8
    (the with_games=True path of fetch_phase_group_sets clamps and backs off automatically)."""
    return """query PhaseGroupSetsFullWithGames($phaseGroupId: ID!, $page: Int!, $perPage: Int!) {
      phaseGroup(id: $phaseGroupId) {
        id
        sets(page: $page, perPage: $perPage, sortType: STANDARD) {
          pageInfo { total totalPages }
          nodes {
            id
            state
            winnerId
            round
            fullRoundText
            startedAt
            completedAt
            slots {
              id
              entrant {
                id
                participants {
                  user { id }
                }
              }
              standing {
                stats {
                  score { label value }
                }
              }
            }
            games {
              id
              orderNum
              winnerId
              entrant1Score
              entrant2Score
              stage { id name }
              selections {
                id
                entrant {
                  id
                  participants { user { id } }
                }
                character { id name }
              }
            }
          }
        }
      }
    }"""


def get_phase_group_sets_minimal_query():
    """Fetch the sets of a phase group with minimal fields (for the DQ filter).
    player_ids per set: only slots[].entrant.participants[].user.id. Lightweight; avoids complexity throttling.
    """
    return """query PhaseGroupSetsMinimal($phaseGroupId: ID!, $page: Int!, $perPage: Int!) {
      phaseGroup(id: $phaseGroupId) {
        id
        sets(page: $page, perPage: $perPage, sortType: STANDARD) {
          pageInfo { total totalPages }
          nodes {
            id
            state
            slots {
              standing {
                stats {
                  score { value }
                }
              }
              entrant {
                participants { user { id } }
              }
            }
          }
        }
      }
    }"""


def get_phase_group_standings_query():
    """Fetch the standings of a phase group (placement / user_id / name).
    Per-phase-group sub-bracket placement. For a phase with multiple phase_groups,
    each group's standings must be fetched separately and combined.
    """
    return """query PhaseGroupStandings($phaseGroupId: ID!, $page: Int!, $perPage: Int!) {
      phaseGroup(id: $phaseGroupId) {
        id
        displayIdentifier
        standings(query: {page: $page, perPage: $perPage}) {
          pageInfo { total totalPages }
          nodes {
            placement
            entrant {
              id
              name
              participants { user { id } }
            }
          }
        }
      }
    }"""


def get_event_phases_named_query():
    """Event phase metadata (name / order / bracketType / displayIdentifier of phaseGroups).
    For detecting class phases (B-class etc.) and placement clipping.
    """
    return """query EventPhasesNamed($eventId: ID!) {
      event(id: $eventId) {
        id
        name
        phases {
          id
          name
          phaseOrder
          bracketType
          numSeeds
          phaseGroups(query: {page: 1, perPage: 500}) {
            nodes {
              id
              displayIdentifier
            }
          }
        }
      }
    }"""

def get_tournaments_by_game_query(country_code="", before_now=True, past=False):
    first_row = """query TournamentsByGame($gameId: ID!, $perPage: Int!, $page: Int!) {"""
    # sortBy uses endAt: even when a TO sets startAt to the announcement/registration date (e.g. Funasuma
    # held 2026-07-19 = startAt 6/8), endAt is almost always set to the actual date
    # (start.gg uses endAt to decide "finished", so moving it far earlier would break the organizer's setup).
    # Listing by startAt would push such tournaments out of the fetch window by the time they run, so they would never be fetched (found 2026-07-21).
    second_row = """tournaments(query: {perPage: $perPage, page: $page, sortBy: "endAt desc", filter: {videogameIds: [$gameId], published: true *other_filters*}}) {"""
    nodes_query = """nodes {
            id
            name
            state
            startAt
            endAt
            countryCode
            isOnline
            addrState
            city
            countryCode
            lat
            lng
            mapsPlaceId
            postalCode
            venueAddress
            venueName
            timezone
            url
          }
          pageInfo {
            totalPages
          }
        }
      }"""
    
    filters = ""
    if country_code:
      filters += f' ,countryCode: "{country_code}" '
    if past:
      filters += """ ,past: true """
    if before_now:
      filters += f" ,beforeDate: {clock.now_ts()} "   # "now" comes from scripts.common.clock (can be pinned in tests)
    
    second_row = second_row.replace("*other_filters*", filters)

    query = "\n".join([first_row, second_row, nodes_query])
    return query


# Used by manual tools (download_specific_event / fix/backfill_events). Was mistakenly removed as "unused" (restored 2026-09-07)
def get_event_details_by_tournament_query():
    """GraphQL query fetching event details from a tournament slug"""
    return """
    query TournamentEventsQuery($tournamentSlug: String!, $eventSlug: String!) {
      tournament(slug: $tournamentSlug) {
        id
        name
        slug
        endAt
        countryCode
        city
        lat
        lng
        venueName
        timezone
        postalCode
        venueAddress
        mapsPlaceId
        url
        events(filter: {slug: $eventSlug}) {
          id
          name
          slug
          startAt
          isOnline
          numEntrants
          state
        }
      }
    }
    """


def get_event_details_by_id_query():
    return """query EventById($eventId: ID!) {
      event(id: $eventId) {
        id
        name
        slug
        startAt
        numEntrants
        isOnline
        state
        tournament {
          id
          name
          slug
          startAt
          endAt
          countryCode
          city
          lat
          lng
          venueName
          timezone
          postalCode
          venueAddress
          mapsPlaceId
          url
        }
      }
    }"""


def get_tournament_event_list_query():
    """List the events (id / name / slug) of the given game from a tournament slug (for URL input in download_specific_event)."""
    return """
    query TournamentEventListQuery($tournamentSlug: String!, $gameId: ID!) {
      tournament(slug: $tournamentSlug) {
        id
        name
        events(filter: {videogameId: [$gameId]}) {
          id
          name
          slug
        }
      }
    }
    """
