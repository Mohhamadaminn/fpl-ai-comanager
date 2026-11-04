import hashlib
import json
import logging
import re
from collections import Counter, defaultdict

import requests
from django.db.models import Sum

from .models import Gameweek, Player, PlayerGameweekStat, Fixture
from .compare_engine import (
    PHOTO_URL,
    RECENT_APPEARANCES,
    FORM_PRIOR_APPS_RECENT,
    _aggregate_stats,
    _availability,
    _fixture_context,
    _get_population as _engine_population,
    _percentile,
    _recent_window_ids,
    _score_player,
    _shrunk_rate,
    _target_gameweek,
    _team_ratings,
)
from .services import (
    _substitute_player_placeholders,
    client,
    redis_client,
    sanitize_markdown,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
WINDOW_MAX = 5                 # latest finalized gameweeks used
WINDOW_MIN = 3                 # below this we say "not enough data"
PRIOR_MINUTES = 90             # shrinkage strength for per-90 rates
POP_MIN_MINUTES = 120          # minutes in the window to be part of the comparison pool

# "Worth another chance": strong underlying numbers, weak points (percentile view of the window).
CHANCE_MIN_MPG = 60            # needs this many minutes per GW
CHANCE_MIN_UNDERLYING = 55     # ...and underlying percentile at least this
CHANCE_MIN_GAP = 20            # ...and underlying - actual percentile gap at least this
CHANCE_MIN_AVAILABILITY = 0.75

# "Time to move on": judged on RESULTS (xGI is deliberately not part of it), using the compare engine.
SQUAD_HORIZON = 3              # upcoming gameweeks the engine looks at (fixtures, team strength)
MOVE_ON_MIN_MPG = 45           # below this a starter is "losing minutes"
# slump: recent points clearly below his own season level, and form below the position average
SLUMP_MIN_GAMES = 3            # needs this many games among his last 5 played
SLUMP_MAX_FORM = 45            # engine form rating (0-100, 50 = position average) at/below this
SLUMP_MIN_EARLIER_GAMES = 3    # games before his last 5 needed to know his earlier level (else no verdict)
SLUMP_MIN_DROP = 1.0           # points per game: earlier level (before his last 5 games) minus last-5 average
# outlook: the whole engine score is clearly below average (form + fixtures + team strength)
MOVE_ON_SCORE_MAX = 45
# wording of the fixtures in the reason text (engine fixture rating, 50 = average)
FIXTURE_GOOD_MIN = 58
FIXTURE_TOUGH_MAX = 42
DRIVER_MAX_SCORE = 45          # an axis only counts as a reason when it is clearly below average
HARD_FIXTURE_MAX_PCT = 30      # fixture-ease percentile at/below this => tough fixtures

# Same-price replacement suggestion (engine score of the candidates, same position)
REPLACEMENT_EXTRA_BUDGET = 0.0   # on top of the outgoing player's price (bank is not known)
REPLACEMENT_MIN_UPGRADE = 10     # candidate must beat the outgoing player by this many engine points
REPLACEMENT_MIN_SCORE = 45      # ...and be at least this good in absolute terms
REPLACEMENT_MIN_GAMES = 3
REPLACEMENT_MIN_MPA = 60         # minutes per appearance
REPLACEMENT_MIN_PLAYING_FACTOR = 0.85
REPLACEMENT_MAX_MISSED = 1       # may miss at most this many of the gameweeks his team played in the window
REPLACEMENT_MIN_FULL_GAMES = 3   # games of 60+ minutes needed in the window (capped by eligible - MAX_MISSED)
REPLACEMENT_FULL_GAME_MINUTES = 60
REPLACEMENT_MAX_OPTIONS = 3      # alternatives shown for one outgoing player
CLUB_LIMIT = 3

RESULT_TTL = 10 * 60
POPULATION_TTL = 30 * 60
REASON_TTL = 6 * 60 * 60
SQUAD_TTL = 2 * 60
POOL_TTL = 30 * 60

TRANSFERS_URL = "https://fantasy.premierleague.com/api/entry/{team_id}/transfers/"

# Underlying quality per position (weights sum to 1). xgc90 is "lower is better".
UNDERLYING = {
    "GKP": {"xgc90": 1.0},
    "DEF": {"xgc90": 0.5, "xgi90": 0.3, "dc90": 0.2},
    "MID": {"xgi90": 1.0},
    "FWD": {"xgi90": 1.0},
}
METRIC_FIELD = {"points90": "points", "xgi90": "xgi", "xgc90": "xgc", "dc90": "dc"}
LOWER_IS_BETTER = {"xgc90"}

TITLES = {
    "another_chance": "Worth another chance",
    "move_on": "Time to move on",
}
POSITION_PLURAL = {"GKP": "goalkeepers", "DEF": "defenders", "MID": "midfielders", "FWD": "forwards"}

_EMPTY = {"minutes": 0.0, "points": 0.0, "xgi": 0.0, "xgc": 0.0, "dc": 0.0,
          "goals": 0.0, "assists": 0.0, "cs": 0.0}


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------
def parse_team_id(text):
    """Accepts a plain ID or an official FPL link (.../entry/<id>/...)."""
    text = (text or "").strip()
    match = re.search(r"/entry/(\d+)", text)
    if match:
        return int(match.group(1))
    if text.isdigit():
        return int(text)
    return None


def _fetch_squad(team_id, gameweek_fpl_id):
    """Wraps accounts.services.get_manager_gameweek_state with friendly errors."""
    from apps.accounts.services import get_manager_gameweek_state

    try:
        return get_manager_gameweek_state(team_id, gameweek_fpl_id)
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code == 404:
            raise ValueError("Team not found. Check the link or ID.")
        raise ValueError("The FPL service returned an error. Try again in a moment.")
    except requests.RequestException:
        raise ValueError("Could not reach the FPL service. Try again in a moment.")


# ---------------------------------------------------------------------------
# The squad as it stands now: last known picks + transfers made since
# ---------------------------------------------------------------------------
def _pending_transfers(team_id, after_gw):
    """Transfers made for gameweeks after `after_gw` (public FPL endpoint), oldest first.
    Any failure returns [] so the squad simply falls back to the last known picks."""
    try:
        response = requests.get(
            TRANSFERS_URL.format(team_id=team_id), timeout=8, headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()
        rows = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Could not load transfers for team %s", team_id, exc_info=True)
        return []
    pending = [r for r in rows if int(r.get("event") or 0) > after_gw]
    return sorted(pending, key=lambda r: r.get("time") or "")


def _apply_transfers(slots, transfers):
    """{element id: squad slot} after the transfers: the incoming player takes the outgoing one's slot."""
    result = dict(slots)
    for transfer in transfers:
        out_id, in_id = transfer["element_out"], transfer["element_in"]
        if out_id in result:
            result[in_id] = result.pop(out_id)
    return result




def _adjusted_bank(bank, transfers):
    """Bank (in £m) after the pending transfers. `element_*_cost` come in tenths of a million and are
    the real sell / buy prices. None when anything is missing or inconsistent (then the budget falls
    back to 0 instead of guessing)."""
    if bank is None:
        return None
    total = float(bank)
    try:
        for t in transfers:
            total += (int(t["element_out_cost"]) - int(t["element_in_cost"])) / 10
    except (KeyError, TypeError, ValueError):
        return None
    return round(total, 1) if total >= 0 else None

def _load_squad(team_id, target):
    """({FPL element id: slot 1-15}, name of the gameweek the picks came from, pending transfer count).

    FPL only publishes picks for a gameweek once it has started, so the picks come from the current
    gameweek (or the last finished one) and the transfers made since then are applied on top. The
    starting XI for the target gameweek is only known after its deadline, so slots are inherited."""
    cache_key = f"squad:slots:v2:{team_id}:{target.fpl_id}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            return ({int(k): v for k, v in data["slots"].items()},
                    data["picks_gw"], data["pending"], data.get("bank"))
    except Exception:
        logger.warning("Squad slots cache read failed", exc_info=True)

    candidates = []
    current = Gameweek.objects.filter(is_current=True, fpl_id__lt=target.fpl_id).first()
    if current:
        candidates.append(current)
    finished = Gameweek.objects.filter(finished=True).order_by("-fpl_id").first()
    if finished and finished not in candidates:
        candidates.append(finished)

    state, picks_gw, error = None, None, None
    for gw in candidates:
        try:
            state, picks_gw = _fetch_squad(team_id, gw.fpl_id), gw
            break
        except ValueError as exc:
            error = exc
    if state is None:
        raise error or ValueError("No finished gameweek yet.")

    transfers = _pending_transfers(team_id, picks_gw.fpl_id)
    slots = _apply_transfers(state["positions"], transfers)
    bank = _adjusted_bank(state.get("bank"), transfers)
    try:
        redis_client.set(
            cache_key,
            json.dumps({"slots": {str(k): v for k, v in slots.items()}, "picks_gw": picks_gw.name,
                        "pending": len(transfers), "bank": bank}),
            ex=SQUAD_TTL,
        )
    except Exception:
        logger.warning("Squad slots cache write failed", exc_info=True)
    return slots, picks_gw.name, len(transfers), bank


# ---------------------------------------------------------------------------
# Window aggregates + same-position population (percentile view, used by "another chance")
# ---------------------------------------------------------------------------
def _aggregate_window(position, window_ids, player_ids=None):
    qs = PlayerGameweekStat.objects.filter(
        is_final=True, gameweek__fpl_id__in=window_ids, player__position=position
    )
    if player_ids is not None:
        qs = qs.filter(player_id__in=player_ids)
    rows = qs.values("player_id").annotate(
        minutes=Sum("minutes"),
        points=Sum("points"),
        xgi=Sum("expected_goal_involvements"),
        xgc=Sum("expected_goals_conceded"),
        dc=Sum("defensive_contribution"),
        goals=Sum("goals_scored"),
        assists=Sum("assists"),
        cs=Sum("clean_sheets"),
    )
    return {
        r["player_id"]: {k: float(v or 0) for k, v in r.items() if k != "player_id"}
        for r in rows
    }


def _needed_keys(position):
    return ["points90", *UNDERLYING[position].keys()]


def _build_population(position, window_ids):
    agg = _aggregate_window(position, window_ids)
    pool = {pid: a for pid, a in agg.items() if a["minutes"] >= POP_MIN_MINUTES}
    if len(pool) < 8:
        pool = {pid: a for pid, a in agg.items() if a["minutes"] > 0}
    if not pool:
        raise ValueError("Not enough finished-gameweek data yet.")

    metrics, medians = {}, {}
    for key in _needed_keys(position):
        field = METRIC_FIELD[key]
        raw = sorted(a[field] / a["minutes"] * 90 for a in pool.values())
        mid = len(raw) // 2
        median = raw[mid] if len(raw) % 2 else (raw[mid - 1] + raw[mid]) / 2
        medians[key] = median
        metrics[key] = sorted(_shrunk_rate(a[field], a["minutes"], median, PRIOR_MINUTES) for a in pool.values())

    prices = sorted(
        float(price) for price in Player.objects.filter(id__in=list(pool)).values_list("price", flat=True)
    )
    return {"metrics": metrics, "medians": medians, "prices": prices}


def _get_population(position, window_ids):
    cache_key = f"squad:pop:v3:{position}:{window_ids[0]}:{len(window_ids)}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("Squad population cache read failed", exc_info=True)

    population = _build_population(position, window_ids)
    try:
        redis_client.set(cache_key, json.dumps(population), ex=POPULATION_TTL)
    except Exception:
        logger.warning("Squad population cache write failed", exc_info=True)
    return population


def _fixture_info(fixture_ctx, team_id):
    """Upcoming fixtures for a team and how tough they are (ease percentile, lower = tougher)."""
    if not fixture_ctx:
        return {"fixtures": [], "ease_pct": None, "hard": False}
    fixtures = fixture_ctx["info"].get(team_id, [])
    if not fixtures:  # blank gameweek: no signal here
        return {"fixtures": [], "ease_pct": None, "hard": False}
    pct = _percentile(fixture_ctx["values"].get(team_id, 0.0), fixture_ctx["sorted_values"])
    return {"fixtures": fixtures, "ease_pct": pct, "hard": pct <= HARD_FIXTURE_MAX_PCT}


# ---------------------------------------------------------------------------
# Evaluating players: percentile view (chance) + compare-engine view (move on)
# ---------------------------------------------------------------------------
def _ratings_for(gameweek, window_ids):
    """Team ratings depend only on the gameweek and the finalized data, so they are cached and
    shared by every user instead of being rebuilt for each request."""
    cache_key = f"squad:ratings:v1:{gameweek.fpl_id}:{window_ids[0]}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return {int(k): v for k, v in json.loads(cached).items()}
    except Exception:
        logger.warning("Team ratings cache read failed", exc_info=True)

    ratings = _team_ratings(gameweek)
    try:
        redis_client.set(cache_key, json.dumps(ratings), ex=POPULATION_TTL)
    except Exception:
        logger.warning("Team ratings cache write failed", exc_info=True)
    return ratings


def _earlier_form(player_ids, gameweek):
    """{player_id: (points, games)} over his games played BEFORE his last RECENT_APPEARANCES games:
    the level he is compared against. (The season average cannot be used: it contains the slump.)"""
    rows = (
        PlayerGameweekStat.objects.filter(
            is_final=True, gameweek__fpl_id__lt=gameweek.fpl_id,
            player_id__in=player_ids, minutes__gt=0,
        )
        .order_by("player_id", "-gameweek__fpl_id")
        .values_list("player_id", "points")
    )
    seen, out = Counter(), {}
    for player_id, points in rows.iterator():
        seen[player_id] += 1
        if seen[player_id] <= RECENT_APPEARANCES:
            continue  # his recent games are not part of the baseline
        total, games = out.get(player_id, (0, 0))
        out[player_id] = (total + int(points or 0), games + 1)
    return out


def _evaluate(player, agg, population, window_len, fixture_ctx):
    a = agg.get(player.id, _EMPTY)
    position = player.position

    def rate(key):
        return _shrunk_rate(a[METRIC_FIELD[key]], a["minutes"], population["medians"][key], PRIOR_MINUTES)

    actual = _percentile(rate("points90"), population["metrics"]["points90"])
    underlying = round(
        sum(
            w * _percentile(rate(k), population["metrics"][k], k in LOWER_IS_BETTER)
            for k, w in UNDERLYING[position].items()
        ),
        1,
    )
    return {
        "player": player,
        "totals": a,
        "mpg": a["minutes"] / window_len,
        "actual": actual,
        "underlying": underlying,
        "gap": round(underlying - actual, 1),
        "availability": _availability(player),
        "price_pct": _percentile(float(player.price), population["prices"]),
        "fixture": _fixture_info(fixture_ctx, player.team_id),
    }


def _evaluate_group(position, players, gameweek, window_ids, ratings, env=None):
    """Both views for the given players of one position. `env` (optional dict) collects what the
    replacement search needs later: per-position fixture context and engine population."""
    ids = [p.id for p in players]
    population = _get_population(position, window_ids)
    agg = _aggregate_window(position, window_ids, player_ids=ids)
    try:
        fixture_ctx = _fixture_context(gameweek, position, SQUAD_HORIZON, ratings=ratings)
    except ValueError:  # fixtures not synced yet
        fixture_ctx = None

    engine_pop = engine_agg = None
    if fixture_ctx:
        try:
            engine_pop = _engine_population(position, gameweek, window_ids)
            engine_agg = _aggregate_stats(position, gameweek, window_ids, player_ids=ids, ratings=ratings)
        except ValueError:
            engine_pop = None
    if env is not None:
        env["fixture_ctx"][position] = fixture_ctx
        env["engine_pop"][position] = engine_pop

    earlier = _earlier_form(ids, gameweek)
    evals = []
    for p in players:
        ev = _evaluate(p, agg, population, len(window_ids), fixture_ctx)
        ev["engine"] = _score_player(p, engine_agg, window_ids, engine_pop, fixture_ctx) if engine_pop else None
        total, games = earlier.get(p.id, (0, 0))
        ev["earlier"] = {"games": games, "ppg": total / games} if games >= SLUMP_MIN_EARLIER_GAMES else None
        evals.append(ev)
    return evals


def _collect_evals(players, gameweek, window_ids):
    ratings = _ratings_for(gameweek, window_ids)  # cached, shared by all users
    env = {"ratings": ratings, "fixture_ctx": {}, "engine_pop": {}}
    evals = []
    for position in {p.position for p in players}:
        group = [p for p in players if p.position == position]
        evals.extend(_evaluate_group(position, group, gameweek, window_ids, ratings, env))
    return evals, env


def _is_chance(ev):
    return (
        ev["mpg"] >= CHANCE_MIN_MPG
        and ev["availability"]["multiplier"] >= CHANCE_MIN_AVAILABILITY
        and ev["underlying"] >= CHANCE_MIN_UNDERLYING
        and ev["gap"] >= CHANCE_MIN_GAP
    )


def _slump_info(ev):
    """The single definition of "in a slump", shared by the website cards and the Telegram check:
    form below the position average, and his last 5 games clearly below his own earlier level.
    Results only (xGI plays no part). Availability is NOT considered here. None when he is not."""
    engine, earlier = ev.get("engine"), ev.get("earlier")
    if not engine or not earlier:
        return None
    form = engine["breakdown"]["form"]
    drop = earlier["ppg"] - form["recent_points_per_game"]
    if form["games"] >= SLUMP_MIN_GAMES and form["score"] <= SLUMP_MAX_FORM and drop >= SLUMP_MIN_DROP:
        return {
            "games": int(form["games"]), "points": int(form["recent_points"]),
            "ppg": form["recent_points_per_game"], "earlier_ppg": earlier["ppg"], "drop": drop,
        }
    return None


def _move_on_signal(ev):
    """(severity, kind) of the strongest reason to move on, or None. Severity tiers: unavailable
    100+, minutes 80+, slump 70+, outlook 60+. Results based: xGI plays no part here."""
    multiplier = ev["availability"]["multiplier"]
    if multiplier == 0:
        return 100 + (100 - ev["underlying"]) / 100, "unavailable"
    if multiplier < CHANCE_MIN_AVAILABILITY:
        return None  # doubtful: not a "move on" case by itself
    if ev["mpg"] < MOVE_ON_MIN_MPG:
        return 80 + (MOVE_ON_MIN_MPG - ev["mpg"]) / MOVE_ON_MIN_MPG, "minutes"

    engine = ev.get("engine")
    if not engine:
        return None
    slump = _slump_info(ev)
    if slump:
        return 70 + min(slump["drop"], 5.0), "slump"
    if engine["score"] <= MOVE_ON_SCORE_MAX:
        return 60 + (MOVE_ON_SCORE_MAX - engine["score"]) / MOVE_ON_SCORE_MAX * 9, "outlook"
    return None


def _classify(evals):
    """Returns (another_chance_eval_or_None, (move_on_eval, kind) or None)."""
    chance = [e for e in evals if _is_chance(e)]
    best_chance = max(chance, key=lambda e: e["gap"]) if chance else None

    best_move = None  # (severity, eval, kind)
    for e in evals:
        if e is best_chance:
            continue
        signal = _move_on_signal(e)
        if signal and (best_move is None or signal[0] > best_move[0]):
            best_move = (signal[0], e, signal[1])
    return best_chance, ((best_move[1], best_move[2]) if best_move else None)


def slump_stats(players, gameweek, window_ids):
    """{player.id: slump info} for the given players who are in a slump, by the website's own rule.
    Public entry point for the Telegram weekly check (so bot and website cannot disagree)."""
    evals, _ = _collect_evals(players, gameweek, window_ids)
    result = {}
    for e in evals:
        info = _slump_info(e)
        if info:
            result[e["player"].id] = info
    return result



def _replacement_window_profile(stats, eligible_gws, window_set, form_median):
    """Reliability of a replacement candidate over the calendar window. Returns None when he fails,
    else {"points", "games"} for that window (shown on the replacement card).
    eligible_gws: gameweeks of the window in which his team had a fixture (a blank is not an absence)."""
    eligible = len(eligible_gws)
    if eligible == 0:
        return None

    gws = stats.get("last_gws", [])
    minutes = stats.get("last_appearance_minutes", [])
    points = stats.get("last_game_points", [])

    played = {gw for gw in gws if gw in window_set}      # distinct gameweeks, safe for doubles
    allowed_missing = REPLACEMENT_MAX_MISSED
    if len(played) < eligible - allowed_missing:
        return None

    full = {gw for gw, m in zip(gws, minutes) if gw in window_set and m >= REPLACEMENT_FULL_GAME_MINUTES}
    if len(full) < min(REPLACEMENT_MIN_FULL_GAMES, eligible - allowed_missing):
        return None

    # Form without his best game, shrunk exactly like _form_value's recent part.
    form_pts = stats.get("last_game_form_points", [])
    if len(form_pts) >= 2:
        rest = sum(form_pts) - max(form_pts)
        shrunk = (rest + form_median * FORM_PRIOR_APPS_RECENT) / (len(form_pts) - 1 + FORM_PRIOR_APPS_RECENT)
        if shrunk < form_median:
            return None

    in_window = [p for gw, p in zip(gws, points) if gw in window_set]
    return {"points": int(sum(in_window)), "games": len(in_window)}


# ---------------------------------------------------------------------------
# Same-price replacement
# ---------------------------------------------------------------------------
def _replacement_pool(position, gameweek, window_ids, env):
    """Engine scores of every reliable, fit player of a position (cached, shared by all users)."""
    cache_key = f"squad:pool:v2:{position}:{gameweek.fpl_id}:{window_ids[0]}:{SQUAD_HORIZON}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("Replacement pool cache read failed", exc_info=True)

    fixture_ctx, engine_pop = env["fixture_ctx"][position], env["engine_pop"][position]
    agg = _aggregate_stats(position, gameweek, window_ids, ratings=env["ratings"])
    players = Player.objects.select_related("team").filter(position=position, id__in=list(agg))

    pool = []

    fixture_ctx, engine_pop = env["fixture_ctx"][position], env["engine_pop"][position]
    agg = _aggregate_stats(position, gameweek, window_ids, ratings=env["ratings"])
    players = Player.objects.select_related("team").filter(position=position, id__in=list(agg))

    window_set = {int(g) for g in window_ids}
    team_gws = defaultdict(set)
    for gw, home_id, away_id in Fixture.objects.filter(gameweek__fpl_id__in=window_ids).values_list(
        "gameweek__fpl_id", "team_home_id", "team_away_id"
    ):
        team_gws[home_id].add(int(gw))
        team_gws[away_id].add(int(gw))
    form_median = engine_pop["medians"]["form_recent"]
    
    for p in players:
        r = _score_player(p, agg, window_ids, engine_pop, fixture_ctx, apply_elite_floor=False)
        if (
            r["availability"]["multiplier"] < CHANCE_MIN_AVAILABILITY
            or r["low_sample"]
            or r["last_games_used"] < REPLACEMENT_MIN_GAMES
            or r["minutes_per_appearance"] < REPLACEMENT_MIN_MPA
            or r["playing_factor"] < REPLACEMENT_MIN_PLAYING_FACTOR
        ):
            continue
        profile = _replacement_window_profile(
            agg[p.id], team_gws.get(p.team_id, set()) & window_set, window_set, form_median
        )
        if profile is None:
            continue
        pool.append({
            "id": p.id, "name": p.web_name, "team": r["team"], "team_id": p.team_id,
            "position": position, "price": r["price"], "score": r["score"], "code": p.code,
            "photo_url": r["photo_url"], "fixtures": r["fixtures"],
            "recent_points": profile["points"], "games": profile["games"],   # CHANGED
        })


    try:
        redis_client.set(cache_key, json.dumps(pool), ex=POOL_TTL)
    except Exception:
        logger.warning("Replacement pool cache write failed", exc_info=True)
    return pool
    


def _pick_replacements(out_player, out_score, squad_players, pool, bank=None, limit=None):
    """Up to `limit` pool candidates (best first) at the outgoing player's price plus the bank, who
    clearly beat him and keep the squad within the club limit. [] when nothing is clearly better."""
    limit = limit or REPLACEMENT_MAX_OPTIONS
    squad_ids = {p.id for p in squad_players}
    club_counts = Counter(p.team_id for p in squad_players)
    club_counts[out_player.team_id] -= 1  # he leaves
    budget = float(out_player.price) + max(bank or 0.0, 0.0) + REPLACEMENT_EXTRA_BUDGET
    floor = max(out_score + REPLACEMENT_MIN_UPGRADE, REPLACEMENT_MIN_SCORE)

    options = [
        c for c in pool
        if c["id"] not in squad_ids
        and c["price"] <= budget + 1e-9
        and c["score"] >= floor
        and club_counts[c["team_id"]] < CLUB_LIMIT
    ]
    options.sort(key=lambda c: c["score"], reverse=True)
    return [
        {
            "id": c["id"], "name": c["name"], "team": c["team"], "position": c["position"],
            "price": c["price"], "code": c["code"], "photo_url": c["photo_url"],
            "next_fixtures": _format_fixtures(c["fixtures"]),
            "recent_points": c["recent_points"], "games": c["games"],
        }
        for c in options[:limit]
    ]


def _suggest_replacements(ev, squad_players, gameweek, window_ids, env, bank=None):
    position = ev["player"].position
    if not ev.get("engine") or not env["engine_pop"].get(position):
        return []
    pool = _replacement_pool(position, gameweek, window_ids, env)
    return _pick_replacements(ev["player"], ev["engine"]["score"], squad_players, pool, bank)



# ---------------------------------------------------------------------------
# Reason text: deterministic template first, optional LLM rewording on top
# ---------------------------------------------------------------------------
TEMPLATES = {
    ("another_chance", "attack"): (
        "{name} has created {xgi:.1f} xGI over the last {n} gameweeks ({ga} goals and assists) "
        "but returned just {pts} points. The chances are coming, so the returns are likely to follow."
    ),
    ("another_chance", "defence"): (
        "{name} has been solid at the back, conceding only {xgc:.1f} xGC over the last {n} gameweeks, "
        "yet has just {cs} clean sheets and {pts} points. The underlying defence is better than the points show."
    ),
    ("move_on", "unavailable"): "{name} is not available ({news}). Consider a replacement.",
    ("move_on", "minutes"): (
        "{name} is averaging only {mpg:.0f} minutes per gameweek lately, "
        "so the points are unlikely to come."
    ),
    ("move_on", "slump"): (
        "{name} has returned just {recent_pts} points in his last {games} games "
        "({ppg:.1f} a game, against {earlier_ppg:.1f} a game earlier in the season).{slump_fixtures}"
    ),
    ("move_on", "outlook"): "{name} could be worth replacing: {drivers}.",
}

SQUAD_REASON_SCHEMA = {
    "name": "fpl_squad_reason",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "minLength": 1,
                "description": "1-2 natural sentences. Refer to the player only via the given {player_<id>} placeholder.",
            }
        },
        "required": ["reason"],
        "additionalProperties": False,
    },
}

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _format_fixtures(fixtures):
    return ", ".join(f"{f['opponent']} {'home' if f['home'] else 'away'}" for f in fixtures)


def _slump_fixtures_text(ev):
    """One sentence about the upcoming fixtures, worded by how the engine rates them."""
    fixtures = ev["fixture"]["fixtures"]
    if not fixtures:
        return ""
    score = ev["engine"]["breakdown"]["fixtures_fdr"]["score"]
    listing = _format_fixtures(fixtures)
    if score >= FIXTURE_GOOD_MIN:
        return f" That is despite a favourable run of fixtures ({listing})."
    if score <= FIXTURE_TOUGH_MAX:
        return f" The next fixtures ({listing}) are tough, so a quick turnaround is unlikely."
    return f" His next fixtures are {listing}."


def _drivers(ev, limit=2):
    """Plain-English reasons (the axes that pull the engine score down most): no jargon, no raw scores."""
    player = ev["player"]
    rows = []
    for key, b in ev["engine"]["breakdown"].items():
        weight = b.get("weight") or 0
        score = b.get("score")
        if weight <= 0 or score is None or score > DRIVER_MAX_SCORE:
            continue
        rows.append((weight * (score - 50), key, b))
    rows.sort(key=lambda r: r[0])

    plural = POSITION_PLURAL.get(player.position, "players")
    parts, underlying_done = [], False
    for _, key, b in rows:
        if len(parts) >= limit:
            break
        if key == "form":
            games = b.get("games") or 0
            parts.append(
                f"he has only {b['recent_points']} points in his last {games} games"
                if games else "his recent form is poor"
            )
        elif key == "fixtures_fdr":
            fixtures = ev["fixture"]["fixtures"]
            parts.append(
                f"a tough run of fixtures is coming up ({_format_fixtures(fixtures)})"
                if fixtures else "he has no fixture in the coming gameweeks"
            )
        elif key == "team_strength":
            parts.append(f"{player.team.short_name} have been out of form as a team")
        elif not underlying_done:  # xGI / xGC / DC / BPS axes all say the same thing to a fan
            underlying_done = True
            if player.position in ("MID", "FWD"):
                parts.append(f"he is creating fewer chances than most {plural}")
            else:
                parts.append(f"he has been less solid at the back than most {plural}")
    return " and ".join(parts) or "his outlook for the next few gameweeks is below average"


def _facts(ev, window_len):
    t = ev["totals"]
    player = ev["player"]
    facts = {
        "name": f"{{player_{player.id}}}",
        "n": window_len,
        "xgi": t["xgi"],
        "xgc": t["xgc"],
        "ga": int(t["goals"] + t["assists"]),
        "cs": int(t["cs"]),
        "pts": int(t["points"]),
        "mpg": ev["mpg"],
        "news": player.news or "injured or suspended",
    }
    if ev.get("engine"):
        form = ev["engine"]["breakdown"]["form"]
        facts.update(
            games=int(form["games"]),
            recent_pts=int(form["recent_points"]),
            ppg=form["recent_points_per_game"],
            earlier_ppg=ev["earlier"]["ppg"] if ev.get("earlier") else 0.0,
            slump_fixtures=_slump_fixtures_text(ev),
            drivers=_drivers(ev),
        )
    return facts


def _template_reason(title_key, kind, ev, window_len):
    if title_key == "another_chance":
        key = (title_key, "attack" if ev["player"].position in ("MID", "FWD") else "defence")
    else:
        key = (title_key, kind)
    return TEMPLATES[key].format(**_facts(ev, window_len))


def _rewrite_reason(base, lang):
    """Optional LLM polish. Falls back to `base` on any problem, or if numbers change."""
    digest = hashlib.sha1(f"{lang}|{base}".encode("utf-8")).hexdigest()
    cache_key = f"squad:reason:v3:{digest}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return cached.decode("utf-8")
    except Exception:
        logger.warning("Reason cache read failed", exc_info=True)

    language_word = "Persian" if lang == "fa" else "English"
    prompt = f"""Rewrite this Fantasy Premier League note in {language_word} as 1-2 natural, friendly sentences.
Use ONLY the facts and numbers in the note, keep every number exactly as written (Latin digits),
add nothing new, and keep the {{player_<id>}} placeholder exactly as written.
Write for a casual fan: never mention FDR or ratings out of 100.

Note: {base}

Respond ONLY with valid JSON: {{"reason": "<1-2 sentences>"}}"""
    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_schema", "json_schema": SQUAD_REASON_SCHEMA},
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "", 1).replace("```", "").strip()
        text = json.loads(raw).get("reason", "").strip()
    except Exception:
        logger.warning("Squad reason rewrite failed, using template", exc_info=True)
        return base

    if not text or len(text) > 400 or set(_NUM_RE.findall(base)) - set(_NUM_RE.findall(text)):
        return base

    try:
        redis_client.set(cache_key, text, ex=REASON_TTL)
    except Exception:
        logger.warning("Reason cache write failed", exc_info=True)
    return text


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def _insight(title_key, kind, ev, window_ids, lang, replacement=None, replacement_options=None):
    player = ev["player"]
    n = len(window_ids)
    base = _template_reason(title_key, kind, ev, n)
    reason = _rewrite_reason(base, lang)
    reason = sanitize_markdown(_substitute_player_placeholders(reason, {player.id: player.web_name}))
    t = ev["totals"]
    return {
        "type": title_key,
        "title": TITLES[title_key],
        "kind": kind,
        "player": {
            "id": player.id,
            "name": player.web_name,
            "team": player.team.short_name,
            "position": player.position,
            "price": round(float(player.price), 1),
            "code": player.code,
            "photo_url": PHOTO_URL.format(code=player.code) if player.code else None,
        },
        "reason": reason,
        "stats": {
            "gameweeks": sorted(window_ids),
            "minutes_per_gameweek": round(ev["mpg"], 1),
            "points": int(t["points"]),
            "season_points": int(player.total_points),
            "xgi": round(t["xgi"], 2),
            "xgc": round(t["xgc"], 2),
            "goals": int(t["goals"]),
            "assists": int(t["assists"]),
            "clean_sheets": int(t["cs"]),
            "underlying_percentile": ev["underlying"],
            "points_percentile": ev["actual"],
            "price_percentile": ev["price_pct"],
            "next_fixtures": _format_fixtures(ev["fixture"]["fixtures"]),
            "hard_fixture": ev["fixture"]["hard"],
        },
        "replacement": replacement,  # same-price swap (move_on only); None when nothing is clearly better
        "replacement_options": replacement_options or [],   # up to 3, best first
    }


def _target_and_window():
    gameweek = _target_gameweek()
    if not gameweek:
        raise ValueError("No active gameweek found.")
    window_ids = _recent_window_ids(gameweek, WINDOW_MAX)
    if len(window_ids) < WINDOW_MIN:
        raise ValueError(f"Not enough finished gameweeks yet (need at least {WINDOW_MIN}).")
    return gameweek, window_ids


def _load_players(slots):
    squad_players = list(Player.objects.select_related("team").filter(fpl_id__in=slots))
    starters = [p for p in squad_players if slots[p.fpl_id] <= 11]
    return squad_players, starters


def get_squad_insights(team_input, lang="en"):
    team_id = parse_team_id(team_input)
    if team_id is None:
        raise ValueError("Could not find a team ID in that link.")

    gameweek, window_ids = _target_and_window()
    slots, picks_name, pending, bank = _load_squad(team_id, gameweek)

    squad_key = hashlib.sha1(",".join(f"{k}:{v}" for k, v in sorted(slots.items())).encode()).hexdigest()[:12]
    cache_key = f"squad:insights:v6:{team_id}:{squad_key}:{gameweek.fpl_id}:{lang}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("Squad insights cache read failed", exc_info=True)

    squad_players, starters = _load_players(slots)
    evals, env = _collect_evals(starters, gameweek, window_ids)
    chance, move_on = _classify(evals)

    insights = []
    if chance:
        insights.append(_insight("another_chance", "underperforming", chance, window_ids, lang))
    if move_on:
        options = _suggest_replacements(move_on[0], squad_players, gameweek, window_ids, env, bank)
        insights.append(_insight("move_on", move_on[1], move_on[0], window_ids, lang,
                                 options[0] if options else None, options))
    result = {
        "gameweek": gameweek.name,
        "squad_gameweek": picks_name,
        "pending_transfers": pending,   # transfers made since that gameweek, already applied
        "window": sorted(window_ids),
        "insights": insights,
    }
    try:
        redis_client.set(cache_key, json.dumps(result), ex=RESULT_TTL)
    except Exception:
        logger.warning("Squad insights cache write failed", exc_info=True)
    return result


def debug_squad_evals(team_input):
    """Shell helper: prints every starter's numbers and which rule fires, so thresholds can be tuned."""
    team_id = parse_team_id(team_input)
    gameweek, window_ids = _target_and_window()
    slots, picks_name, pending, bank = _load_squad(team_id, gameweek)
    squad_players, starters = _load_players(slots)
    evals, env = _collect_evals(starters, gameweek, window_ids)
    chance, move_on = _classify(evals)

    print(f"squad: {picks_name} + {pending} pending transfer(s) | bank: {bank} | target: {gameweek.name} | window: {sorted(window_ids)}")
    for e in sorted(evals, key=lambda e: e["engine"]["score"] if e.get("engine") else 999):
        p, eng = e["player"], e.get("engine")
        signal = _move_on_signal(e)
        if eng:
            f = eng["breakdown"]["form"]
            axes = "  ".join(f"{k}={b['score']:.0f}" for k, b in eng["breakdown"].items() if b.get("weight"))
            earlier = e.get("earlier")
            earlier_text = f"{earlier['ppg']:.1f} ({earlier['games']}g)" if earlier else "n/a"
            head = (f"engine={eng['score']:5.1f} form={f['score']:4.0f} "
                    f"ppg(last {f['games']})={f['recent_points_per_game']:.1f} earlier={earlier_text}")
        else:
            axes, head = "-", "engine=  n/a"
        print(f"{p.web_name:<14}{p.position}  {head}  xGI-pct={e['underlying']:5.1f} pts-pct={e['actual']:5.1f} "
              f"mpg={e['mpg']:5.1f} avail={e['availability']['multiplier']}  -> {signal[1] if signal else '-'}")
        print(f"{'':<17}{axes} | next: {_format_fixtures(e['fixture']['fixtures']) or '-'}")
    print("chance :", chance and chance["player"].web_name)
    print("move_on:", move_on and (move_on[0]["player"].web_name, move_on[1]))
    if move_on:
        print("replacements:", _suggest_replacements(move_on[0], squad_players, gameweek, window_ids, env, bank))