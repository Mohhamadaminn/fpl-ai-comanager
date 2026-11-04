"""Squad-health data for the weekly Telegram check.

Lives in the notifications app on purpose: the website knows nothing about the bot. The dependency
is one-way (this module reads from fpl_data, never the other way around).

Whether a player is "in decline" is NOT decided here: it comes from the website's own slump rule
(squad_insights.slump_stats), so the bot and the "Time to move on" card can never disagree.
This module only adds the position-specific numbers shown in the message.
"""
import logging

from apps.fpl_data.compare_engine import RECENT_APPEARANCES, _recent_window_ids, _target_gameweek
from apps.fpl_data.models import PlayerGameweekStat
from apps.fpl_data.squad_insights import WINDOW_MAX, WINDOW_MIN, slump_stats

logger = logging.getLogger(__name__)


def squad_form_context():
    """Built once per task run (not once per user). None when there is not enough data yet."""
    gameweek = _target_gameweek()
    if not gameweek:
        return None
    window_ids = _recent_window_ids(gameweek, WINDOW_MAX)
    if len(window_ids) < WINDOW_MIN:
        return None
    return {"gameweek": gameweek, "window_ids": window_ids}


def _last_games(player_ids, gameweek, n=RECENT_APPEARANCES):
    """{player_id: totals} over his last n games played (the same games the website's form uses)."""
    rows = (
        PlayerGameweekStat.objects.filter(
            is_final=True, gameweek__fpl_id__lt=gameweek.fpl_id,
            player_id__in=player_ids, minutes__gt=0,
        )
        .order_by("player_id", "-gameweek__fpl_id")
        .values_list(
            "player_id", "minutes", "points", "clean_sheets", "expected_goals_conceded",
            "bps", "defensive_contribution", "expected_goal_involvements",
        )
    )
    out = {}
    for pid, minutes, points, cs, xgc, bps, dc, xgi in rows.iterator():
        s = out.setdefault(pid, {"games": 0, "minutes": 0, "points": 0, "cs": 0,
                                 "xgc": 0.0, "bps": 0, "dc": 0, "xgi": 0.0})
        if s["games"] >= n:
            continue
        s["games"] += 1
        s["minutes"] += int(minutes or 0)
        s["points"] += int(points or 0)
        s["cs"] += int(cs or 0)
        s["xgc"] += float(xgc or 0)
        s["bps"] += int(bps or 0)
        s["dc"] += int(dc or 0)
        s["xgi"] += float(xgi or 0)
    return out


def squad_form_snapshot(players, ctx):
    """{player.id: {...}}: "slump" says whether the website's rule flags him; the rest are the
    last-5-games numbers for the message."""
    if not ctx:
        return {}
    try:
        slumps = slump_stats(players, ctx["gameweek"], ctx["window_ids"])
    except ValueError:  # not enough finished-gameweek data
        logger.warning("Slump check skipped", exc_info=True)
        slumps = {}

    last = _last_games([p.id for p in players], ctx["gameweek"])
    result = {}
    for p in players:
        s = last.get(p.id)
        if not s:
            continue
        minutes = s["minutes"]
        result[p.id] = {
            "slump": p.id in slumps,
            "games": s["games"],
            "points": s["points"],
            "clean_sheets": s["cs"],
            "xgc90": round(s["xgc"] / minutes * 90, 2) if minutes else 0.0,
            "bps": s["bps"],
            "dc_avg": round(s["dc"] / s["games"], 1),
            "xgi": round(s["xgi"], 2),
            "xgi90": round(s["xgi"] / minutes * 90, 2) if minutes else 0.0,
        }
    return result