import requests
import json
import time

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
          nodes {
            id
            state
            winnerId
            round
            fullRoundText
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
    return """query EventStandings($eventId: ID!) {
      event(id: $eventId) {
        standings(query: {perPage: 100}) {
          nodes {
            placement
            entrant {
              id
              name
              participants {
                user {
                  id
                  genderPronoun
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

def get_tournament_query():
    return """query TournamentInfo($tournamentId: ID!, $gameId: ID!) {
      tournament(id: $tournamentId) {
        id
        name
        isOnline
        events(filter: {videogameId: [$gameId]}) {
          id
          name
          startAt
        }
      }
    }""" 

def get_phase_groups_query():
    return """query PhaseGroupsByEvent($eventId: ID!) {
      event(id: $eventId) {
        phases {
          id
          phaseGroups(query: {page: 1, perPage: 1}) {
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

def get_tournaments_by_game_query():
    return """query TournamentsByGame($gameId: ID!, $perPage: Int!, $page: Int!) {
      tournaments(query: {perPage: $perPage, page: $page, sortBy: "startAt desc", filter: {videogameIds: [$gameId], past: true}}) {
        nodes {
          id
          name
          startAt
          countryCode
        }
        pageInfo {
          totalPages
        }
      }
    }"""

# グローバル変数の初期化
__max_retries = 100
__retry_delay = 5
__api_url = "https://api.start.gg/gql/alpha"
__headers = {}

def set_retry_parameters(max_retries, retry_delay):
    global __max_retries, __retry_delay
    __max_retries = max_retries
    __retry_delay = retry_delay

def set_api_parameters(url, token):
    global __api_url, __headers
    __api_url = url
    __headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }

def fetch_data_with_retries(query, variables):
    for attempt in range(__max_retries):
        try:
            response = requests.post(__api_url, json={"query": query, "variables": json.dumps(variables)}, headers=__headers)
            response.raise_for_status()  # HTTPエラーが発生した場合に例外を発生させる
            response_data = json.loads(response.text)
            return response_data
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            print(query)
            print(variables)
            print(f"Request or JSON parsing failed: {e}. Retrying {attempt + 1}/{__max_retries}...")
            time.sleep(__retry_delay)
    raise Exception("Max retries exceeded")

