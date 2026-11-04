"""Backtest of the squad-insights rules (run from `python manage.py shell`).

For every past gameweek g it scores all regular players with the SAME code the website uses, using
only data from before g, then checks what they really scored over the next `horizon` gameweeks
compared with same-position peers. A "move on" rule is useful when its group scores clearly BELOW
its peers (negative mean_delta); the "chance profile" group should score ABOVE them.

    from apps.fpl_data.squad_backtest import backtest
    backtest(start_gw=8, horizon=3)

Limits: availability is not stored historically, so injured/doubtful players are not tested here
(only the minutes, slump and outlook rules); a player appears in several overlapping windows, so
the t-values are optimistic: treat |t| < 2 as "no evidence" and be careful with small n.
"""
import statistics

from django.db.models import Sum

from .compare_engine import _recent_window_ids
from .models import Gameweek, Player, PlayerGameweekStat
from .squad_insights import (
    CHANCE_MIN_UNDERLYING,
    WINDOW_MAX,
    WINDOW_MIN,
    _evaluate_group,
    _is_chance,
    _move_on_signal,
    _ratings_for,
)

POSITIONS = ("GKP", "DEF", "MID", "FWD")
MIN_WINDOW_MINUTES = 90      # a player must have played this much in the window to be included
MIN_GROUP_SIZE = 10          # skip a gameweek/position with fewer players than this
GROUPS = ["minutes", "slump", "slump, xGI strong", "slump, xGI weak", "outlook", "chance profile"]


def _groups_of(ev):
    """Which rule groups a player belongs to (history has no availability, so everyone is fit)."""
    ev = dict(ev, availability={"multiplier": 1.0})
    groups = []
    signal = _move_on_signal(ev)
    if signal:
        groups.append(signal[1])
        if signal[1] == "slump":
            groups.append("slump, xGI strong" if ev["underlying"] >= CHANCE_MIN_UNDERLYING else "slump, xGI weak")
    if _is_chance(ev):
        groups.append("chance profile")
    return groups


def summarize(samples):
    """samples: [(groups, delta)] -> one row per group with n, mean delta and a rough t-value."""
    rows = []
    for name in GROUPS:
        deltas = [d for groups, d in samples if name in groups]
        n = len(deltas)
        mean = statistics.fmean(deltas) if n else 0.0
        t_value = None
        if n > 2:
            spread = statistics.stdev(deltas)
            t_value = round(mean / (spread / n ** 0.5), 1) if spread else None
        rows.append({"group": name, "n": n, "mean_delta": round(mean, 2), "t": t_value})
    return rows


def backtest(start_gw=8, horizon=3):
    last_final = (
        PlayerGameweekStat.objects.filter(is_final=True)
        .order_by("-gameweek__fpl_id").values_list("gameweek__fpl_id", flat=True).first()
    )
    if last_final is None:
        print("No finalized gameweek data.")
        return []

    samples, observed = [], 0
    for g in range(start_gw, last_final - horizon + 2):
        gameweek = Gameweek.objects.filter(fpl_id=g).first()
        if not gameweek:
            continue
        window_ids = _recent_window_ids(gameweek, WINDOW_MAX)
        if len(window_ids) < WINDOW_MIN:
            continue
        ratings = _ratings_for(gameweek, window_ids)
        future = {
            row["player_id"]: row["pts"] or 0
            for row in PlayerGameweekStat.objects.filter(
                is_final=True, gameweek__fpl_id__gte=g, gameweek__fpl_id__lt=g + horizon
            ).values("player_id").annotate(pts=Sum("points"))
        }
        for position in POSITIONS:
            played = set(
                PlayerGameweekStat.objects.filter(
                    is_final=True, gameweek__fpl_id__in=window_ids, player__position=position, minutes__gt=0
                ).values_list("player_id", flat=True)
            )
            players = list(Player.objects.select_related("team").filter(id__in=played))
            try:
                evals = _evaluate_group(position, players, gameweek, window_ids, ratings)
            except ValueError:
                continue
            evals = [e for e in evals if e["totals"]["minutes"] >= MIN_WINDOW_MINUTES]
            if len(evals) < MIN_GROUP_SIZE:
                continue
            # future points per gameweek (a gameweek he does not play counts as 0)
            outcome = {e["player"].id: future.get(e["player"].id, 0) / horizon for e in evals}
            peer_mean = statistics.fmean(outcome.values())
            for e in evals:
                samples.append((_groups_of(e), outcome[e["player"].id] - peer_mean))
                observed += 1

    rows = summarize(samples)
    print(f"{observed} player-windows, gameweeks {start_gw}-{last_final - horizon + 1}, next {horizon} GWs\n")
    print(f"{'group':<20}{'n':>6}{'mean_delta':>12}{'t':>8}")
    for r in rows:
        print(f"{r['group']:<20}{r['n']:>6}{r['mean_delta']:>12}{str(r['t']):>8}")
    print("\nmean_delta = points per gameweek over the next gameweeks minus same-position peers.")
    print("Move-on groups should be clearly negative; 'chance profile' should be positive.")
    return rows