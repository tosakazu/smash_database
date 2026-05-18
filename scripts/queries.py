from datetime import datetime

def get_event_sets_slim_query():
    """Slim version of get_event_sets_query: drops `games` (stage/character selections) to
    reduce query complexity ~10x. Use for matches.json re-fetch when only winner/loser/phase/round
    are needed (SPSP build does not use games data).
    """
    return """query EventSetsSlim($eventId: ID!, $page: Int!, $perPage: Int!) {
      event(id: $eventId) {
        id
        sets(page: $page, perPage: $perPage, sortType: STANDARD) {
          nodes {
            id
            state
            round
            fullRoundText
            phaseGroup {
              id
              displayIdentifier
            }
            slots {
              entrant {
                participants { user { id } }
              }
              standing {
                stats { score { value } }
              }
            }
          }
        }
      }
    }"""


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
    # videogameId フィルタを外し、イベントの videogame.id を取得して
    # download.py 側で「SSBU タグ or 下位クラス bracket 名」を判定する.
    # (start.gg では Bクラス side event を videogameId 未設定で登録するケースがあるため)
    # $gameId は呼び出し側互換のため受け取るが、クエリ内では未使用.
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
    """Event の phases 一覧 (phase_groups 含む、メタ情報付き). 各 phase の num_seeds, bracket_type, name を取得.
    各 phase_group の id, displayIdentifier, wave も含める."""
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
    """Phase group 内の sets を取得 (games フィールド除外で complexity 抑制).
    page/perPage 指定可能. 224 sets × 100 perPage で complexity 1000以下に収まる.
    games (character/stage 選択履歴) は ranking 計算で不要なため除外."""
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


def get_phase_group_sets_minimal_query():
    """Phase group の sets を最小限のフィールドで取得 (DQ filter 用).
    player_ids per set: slots[].entrant.participants[].user.id のみ. 軽量で complexity throttling 回避.
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
    """Phase group の standings を取得 (placement / user_id / 名前).
    Phase group ごとの sub-bracket placement. 複数 phase_groups を持つ phase の場合、
    各 group の standings を別々に取得して合算する必要がある.
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
    """Event の phase メタ (name / order / bracketType / phaseGroups の displayIdentifier).
    クラス phase (B-class etc) 検出と placement clip 用.
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

def get_event_details_by_tournament_query():
    """トーナメントスラッグからイベント詳細を取得するGraphQLクエリ"""
    return """
    query TournamentEventsQuery($tournamentSlug: String!, $eventSlug: String!) {
      tournament(slug: $tournamentSlug) {
        id
        name
        slug
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
