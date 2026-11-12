import bisect
import re
import hashlib
import json
import logging
import statistics

from collections import Counter
from django.db.models import Count, Max, Q, Sum

from .models import Fixture, Gameweek, Player, PlayerGameweekStat, Team
from .services import (
    COMPARISON_RESPONSE_SCHEMA,
    _substitute_player_placeholders,
    client,
    redis_client,
    sanitize_markdown,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
# Form (see _form_value) = FORM_RECENT_WEIGHT x (his last RECENT_APPEARANCES games played)
#                        + FORM_SEASON_WEIGHT x (points per game over the whole season; the lowest weight).
# In the recent games each game is worth its points PLUS bonuses, so explosive and consistent players
# are rewarded without the real gap in points between two players being averaged away:
#   return : a game with at least RETURN_POINTS points adds RETURN_BONUS
#   haul   : a game with at least HAUL_POINTS points adds HAUL_BONUS (on top of the return bonus)
# and every game is scaled by how hard that fixture was (PAST_FIXTURE_ADJUST).
FORM_RECENT_WEIGHT = 0.7      # weight of the last-5-games part of form
FORM_SEASON_WEIGHT = 0.3      # weight of points per game over the whole season (lowest weight)
RETURN_POINTS = 5             # a game with at least this many points counts as a "return"
RETURN_BONUS = 1.0            # extra points-equivalent a return adds to that game
HAUL_POINTS = 10              # a game with at least this many points counts as a "haul" (double digits)
HAUL_BONUS = 2.0              # extra points-equivalent a haul adds on top of the return bonus
# Points scored against a hard opponent count a bit more, against an easy one a bit less:
# multiplier = 1 + PAST_FIXTURE_ADJUST * (0.5 - ease of that fixture), ease on the usual 0-1 scale
# (0 turns it off). Only the opponent's weakness and the venue count, not the player's own team.
PAST_FIXTURE_ADJUST = 0.4
# Form is rated with a z-score, not a percentile: a percentile squeezes the top of the table (the gap
# between a 98 and a 94 can be many points per game), a z-score keeps the real size of the gap.
FORM_RATING_SPREAD = 22       # z-score -> rating points for form (rating = 50 + spread * z, clipped to 0-100)
FORM_PRIOR_APPS_RECENT = 1.5  # pseudo-appearances of pool-average output (shrinks tiny samples)
FORM_PRIOR_APPS_SEASON = 3.0
HOME_EASE_SHIFT = 0.05        # home side easier / away side harder (0-1 ease scale), form part only
RATING_SPREAD = 25            # z-score -> rating points (rating = 50 + spread * z, clipped to 0-100)
MAX_HORIZON = 5               # most gameweeks a user can look ahead
UPCOMING_FIXTURE_WEIGHTS = (0.5, 0.25, 0.25)
TEAM_FORM_WINDOW = 5          # a team's last N PLAYED gameweeks count as its recent form (blanks are skipped)
TEAM_FORM_PRIOR = 3           # matches of league-average output added as a prior
TEAM_FORM_SHARE = 0.7         # weight of team strength/form vs official FDR in fixture ease
TEAM_SEASON_SHARE = 0.5       # season-long vs recent-window share inside a team's rating
TEAM_XG_SHARE = 0.3           # share of xG / xGC inside a team's rating; the rest is real results (points)
RECENT_WINDOW = 5            # finalized gameweeks used for playing time (minutes per gameweek, absences)
RECENT_APPEARANCES = 5       # a player's "recent" stats = his last N games actually played, however long ago
LOW_MINUTES_PER_APPEARANCE = 45   # below this he is mostly a substitute: shown to the user as a note
RECENT_LOW_MINUTES_THRESHOLD = 45  # threshold for consecutive finalized gameweeks, including benchings
RECENT_LOW_APPEARANCE_THRESHOLD = 60
RECENT_LOW_APPEARANCES_WINDOW = 3
RECENT_LOW_APPEARANCES_REQUIRED = 2
RECENT_LOW_MINUTES_FACTOR = 0.62  # strong penalty for consecutive severe short appearances / gameweeks
RECENT_ROTATION_FACTOR = 0.78    # moderate penalty for two short appearances among the last three
# A single short (or missed) last game: lighter than the two-games penalty above. A game with 0 minutes
# only counts while the player is fit (status "a") and his team really had a fixture that gameweek.
LAST_GAME_LOW_MINUTES = 40
LAST_GAME_LOW_FACTOR = 0.85
# False: the score is pure performance and injury / doubt is shown as a clear note next to it.
# True: the score is also multiplied by his chance of playing (0 for injured / suspended players).
AVAILABILITY_AFFECTS_SCORE = False
PRIOR_MINUTES = 180         # pseudo-minutes used to shrink small-sample per-90 rates
MIN_POP_MINUTES = 180        # season minutes needed to be part of the comparison pool
FULL_MINUTES_PER_GW = 80     # >= this many effective minutes/GW => no playing-time penalty
CLOSE_CALL_MARGIN = 3.0      # score gap (0-100 scale) below which we say "too close"
POPULATION_TTL = 30 * 60     # seconds
VERDICT_TTL = 3 * 60 * 60    # seconds

# Playing-time multiplier. Effective minutes blend two things:
#   - minutes per gameweek over the whole window (counts missed games), and
#   - minutes per appearance (what he plays when he is actually in the team).
# A larger WHEN_PLAYING_SHARE makes a short injury absence hurt less, while
# rotation / cameo players are still penalised through their low minutes per appearance.
WHEN_PLAYING_SHARE = 0.65
MIN_APPEARANCES_FOR_SHARE = 1    # appearances needed in the window before missed games count (see _score_player)
# "Just back from a spell out": he has missed at least RETURN_MIN_ABSENCE gameweeks in a row and has
# played at most RETURN_RECENT_GAMES games since. Shorter gaps (a one-match ban, a knock) are ignored.
RETURN_MIN_ABSENCE = 3
RETURN_RECENT_GAMES = 2
ELITE_BASE_SCORE = 70            # base score (before the multiplier) that marks an elite option
ELITE_MIN_FACTOR = 0.92          # elite options never lose more than 8% to playing time

# Comparison-level rule: when BOTH players average at least this many minutes in the games
# they actually play, AND both played at least EQUAL_MINUTES_LAST_GAME in their last game,
# playing time gives neither of them an edge (multiplier 1.0 for both).
EQUAL_MINUTES_PER_APPEARANCE = 70
EQUAL_MINUTES_LAST_GAME = 70
# A player who scores very well per game played (points-per-appearance percentile at or above
# HIGH_POINTS_PCT) gets the same protection as an elite option: missed games barely count.
HIGH_POINTS_PCT = 80
MIN_APPEARANCES_FOR_POINTS = 2
# Per-90 rates are pulled toward the pool median only while the sample is smaller than this,
# so a player who missed a few games is not marked down for having fewer minutes.
SHRINK_FULL_MINUTES = 180

# Price is deliberately NOT part of the score: the comparison is raw performance only.
# The LLM verdict receives the prices and may mention them as budget context.

# Defensive contribution (DC) for midfielders only counts as an advantage when a player
# really has a DC threat: an average of MID_DC_AVG_MIN per appearance, or the DC threshold
# reached in at least MID_DC_HIT_SHARE of his recent appearances (3 of 5). Meeting both
# gives the full bonus, one of them 75% of it. Otherwise DC has no effect at all.
MID_DC_THRESHOLD = 12            # DC in one gameweek that earns the FPL bonus points for a midfielder
MID_DC_AVG_MIN = 10
MID_DC_HIT_SHARE = 0.6
MID_DC_MIN_APPEARANCES = 3
MID_DC_BONUS_MAX = 6.0           # most points a midfielder can add to his base score from DC

# DC per position: 10 in one game earns the FPL bonus for defenders, 12 for midfielders.
# For both it is a capped bonus (not a weighted axis) that only counts with a real DC threat.
DC_THRESHOLD = {"DEF": 10, "MID": MID_DC_THRESHOLD}
DC_AVG_MIN = {"DEF": 8, "MID": MID_DC_AVG_MIN}         # DEF: starting point, not tuned yet
DC_BONUS_MAX = {"DEF": 4.0, "MID": MID_DC_BONUS_MAX}   # DEF smaller: DC points are already inside form

# Form: top end bends instead of clipping at 100 (rating 80 and above counts half per extra point)
FORM_RATING_KNEE = 30
FORM_RATING_TOP_SLOPE = 0.5
# Form: robustness against one lone spike and against absences
FORM_BEST_OUT_SHARE = 0.4        # share of recent form measured WITHOUT his single best game
BLANK_POINTS = 2                 # a game with at most this many points is a "blank"
ISOLATED_MIN_BLANK_SHARE = 0.75  # lone return + this share of the OTHER games blank => bonuses dropped
ISOLATED_BONUS_FACTOR = 0.0
ABSENCE_MIN = 2                  # missed gameweeks in a row that count as an absence
POST_ABSENCE_WEIGHT = 2.0        # games played after his latest absence count this many times

WEAK_FORM_PERCENTILE = 51  # below this, a player's recent form is clearly below average

PHOTO_URL = "https://resources.premierleague.com/premierleague25/photos/players/110x140/{code}.png"

# Weights per position (each row sums to 1.0). Playing time and availability are
# applied as multipliers on top, not as weighted axes.
POSITION_WEIGHTS = {
    "GKP": {"form": 0.28, "xgc_season": 0.15, "cs_season": 0.10, "bps_recent": 0.12,
            "team_strength": 0.10, "fixtures_fdr": 0.25},
    "DEF": {"form": 0.31, "xgi_recent": 0.11, "xgi_season": 0.09, "xgc_season": 0.14,
            "team_strength": 0.11, "fixtures_fdr": 0.24},
    "MID": {"form": 0.47, "xgi_recent": 0.19, "xgi_season": 0.14,
            "team_strength": 0.08, "fixtures_fdr": 0.12},
    "FWD": {"form": 0.47, "xgi_recent": 0.19, "xgi_season": 0.14,
            "team_strength": 0.08, "fixtures_fdr": 0.12},
}

for _pos, _w in POSITION_WEIGHTS.items():
    assert abs(sum(_w.values()) - 1.0) < 1e-9, f"weights for {_pos} must sum to 1"

# Which side of the fixture matters for each position:
# "attack"  -> opponent's defensive weakness + own attacking strength
# "defence" -> opponent's attacking weakness + own defensive strength
FIXTURE_MODE_BLEND = {
    "GKP": {"defence": 1.0},
    "DEF": {"defence": 0.6, "attack": 0.4},
    "MID": {"attack": 1.0},
    "FWD": {"attack": 1.0},
}

# Per-90 metrics aggregated from PlayerGameweekStat.
METRICS = {
    "points_season": {"label": "Season Points", "num": "season_points", "den": "season_minutes", "lower": False},
    "points_recent": {"label": "Recent Points", "num": "last_points", "den": "last_minutes", "lower": False},
    "xgi_recent": {"label": f"Recent xGI", "num": "last_xgi", "den": "last_minutes", "lower": False},
    "xgi_season": {"label": "Season xGI", "num": "season_xgi", "den": "season_minutes", "lower": False},
    "xgc_season": {"label": "Def. Solidity", "num": "season_xgc", "den": "season_minutes", "lower": True},
    "cs_season": {"label": "Clean Sheets", "num": "season_cs", "den": "season_minutes", "lower": False},
    "bps_recent": {"label": "Shot-stopping (BPS)", "num": "last_bps", "den": "last_minutes", "lower": False},
    "dc_recent": {"label": "Defensive Actions", "num": "last_dc", "den": "last_minutes", "lower": False},
}
# Plain-English status names for the notes shown next to a player.
STATUS_LABELS = {"i": "injured", "s": "suspended", "u": "unavailable", "n": "not in the squad", "d": "doubtful"}
EXTRA_LABELS = {"form": "Form", "fixtures_fdr": "Fixture Difficulty", "team_strength": "Team Strength",
                "minutes_per_game": "Minutes (per Appearance)"}


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------
def _percentile(value, sorted_values, lower_is_better=False):
    """Mid-rank percentile (0-100) of `value` inside an ascending-sorted list."""
    n = len(sorted_values)
    if n == 0:
        return 50.0
    below = bisect.bisect_left(sorted_values, value)
    equal = bisect.bisect_right(sorted_values, value) - below
    pct = (below + 0.5 * equal) / n * 100
    return round(100 - pct if lower_is_better else pct, 1)


def _shrunk_rate(total, minutes, pop_rate, prior_minutes=PRIOR_MINUTES):
    """Per-90 rate pulled toward the pool's median when the sample is small."""
    return (total + pop_rate * prior_minutes / 90) / (minutes + prior_minutes) * 90


def _shrunk_rate_capped(total, minutes, pop_rate, full_minutes=SHRINK_FULL_MINUTES):
    """Per-90 rate that is only pulled toward the pool median while minutes < full_minutes."""
    prior = max(0.0, full_minutes - minutes)
    if minutes + prior <= 0:
        return pop_rate
    return (total + pop_rate * prior / 90) / (minutes + prior) * 90


def _norm(value, lo, hi):
    return 0.5 if hi == lo else (value - lo) / (hi - lo)


def _rating_from_stats(value, mean, std, spread=None):
    """0-100 rating from a known mean and standard deviation (50 = average)."""
    if std <= 0:
        return 50.0
    spread = RATING_SPREAD if spread is None else spread
    return max(0.0, min(100.0, 50 + spread * (value - mean) / std))


def _zscore_rating(value, values, spread=None):
    """0-100 rating that keeps the real size of gaps: near-equal teams stay near-equal
    (unlike a percentile, which spreads them evenly over the whole range)."""
    n = len(values)
    if n < 2:
        return 50.0
    mean = sum(values) / n
    std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
    return round(_rating_from_stats(value, mean, std, spread), 1)


def _form_rating(value, values):
    """Like _zscore_rating for form, but the top end bends instead of clipping at 100, so two elite
    players (6.7 and 7.3 points a game) no longer both get 100."""
    n = len(values)
    if n < 2:
        return 50.0
    mean = sum(values) / n
    std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
    if std <= 0:
        return 50.0
    x = FORM_RATING_SPREAD * (value - mean) / std
    if x > FORM_RATING_KNEE:
        x = FORM_RATING_KNEE + FORM_RATING_TOP_SLOPE * (x - FORM_RATING_KNEE)
    return round(max(0.0, min(100.0, 50 + x)), 1)


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def _target_gameweek():
    """The gameweek the user is picking for = the NEXT one (fallback: current)."""
    return (
        Gameweek.objects.filter(is_next=True).first()
        or Gameweek.objects.filter(is_current=True).first()
    )


def _recent_window_ids(gameweek, n=RECENT_WINDOW):
    """fpl_ids of the latest n finalized gameweeks (that actually have stats) before `gameweek`."""
    return list(
        PlayerGameweekStat.objects.filter(is_final=True, gameweek__fpl_id__lt=gameweek.fpl_id)
        .values_list("gameweek__fpl_id", flat=True)
        .distinct()
        .order_by("-gameweek__fpl_id")[:n]
    )


def _aggregate_stats(position, gameweek, window_ids, player_ids=None, past_mult=None, ratings=None):
    """Per player: season totals plus two kinds of "recent" totals.

    recent_* : the last few calendar gameweeks (window_ids). Used for playing time and absences.
    last_*   : his last RECENT_APPEARANCES games actually played, however long ago. Used for the
               quality metrics (xGI, BPS, DC, form), so a player who was injured for a while is
               judged on the games he really played instead of on an empty window.
    gw_minutes : {gameweek: minutes} for the latest two finalized gameweeks, including games with
               0 minutes (bench / not used), but only where his team really had a fixture.

    past_mult (optional) scales each of those games by fixture difficulty; when it is not given it is
    built here (pass `ratings` to reuse team ratings that were already computed)."""
    if past_mult is None and PAST_FIXTURE_ADJUST:
        past_mult = _past_multipliers(gameweek, position, ratings if ratings is not None else _team_ratings(gameweek))
    recent = Q(gameweek__fpl_id__in=window_ids)
    qs = PlayerGameweekStat.objects.filter(
        is_final=True,
        gameweek__fpl_id__lt=gameweek.fpl_id,
        player__position=position,
    )
    if player_ids is not None:
        qs = qs.filter(player_id__in=player_ids)

    rows = qs.values("player_id", "player__team_id").annotate(
        season_minutes=Sum("minutes"),
        season_points=Sum("points"),
        season_apps=Count("id", filter=Q(minutes__gt=0)),
        recent_minutes=Sum("minutes", filter=recent),
        season_xgi=Sum("expected_goal_involvements"),
        recent_xgi=Sum("expected_goal_involvements", filter=recent),
        season_xgc=Sum("expected_goals_conceded"),
        season_cs=Sum("clean_sheets"),
        recent_bps=Sum("bps", filter=recent),
        recent_dc=Sum("defensive_contribution", filter=recent),
        recent_points=Sum("points", filter=recent),
        recent_apps=Count("id", filter=recent & Q(minutes__gt=0)),
        recent_dc_hits=Count(
            "id",
            filter=recent & Q(minutes__gt=0) & Q(defensive_contribution__gte=DC_THRESHOLD.get(position, MID_DC_THRESHOLD)),
        ),
    )
    out = {}
    for row in rows:
        stats = {k: float(v or 0) for k, v in row.items() if k not in ("player_id", "player__team_id")}
        stats["team_id"] = row["player__team_id"]
        out[row["player_id"]] = stats
    for stats in out.values():
        stats.update(_EMPTY_LAST)
        stats["last_gws"] = []  # gameweeks of his last games played, newest first
        stats["last_appearance_minutes"] = []
        stats["last_game_points"] = []        # raw points per game, aligned with last_gws
        stats["last_game_form_points"] = []   # _game_form_points per game, aligned with last_gws
        stats["last_game_mults"] = []                         # NEW
        stats["gw_minutes"] = {}

    appearances = (
        qs.filter(minutes__gt=0)
        .order_by("player_id", "-gameweek__fpl_id")
        .values_list(
            "player_id", "gameweek__fpl_id", "minutes", "points",
            "expected_goal_involvements", "bps", "defensive_contribution",
        )
    )
    _add_last_games(out, appearances.iterator(), past_mult, DC_THRESHOLD.get(position, MID_DC_THRESHOLD))
    _add_gameweek_minutes(out, qs, window_ids)
    return out


def _add_gameweek_minutes(out, qs, window_ids):
    """Fill stats["gw_minutes"] with his minutes in the latest two finalized gameweeks, 0-minute rows
    included. A game only counts when his team had a fixture that gameweek (a blank is not a benching)."""
    latest_two = [int(g) for g in window_ids[:2]]
    if not latest_two:
        return
    had_fixture = set()
    for gw, home_id, away_id in Fixture.objects.filter(gameweek__fpl_id__in=latest_two).values_list(
        "gameweek__fpl_id", "team_home_id", "team_away_id"
    ):
        had_fixture.add((home_id, int(gw)))
        had_fixture.add((away_id, int(gw)))
    for player_id, gw, minutes in qs.filter(gameweek__fpl_id__in=latest_two).values_list(
        "player_id", "gameweek__fpl_id", "minutes"
    ):
        stats = out.get(player_id)
        if stats is None or (stats.get("team_id"), int(gw)) not in had_fixture:
            continue
        stats["gw_minutes"][int(gw)] = float(minutes or 0)


_EMPTY_LAST = {
    "last_apps": 0.0, "last_minutes": 0.0, "last_points": 0.0, "last_xgi": 0.0,
    "last_bps": 0.0, "last_dc": 0.0, "last_dc_hits": 0.0, "last_played_gw": 0.0,
    "last_form_points": 0.0, "last_returns": 0.0, "last_hauls": 0.0, "last_appearance_minutes": [],
}


def _game_form_points(points, mult=1.0):
    """What one game is worth for form: its points plus the return / haul bonuses, scaled by how
    hard the fixture was (mult). The bonuses depend on the real points, not on the scaled ones."""
    bonus = (RETURN_BONUS if points >= RETURN_POINTS else 0.0) + (HAUL_BONUS if points >= HAUL_POINTS else 0.0)
    return (points + bonus) * mult


def _add_last_games(out, appearances, past_mult=None, dc_threshold=MID_DC_THRESHOLD):
    """Fill the last_* totals from rows (player_id, gameweek, minutes, points, xgi, bps, dc) that
    are sorted by player and then newest gameweek first; keeps each player's newest games only.
    past_mult: {(team_id, gameweek): multiplier} that scales a game by how hard the fixture was."""
    for player_id, gw, minutes, points, xgi, bps, dc in appearances:
        stats = out.get(player_id)
        if stats is None or stats["last_apps"] >= RECENT_APPEARANCES:
            continue
        pts = float(points or 0)
        mult = past_mult.get((stats.get("team_id"), int(gw)), 1.0) if past_mult else 1.0
        form_pts = _game_form_points(pts, mult)
        stats["last_apps"] += 1
        stats["last_minutes"] += float(minutes or 0)
        stats["last_points"] += pts
        stats["last_form_points"] += form_pts       
        if pts >= RETURN_POINTS:
            stats["last_returns"] += 1
        if pts >= HAUL_POINTS:
            stats["last_hauls"] += 1
        stats["last_xgi"] += float(xgi or 0)
        stats["last_bps"] += float(bps or 0)
        stats["last_dc"] += float(dc or 0)
        if float(dc or 0) >= dc_threshold:
            stats["last_dc_hits"] += 1
        stats["last_gws"].append(int(gw))
        stats["last_appearance_minutes"].append(float(minutes or 0))
        stats["last_game_points"].append(pts)
        stats["last_game_form_points"].append(form_pts) 
        stats["last_game_mults"].append(mult)  
        if not stats["last_played_gw"]:
            stats["last_played_gw"] = float(gw)  # rows are newest first


def _return_info(stats, latest_gw):
    """Is he just back from a real spell out? Looks at the gaps between his last games played.
    Returns {"games_since", "missed"} or None. A gap shorter than RETURN_MIN_ABSENCE gameweeks
    (one-match ban, minor knock, a blank gameweek) does not count."""
    gws = stats.get("last_gws") or []  # newest first
    if not gws or latest_gw - gws[0] > 1:
        return None  # not playing right now: that is an absence, not a return
    for i in range(1, min(len(gws), RETURN_RECENT_GAMES + 1)):
        missed = gws[i - 1] - gws[i] - 1
        if missed >= RETURN_MIN_ABSENCE:
            return {"games_since": i, "missed": missed}
    return None


def _recent_game_minutes(stats, window_ids, status):
    """(latest, previous) minutes in his last two finalized gameweeks, None when unknown.
    A 0-minute game counts only while he is fit (status "a"); for an injured / suspended player the
    0 is explained by availability, not by the manager's choice, so it is ignored here."""
    by_gw = stats.get("gw_minutes") or {}
    values = []
    for gw in window_ids[:2]:
        minutes = by_gw.get(int(gw))
        if minutes is not None and minutes <= 0 and status != "a":
            minutes = None
        values.append(minutes)
    while len(values) < 2:
        values.append(None)
    return values[0], values[1]


def _has_repeated_short_appearances(minutes):
    recent = minutes[:RECENT_LOW_APPEARANCES_WINDOW]
    return sum(m < RECENT_LOW_APPEARANCE_THRESHOLD for m in recent) >= RECENT_LOW_APPEARANCES_REQUIRED


def _recent_minutes_risk(appearance_minutes, latest_minutes, previous_minutes):
    severe_short_appearances = (
        len(appearance_minutes) >= 2
        and all(m <= RECENT_LOW_MINUTES_THRESHOLD for m in appearance_minutes[:2])
    )
    two_low_games = (
        latest_minutes is not None and previous_minutes is not None
        and latest_minutes <= RECENT_LOW_MINUTES_THRESHOLD
        and previous_minutes <= RECENT_LOW_MINUTES_THRESHOLD
    )
    if severe_short_appearances or two_low_games:
        return "severe"
    if _has_repeated_short_appearances(appearance_minutes):
        return "rotation"
    if latest_minutes is not None and latest_minutes < LAST_GAME_LOW_MINUTES:
        return "single"
    return None


_EMPTY_AGG = {
    "season_minutes": 0.0, "season_points": 0.0, "season_apps": 0.0, "recent_minutes": 0.0, "season_xgi": 0.0, "recent_xgi": 0.0,
    "season_xgc": 0.0, "season_cs": 0.0, "recent_bps": 0.0, "recent_dc": 0.0, "recent_apps": 0.0,
    "recent_dc_hits": 0.0, "recent_points": 0.0,
    **_EMPTY_LAST,
    "last_gws": (),
    "last_game_points": (),
    "last_game_form_points": (),
    "last_game_mults": (),
    "gw_minutes": {},
}


def _post_absence_count(gws):
    """How many of his newest games came after his latest absence of ABSENCE_MIN+ gameweeks.
    gws: gameweeks of his last games played, newest first. 0 when there is no such absence.
    (A blank gameweek of his team looks like an absence here; it only re-weights, it never punishes.)"""
    for i in range(1, len(gws)):
        if gws[i - 1] - gws[i] - 1 >= ABSENCE_MIN:
            return i
    return 0


def _recent_form_games(stats):
    """[(form points, weight)] for his last games, newest first.
    - A lone return/haul (his only return, the other games mostly blanks) loses its bonuses.
    - Games after his latest absence weigh POST_ABSENCE_WEIGHT times."""
    points, mults = stats["last_game_points"], stats["last_game_mults"]
    returns = [i for i, p in enumerate(points) if p >= RETURN_POINTS]
    isolated = None
    if len(returns) == 1 and len(points) >= 3:
        others = [p for i, p in enumerate(points) if i != returns[0]]
        if sum(1 for p in others if p <= BLANK_POINTS) / len(others) >= ISOLATED_MIN_BLANK_SHARE:
            isolated = returns[0]
    after = _post_absence_count(stats["last_gws"])
    games = []
    for i, (p, mult) in enumerate(zip(points, mults)):
        bonus = (RETURN_BONUS if p >= RETURN_POINTS else 0.0) + (HAUL_BONUS if p >= HAUL_POINTS else 0.0)
        if i == isolated:
            bonus *= ISOLATED_BONUS_FACTOR
        games.append(((p + bonus) * mult, POST_ABSENCE_WEIGHT if i < after else 1.0))
    return games


def _shrunk_recent(games, median):
    weight = sum(w for _, w in games)
    return (sum(f * w for f, w in games) + median * FORM_PRIOR_APPS_RECENT) / (weight + FORM_PRIOR_APPS_RECENT)


def _recent_form(stats, median):
    """Per-game recent form, robust to one spike: a blend of all his games and the same without his
    single best game (3+ games needed, else all games)."""
    games = _recent_form_games(stats)
    full = _shrunk_recent(games, median)
    if len(games) < 3:
        return full
    best = max(range(len(games)), key=lambda i: games[i][0])
    rest = games[:best] + games[best + 1:]
    return (1 - FORM_BEST_OUT_SHARE) * full + FORM_BEST_OUT_SHARE * _shrunk_recent(rest, median)


def _form_value(stats, medians):
    """Our own form (points-per-game scale): the recent part is robust (see _recent_form), the season
    part is plain points per game shrunk toward the pool median. Returns (form, recent, season)."""
    recent = _recent_form(stats, medians["form_recent"])
    season = (stats["season_points"] + medians["form_season"] * FORM_PRIOR_APPS_SEASON) / (
        stats["season_apps"] + FORM_PRIOR_APPS_SEASON
    )
    return FORM_RECENT_WEIGHT * recent + FORM_SEASON_WEIGHT * season, recent, season


def _build_population(position, gameweek, window_ids):
    agg = _aggregate_stats(position, gameweek, window_ids)
    pool = {pid: a for pid, a in agg.items() if a["season_minutes"] >= MIN_POP_MINUTES}
    if len(pool) < 8:  # very early season: fall back to anyone who has played
        pool = {pid: a for pid, a in agg.items() if a["season_minutes"] > 0}
    if not pool:
        raise ValueError("Not enough finished-gameweek data to compare players yet.")

    metrics, medians = {}, {}
    recent_ppg = [a["last_form_points"] / a["last_apps"] for a in pool.values() if a["last_apps"] > 0]
    season_ppg = [a["season_points"] / a["season_apps"] for a in pool.values() if a["season_apps"] > 0]
    medians["form_recent"] = statistics.median(recent_ppg) if recent_ppg else 0.0
    medians["form_season"] = statistics.median(season_ppg) if season_ppg else 0.0

    keys = list(POSITION_WEIGHTS[position])
    if position in DC_BONUS_MAX and "dc_recent" not in keys:
        keys.append("dc_recent")   # needed for the DC bonus (midfielders and defenders)

    
    for key in keys:
        spec = METRICS.get(key)
        if not spec:
            continue
        raw = [a[spec["num"]] / a[spec["den"]] * 90 for a in pool.values() if a[spec["den"]] > 0]
        median = statistics.median(raw) if raw else 0.0
        medians[key] = median
        metrics[key] = sorted(_shrunk_rate_capped(a[spec["num"]], a[spec["den"]], median) for a in pool.values())

    metrics["form"] = sorted(_form_value(a, medians)[0] for a in pool.values())
    metrics["points_per_app"] = sorted(
        a["last_points"] / a["last_apps"]
        for a in pool.values()
        if a["last_apps"] >= MIN_APPEARANCES_FOR_POINTS
    )
    return {"metrics": metrics, "medians": medians, "pool_size": len(pool)}


def _get_population(position, gameweek, window_ids):
    key = f"compare:pop:v13:{position}:{gameweek.fpl_id}:{window_ids[0]}"
    try:
        cached = redis_client.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("Population cache read failed", exc_info=True)

    population = _build_population(position, gameweek, window_ids)

    try:
        redis_client.set(key, json.dumps(population), ex=POPULATION_TTL)
    except Exception:
        logger.warning("Population cache write failed", exc_info=True)
    return population


# ---------------------------------------------------------------------------
# Team strength and fixtures for the target gameweek(s)
# ---------------------------------------------------------------------------
def _team_gw_records(gameweek):
    """Per team, one record for every gameweek it actually played before `gameweek`, oldest first:
    {"matches", "xg", "xgc", "pts", "res_matches"}.

    Results come from the real fixture score when it is synced (Fixture.team_home_score / team_away_score).
    Until then they are derived from player goals, which misses own goals and only works for
    single-match gameweeks; xG / xGC are counted for every played match.
    Blank or postponed gameweeks simply have no record, so "the last N records" of a team are
    its last N gameweeks it really played."""
    stats = {}  # (team_id, gw) -> goals, xg, xgc
    rows = (
        PlayerGameweekStat.objects.filter(is_final=True, gameweek__fpl_id__lt=gameweek.fpl_id, minutes__gt=0)
        .values("player__team_id", "gameweek__fpl_id")
        .annotate(goals=Sum("goals_scored"), team_xg=Sum("expected_goals"), team_xgc=Max("expected_goals_conceded"))
    )
    for row in rows:
        stats[(row["player__team_id"], row["gameweek__fpl_id"])] = (
            float(row["goals"] or 0), float(row["team_xg"] or 0), float(row["team_xgc"] or 0),
        )
    if not stats:
        return {}

    played_gws = {gw for _, gw in stats}
    fixtures = list(Fixture.objects.filter(gameweek__fpl_id__in=played_gws).select_related("gameweek"))
    per_gw_count = {}
    for f in fixtures:
        for team_id in (f.team_home_id, f.team_away_id):
            key = (team_id, f.gameweek.fpl_id)
            per_gw_count[key] = per_gw_count.get(key, 0) + 1

    records = {}  # (team_id, gw) -> record

    def record(key):
        return records.setdefault(key, {"matches": 0, "xg": 0.0, "xgc": 0.0, "pts": 0, "res_matches": 0})

    for f in fixtures:
        gw = f.gameweek.fpl_id
        home, away = (f.team_home_id, gw), (f.team_away_id, gw)
        if home not in stats or away not in stats:
            continue  # not played (postponed / not synced)
        rec_home, rec_away = record(home), record(away)
        rec_home["matches"] += 1
        rec_away["matches"] += 1
        if f.team_home_score is not None and f.team_away_score is not None:
            # Real score (own goals included), known for every fixture, double gameweeks too.
            hg, ag = f.team_home_score, f.team_away_score
        elif per_gw_count[home] == 1 and per_gw_count[away] == 1:
            # Fallback while the real score is not synced yet: players' goals of a single-match gameweek.
            hg, ag = stats[home][0], stats[away][0]
        else:
            continue  # result unknown
        for rec, gf, ga in ((rec_home, hg, ag), (rec_away, ag, hg)):
            rec["pts"] += 3 if gf > ga else 1 if gf == ga else 0
            rec["res_matches"] += 1

    for key, rec in records.items():
        rec["xg"], rec["xgc"] = stats[key][1], stats[key][2]

    by_team = {}
    for (team_id, gw), rec in sorted(records.items(), key=lambda item: item[0][1]):
        by_team.setdefault(team_id, []).append(rec)
    return by_team


def _team_profile(records_by_team):
    """Ratings across teams from their gameweek records: shrunk xG / xGC / points per match ->
    {team_id: {"att", "leaky", "res"}} (0-100, higher = better attack / leakier defence /
    better results)."""
    matches, xg, xgc, pts, res_matches = {}, {}, {}, {}, {}
    for team_id, recs in records_by_team.items():
        played = sum(r["matches"] for r in recs)
        if played <= 0:
            continue
        matches[team_id] = played
        xg[team_id] = sum(r["xg"] for r in recs)
        xgc[team_id] = sum(r["xgc"] for r in recs)
        results = sum(r["res_matches"] for r in recs)
        if results:
            res_matches[team_id] = results
            pts[team_id] = sum(r["pts"] for r in recs)

    teams = list(matches)
    if not teams:
        return {}

    def shrunk(totals, counts, tlist):
        total_n = sum(counts[t] for t in tlist if t in counts)
        if not total_n:
            return {}
        mean = sum(totals.get(t, 0.0) for t in tlist if t in counts) / total_n
        return {
            t: (totals.get(t, 0.0) + mean * TEAM_FORM_PRIOR) / (counts[t] + TEAM_FORM_PRIOR)
            for t in tlist if t in counts
        }

    att, leaky = shrunk(xg, matches, teams), shrunk(xgc, matches, teams)
    res = shrunk(pts, res_matches, [t for t in teams if t in res_matches])

    def rate(values):
        ordered = list(values.values())
        return {t: _zscore_rating(v, ordered) for t, v in values.items()}

    att_r, leaky_r, res_r = rate(att), rate(leaky), rate(res)
    return {
        t: {"att": att_r.get(t, 50.0), "leaky": leaky_r.get(t, 50.0), "res": res_r.get(t, 50.0)}
        for t in teams
    }


def _team_ratings(gameweek):
    """Blend of the season-long profile and the recent one (each team's last TEAM_FORM_WINDOW
    played gameweeks). Both come from the same data, loaded once."""
    records = _team_gw_records(gameweek)
    season = _team_profile(records)
    recent = _team_profile({team_id: recs[-TEAM_FORM_WINDOW:] for team_id, recs in records.items()})

    ratings = {}
    for team_id in set(season) | set(recent):
        s_, r_ = season.get(team_id), recent.get(team_id)
        if s_ and r_:
            ratings[team_id] = {
                k: TEAM_SEASON_SHARE * s_[k] + (1 - TEAM_SEASON_SHARE) * r_[k] for k in ("att", "leaky", "res")
            }
        else:
            ratings[team_id] = dict(s_ or r_)
    return ratings


def _side_ease(own, opp, is_home, fdr, mode, ratings, own_share=0.0):
    """0-1: how favourable one fixture is for `own`: official FDR blended with team strength/form.
    Results (points) carry most of the team rating, xG / xGC the TEAM_XG_SHARE rest.
    own_share: how much of the form part is the own team's strength (the rest is the opponent's
    weakness). The default is 0: the fixture rates the opponent and the venue only, because the
    player's own team is already counted by the separate team_strength axis."""
    fdr_ease = (5 - fdr) / 4 if fdr else 0.5
    o, p = ratings.get(own.id), ratings.get(opp.id)
    if not o or not p:
        return fdr_ease

    xg_share, res_share = TEAM_XG_SHARE, 1 - TEAM_XG_SHARE
    if mode == "attack":
        # weak opposing defence / poor results + strong own attack / good results
        opp_weak = xg_share * p["leaky"] + res_share * (100 - p["res"])
        own_good = xg_share * o["att"] + res_share * o["res"]
    else:
        # weak opposing attack / poor results + strong own defence / good results
        opp_weak = xg_share * (100 - p["att"]) + res_share * (100 - p["res"])
        own_good = xg_share * (100 - o["leaky"]) + res_share * o["res"]
    form_ease = ((1 - own_share) * opp_weak + own_share * own_good) / 100
    form_ease += HOME_EASE_SHIFT if is_home else -HOME_EASE_SHIFT  # home advantage is real in the PL
    form_ease = max(0.0, min(1.0, form_ease))
    return (1 - TEAM_FORM_SHARE) * fdr_ease + TEAM_FORM_SHARE * form_ease


def _team_strength(ratings, position):
    """How good the player's own team is for his job (0-100): real results (season + recent form)
    mostly, with a smaller share of attack (xG) or defence (xGC)."""
    out = {}
    for team_id, r in ratings.items():
        if position in ("GKP", "DEF"):
            xg_part = 100 - r["leaky"]
        else:
            xg_part = r["att"]
        out[team_id] = round(TEAM_XG_SHARE * xg_part + (1 - TEAM_XG_SHARE) * r["res"], 1)
    return out


def _horizon_gameweeks(gameweek, horizon):
    """The target gameweek plus the following ones that exist (horizon = 1..MAX_HORIZON)."""
    last = gameweek.fpl_id + horizon - 1
    return list(Gameweek.objects.filter(fpl_id__gte=gameweek.fpl_id, fpl_id__lte=last).order_by("fpl_id"))


class _TeamRef:
    """`_side_ease` only needs an `id`, so past fixtures can be rated without loading Team rows."""
    __slots__ = ("id",)

    def __init__(self, team_id):
        self.id = team_id


def _past_multipliers(gameweek, position, ratings):
    """{(team_id, gameweek): multiplier} for every fixture before `gameweek`: points scored against a
    hard opponent count a bit more, against an easy one a bit less. A double gameweek averages its
    two fixtures. Only the opponent's weakness and the venue count (own_share=0)."""
    if not PAST_FIXTURE_ADJUST:
        return {}
    blend = FIXTURE_MODE_BLEND[position]
    totals = {}  # (team_id, gw) -> [sum of ease, fixtures]
    fixtures = Fixture.objects.filter(gameweek__fpl_id__lt=gameweek.fpl_id).select_related("gameweek")
    for f in fixtures:
        gw = f.gameweek.fpl_id
        sides = (
            (f.team_home_id, f.team_away_id, True, f.difficulty_home),
            (f.team_away_id, f.team_home_id, False, f.difficulty_away),
        )
        for own_id, opp_id, is_home, fdr in sides:
            own, opp = _TeamRef(own_id), _TeamRef(opp_id)
            ease = sum(
                w * _side_ease(own, opp, is_home, fdr, mode, ratings, own_share=0.0)
                for mode, w in blend.items()
            )
            slot = totals.setdefault((own_id, gw), [0.0, 0])
            slot[0] += ease
            slot[1] += 1
    return {key: 1.0 + PAST_FIXTURE_ADJUST * (0.5 - s / n) for key, (s, n) in totals.items()}


def _fixture_context(gameweek, position, horizon=1, ratings=None):
    """Per team: summed fixture ease over the next `horizon` gameweeks (a double gameweek adds a
    second fixture, a blank adds nothing), plus fixture info. Only the opponent and the venue are
    rated here; the player's own team is scored by the team_strength axis."""
    gameweeks = _horizon_gameweeks(gameweek, horizon)
    fixtures = list(
        Fixture.objects.filter(gameweek__in=gameweeks)
        .select_related("team_home", "team_away", "gameweek")
        .order_by("gameweek__fpl_id", "kickoff_time")
    )
    if not fixtures:
        raise ValueError(f"Fixtures for {gameweek.name} are not synced yet.")

    teams = list(Team.objects.all())
    if ratings is None:
        ratings = _team_ratings(gameweek)

    blend = FIXTURE_MODE_BLEND[position]
    values = {t.id: 0.0 for t in teams}
    info = {t.id: [] for t in teams}
    per_gw = {t.id: {} for t in teams}   # team -> {gameweek: [ease of each fixture]}

    for f in fixtures:
        sides = (
            (f.team_home, f.team_away, True, f.difficulty_home),
            (f.team_away, f.team_home, False, f.difficulty_away),
        )
        for own, opp, is_home, fdr in sides:
            ease = sum(
                w * _side_ease(own, opp, is_home, fdr, mode, ratings, own_share=0.0)
                for mode, w in blend.items()
            )
            values[own.id] += ease
            per_gw[own.id].setdefault(f.gameweek.fpl_id, []).append(ease)
            info[own.id].append({
                "gameweek": f.gameweek.fpl_id, "opponent": opp.short_name, "home": is_home, "fdr": fdr,
            })
            
    # Weighted view: his first game weighs most. A double gameweek is the average of its two games,
    # a blank is skipped (weights of the games he really has are renormalized).
    weighted_values, next_values = {}, {}
    for team_id, by_gw in per_gw.items():
        if not by_gw:
            continue
        num = den = 0.0
        for i, gw in enumerate(sorted(by_gw)):
            w = UPCOMING_FIXTURE_WEIGHTS[min(i, len(UPCOMING_FIXTURE_WEIGHTS) - 1)]
            eases = by_gw[gw]
            num += w * sum(eases) / len(eases)
            den += w
        weighted_values[team_id] = num / den
        first = by_gw[min(by_gw)]
        next_values[team_id] = sum(first) / len(first)
    weighted_list, next_list = list(weighted_values.values()), list(next_values.values())   
    all_values = list(values.values())
    return {
        "sorted_values": sorted(all_values),
        "values": values,
        "scores": {team_id: _zscore_rating(v, all_values) for team_id, v in values.items()},
        "info": info,
        "gameweeks": [g.fpl_id for g in gameweeks],
        "team_strength": _team_strength(ratings, position),
        "weighted_scores": {t: _zscore_rating(v, weighted_list) for t, v in weighted_values.items()},
        "next_scores": {t: _zscore_rating(v, next_list) for t, v in next_values.items()},
    }


# ---------------------------------------------------------------------------
# Availability & playing time (multipliers, not weighted axes)
# ---------------------------------------------------------------------------
def _availability(player):
    chance = player.chance_of_playing_next_round
    if chance is not None:
        mult = chance / 100
    elif player.status == "a":
        mult = 1.0
    elif player.status == "d":
        mult = 0.5  # doubtful with no stated chance (assumption)
    else:
        mult = 0.0  # injured / suspended / unavailable / not in squad
    return {
        "status": player.status,
        "chance_of_playing": chance,
        "news": player.news,
        "multiplier": round(mult, 2),
    }


def _dc_bonus(stats, population, position):
    """DC bonus for midfielders and defenders, only if he has a real DC threat."""
    apps = stats["last_apps"]
    avg = stats["last_dc"] / apps if apps else 0.0
    by_average = apps >= MID_DC_MIN_APPEARANCES and avg >= DC_AVG_MIN[position]
    by_hits = apps >= MID_DC_MIN_APPEARANCES and stats["last_dc_hits"] / apps >= MID_DC_HIT_SHARE
    # The real percentile is always computed: it is what the radar / table show. Only the BONUS
    # depends on a real DC threat, so a player without one is no longer drawn as a hard 0.
    if apps:
        rate = _shrunk_rate_capped(stats["last_dc"], stats["last_minutes"], population["medians"]["dc_recent"])
        pct = _percentile(rate, population["metrics"]["dc_recent"])
    else:
        pct = 0.0
    if not (by_average or by_hits):
        return {"active": False, "avg": avg, "pct": pct, "bonus": 0.0}
    strength = 1.0 if (by_average and by_hits) else 0.75
    return {"active": True, "avg": avg, "pct": pct,
            "bonus": round(DC_BONUS_MAX[position] * strength * pct / 100, 2)}


def _midfielder_dc(stats, population):
    return _dc_bonus(stats, population, "MID")


def _out_label(status):
    """Plain word for why a player is ruled out ('d' with a 0% chance is simply unavailable)."""
    return STATUS_LABELS.get(status, "unavailable") if status != "d" else "unavailable"


def _player_notes(player, availability, stats, window_ids, minutes_per_appearance, returned=None):
    """Plain-English notes for the user (level: high / medium / low): availability, long absence,
    very few minutes. They are shown next to the player and passed to the verdict."""
    notes = []
    mult = availability["multiplier"]
    news = (player.news or "").strip()
    if mult < 1:
        if mult <= 0:
            label = _out_label(player.status)
            text = f"Ruled out for the next gameweek ({label}), so he is not recommended as a pick."
            level = "high"
        else:
            text = f"Doubtful for the next gameweek (about a {int(round(mult * 100))}% chance of playing)."
            level = "medium"
        if news:
            text += f" News: {news.rstrip('.')}."
        if not AVAILABILITY_AFFECTS_SCORE:
            text += " His score only shows how well he plays when fit."
        notes.append({"type": "availability", "level": level, "text": text})

    window = len(window_ids)
    window_apps = int(stats["recent_apps"])
    last_apps = int(stats["last_apps"])
    last_gw = int(stats["last_played_gw"])
    if last_apps == 0:
        notes.append({
            "type": "no_games", "level": "high",
            "text": "Has not played any minutes this season, so there is nothing to judge him on yet.",
        })
        return notes

    if window_apps == 0:
        notes.append({
            "type": "absent", "level": "high",
            "text": (f"Has not played in the last {window} gameweeks (last appeared in gameweek {last_gw}). "
                     f"His numbers come from his last {last_apps} games, so they may be out of date."),
        })
    elif returned:
        games = returned["games_since"]
        notes.append({
            "type": "returned", "level": "medium",
            "text": (f"Just back after missing about {returned['missed']} gameweeks, with "
                     f"{games} game{'s' if games > 1 else ''} played since. The rest of his numbers are "
                     "from before the break, so he may not be at full minutes yet."),
        })
    elif window_apps <= window - 2:
        notes.append({
            "type": "absent", "level": "low",
            "text": (f"Played only {window_apps} of the last {window} gameweeks. "
                     "His numbers only count the games he actually played."),
        })

    # No note for low playing time: its effect is shown only through the Minutes axis (the multiplier).
    if stats["season_minutes"] < MIN_POP_MINUTES:
        notes.append({
            "type": "low_sample", "level": "low",
            "text": "Very little playing time this season, so his numbers are less reliable.",
        })
    return notes


def _score_player(player, agg, window_ids, population, fixture_ctx, ignore_minutes=False, apply_elite_floor=True):
    position = player.position
    stats = agg.get(player.id, _EMPTY_AGG)
    fixture_list = fixture_ctx["info"].get(player.team_id, [])
    fdrs = [f["fdr"] for f in fixture_list if f["fdr"] is not None]
    avg_fdr = round(sum(fdrs) / len(fdrs), 2) if fdrs else None
    fixture_pct = fixture_ctx["scores"].get(player.team_id, 50.0)

    breakdown = {}
    base = 0.0
    for key, weight in POSITION_WEIGHTS[position].items():
        if key == "form":
            value, form_recent, form_season = _form_value(stats, population["medians"])
            pct = _form_rating(value, population["metrics"]["form"])
        elif key == "fixtures_fdr":
            value, pct = avg_fdr, fixture_pct
        elif key == "team_strength":
            pct = fixture_ctx.get("team_strength", {}).get(player.team_id, 50.0)
            value = pct
        else:
            spec = METRICS[key]
            value = _shrunk_rate_capped(stats[spec["num"]], stats[spec["den"]], population["medians"][key])
            pct = _percentile(value, population["metrics"][key], spec["lower"])
        label = METRICS[key]["label"] if key in METRICS else EXTRA_LABELS[key]
        breakdown[key] = {
            "label": label,
            "value": round(value, 2) if value is not None else None,
            "score": pct,
            "weight": weight,
        }
        base += pct * weight

    # These two numbers are "lower is better": say what they are so they are not read as a score.
    if "fixtures_fdr" in breakdown:
        breakdown["fixtures_fdr"]["display"] = (
            f"{avg_fdr:g} / 5"
            if avg_fdr is not None else "No upcoming fixture"
        )
    if "xgc_season" in breakdown:
        breakdown["xgc_season"]["display"] = f"{breakdown['xgc_season']['value']} xGC/90"

    if "form" in breakdown:
        games = int(stats["last_apps"])
        total = int(stats["last_points"])
        returns, hauls = int(stats["last_returns"]), int(stats["last_hauls"])
        breakdown["form"].update({
            "games": games,
            "recent_points": total,
            "returns": returns,
            "hauls": hauls,
            "recent_points_per_game": round(total / games, 2) if games else 0.0,
            "season_points_per_game": round(form_season, 2),
            "display": (f"{total} pts{'' if games >= RECENT_APPEARANCES else f' in {games}'}"
                        f" · {returns} {'return' if returns == 1 else 'returns'}"
                        f" · {hauls} {'haul' if hauls == 1 else 'hauls'}"),
        })

    if position in DC_BONUS_MAX:                                              # CHANGED (was == "MID")
        dc = _dc_bonus(stats, population, position)
        base += dc["bonus"]
        breakdown["dc_recent"] = {
            "label": METRICS["dc_recent"]["label"],
            "value": round(dc["avg"], 1),
            "score": dc["pct"],
            "weight": 0,  # a bonus only for players with a real DC threat
            "active": dc["active"],
            "bonus": dc["bonus"],                                             # (debug / display)
        }

    minutes_per_gw = stats["recent_minutes"] / len(window_ids)
    appearances = int(stats["recent_apps"])      # games played in the last few gameweeks
    last_apps = int(stats["last_apps"])          # his last games actually played (up to RECENT_APPEARANCES)
    minutes_per_appearance = stats["last_minutes"] / last_apps if last_apps else 0.0
    points_per_appearance = stats["last_points"] / last_apps if last_apps else 0.0
    points_pct = (
        _percentile(points_per_appearance, population["metrics"]["points_per_app"])
        if last_apps >= MIN_APPEARANCES_FOR_POINTS
        else None
    )

    returned = _return_info(stats, window_ids[0])
    if appearances >= MIN_APPEARANCES_FOR_SHARE and not returned:
        effective_minutes = (
            (1 - WHEN_PLAYING_SHARE) * minutes_per_gw + WHEN_PLAYING_SHARE * minutes_per_appearance
        )
    else:
        # Out of the window altogether, or just back from a real spell out: missed games are not held
        # against him, he is judged on what he plays when he does play. The absence is shown to the
        # user as a note instead.
        effective_minutes = minutes_per_appearance
    playing_factor = min(effective_minutes / FULL_MINUTES_PER_GW, 1.0)

    # His last two finalized gameweeks, 0-minute games included (only while he is fit).
    recent_games = _recent_game_minutes(stats, window_ids, player.status)
    latest_minutes, previous_minutes = recent_games
    recent_appearance_minutes = stats.get("last_appearance_minutes", [])
    minutes_risk = _recent_minutes_risk(recent_appearance_minutes, latest_minutes, previous_minutes)
    # Repeated short appearances signal rotation/substitute risk; a single short or missed last game
    # gets a lighter penalty. These risks bypass elite-score protection below;
    # missed gameweeks due to injury remain handled separately.
    if minutes_risk == "severe":
        playing_factor = min(playing_factor, RECENT_LOW_MINUTES_FACTOR)
    elif minutes_risk == "rotation":
        playing_factor = min(playing_factor, RECENT_ROTATION_FACTOR)
    elif minutes_risk == "single":
        playing_factor = min(playing_factor, LAST_GAME_LOW_FACTOR)
    sub_risk = minutes_risk is not None
    scores_well = points_pct is not None and points_pct >= HIGH_POINTS_PCT
    if apply_elite_floor and not sub_risk and (base >= ELITE_BASE_SCORE or scores_well) and last_apps > 0:
        playing_factor = max(playing_factor, ELITE_MIN_FACTOR)
    if ignore_minutes and not sub_risk:  # equalize only when neither shows recent sub risk
        playing_factor = 1.0
    breakdown["minutes_per_game"] = {
        "label": "Minutes",
        "value": round(minutes_per_appearance, 1),
        "score": round(playing_factor * 100, 1),
        "weight": 0,  # multiplier, see below
        "last_game": latest_minutes,
    }

    availability = _availability(player)
    final = base * playing_factor * (availability["multiplier"] if AVAILABILITY_AFFECTS_SCORE else 1.0)
    price = float(player.price)
    last_gw = int(stats["last_played_gw"])
    notes = _player_notes(player, availability, stats, window_ids, minutes_per_appearance, returned)

    return {
        "id": player.id,
        "name": player.web_name,
        "team": player.team.short_name,
        "position": position,
        "price": round(price, 1),
        "code": player.code,
        "photo_url": PHOTO_URL.format(code=player.code) if player.code else None,
        "status": player.status,
        "recommended": availability["multiplier"] > 0,  # False = ruled out for the next gameweek
        "score": round(final, 2),
        "base_score": round(base, 2),  # before the playing-time and availability multipliers
        "playing_factor": round(playing_factor, 2),
        "minutes_equalized": ignore_minutes,
        "minutes_per_gameweek": round(minutes_per_gw, 1),
        "minutes_per_appearance": round(minutes_per_appearance, 1),
        "last_game_minutes": latest_minutes,
        "previous_game_minutes": previous_minutes,
        "recent_appearances": appearances,
        "window_gameweeks": len(window_ids),
        "last_games_used": last_apps,
        "last_played_gameweek": last_gw or None,
        "gameweeks_since_played": (window_ids[0] - last_gw) if last_gw else None,
        "returned_from_layoff": returned is not None,
        "notes": notes,
        "points_per_appearance": round(points_per_appearance, 1),
        "points_percentile": points_pct,
        "score_per_million": round(final / price, 2) if price > 0 else None,
        "low_sample": stats["season_minutes"] < MIN_POP_MINUTES,
        "availability": availability,
        "fixtures": fixture_list,
        "breakdown": breakdown,
        "season_points": int(player.total_points),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def _pick_winner(entry_a, entry_b):
    """(winner_id, ruled_out_override, caution). A player who is ruled out for the next gameweek is
    never the recommended pick, whatever his numbers. If both are out, or the winner is only
    doubtful, the higher score still wins but a caution explains the risk."""
    out_a = entry_a["availability"]["multiplier"] <= 0
    out_b = entry_b["availability"]["multiplier"] <= 0
    by_score = entry_a if entry_a["score"] >= entry_b["score"] else entry_b

    if out_a != out_b:
        winner, loser = (entry_b, entry_a) if out_a else (entry_a, entry_b)
        caution = (f"{loser['name']} is ruled out for the next gameweek, so he is not recommended, "
                   "whatever his numbers.")
        return winner["id"], True, caution
    if out_a and out_b:
        caution = "Both players are ruled out for the next gameweek. Neither is a good pick right now."
        return by_score["id"], False, caution
    if by_score["availability"]["multiplier"] < 1:
        caution = (f"{by_score['name']} has the higher score but is doubtful for the next gameweek. "
                   "Check the latest news before choosing him.")
        return by_score["id"], False, caution
    return by_score["id"], False, None


def compare_players(player_a_id: int, player_b_id: int, lang: str = "en", horizon: int = 1) -> dict:
    if player_a_id == player_b_id:
        raise ValueError("Choose two different players.")
    if not isinstance(horizon, int) or not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"Choose between 1 and {MAX_HORIZON} gameweeks to look ahead.")

    players = {
        p.id: p for p in Player.objects.select_related("team").filter(id__in=[player_a_id, player_b_id])
    }
    player_a, player_b = players.get(player_a_id), players.get(player_b_id)
    if not player_a or not player_b:
        raise ValueError("One or both players not found.")
    if player_a.position != player_b.position:
        raise ValueError("Players must be in the same position.")

    gameweek = _target_gameweek()
    if not gameweek:
        raise ValueError("No active gameweek found.")

    window_ids = _recent_window_ids(gameweek)
    if not window_ids:
        raise ValueError("Not enough finished-gameweek data to compare players yet.")

    position = player_a.position
    ratings = _team_ratings(gameweek)  # computed once: used by both the past-game and the upcoming-fixture ease
    population = _get_population(position, gameweek, window_ids)
    agg = _aggregate_stats(position, gameweek, window_ids, player_ids=[player_a.id, player_b.id], ratings=ratings)
    fixture_ctx = _fixture_context(gameweek, position, horizon, ratings=ratings)

    def _minutes_per_appearance(player_id):
        a = agg.get(player_id, _EMPTY_AGG)
        return a["last_minutes"] / a["last_apps"] if a["last_apps"] else 0.0

    def _last_game_full(player):
        # Unknown last game (None) does not block the equalization.
        latest, _ = _recent_game_minutes(agg.get(player.id, _EMPTY_AGG), window_ids, player.status)
        return latest is None or latest >= EQUAL_MINUTES_LAST_GAME

    ignore_minutes = (
        _minutes_per_appearance(player_a.id) >= EQUAL_MINUTES_PER_APPEARANCE
        and _minutes_per_appearance(player_b.id) >= EQUAL_MINUTES_PER_APPEARANCE
        and _last_game_full(player_a)
        and _last_game_full(player_b)
    )

    entry_a = _score_player(player_a, agg, window_ids, population, fixture_ctx, ignore_minutes)
    entry_b = _score_player(player_b, agg, window_ids, population, fixture_ctx, ignore_minutes)

    # Midfielders and defenders: if neither has a real DC threat, DC plays no part, so hide the axis.
    # (This used to compare the position string with the dict itself, so it never ran.)
    if position in DC_BONUS_MAX and not any(e["breakdown"]["dc_recent"]["active"] for e in (entry_a, entry_b)):
        for e in (entry_a, entry_b):
            del e["breakdown"]["dc_recent"]

    winner_id, ruled_out_override, caution = _pick_winner(entry_a, entry_b)
    margin = round(abs(entry_a["score"] - entry_b["score"]), 2)
    close_call = False if ruled_out_override else margin < CLOSE_CALL_MARGIN

    try:
        verdict = generate_comparison_verdict(
            entry_a, entry_b, winner_id, gameweek, lang, close_call, horizon,
            ruled_out_override=ruled_out_override,
        )
    except Exception:
        logger.exception("Comparison verdict generation failed")
        verdict = ""

    covered = fixture_ctx["gameweeks"]
    label = gameweek.name if len(covered) == 1 else f"Gameweeks {covered[0]}-{covered[-1]}"
    return {
        "gameweek": label,
        "horizon": len(covered),
        "gameweeks": covered,
        "winner_id": winner_id,
        "margin": margin,
        "close_call": close_call,
        "caution": caution,
        "players": {str(entry_a["id"]): entry_a, str(entry_b["id"]): entry_b},
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# LLM verdict
# ---------------------------------------------------------------------------
# The CODE decides who is ahead on every aspect (from the same scores and weights that make the
# final score); the LLM only puts that into words. Its answer is checked against those decisions
# before it is shown, and a plain template is used if it keeps contradicting them.
EDGE_CLEAR = 1.5          # an aspect moves the overall score by at least this => "clearly" ahead
EDGE_SLIGHT = 0.5         # below this the two players are level on that aspect
PLAYING_TIME_GAP = 0.05   # playing-time multipliers closer than this => equal
VERDICT_ATTEMPTS = 2

ASPECT_WORDS = {
    "en": {
        "form": "recent form (points)",
        "xgi_recent": "recent goal involvement (chances created and taken)",
        "xgi_season": "goal involvement over the whole season",
        "xgc_season": "defensive solidity this season",
        "cs_season": "clean sheets this season",
        "bps_recent": "recent shot-stopping (bonus points)",
        "dc_recent": "recent defensive actions",
        "team_strength": "strength and form of his team",
        "fixtures_fdr": "the upcoming fixtures",
        "playing_time": "playing time",
    },
    "fa": {
        "form": "فرم اخیر",
        "xgi_recent": "مشارکت اخیر در گل",
        "xgi_season": "مشارکت در گل در کل فصل",
        "xgc_season": "استحکام دفاعی در این فصل",
        "cs_season": "کلین‌شیت در این فصل",
        "bps_recent": "دفاع از دروازه در بازی‌های اخیر",
        "dc_recent": "اقدامات دفاعی اخیر",
        "team_strength": "قدرت و فرم تیمش",
        "fixtures_fdr": "بازی‌های پیش رو",
        "playing_time": "دقایق بازی",
    },
}
_PLAYING_TIME_RE = re.compile(r"\bminutes?\b|playing time|rotat|starting (?:place|spot|role)|bench|دقیقه|دقایق|نیمکت|چرخش", re.I)


def _role(points):
    if points >= EDGE_SLIGHT:
        return "winner"
    if points <= -EDGE_SLIGHT:
        return "other"
    return "level"


def _verdict_edges(winner, other):
    """Who is ahead on each aspect, in overall-score points: weight x score gap per axis, which is
    exactly how the final score is built. Playing time is the gap between the two multipliers."""
    points = {}
    for key, w in winner["breakdown"].items():
        o = other["breakdown"].get(key)
        weight = w.get("weight") or 0
        if weight <= 0 or o is None or w.get("score") is None or o.get("score") is None:
            continue
        points[key] = weight * (w["score"] - o["score"])
    gap = winner["playing_factor"] - other["playing_factor"]
    avg_base = (winner["base_score"] + other["base_score"]) / 2
    points["playing_time"] = gap * avg_base if abs(gap) >= PLAYING_TIME_GAP else 0.0

    words = ASPECT_WORDS["en"]
    edges = sorted(
        (
            {"aspect": k, "meaning": words[k], "ahead": _role(p), "size": "clearly" if abs(p) >= EDGE_CLEAR else "slightly"}
            for k, p in points.items() if _role(p) != "level"
        ),
        key=lambda e: -abs(points[e["aspect"]]),
    )
    return {
        "edges": edges,
        "level_on": [k for k, p in points.items() if _role(p) == "level"],
        "truth": {k: _role(p) for k, p in points.items()},
        "keeps_it_close": next((e for e in edges if e["ahead"] == "other"), None),
    }


def _verdict_player(entry, covered):
    """Plain facts about one player. No raw minutes, no FDR numbers and no fixture list: fixture
    quality and playing time are already decided in the edges."""
    form = entry["breakdown"]["form"]
    per_gw = Counter(f["gameweek"] for f in entry["fixtures"])
    return {
        "team": entry["team"],
        "position": entry["position"],
        "price_million": entry["price"],
        "recent_games": {
            "games": form["games"],
            "points": form["recent_points"],
            "returns_of_5_plus_points": form["returns"],
            "hauls_of_10_plus_points": form["hauls"],
            "gameweeks_since_he_last_played": entry["gameweeks_since_played"],
        },
        "availability": entry["availability"],
        "notes_shown_to_the_reader": [n["text"] for n in entry["notes"]],
        "no_fixture_in_gameweeks": [g for g in covered if g not in per_gw],
        "double_gameweeks": [g for g, c in per_gw.items() if c > 1],
    }


def _verdict_consistent(text, claims, facts, has_notes):
    """The verdict is only shown when what it claims matches the decisions made in code."""
    if not claims:
        return False
    truth = facts["truth"]
    if any(truth.get(c.get("aspect")) != c.get("ahead") for c in claims):
        return False
    if truth.get("playing_time") == "level" and not has_notes and _PLAYING_TIME_RE.search(text):
        return False
    return True


def _fallback_verdict(winner, other, facts, lang, close_call, ruled_out_override, both_out):
    """Plain sentence built from the decisions, used when the LLM is unavailable or keeps contradicting them."""
    fa = lang == "fa"
    w, o = f"{{player_{winner['id']}}}", f"{{player_{other['id']}}}"
    if both_out:
        return ("هر دو بازیکن برای گیم‌ویک بعد خارج از دسترس‌اند، پس الان هیچ‌کدوم انتخاب خوبی نیستن." if fa
                else "Both players are ruled out for the next gameweek, so neither is a good pick right now.")
    if ruled_out_override:
        return (f"{w} انتخاب این گیم‌ویکه، چون {o} برای گیم‌ویک بعد خارج از دسترسه." if fa
                else f"{w} is the pick because {o} is ruled out for the next gameweek.")

    words = ASPECT_WORDS["fa" if fa else "en"]
    mine = [words[e["aspect"]] for e in facts["edges"] if e["ahead"] == "winner"][:2]
    theirs = facts["keeps_it_close"]
    if fa:
        text = f"{w} انتخاب بهتریه" + (f"، چون در {' و '.join(mine)} جلوتره." if mine else ".")
        if theirs:
            text += f" {o} در {words[theirs['aspect']]} جلوتره" + ("، برای همین انتخاب خیلی نزدیکه." if close_call else ".")
        elif close_call:
            text += " انتخاب خیلی نزدیکه."
        return text
    text = f"{w} is the better pick" + (f", ahead on {' and '.join(mine)}." if mine else ".")
    if theirs:
        text += f" {o} is ahead on {words[theirs['aspect']]}" + (", which is why it is a narrow call." if close_call else ".")
    elif close_call:
        text += " It is a very narrow call."
    return text


def generate_comparison_verdict(entry_a: dict, entry_b: dict, winner_id: int, gameweek,
                                lang: str = "en", close_call: bool = False, horizon: int = 1,
                                ruled_out_override: bool = False) -> str:
    first, second = sorted([entry_a, entry_b], key=lambda e: e["id"])
    winner, other = (entry_a, entry_b) if entry_a["id"] == winner_id else (entry_b, entry_a)
    other_id = other["id"]
    both_out = all(e["availability"]["multiplier"] <= 0 for e in (entry_a, entry_b))
    both_weak = all(e["breakdown"]["form"]["score"] < WEAK_FORM_PERCENTILE for e in (entry_a, entry_b))
    has_notes = any(e["notes"] for e in (entry_a, entry_b))
    facts = _verdict_edges(winner, other)

    # The key includes a fingerprint of everything that drives the verdict, so a cached
    # verdict is discarded automatically when scores, price or availability change.
    fingerprint = hashlib.sha1(
        "|".join(
            str(x)
            for e in (first, second)
            for x in (e["score"], e["price"], e["availability"]["multiplier"], e["availability"]["status"],
                      "/".join(n["type"] + n["level"] for n in e["notes"]))
        ).encode("utf-8")
        + f"|{int(close_call)}|{horizon}|{int(ruled_out_override)}|{int(both_out)}|{int(both_weak)}|{winner_id}".encode("utf-8")
        + json.dumps(facts["truth"], sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    cache_key = f"compare:verdict:v10:{first['id']}:{second['id']}:{gameweek.id}:{horizon}:{lang}:{fingerprint}"
    cached = redis_client.get(cache_key)
    if cached:
        return cached.decode("utf-8")

    id_to_name = {entry_a["id"]: entry_a["name"], entry_b["id"]: entry_b["name"]}

    def finish(text):
        return sanitize_markdown(_substitute_player_placeholders(text, id_to_name))

    language_word = "Persian" if lang == "fa" else "English"
    covered = [g for g in range(gameweek.fpl_id, gameweek.fpl_id + horizon) if g <= 38]
    summary = {
        "winner_id": winner_id,
        "other_id": other_id,
        "close_call": close_call,
        "both_players_in_poor_recent_form": both_weak,
        "edges": facts["edges"],
        "level_on": facts["level_on"],
        "what_keeps_it_close": facts["keeps_it_close"],
        "players": {str(e["id"]): _verdict_player(e, covered) for e in (entry_a, entry_b)},
    }

    if ruled_out_override:
        lead = (f"Player with id {winner_id} is the pick because the other player is ruled out for the next "
                "gameweek (injured or suspended), whatever his numbers. Say this plainly first, then add one "
                "short point about the pick's form or fixtures. Do not say the pick scores higher.")
    elif both_out:
        lead = ("Both players are ruled out for the next gameweek. Recommend neither and say so plainly; "
                "do not call either of them the better pick.")
    elif close_call:
        lead = (f"Player with id {winner_id} scores slightly higher overall. Say plainly that it is a narrow "
                "call. If \"what_keeps_it_close\" is not null, mention it as the reason it is close; otherwise "
                "just say the two are very close. Do not suggest that anything else could swing it.")
    else:
        lead = (f"Player with id {winner_id} scores higher overall. Explain clearly why he is the better pick "
                "using the edges that favour him; if an edge for the other player is \"clearly\", acknowledge it "
                "in one short clause.")

    if both_weak and not (ruled_out_override or both_out):
        lead += (" Both players are in poor recent form. Say this plainly in the first sentence and present "
                 "the pick as the less bad option, not as a good one. Do not describe either player in "
                 "positive absolute terms; only compare them to each other.")

    scope = (
        f"the upcoming gameweek ({gameweek.name})" if horizon == 1
        else f"the next {horizon} gameweeks starting with {gameweek.name}"
    )
    prompt = f"""Compare these two FPL players for {scope}, both in the same position.
This is only about that period, not the long term.

{json.dumps(summary, indent=2)}

{lead}
Write a short verdict (2-3 sentences) in {language_word}.

How to use the data:
- "edges" is the complete and final comparison. Each entry says who is ahead on one aspect
  ("winner" = player {winner_id}, "other" = player {other_id}) and by how much ("clearly" or "slightly").
  Base the verdict ONLY on these edges: never contradict a direction, never claim an edge on an aspect
  listed in "level_on", and never compare raw statistics yourself.
- Give the 1-2 most relevant reasons as comparisons between the two players ("A is ahead of B on ...").
  Do not describe either player in absolute terms (strong, poor, ...), only compared with the other,
  unless both_players_in_poor_recent_form says otherwise.
- The aspect "fixtures_fdr" already weighs every fixture in the period, home or away, and how strong each
  opponent really is. Do not judge the fixtures yourself and do not name individual opponents.
- If "playing_time" is in "level_on", both are regular starters: do not mention minutes, rotation or
  playing time at all. Never speculate about future minutes, form or fixtures.
- If a team has no fixture ("no_fixture_in_gameweeks") or a double gameweek, say so.
- The score is pure performance and does NOT include injuries. If the higher-scoring player is
  injured, suspended or doubtful, say so clearly: it matters more than the score. If a player has
  not played for a long time or plays very few minutes (see notes_shown_to_the_reader), mention the
  most important point in simple words a beginner understands.
- Do not describe a player as weaker just because he missed recent gameweeks (for example through
  injury): judge him on how he performed in the games he played.
- Price is NOT part of the score. Mention the price difference only when it is large, as budget
  context for the reader, never as a reason a player performs better.

Whenever you refer to a player, use {{player_<id>}} (e.g. {{player_{winner_id}}}) - never write
their actual name.
Finally fill "claims": one entry for every aspect your verdict says someone is ahead on or level on
(aspect = the code used in "edges" / "level_on", ahead = winner, other or level). Claim nothing else.
"""

    checked = not (ruled_out_override or both_out)
    verdict = ""
    for _ in range(VERDICT_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                response_format={"type": "json_schema", "json_schema": COMPARISON_RESPONSE_SCHEMA},
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "", 1).replace("```", "").strip()
            parsed = json.loads(raw)
        except Exception:
            logger.warning("Verdict generation failed", exc_info=True)
            continue
        text = (parsed.get("verdict") or "").strip()
        if text and (not checked or _verdict_consistent(text, parsed.get("claims") or [], facts, has_notes)):
            verdict = finish(text)
            redis_client.set(cache_key, verdict, ex=VERDICT_TTL)
            return verdict
        logger.warning("Verdict rejected by the consistency check: %r", text[:200])

    # Not cached: a transient failure should not hide a good LLM verdict for hours.
    return finish(_fallback_verdict(winner, other, facts, lang, close_call, ruled_out_override, both_out))