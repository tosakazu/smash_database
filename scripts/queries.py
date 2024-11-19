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

def get_tournaments_by_game_query():
    return """query TournamentsByGame($gameId: ID!, $perPage: Int!, $page: Int!) {
      tournaments(query: {perPage: $perPage, page: $page, sortBy: "startAt desc", filter: {videogameIds: [$gameId], past: true, countryCode: "JP"}}) {
        nodes {
          id
          name
          startAt
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

