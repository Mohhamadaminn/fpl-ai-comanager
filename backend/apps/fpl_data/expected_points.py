"""Expected points (xP) for the upcoming gameweek, with a P10-P90 range.

Squad Check uses this to show every player of a squad with what he is expected to score and how wide
the realistic spread is. Like the rest of the project it is statistics first: no LLM, no invented numbers.

How a player's number is built
------------------------------
For every match he has in the target gameweek (none = blank gameweek, two = double gameweek):

1. Minutes. P(he plays) = availability (FPL's chance of playing, see compare_engine._availability)
   x how often he was used by his team recently. If he plays, the share of 60+ minute games among his
   last appearances decides between a full game (2 appearance points) and a cameo (1 point).
2. The match. A goals model of the two teams (Poisson, home advantage, attack and defence ratings from
   results with a smaller share of xG, season and last-5 blended) gives the opponent's effect on his
   goals / assists, the clean-sheet chance and the saves he faces.
3. Per-90 rates for xG, xA, saves, cards come from his own season and recent games, shrunk towards the
   position average so a small sample cannot run away (same idea as compare_engine).
4. Every FPL scoring rule that has data behind it becomes a small distribution: appearance, goals,
   assists, clean sheet and goals conceded, saves, bonus, defensive contribution, yellow and red cards.
   The distributions are convolved EXACTLY (no random simulation), so the answer is deterministic.

xP is the mean of that distribution; the range is its 10th and 90th percentile (about 80% of outcomes).

Known simplifications (see the README section): own goals, penalty misses / saves and captaincy are not
modelled; the components of one match are treated as independent; squad totals assume players are
independent of each other, so the total's range is a little narrower than reality.

Data enters through a `Dataset` (plain Python lists), so everything below the loaders is pure and can be
tested, or backtested for any past gameweek, without a database.
"""
import logging
import math
from dataclasses import dataclass, field

from django.db.models import Max, Sum

from .compare_engine import (
    PHOTO_URL,
    PRIOR_MINUTES,
    RECENT_APPEARANCES,
    TEAM_FORM_PRIOR,
    TEAM_FORM_WINDOW,
    TEAM_SEASON_SHARE,
    TEAM_XG_SHARE,
    ELITE_BASE_SCORE,
    _availability,
    _recent_window_ids,
    _target_gameweek,
    _team_ratings,
)
from .models import Fixture, Player, PlayerGameweekStat, Team
from .services import ASSIST_POINTS, CLEAN_SHEET_POINTS, DC_POINTS, DC_THRESHOLD, GOAL_POINTS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunable constants (start values; `manage.py backtest_xp` is how they should be checked and tuned)
# ---------------------------------------------------------------------------
XP_RECENT_WEIGHT = 0.4         # share of the last RECENT_APPEARANCES games in a per-90 rate (rest: season)
SELECTION_PRIOR_GAMES = 2      # pseudo-games of his season usage share added to his recent usage
BONUS_PRIOR_GAMES = 3          # pseudo-appearances of the position average for bonus and DC rates
FULL_GAME_MINUTES = 60         # FPL: 60+ minutes = 2 appearance points, clean-sheet eligible
RANGE_LOW, RANGE_HIGH = 0.10, 0.90
LOW_DATA_APPEARANCES = 3       # fewer appearances than this => flagged "limited data"
YELLOW_POINTS, RED_POINTS = -1, -3
GOAL_KMAX, ASSIST_KMAX, CONCEDED_KMAX, SAVES_KMAX = 6, 6, 12, 15   # Poisson tails are folded into the last bin
MIN_PROB = 1e-12
# Optional previous-season prior (Dataset.priors). Empty today; meant for the start of next season.
PRIOR_SEASON_MAX_MINUTES = 1500   # a previous season never counts as more than this many minutes...
PRIOR_SEASON_DISCOUNT = 0.5       # ...and is worth only this share of the same minutes played now


# ---------------------------------------------------------------------------
# Distributions: {points: probability}
# ---------------------------------------------------------------------------
def _poisson_probs(mu, kmax):
    mu = max(0.0, float(mu))
    probs, term, cum = [], math.exp(-mu), 0.0
    for k in range(kmax):
        probs.append(term)
        cum += term
        term = term * mu / (k + 1)
    probs.append(max(0.0, 1.0 - cum))
    return probs


def _add(out, points, prob):
    if prob > MIN_PROB:
        out[points] = out.get(points, 0.0) + prob


def _count_pmf(mu, points_each, kmax):
    """Poisson count of events worth `points_each` each."""
    out = {}
    for k, p in enumerate(_poisson_probs(mu, kmax)):
        _add(out, k * points_each, p)
    return out


def _bernoulli(p, points):
    p = min(1.0, max(0.0, p))
    out = {}
    _add(out, 0, 1.0 - p)
    _add(out, points, p)
    return out


def _bonus_pmf(mean_bonus):
    """Bonus is 0-3: Binomial(3, mean/3), which keeps the player's mean and the 0-3 limits."""
    q = min(1.0, max(0.0, mean_bonus / 3.0))
    out = {}
    for k, comb in enumerate((1, 3, 3, 1)):
        _add(out, k, comb * q ** k * (1 - q) ** (3 - k))
    return out


def _defence_pmf(position, lam, cs_eligible):
    """Clean sheet and goals conceded come from ONE variable (goals conceded while he is on the pitch),
    so they are built together: CS points when it is 0 (60+ minutes only), -1 per 2 goals for GKP / DEF."""
    cs_points = CLEAN_SHEET_POINTS.get(position, 0) if cs_eligible else 0
    out = {}
    for k, p in enumerate(_poisson_probs(lam, CONCEDED_KMAX)):
        points = cs_points if k == 0 else 0
        if position in ("GKP", "DEF"):
            points -= k // 2
        _add(out, points, p)
    return out


def _saves_pmf(mu):
    out = {}
    for k, p in enumerate(_poisson_probs(mu, SAVES_KMAX)):
        _add(out, k // 3, p)       # 1 point per 3 saves
    return out


def _convolve(a, b):
    out = {}
    for x, px in a.items():
        for y, py in b.items():
            _add(out, x + y, px * py)
    return out


def _convolve_all(parts):
    result = {0: 1.0}
    for part in parts:
        result = _convolve(result, part)
    return result


def _mix(weighted):
    out = {}
    for weight, pmf in weighted:
        if weight <= 0:
            continue
        for x, p in pmf.items():
            _add(out, x, weight * p)
    return out


def pmf_mean(pmf):
    return sum(x * p for x, p in pmf.items())


def pmf_quantile(pmf, q):
    """Smallest outcome whose cumulative probability reaches q."""
    cum = 0.0
    for x in sorted(pmf):
        cum += pmf[x]
        if cum >= q - 1e-9:
            return x
    return max(pmf)


def summarize(pmf):
    """Mean and P10-P90. The average can fall just outside the percentiles when the distribution is
    lopsided (e.g. a 9% chance of 0 points pulls the mean under P10), which would look like a bug on
    screen, so the range is widened to the nearest whole point that contains the average."""
    mean = pmf_mean(pmf)
    low = min(pmf_quantile(pmf, RANGE_LOW), math.floor(mean + 1e-9))
    high = max(pmf_quantile(pmf, RANGE_HIGH), math.ceil(mean - 1e-9))
    return {"xp": mean, "low": low, "high": high}


# ---------------------------------------------------------------------------
# Team goals model
# ---------------------------------------------------------------------------
@dataclass
class TeamModel:
    mu_home: float                       # league average goals of a home side
    mu_away: float
    att: dict = field(default_factory=dict)    # team id -> attack rating (1.0 = league average)
    deff: dict = field(default_factory=dict)   # team id -> how many goals it concedes vs average (>1 = leaky)


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def build_team_model(matches):
    """matches: one record per team per finished match, oldest first:
    {"gw", "team", "home" (bool), "gf", "ga", "xgf", "xga"} (xG may be None).

    Same recipe as the engine's team ratings: results carry most of the weight, xG the TEAM_XG_SHARE rest;
    season and last-TEAM_FORM_WINDOW matches are blended; small samples are shrunk to the league average.
    Every match is first divided by what a typical home (or away) side scores / concedes, so a team that
    happened to play more home games is not rated higher for it."""
    if not matches:
        raise ValueError("Not enough finished matches yet to estimate expected points.")

    mu_home = _mean(m["gf"] for m in matches if m["home"])
    mu_away = _mean(m["gf"] for m in matches if not m["home"])
    if mu_home <= 0 or mu_away <= 0:
        raise ValueError("Not enough goals scored yet to estimate expected points.")

    def blend(m, goals_key, xg_key):
        xg = m[xg_key]
        return m[goals_key] if xg is None else (1 - TEAM_XG_SHARE) * m[goals_key] + TEAM_XG_SHARE * xg

    venue_mean = {}
    for goals_key, xg_key in (("gf", "xgf"), ("ga", "xga")):
        for home in (True, False):
            venue_mean[(goals_key, home)] = _mean(blend(m, goals_key, xg_key) for m in matches if m["home"] == home)

    def relative(m, goals_key, xg_key):
        mean = venue_mean[(goals_key, m["home"])]
        return blend(m, goals_key, xg_key) / mean if mean > 0 else 1.0

    by_team = {}
    for m in matches:
        by_team.setdefault(m["team"], []).append(m)

    def rating(recs, goals_key, xg_key):
        values = [relative(m, goals_key, xg_key) for m in recs]
        blended = TEAM_SEASON_SHARE * _mean(values) + (1 - TEAM_SEASON_SHARE) * _mean(values[-TEAM_FORM_WINDOW:])
        return (len(values) * blended + TEAM_FORM_PRIOR * 1.0) / (len(values) + TEAM_FORM_PRIOR)

    model = TeamModel(mu_home=mu_home, mu_away=mu_away)
    for team_id, recs in by_team.items():
        model.att[team_id] = rating(recs, "gf", "xgf")
        model.deff[team_id] = rating(recs, "ga", "xga")
    return model


@dataclass
class MatchContext:
    opponent: str
    opponent_id: int
    home: bool
    fdr: int | None
    att_mult: float        # opponent's effect on the player's goals / assists
    save_mult: float       # opponent's effect on the saves a goalkeeper faces
    lam_against: float     # goals his team is expected to concede over 90 minutes


def match_context(model, entry, team_names):
    """The numbers of one fixture for the player's team. entry: {"team", "opp", "home", "fdr"}."""
    team, opp, home = entry["team"], entry["opp"], entry["home"]
    mu_for = model.mu_home if home else model.mu_away
    mu_opp = model.mu_away if home else model.mu_home
    mu_all = (model.mu_home + model.mu_away) / 2
    return MatchContext(
        opponent=team_names.get(opp, "?"),
        opponent_id=opp,
        home=home,
        fdr=entry.get("fdr"),
        # his xG already reflects his own team's attack, so only the opponent's defence and the venue change it
        att_mult=model.deff.get(opp, 1.0) * mu_for / mu_all,
        save_mult=model.att.get(opp, 1.0) * mu_opp / mu_all,
        lam_against=mu_opp * model.att.get(opp, 1.0) * model.deff.get(team, 1.0),
    )


# ---------------------------------------------------------------------------
# Player profile (per-90 rates, minutes habits)
# ---------------------------------------------------------------------------
@dataclass
class Profile:
    position: str
    p_sel: float           # chance his team uses him in a match when he is fit
    f60: float             # share of his games that last 60+ minutes
    m_full: float          # average minutes of those
    m_sub: float           # average minutes of his short games
    xg90: float
    xa90: float
    saves90: float
    yc90: float
    rc90: float
    bonus_full: float      # bonus points per 60+ minute game
    dc_full: float         # chance of reaching the defensive-contribution threshold in a 60+ minute game
    appearances: int
    low_data: bool


def _shrunk_mean(values, pop_mean, prior_n):
    return (sum(values) + pop_mean * prior_n) / (len(values) + prior_n)


def _blend_recent(season, recent):
    return (1 - XP_RECENT_WEIGHT) * season + XP_RECENT_WEIGHT * recent


def _rate90(apps, key, prior90, prior_minutes=PRIOR_MINUTES):
    """Per-90 rate of `key` from his appearances, shrunk towards a prior worth `prior_minutes` minutes
    (the position average, or his own previous season when there is one). Rows where the stat is NULL
    (not synced yet) are left out; with none left the prior itself is used."""
    known = [r for r in apps if r.get(key) is not None]
    if not known:
        return prior90

    def shrunk(rows):
        minutes = sum(r["minutes"] for r in rows)
        return (sum(r[key] for r in rows) + prior90 / 90 * prior_minutes) / (minutes + prior_minutes) * 90

    return _blend_recent(shrunk(known), shrunk(known[-RECENT_APPEARANCES:]))


def _season_prior(pop90, prior, key):
    """(rate, strength in minutes) this season's rate is shrunk to. Without a previous season it is the
    position average; with one, his own previous-season rate (itself shrunk to the position average)
    carries extra, discounted weight."""
    rate = (prior or {}).get(f"{key}90")
    minutes = (prior or {}).get("minutes")
    if rate is None or not minutes:
        return pop90, PRIOR_MINUTES
    weight = min(minutes, PRIOR_SEASON_MAX_MINUTES) * PRIOR_SEASON_DISCOUNT
    own = (rate * weight + pop90 * PRIOR_MINUTES) / (weight + PRIOR_MINUTES)
    return own, PRIOR_MINUTES + weight


def _per_game_mean(values, pop_mean):
    if not values:
        return pop_mean
    return _blend_recent(
        _shrunk_mean(values, pop_mean, BONUS_PRIOR_GAMES),
        _shrunk_mean(values[-RECENT_APPEARANCES:], pop_mean, BONUS_PRIOR_GAMES),
    )


def build_population(rows_by_player, positions):
    """Position averages used as the prior of every shrunk rate (minute-weighted, so cameos don't dominate)."""
    acc = {}
    for pid, rows in rows_by_player.items():
        pos = positions.get(pid)
        if pos is None:
            continue
        a = acc.setdefault(pos, {"sums": {}, "mins": {}, "bonus": [], "dc": []})
        for r in rows:
            for key in ("xg", "xa", "saves", "yc", "rc"):
                if r.get(key) is not None:
                    a["sums"][key] = a["sums"].get(key, 0.0) + r[key]
                    a["mins"][key] = a["mins"].get(key, 0.0) + r["minutes"]
            if r["minutes"] >= FULL_GAME_MINUTES:
                if r.get("bonus") is not None:
                    a["bonus"].append(r["bonus"])
                threshold = DC_THRESHOLD.get(pos)
                if threshold:
                    a["dc"].append(1.0 if r["dc"] >= threshold else 0.0)

    pop = {}
    for pos, a in acc.items():
        pop[pos] = {
            key: (a["sums"].get(key, 0.0) / a["mins"][key] * 90 if a["mins"].get(key) else 0.0)
            for key in ("xg", "xa", "saves", "yc", "rc")
        }
        pop[pos]["bonus"] = _mean(a["bonus"])
        pop[pos]["dc"] = _mean(a["dc"])
    return pop


_EMPTY_POP = {"xg": 0.0, "xa": 0.0, "saves": 0.0, "yc": 0.0, "rc": 0.0, "bonus": 0.0, "dc": 0.0}


def build_profile(position, apps, team_gws, pop, prior=None):
    """apps: his appearances (minutes > 0) before the target gameweek, oldest first.
    team_gws: the gameweeks his team played before the target, oldest first. pop: position averages.
    prior: optional previous-season summary {"minutes", "xg90", "xa90", "saves90", "yc90", "rc90"}."""
    p = pop.get(position, _EMPTY_POP)
    recent = apps[-RECENT_APPEARANCES:]

    if recent:
        full = [r["minutes"] for r in recent if r["minutes"] >= FULL_GAME_MINUTES]
        short = [r["minutes"] for r in recent if r["minutes"] < FULL_GAME_MINUTES]
        f60 = len(full) / len(recent)
        # the defaults below are only ever used with weight 0 (no such games => f60 is 1 or 0)
        m_full = _mean(full) if full else 90.0
        m_sub = _mean(short) if short else 20.0
    else:
        f60, m_full, m_sub = 0.0, 90.0, 20.0

    # How often his team uses him. Games before his first appearance of the last few team games are an
    # absence (injury / just back), not rotation, so they are not held against him.
    appeared = {r["gw"] for r in apps}
    window = team_gws[-TEAM_FORM_WINDOW:]
    season_share = (sum(1 for g in team_gws if g in appeared) / len(team_gws)) if team_gws else 0.0
    first = next((g for g in window if g in appeared), None)
    if first is None:
        p_sel = season_share
    else:
        considered = [g for g in window if g >= first]
        hits = sum(1 for g in considered if g in appeared)
        p_sel = (hits + season_share * SELECTION_PRIOR_GAMES) / (len(considered) + SELECTION_PRIOR_GAMES)

    full_rows = [r for r in apps if r["minutes"] >= FULL_GAME_MINUTES]
    bonus_values = [r["bonus"] for r in full_rows if r.get("bonus") is not None]
    threshold = DC_THRESHOLD.get(position)
    dc_values = [1.0 if r["dc"] >= threshold else 0.0 for r in full_rows] if threshold else []

    return Profile(
        position=position,
        p_sel=min(1.0, max(0.0, p_sel)),
        f60=f60,
        m_full=m_full,
        m_sub=m_sub,
        xg90=_rate90(apps, "xg", *_season_prior(p["xg"], prior, "xg")),
        xa90=_rate90(apps, "xa", *_season_prior(p["xa"], prior, "xa")),
        saves90=_rate90(apps, "saves", *_season_prior(p["saves"], prior, "saves")) if position == "GKP" else 0.0,
        yc90=_rate90(apps, "yc", *_season_prior(p["yc"], prior, "yc")),
        rc90=_rate90(apps, "rc", *_season_prior(p["rc"], prior, "rc")),
        bonus_full=_per_game_mean(bonus_values, p["bonus"]),
        dc_full=_per_game_mean(dc_values, p["dc"]) if threshold else 0.0,
        appearances=len(apps),
        low_data=len(apps) < LOW_DATA_APPEARANCES,
    )


# ---------------------------------------------------------------------------
# One match -> distribution of his points
# ---------------------------------------------------------------------------
def _state_parts(profile, ctx, full):
    """The independent scoring components of one match, given that he plays it as a full game (60+ min)
    or a cameo, each as a {points: probability} distribution."""
    pos = profile.position
    minutes = profile.m_full if full else profile.m_sub
    frac = minutes / 90.0
    # bonus and defensive contribution are estimated per full game; a cameo gets the same share of minutes
    scale = 1.0 if full else min(1.0, profile.m_sub / profile.m_full)

    parts = [{2 if full else 1: 1.0}]   # appearance points
    parts.append(_count_pmf(profile.xg90 * frac * ctx.att_mult, GOAL_POINTS[pos], GOAL_KMAX))
    parts.append(_count_pmf(profile.xa90 * frac * ctx.att_mult, ASSIST_POINTS, ASSIST_KMAX))
    if pos in ("GKP", "DEF", "MID"):
        parts.append(_defence_pmf(pos, ctx.lam_against * frac, cs_eligible=full))
    if pos == "GKP":
        parts.append(_saves_pmf(profile.saves90 * frac * ctx.save_mult))
    parts.append(_bonus_pmf(profile.bonus_full * scale))
    if DC_THRESHOLD.get(pos):
        parts.append(_bernoulli(profile.dc_full * scale, DC_POINTS))
    parts.append(_bernoulli(1 - math.exp(-profile.yc90 * frac), YELLOW_POINTS))
    parts.append(_bernoulli(1 - math.exp(-profile.rc90 * frac), RED_POINTS))
    return parts


def _state_pmf(profile, ctx, full):
    return _convolve_all(_state_parts(profile, ctx, full))


def match_pmf(profile, ctx):
    """His points in one match, given he is fit: not used (0), a cameo or a full game."""
    p_full = profile.p_sel * profile.f60
    p_sub = profile.p_sel * (1 - profile.f60)
    return _mix([
        (1 - profile.p_sel, {0: 1.0}),
        (p_full, _state_pmf(profile, ctx, True) if p_full > 0 else {0: 1.0}),
        (p_sub, _state_pmf(profile, ctx, False) if p_sub > 0 else {0: 1.0}),
    ])


def player_pmf(profile, contexts, availability):
    """Whole gameweek. A blank gameweek has no contexts (0 points); a double gameweek adds two matches.
    Availability is one coin for the whole gameweek: an injured player misses both matches."""
    if not contexts:
        return {0: 1.0}
    played = _convolve_all([match_pmf(profile, ctx) for ctx in contexts])
    return _mix([(1 - availability, {0: 1.0}), (availability, played)])


# ---------------------------------------------------------------------------
# Dataset (the only part that touches the database is load_dataset)
# ---------------------------------------------------------------------------
@dataclass
class Dataset:
    rows: dict            # player id -> appearances (minutes > 0), oldest first: dicts, see load_dataset
    players: dict         # player id -> (position, team id)
    matches: list         # finished matches for build_team_model, oldest first, each with "gw"
    schedule: dict        # gameweek -> team id -> [{"team", "opp", "home", "fdr"}]
    team_names: dict      # team id -> short name
    # Optional previous-season summaries, {player id: {"minutes", "xg90", "xa90", "saves90", "yc90", "rc90"}},
    # used as a better prior than the position average. load_dataset leaves it empty (see README).
    priors: dict = field(default_factory=dict)

    def before(self, target_gw):
        rows = {pid: [r for r in rs if r["gw"] < target_gw] for pid, rs in self.rows.items()}
        matches = [m for m in self.matches if m["gw"] < target_gw]
        return rows, matches


def _xg_per_match_by_team_gw(qs):
    """(team id, gameweek) -> (team xG, team xGC) of the whole gameweek, from players' stats like the engine."""
    out = {}
    for row in (
        qs.values("player__team_id", "gameweek__fpl_id")
        .annotate(xg=Sum("expected_goals"), xgc=Max("expected_goals_conceded"))
    ):
        out[(row["player__team_id"], row["gameweek__fpl_id"])] = (float(row["xg"] or 0), float(row["xgc"] or 0))
    return out


def load_dataset(max_gw_exclusive=None):
    """Everything the model needs from the database, in a handful of queries."""
    stats = PlayerGameweekStat.objects.filter(is_final=True, minutes__gt=0)
    if max_gw_exclusive is not None:
        stats = stats.filter(gameweek__fpl_id__lt=max_gw_exclusive)

    rows = {}
    for (pid, gw, minutes, xg, xa, saves, bonus, yc, rc, dc, points) in stats.order_by("gameweek__fpl_id").values_list(
        "player_id", "gameweek__fpl_id", "minutes", "expected_goals", "expected_assists",
        "saves", "bonus", "yellow_cards", "red_cards", "defensive_contribution", "points",
    ):
        rows.setdefault(pid, []).append({
            "gw": gw, "minutes": minutes, "xg": float(xg), "xa": float(xa),
            "saves": saves, "bonus": bonus, "yc": yc, "rc": rc, "dc": dc, "points": points,
        })

    players = {pid: (pos, team_id) for pid, pos, team_id in Player.objects.values_list("id", "position", "team_id")}
    team_names = dict(Team.objects.values_list("id", "short_name"))
    team_xg = _xg_per_match_by_team_gw(stats)

    fixtures = list(
        Fixture.objects.filter(gameweek__isnull=False)
        .values_list("gameweek__fpl_id", "kickoff_time", "team_home_id", "team_away_id",
                     "difficulty_home", "difficulty_away", "finished", "team_home_score", "team_away_score")
    )
    fixtures.sort(key=lambda f: (f[0], f[1] is None, f[1] or 0))

    schedule, finished = {}, []
    matches_per_team_gw = {}
    for gw, _, home, away, fdr_h, fdr_a, done, score_h, score_a in fixtures:
        by_team = schedule.setdefault(gw, {})
        by_team.setdefault(home, []).append({"team": home, "opp": away, "home": True, "fdr": fdr_h})
        by_team.setdefault(away, []).append({"team": away, "opp": home, "home": False, "fdr": fdr_a})
        if done and score_h is not None and score_a is not None and (max_gw_exclusive is None or gw < max_gw_exclusive):
            finished.append((gw, home, away, score_h, score_a))
            for t in (home, away):
                matches_per_team_gw[(t, gw)] = matches_per_team_gw.get((t, gw), 0) + 1

    matches = []
    for gw, home, away, score_h, score_a in finished:
        for team, opp, is_home, gf, ga in ((home, away, True, score_h, score_a), (away, home, False, score_a, score_h)):
            xg_row = team_xg.get((team, gw))
            n = matches_per_team_gw[(team, gw)]    # a double gameweek's xG is shared between its matches
            matches.append({
                "gw": gw, "team": team, "home": is_home, "gf": gf, "ga": ga,
                "xgf": xg_row[0] / n if xg_row else None,
                "xga": xg_row[1] / n if xg_row else None,
            })
    return Dataset(rows=rows, players=players, matches=matches, schedule=schedule, team_names=team_names)


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------
@dataclass
class Prepared:
    rows: dict
    model: TeamModel
    pop: dict
    team_gws: dict
    schedule: dict


def prepare(dataset, target_gw):
    """The per-gameweek building blocks shared by every prediction (team model, position averages...)."""
    rows, matches = dataset.before(target_gw)
    model = build_team_model(matches)
    positions = {pid: pos for pid, (pos, _) in dataset.players.items()}
    team_gws = {}
    for m in matches:
        gws = team_gws.setdefault(m["team"], [])
        if not gws or gws[-1] != m["gw"]:
            gws.append(m["gw"])
    return Prepared(rows, model, build_population(rows, positions), team_gws, dataset.schedule.get(target_gw, {}))


def _profile_and_contexts(dataset, prep, pid):
    position, team_id = dataset.players[pid]
    profile = build_profile(
        position, prep.rows.get(pid, []), prep.team_gws.get(team_id, []), prep.pop, prior=dataset.priors.get(pid)
    )
    contexts = [match_context(prep.model, e, dataset.team_names) for e in prep.schedule.get(team_id, [])]
    return profile, contexts


def predict_gameweek(dataset, target_gw, player_ids, availability=None, with_pmf=False):
    """{player id: {"xp", "low", "high", "p_play", "fixtures", "low_data"[, "pmf"]}} for the target gameweek,
    using only data from earlier gameweeks (so it can be run for past gameweeks too).

    availability: {player id: 0-1}; players not in it count as fully available."""
    availability = availability or {}
    prep = prepare(dataset, target_gw)
    out = {}
    for pid in player_ids:
        profile, contexts = _profile_and_contexts(dataset, prep, pid)
        a = min(1.0, max(0.0, availability.get(pid, 1.0)))
        pmf = player_pmf(profile, contexts, a)
        result = summarize(pmf)
        result.update({
            "p_play": a * (1 - (1 - profile.p_sel) ** len(contexts)) if contexts else 0.0,
            "fixtures": [
                {"opponent": c.opponent, "opponent_id": c.opponent_id, "home": c.home, "fdr": c.fdr} for c in contexts
            ],
            "low_data": profile.low_data,
        })
        if with_pmf:
            result["pmf"] = pmf
        out[pid] = result
    return out


# ---------------------------------------------------------------------------
# Verdict: one short word per player instead of numbers
# ---------------------------------------------------------------------------
# The word comes from the same compare_engine score the Squad Check cards use (0-100, 50 = average for the
# position: form, fixtures, team strength, minutes) so tags and cards always agree. The thresholds reuse the
# engine's own landmarks: ELITE_BASE_SCORE (70) and the move-on outlook line (45).
STANDOUT_SCORE = ELITE_BASE_SCORE        # 70
PROMISING_SCORE = 60
QUIET_SCORE = 45                         # squad_insights.MOVE_ON_SCORE_MAX: below it the engine says move on
SAFE_MIN_SCORE = 50                      # "Safe": average or better AND almost always plays
SAFE_MIN_PLAY = 0.9
ROTATION_MAX_PLAY, UNLIKELY_MAX_PLAY = 0.6, 0.3

VERDICT_NOTES = {
    "Standout": "One of the strongest options in his position right now.",
    "Promising": "A good outlook for the week.",
    "Safe": "Plays almost every week and should bring steady points.",
    "Steady": "Fine to start, but nothing special is expected.",
    "Quiet": "Not expected to score much this week.",
    "Rotation risk": "He may not start this week.",
    "Unlikely to play": "He is unlikely to play this week.",
    "No game": "Blank gameweek: his team has no match.",
}
MIN_FORM_FOR_PRAISE = 60                 # engine form rating (50 = average): below it he is never praised (Steady at best)
FLAG_NOTES = {                           # why the Squad Check "move on" rules flag him (kind -> note)
    "slump": "His recent games have been well below his usual level.",
    "outlook": "His overall outlook is below average right now.",
    "minutes": "He is losing minutes.",
}
FORM_NOTE = "Decent numbers overall, but his recent form is only modest."
TOP_TEAM_NOTE = "Faces one of the strongest teams, so don't expect much."
ORDER = ("Quiet", "Steady", "Safe", "Promising", "Standout")


def player_verdict(entry, engine_score=None, flag=None, form=None):
    """{"label", "tone", "note"}: a word for how the week looks for this player, no numbers.

    1. Availability and minutes first (out, doubt, blank, rotation).
    2. Otherwise the engine score gives the word; without it (too little data) he is "Steady".
    3. Praise needs recent form: with an engine form rating below MIN_FORM_FOR_PRAISE he is Steady at best.
    4. Caps: if the Squad Check 'Time to move on' rules flag him (`flag` = slump / outlook / minutes) he is
       Quiet; a single match against a top-5 team means at most Steady, unless he is a premium attacker
       (see prime_index) or it is a double gameweek."""
    status, chance = entry["status"], entry.get("chance_of_playing_next_round")
    if status in ("i", "s", "u", "n"):
        return {"label": "Out", "tone": "bad", "note": entry.get("news") or "He is not available."}
    if status == "d" or (chance is not None and chance < 100):
        return {"label": "Doubt", "tone": "warn", "note": entry.get("news") or "His availability is in doubt."}
    if not entry["fixtures"]:
        return {"label": "No game", "tone": "muted", "note": VERDICT_NOTES["No game"]}
    if entry["p_play"] < UNLIKELY_MAX_PLAY:
        return {"label": "Unlikely to play", "tone": "warn", "note": VERDICT_NOTES["Unlikely to play"]}
    if entry["p_play"] < ROTATION_MAX_PLAY:
        return {"label": "Rotation risk", "tone": "warn", "note": VERDICT_NOTES["Rotation risk"]}

    if engine_score is None:
        label = "Steady"
    elif engine_score >= STANDOUT_SCORE:
        label = "Standout"
    elif engine_score >= PROMISING_SCORE:
        label = "Promising"
    elif engine_score < QUIET_SCORE:
        label = "Quiet"
    elif engine_score >= SAFE_MIN_SCORE and entry["p_play"] >= SAFE_MIN_PLAY:
        label = "Safe"
    else:
        label = "Steady"

    note = VERDICT_NOTES[label]
    if form is not None and form < MIN_FORM_FOR_PRAISE and ORDER.index(label) > ORDER.index("Steady"):
        label, note = "Steady", FORM_NOTE
    if flag:
        label, note = "Quiet", FLAG_NOTES.get(flag, VERDICT_NOTES["Quiet"])
    elif entry.get("faces_top_team") and entry.get("prime", 0.0) < PRIME_LABEL_AT and ORDER.index(label) > ORDER.index("Steady"):
        label, note = "Steady", TOP_TEAM_NOTE
    if len(entry["fixtures"]) > 1:
        note += " Double gameweek."
    tone = {"Standout": "great", "Promising": "good", "Safe": "safe", "Steady": "neutral", "Quiet": "low"}[label]
    return {"label": label, "tone": tone, "note": note}


# ---------------------------------------------------------------------------
# Squad Check entry point
# ---------------------------------------------------------------------------
def _player_entry(player, result, slot, prime=0.0, recent_points=None):
    return {
        "id": player.id,
        "slot": slot,
        "name": player.web_name,
        "team": player.team.short_name,
        "position": player.position,
        "price": float(player.price),
        "photo_url": PHOTO_URL.format(code=player.code) if player.code else None,
        "status": player.status,
        "news": player.news,
        "chance_of_playing_next_round": player.chance_of_playing_next_round,
        "xp": round(result["xp"], 2),    # internal (captain ranking, XI estimate): the screen shows words only
        "p_play": round(result["p_play"], 2),
        "fixtures": result["fixtures"],
        "low_data": result["low_data"],
        "recent_points": recent_points or [],
        "verdict": None,
        "prime": round(prime, 2),
        # "Premium" in the FPL sense: an expensive attacker who is also heavily owned (not just popular)
        "premium": prime >= PRIME_LABEL_AT and float(player.price) >= PREMIUM_MIN_PRICE,
        "faces_top_team": False,
        "is_captain": False,
        "is_vice_captain": False,
    }


# ---------------------------------------------------------------------------
# Captain
# ---------------------------------------------------------------------------
# Captain choice. The prime bonus is deliberately strong: in FPL the armband almost always goes to an
# expensive, heavily owned attacker, and the model's number alone kept preferring cheaper players with a
# soft fixture over them. A prime player is passed over only if he is unfit, a likely benchwarmer, or his
# predicted score is clearly lower. Tune these three with the captain section of the README.
TOP_TEAMS = 5                # a non-prime player facing one of this many strongest teams is never captain
PRIME_BONUS = 0.5            # a perfectly prime player's score counts x1.5; a non-prime one x1.0
PREMIUM_MIN_PRICE = 9.0      # the "Premium player" tag also needs a price of at least this (millions)
PRIME_LABEL_AT = 0.7         # at or above this the card shows "Premium player"
CAPTAIN_MIN_PLAY = 0.7       # chance he plays below this: not a captain (rotation / doubt)
CAPTAIN_TIE_MARGIN = 0.03    # scores within 3% of the top are separated by how sure the player is to play
CAPTAIN_POSITIONS = ("MID", "FWD")
EASY_FDR = 2


def prime_index(price, ownership, pool):
    """0-1: how 'premium' a player is, from his price and his ownership compared with the attackers of the
    whole league (pool = [(price, ownership %)]). Both are percentile ranks, averaged and then squared so
    that only the genuinely top players (a Haaland, a Salah) get near 1 and the broad upper-middle does not."""
    if not pool:
        return 0.0

    def pct(value, index):
        below = sum(1 for row in pool if row[index] < value)
        equal = sum(1 for row in pool if row[index] == value)
        return (below + 0.5 * equal) / len(pool)

    return ((pct(price, 0) + pct(ownership, 1)) / 2) ** 2


def entry_prime(player, pool):
    """prime_index for attackers only: the pool is the league's midfielders and forwards, so a defender or
    keeper compared with it would be meaningless (a popular defender would look 'premium')."""
    if player.position not in CAPTAIN_POSITIONS:
        return 0.0
    return prime_index(float(player.price), float(player.selected_by_percent), pool)


def captain_score(entry):
    """Predicted points, raised for prime players. Only used to rank captain candidates."""
    return entry["xp"] * (1 + PRIME_BONUS * entry.get("prime", 0.0))


def overall_strength(rating):
    """One 0-100 number for how strong a team is, from compare_engine's team ratings: real results (season
    + recent form) carry 70%, the average of its xG attack and xGC defence the other 30% (the same split
    as the engine's team_strength axis, but not specific to one position)."""
    xg_part = (rating["att"] + (100 - rating["leaky"])) / 2
    return TEAM_XG_SHARE * xg_part + (1 - TEAM_XG_SHARE) * rating["res"]


def top_teams(ratings, n=None):
    """Ids of the n (default TOP_TEAMS) strongest teams. ratings: {team id: {"att", "leaky", "res"}}."""
    n = TOP_TEAMS if n is None else n
    ranked = sorted(ratings, key=lambda team: -overall_strength(ratings[team]))
    return set(ranked[:n])


def _captain_eligible(entry):
    """Never an injured, doubtful, suspended or unavailable player (FPL status 'a' only, the same safety
    rule as the bot), he must have a match, and he must be likely to actually play. A non-prime player
    whose one opponent is a top-5 side is out too; a double gameweek lifts that rule because the second
    match can make up for the hard one, and a prime player is exempt (see prime_index)."""
    if entry["status"] != "a" or not entry["fixtures"] or entry["p_play"] < CAPTAIN_MIN_PLAY:
        return False
    return not (entry.get("faces_top_team") and entry.get("prime", 0.0) < PRIME_LABEL_AT)


def ranked_captains(starters, limit=3):
    """The best captain candidates of the starting XI, best first (at most `limit`).

    Only midfielders and forwards are considered (a defender or keeper is a candidate only if the XI has no
    eligible attacker). They are ranked by predicted points x prime bonus; when two are practically level
    the one more certain to play comes first. A captain must start: a benched captain scores nothing."""
    eligible = [e for e in starters if _captain_eligible(e)]
    pool = [e for e in eligible if e["position"] in CAPTAIN_POSITIONS] or eligible
    pool = sorted(pool, key=lambda e: -captain_score(e))

    ranked = []
    while pool and len(ranked) < limit:
        top = captain_score(pool[0])
        close = [e for e in pool if top - captain_score(e) <= top * CAPTAIN_TIE_MARGIN]
        pick = max(close, key=lambda e: (e["p_play"], captain_score(e)))
        ranked.append(pick)
        pool = [e for e in pool if e["id"] != pick["id"]]
    return ranked


def pick_captains(starters):
    """(captain, vice) from the starting XI, or (None, None) when nobody qualifies."""
    ranked = ranked_captains(starters, 2)
    return (ranked[0] if ranked else None), (ranked[1] if len(ranked) > 1 else None)


def captain_alternatives(starters, captain, limit=2):
    """The next best candidates after the recommended one: the choice stays with the user."""
    return [e for e in ranked_captains(starters, limit + 1) if captain is None or e["id"] != captain["id"]][:limit]


def captain_reasons(captain, starters):
    """Plain-language reasons built from the numbers, e.g. 'Home vs BOU'. No LLM."""
    reasons = []
    fixtures = captain["fixtures"]
    if len(fixtures) > 1:
        reasons.append("Double gameweek")
    for f in fixtures:
        reasons.append(f"{'Home vs' if f['home'] else 'Away at'} {f['opponent']}")
    known = [f["fdr"] for f in fixtures if f["fdr"] is not None]
    if known and min(known) <= EASY_FDR:
        reasons.append("Easy fixture")
    if captain.get("premium"):
        reasons.append("Premium player")
    if captain["p_play"] >= 0.9:
        reasons.append("Almost certain to play")
    return reasons[:3]


def _team_strength_ratings(target):
    """compare_engine's team ratings, from the shared cache the Squad Check cards use when possible."""
    from .squad_insights import WINDOW_MAX, _ratings_for

    window = _recent_window_ids(target, WINDOW_MAX)
    return _ratings_for(target, window) if window else _team_ratings(target)


XI_SIZE = 11
XI_RANGE_WIDTH = 5           # the shown range for the whole XI is exactly this many points wide
RECENT_POINTS_SHOWN = 5


def xi_estimate(starters, captain):
    """A rough points range for the starting XI: the sum of the players' predicted points (the captain
    counted twice) with a fixed, narrow width. It is a guide, not a promise: real weeks swing more."""
    if len(starters) < XI_SIZE:
        return None    # an incomplete XI (players not matched yet) would give a misleadingly low range
    mean = sum(e["xp"] for e in starters) + (captain["xp"] if captain else 0.0)
    low = max(0, round(mean - XI_RANGE_WIDTH / 2))
    return {"low": low, "high": low + XI_RANGE_WIDTH, "includes_captain": captain is not None}


def _formation(starters):
    counts = {pos: sum(1 for e in starters if e["position"] == pos) for pos in ("GKP", "DEF", "MID", "FWD")}
    if len(starters) != 11 or counts["GKP"] != 1:
        return None
    return f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"


def _engine_views(players, target):
    """{player id: (compare_engine score or None, form rating or None, move-on flag or None)} from the same evaluation the Squad Check
    cards use, so a tag can never contradict a card. Empty when the engine cannot run (too little data)."""
    from .squad_insights import WINDOW_MAX, _collect_evals, _move_on_signal

    window = _recent_window_ids(target, WINDOW_MAX)
    if not window:
        return {}
    try:
        evals, _env = _collect_evals(players, target, window)
    except Exception:
        # the words are a layer on top of the lineup: if the engine cannot run, the lineup still shows
        logger.warning("Squad engine views failed; verdicts fall back to availability only", exc_info=True)
        return {}
    out = {}
    for ev in evals:
        engine = ev.get("engine")
        signal = _move_on_signal(ev, "moderate")
        out[ev["player"].id] = (
            engine["score"] if engine else None,
            engine["breakdown"]["form"]["score"] if engine else None,
            signal[1] if signal else None,
        )
    return out


def get_squad_expected_points(team_input):
    # imported here: squad_insights imports this module's siblings, keep the import graph one-directional
    from .squad_insights import _load_squad, parse_team_id

    team_id = parse_team_id(team_input)
    if team_id is None:
        raise ValueError("Could not find a team ID in that link.")

    target = _target_gameweek()
    if not target:
        raise ValueError("No active gameweek found.")
    slots, picks_name, pending, _bank = _load_squad(team_id, target)

    players = list(Player.objects.select_related("team").filter(fpl_id__in=slots))
    if not players:
        raise ValueError("Could not match that squad to the player data yet.")

    dataset = load_dataset(target.fpl_id)
    results = predict_gameweek(
        dataset,
        target.fpl_id,
        [p.id for p in players],
        availability={p.id: _availability(p)["multiplier"] for p in players},
    )

    pool = [
        (float(price), float(own))
        for price, own in Player.objects.filter(position__in=CAPTAIN_POSITIONS, total_points__gt=0)
        .values_list("price", "selected_by_percent")
    ]
    entries = sorted(
        (
            _player_entry(
                p, results[p.id], slots[p.fpl_id], entry_prime(p, pool),
                [r["points"] for r in dataset.rows.get(p.id, []) if r["gw"] < target.fpl_id][-RECENT_POINTS_SHOWN:],
            )
            for p in players
        ),
        key=lambda e: e["slot"],
    )
    starters = [e for e in entries if e["slot"] <= 11]
    bench = [e for e in entries if e["slot"] > 11]

    strongest = top_teams(_team_strength_ratings(target))
    for e in entries:
        e["faces_top_team"] = len(e["fixtures"]) == 1 and e["fixtures"][0]["opponent_id"] in strongest

    engine = _engine_views(players, target)
    for e in entries:
        score, form, flag = engine.get(e["id"], (None, None, None))
        e["verdict"] = player_verdict(e, score, flag, form)

    captain, vice = pick_captains(starters)
    if captain:
        captain["is_captain"] = True
    if vice:
        vice["is_vice_captain"] = True

    data_through = max((r["gw"] for rs in dataset.rows.values() for r in rs), default=None)
    xi_range = xi_estimate(starters, captain)

    captain_block = None
    if captain:
        captain_block = {
            "player": captain,
            "reasons": captain_reasons(captain, starters),
            "vice": vice,
            "alternatives": captain_alternatives(starters, captain),
        }

    return {
        "gameweek": {"id": target.fpl_id, "name": target.name, "deadline": target.deadline_time.isoformat()},
        "squad_gameweek": picks_name,
        "pending_transfers": pending,        # transfers made since that gameweek, already applied
        "lineup_note": (
            "The starting XI is the lineup of your last known gameweek with your transfers applied; "
            "FPL does not publish the lineup for the upcoming gameweek before its deadline."
        ),
        "data_through_gameweek": data_through,
        "formation": _formation(starters),
        "starting_xi": starters,
        "bench": bench,
        "recommended_captain": captain_block,
        "captain_note": None if captain else (
            "No safe captain pick this week: your fit attackers either face a top-5 side, are doubtful "
            "or are unlikely to play."
        ),
        "xi_estimate": xi_range,
        "missing_players": max(0, len(slots) - len(players)),
    }
