from datetime import datetime

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
            phaseGroup {
              id
              displayIdentifier
              wave {
                id
                identifier
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

def get_tournament_events_query():
    return """query TournamentEvents($tournamentId: ID!, $gameId: ID!) {
      tournament(id: $tournamentId) {
        id
        name
        events(filter: {videogameId: [$gameId]}) {
          id
          name
          startAt
          isOnline
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

def get_tournaments_by_game_query(jp_only, before_now=True, past=False):
    first_row = """query TournamentsByGame($gameId: ID!, $perPage: Int!, $page: Int!) {"""
    second_row = """tournaments(query: {perPage: $perPage, page: $page, sortBy: "startAt desc", filter: {videogameIds: [$gameId], published: true, *other_filters*}}) {"""
    nodes_query = """nodes {
            id
            name
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
          }
          pageInfo {
            totalPages
          }
        }
      }"""
    
    filters = ""
    if jp_only:
      filters += """ ,countryCode: "JP" """
    if past:
      filters += """ ,past: true """
    if before_now:
      filters += f" ,beforeDate: {int(datetime.now().timestamp())} "
    
    second_row = second_row.replace("*other_filters*", filters)

    query = "\n".join([first_row, second_row, nodes_query])
    return query

def get_tournament_url_query():
    return """query Tournament($tournamentId: ID!) {
      tournament(id: $tournamentId) {
        url
      }
    }"""
