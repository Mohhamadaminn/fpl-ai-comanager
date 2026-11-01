from apps.fpl_data.compare_engine import (
    FORM_RATING_SPREAD,
    RECENT_APPEARANCES,
    _aggregate_stats,
    _form_value,
    _get_population,
    _recent_window_ids,
    _target_gameweek,
    _team_ratings,
    _zscore_rating,
)
from apps.fpl_data.models import PlayerGameweekStat

SQUAD_FORM_FLAG_RATING = 40     # form rating (0-100, 50 = position average) below this is flagged
SQUAD_FORM_MIN_APPS = 3         # needs at least this many games among his last RECENT_APPEARANCES
SQUAD_FORM_MIN_MINUTES = 45     # average minutes per game; below this he is a sub, form is not judged


def squad_form_context():
    """Heavy shared data, built once per task run (not once per user)."""
    gameweek = _target_gameweek()
    if not gameweek:
        return None
    window_ids = _recent_window_ids(gameweek)
    if not window_ids:
        return None
    return {
        "gameweek": gameweek,
        "window_ids": window_ids,
        "ratings": _team_ratings(gameweek),
        "populations": {},
    }


def _last_games_extra(player_ids, gameweek, n=RECENT_APPEARANCES):
    """{player_id: (clean_sheets, xgc_total)} over his last n games played."""
    rows = (
        PlayerGameweekStat.objects.filter(
            is_final=True, gameweek__fpl_id__lt=gameweek.fpl_id,
            player_id__in=player_ids, minutes__gt=0,
        )
        .order_by("player_id", "-gameweek__fpl_id")
        .values_list("player_id", "clean_sheets", "expected_goals_conceded")
    )
    out, counts = {}, {}
    for pid, cs, xgc in rows.iterator():
        if counts.get(pid, 0) >= n:
            continue
        counts[pid] = counts.get(pid, 0) + 1
        prev_cs, prev_xgc = out.get(pid, (0, 0.0))
        out[pid] = (prev_cs + int(cs or 0), prev_xgc + float(xgc or 0))
    return out


def squad_form_snapshot(players, ctx):
    """{player.id: {...}} form rating + last-5-games stats for players with enough recent games.
    Rating is the same z-score form used by compare_players (vs the same position only)."""
    if not ctx:
        return {}
    gameweek, window_ids, ratings = ctx["gameweek"], ctx["window_ids"], ctx["ratings"]

    by_pos = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p)

    result = {}
    for position, plist in by_pos.items():
        population = ctx["populations"].get(position)
        if population is None:
            try:
                population = _get_population(position, gameweek, window_ids)
            except ValueError:
                continue
            ctx["populations"][position] = population

        ids = [p.id for p in plist]
        agg = _aggregate_stats(position, gameweek, window_ids, player_ids=ids, ratings=ratings)
        extra = _last_games_extra(ids, gameweek)

        for p in plist:
            s = agg.get(p.id)
            if not s or s["last_apps"] < SQUAD_FORM_MIN_APPS:
                continue
            apps, minutes = s["last_apps"], s["last_minutes"]
            if minutes / apps < SQUAD_FORM_MIN_MINUTES:
                continue
            value = _form_value(s, population["medians"])[0]
            rating = _zscore_rating(value, population["metrics"]["form"], FORM_RATING_SPREAD)
            cs, xgc = extra.get(p.id, (0, 0.0))
            result[p.id] = {
                "rating": rating,
                "games": int(apps),
                "points": int(s["last_points"]),
                "clean_sheets": cs,
                "xgc90": round(xgc / minutes * 90, 2) if minutes else 0.0,
                "bps": int(s["last_bps"]),
                "dc_avg": round(s["last_dc"] / apps, 1),
                "xgi": round(s["last_xgi"], 2),
                "xgi90": round(s["last_xgi"] / minutes * 90, 2) if minutes else 0.0,
            }
    return result