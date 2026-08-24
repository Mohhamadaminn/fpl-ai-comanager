import requests
from .models import Team, Player, Gameweek, Fixture, PlayerGameweekStat

FPL_BASE_URL = "https://fantasy.premierleague.com/api"


def sync_bootstrap_data():
    response = requests.get(f"{FPL_BASE_URL}/bootstrap-static/")
    response.raise_for_status()
    data = response.json()

    for team_data in data["teams"]:
        Team.objects.update_or_create(
            fpl_id=team_data["id"],
            defaults={
                "name": team_data["name"],
                "short_name": team_data["short_name"],
                "strength_overall_home": team_data.get("strength_overall_home"),
                "strength_overall_away": team_data.get("strength_overall_away"),
                "strength_attack_home": team_data.get("strength_attack_home"),
                "strength_attack_away": team_data.get("strength_attack_away"),
                "strength_defence_home": team_data.get("strength_defence_home"),
                "strength_defence_away": team_data.get("strength_defence_away"),
            },
        )

    for event_data in data["events"]:
        Gameweek.objects.update_or_create(
            fpl_id=event_data["id"],
            defaults={
                "name": event_data["name"],
                "deadline_time": event_data["deadline_time"],
                "is_current": event_data["is_current"],
                "is_next": event_data["is_next"],
                "finished": event_data["finished"],
                "average_entry_score": event_data.get("average_entry_score"),
                "highest_score": event_data.get("highest_score"),
            },
        )

    for player_data in data["elements"]:
        team = Team.objects.get(fpl_id=player_data["team"])
        position = Player.Position.values[player_data["element_type"] - 1]

        Player.objects.update_or_create(
            fpl_id=player_data["id"],
            defaults={
                "first_name": player_data["first_name"],
                "second_name": player_data["second_name"],
                "web_name": player_data["web_name"],
                "team": team,
                "position": position,
                "price": player_data["now_cost"] / 10,
                "form": player_data["form"] or 0,
                "total_points": player_data["total_points"],
                "selected_by_percent": player_data["selected_by_percent"],
                "expected_goals": player_data.get("expected_goals") or 0,
                "expected_assists": player_data.get("expected_assists") or 0,
                "expected_goal_involvements": player_data.get("expected_goal_involvements") or 0,
                "ict_index": player_data.get("ict_index") or 0,
                "status": player_data["status"],
                "news": player_data.get("news", ""),
                "chance_of_playing_next_round": player_data.get("chance_of_playing_next_round"),
            },
        )


def sync_fixtures():
    response = requests.get(f"{FPL_BASE_URL}/fixtures/")
    response.raise_for_status()
    data = response.json()

    for fixture_data in data:
        gameweek = None
        if fixture_data.get("event"):
            gameweek = Gameweek.objects.filter(fpl_id=fixture_data["event"]).first()

        team_home = Team.objects.get(fpl_id=fixture_data["team_h"])
        team_away = Team.objects.get(fpl_id=fixture_data["team_a"])

        Fixture.objects.update_or_create(
            fpl_id=fixture_data["id"],
            defaults={
                "gameweek": gameweek,
                "team_home": team_home,
                "team_away": team_away,
                "difficulty_home": fixture_data.get("team_h_difficulty"),
                "difficulty_away": fixture_data.get("team_a_difficulty"),
                "kickoff_time": fixture_data.get("kickoff_time"),
                "finished": fixture_data.get("finished", False),
            },
        )


def sync_live_gameweek_stats(gameweek_fpl_id: int):
    response = requests.get(f"{FPL_BASE_URL}/event/{gameweek_fpl_id}/live/")
    response.raise_for_status()
    data = response.json()

    gameweek = Gameweek.objects.get(fpl_id=gameweek_fpl_id)

    for element in data["elements"]:
        player = Player.objects.filter(fpl_id=element["id"]).first()
        if not player:
            continue

        stats = element["stats"]
        PlayerGameweekStat.objects.update_or_create(
            player=player,
            gameweek=gameweek,
            defaults={
                "minutes": stats["minutes"],
                "goals_scored": stats["goals_scored"],
                "assists": stats["assists"],
                "clean_sheets": stats["clean_sheets"],
                "points": stats["total_points"],
                "expected_goals": stats.get("expected_goals") or 0,
                "expected_assists": stats.get("expected_assists") or 0,
                "expected_goal_involvements": stats.get("expected_goal_involvements") or 0,
                "bps": stats.get("bps", 0),
                "ict_index": stats.get("ict_index") or 0,
                "is_final": gameweek.finished,
            },
        )