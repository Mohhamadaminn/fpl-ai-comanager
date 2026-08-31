import requests
from fpl_data.models import Player

FPL_BASE_URL = "https://fantasy.premierleague.com/api"


def get_current_squad(fpl_team_id: int, gameweek_fpl_id: int):
    """Returns the list of Player objects currently in the manager's squad for a gameweek."""
    response = requests.get(f"{FPL_BASE_URL}/entry/{fpl_team_id}/event/{gameweek_fpl_id}/picks/")
    response.raise_for_status()
    data = response.json()

    player_fpl_ids = [pick["element"] for pick in data["picks"]]
    return list(Player.objects.filter(fpl_id__in=player_fpl_ids))