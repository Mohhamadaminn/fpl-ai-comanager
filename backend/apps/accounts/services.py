import requests
from apps.fpl_data.models import Player

FPL_BASE_URL = "https://fantasy.premierleague.com/api"


def get_current_squad(fpl_team_id: int, gameweek_fpl_id: int):
    """Returns the list of Player objects currently in the manager's squad for a gameweek."""
    response = requests.get(f"{FPL_BASE_URL}/entry/{fpl_team_id}/event/{gameweek_fpl_id}/picks/")
    response.raise_for_status()
    data = response.json()

    player_fpl_ids = [pick["element"] for pick in data["picks"]]
    return list(Player.objects.filter(fpl_id__in=player_fpl_ids))


def get_manager_gameweek_state(fpl_team_id: int, gameweek_fpl_id: int):
    response = requests.get(f"{FPL_BASE_URL}/entry/{fpl_team_id}/event/{gameweek_fpl_id}/picks/")
    response.raise_for_status()
    data = response.json()

    picks = data["picks"]

    player_fpl_ids = [pick["element"] for pick in picks]
    squad = list(Player.objects.filter(fpl_id__in=player_fpl_ids))

    captain_fpl_id = next((p["element"] for p in data["picks"] if p["is_captain"]), None)
    vice_captain_fpl_id = next((p["element"] for p in data["picks"] if p["is_vice_captain"]), None)

    positions = {
        pick["element"]: pick["position"]
        for pick in picks
    }

    return {
        "squad": squad,
        "bank": data["entry_history"]["bank"] / 10,
        "transfers_made": data["entry_history"].get("event_transfers", 0),
        "chip_active": data.get("active_chip"),
        "captain_fpl_id": captain_fpl_id,
        "vice_captain_fpl_id": vice_captain_fpl_id,
        "positions": positions,
    }

def sync_free_transfers(profile):
    from apps.fpl_data.models import Gameweek

    last_finished = (
        Gameweek.objects.filter(finished=True).order_by("-fpl_id").first()
    )
    if not last_finished:
        return profile.free_transfers

    if profile.last_synced_gameweek_id == last_finished.id:
        return profile.free_transfers

    state = get_manager_gameweek_state(profile.fpl_team_id, last_finished.fpl_id)
    transfers_made = state["transfers_made"]  # fixed key name

    new_free = min(5, max(0, profile.free_transfers - transfers_made) + 1)

    profile.free_transfers = new_free
    profile.last_synced_gameweek = last_finished
    profile.save(update_fields=["free_transfers", "last_synced_gameweek"])

    return new_free