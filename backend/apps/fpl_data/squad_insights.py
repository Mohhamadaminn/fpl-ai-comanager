import hashlib
import json
import logging
import re
 
import requests
from django.db.models import Sum
 
from .models import Gameweek, Player, PlayerGameweekStat
from .compare_engine import (
    PHOTO_URL,
    _availability,
    _fixture_context,
    _percentile,
    _recent_window_ids,
    _shrunk_rate,
    _target_gameweek,
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
 
CHANCE_MIN_MPG = 60            # "another chance" needs this many minutes per GW
CHANCE_MIN_UNDERLYING = 55     # ...and underlying percentile at least this
CHANCE_MIN_GAP = 20            # ...and underlying - actual percentile gap at least this
CHANCE_MIN_AVAILABILITY = 0.75
 
MOVE_ON_MIN_MPG = 45           # below this a starter is "losing minutes"
MOVE_ON_WEAK_MAX = 40          # both percentiles at/below this => weak on both counts
 
# Expensive players who under-deliver are judged more strictly.
PREMIUM_PRICE_PCT = 80             # price percentile (same position) that counts as premium
PREMIUM_SEASON_PTS_MAX_PCT = 50    # ...with season points at or below the position median
PREMIUM_WEAK_MAX = 55              # looser "weak on both counts" limit for such players
PREMIUM_WEAK_MAX_HARD_FIXTURE = 65 # looser still when the next fixture is tough
HARD_FIXTURE_MAX_PCT = 30          # fixture-ease percentile at/below this => tough fixture
 
RESULT_TTL = 10 * 60
POPULATION_TTL = 30 * 60
REASON_TTL = 6 * 60 * 60
 
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
# Window aggregates + same-position population
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
 
    price_rows = Player.objects.filter(id__in=list(pool)).values_list("price", "total_points")
    prices = sorted(float(price) for price, _ in price_rows)
    season_points = sorted(int(points) for _, points in price_rows)
    return {"metrics": metrics, "medians": medians, "prices": prices, "season_points": season_points}
 
 
def _get_population(position, window_ids):
    cache_key = f"squad:pop:v2:{position}:{window_ids[0]}:{len(window_ids)}"
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
    """Next-gameweek fixtures for a team and how tough they are (ease percentile, lower = tougher)."""
    if not fixture_ctx:
        return {"fixtures": [], "ease_pct": None, "hard": False}
    fixtures = fixture_ctx["info"].get(team_id, [])
    if not fixtures:  # blank gameweek: no signal here
        return {"fixtures": [], "ease_pct": None, "hard": False}
    pct = _percentile(fixture_ctx["values"].get(team_id, 0.0), fixture_ctx["sorted_values"])
    return {"fixtures": fixtures, "ease_pct": pct, "hard": pct <= HARD_FIXTURE_MAX_PCT}
 
 
# ---------------------------------------------------------------------------
# Evaluating one player
# ---------------------------------------------------------------------------
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
    price_pct = _percentile(float(player.price), population["prices"])
    season_pts_pct = _percentile(int(player.total_points), population["season_points"])
    fixture = _fixture_info(fixture_ctx, player.team_id)
 
    return {
        "player": player,
        "totals": a,
        "mpg": a["minutes"] / window_len,
        "actual": actual,
        "underlying": underlying,
        "gap": round(underlying - actual, 1),
        "availability": _availability(player),
        "price_pct": price_pct,
        "season_pts_pct": season_pts_pct,
        "premium_under": price_pct >= PREMIUM_PRICE_PCT and season_pts_pct <= PREMIUM_SEASON_PTS_MAX_PCT,
        "fixture": fixture,
    }
 
 
def _classify(evals):
    """Returns (another_chance_eval_or_None, (move_on_eval, kind) or None)."""
    chance = [
        e for e in evals
        if e["mpg"] >= CHANCE_MIN_MPG
        and e["availability"]["multiplier"] >= CHANCE_MIN_AVAILABILITY
        and e["underlying"] >= CHANCE_MIN_UNDERLYING
        and e["gap"] >= CHANCE_MIN_GAP
    ]
    best_chance = max(chance, key=lambda e: e["gap"]) if chance else None
 
    candidates = []  # (severity, eval, kind)
    for e in evals:
        if e is best_chance:
            continue
        if e["availability"]["multiplier"] == 0:
            candidates.append((100 + (100 - e["underlying"]) / 100, e, "unavailable"))
            continue
        if e["availability"]["multiplier"] < CHANCE_MIN_AVAILABILITY:
            continue  # doubtful: not a "move on" case by itself
        if e["mpg"] < MOVE_ON_MIN_MPG:
            candidates.append((80 + (MOVE_ON_MIN_MPG - e["mpg"]) / MOVE_ON_MIN_MPG, e, "minutes"))
            continue
 
        premium = e["premium_under"]
        hard = e["fixture"]["hard"]
        weak_max = MOVE_ON_WEAK_MAX
        if premium:
            weak_max = PREMIUM_WEAK_MAX_HARD_FIXTURE if hard else PREMIUM_WEAK_MAX
 
        if e["underlying"] <= weak_max and e["actual"] <= weak_max:
            severity = (200 - e["underlying"] - e["actual"]) / 2
            if premium:
                severity += 5 + (5 if hard else 0)
            candidates.append((severity, e, "premium" if premium else "performance"))
 
    best_move = max(candidates, key=lambda c: c[0]) if candidates else None
    return best_chance, ((best_move[1], best_move[2]) if best_move else None)
 
 
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
    ("move_on", "performance_attack"): (
        "{name} has returned {pts} points over the last {n} gameweeks and the underlying numbers "
        "({xgi:.1f} xGI) do not suggest a turnaround."
    ),
    ("move_on", "performance_defence"): (
        "{name} has returned {pts} points over the last {n} gameweeks and has conceded {xgc:.1f} xGC, "
        "so the defensive numbers do not suggest a turnaround."
    ),
    ("move_on", "premium"): (
        "{name} costs £{price:.1f}m but has returned just {season_pts} points this season, "
        "and the recent numbers ({stat_text}) do not justify the price.{fixture_text}"
    ),
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
 
 
def _facts(ev, window_len):
    t = ev["totals"]
    player = ev["player"]
    is_attacker = player.position in ("MID", "FWD")
    stat_text = (
        f"{t['xgi']:.1f} xGI over the last {window_len} gameweeks"
        if is_attacker
        else f"{t['xgc']:.1f} xGC over the last {window_len} gameweeks"
    )
    fixture_text = ""
    if ev["fixture"]["hard"]:
        fixture_text = f" The next fixture ({_format_fixtures(ev['fixture']['fixtures'])}) is also tough."
    return {
        "name": f"{{player_{player.id}}}",
        "n": window_len,
        "xgi": t["xgi"],
        "xgc": t["xgc"],
        "ga": int(t["goals"] + t["assists"]),
        "cs": int(t["cs"]),
        "pts": int(t["points"]),
        "mpg": ev["mpg"],
        "news": player.news or "injured or suspended",
        "price": float(player.price),
        "season_pts": int(player.total_points),
        "stat_text": stat_text,
        "fixture_text": fixture_text,
    }
 
 
def _template_reason(title_key, kind, ev, window_len):
    is_attacker = ev["player"].position in ("MID", "FWD")
    if title_key == "another_chance":
        key = (title_key, "attack" if is_attacker else "defence")
    elif kind == "performance":
        key = (title_key, "performance_attack" if is_attacker else "performance_defence")
    else:
        key = (title_key, kind)
    return TEMPLATES[key].format(**_facts(ev, window_len))
 
 
def _rewrite_reason(base, lang):
    """Optional LLM polish. Falls back to `base` on any problem, or if numbers change."""
    digest = hashlib.sha1(f"{lang}|{base}".encode("utf-8")).hexdigest()
    cache_key = f"squad:reason:v1:{digest}"
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
def _insight(title_key, kind, ev, window_ids, lang):
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
    }
 
 
def get_squad_insights(team_input, lang="en"):
    team_id = parse_team_id(team_input)
    if team_id is None:
        raise ValueError("Could not find a team ID in that link.")
 
    gameweek = _target_gameweek()
    if not gameweek:
        raise ValueError("No active gameweek found.")
 
    window_ids = _recent_window_ids(gameweek, WINDOW_MAX)
    if len(window_ids) < WINDOW_MIN:
        raise ValueError(f"Not enough finished gameweeks yet (need at least {WINDOW_MIN}).")
 
    squad_gw = Gameweek.objects.filter(finished=True).order_by("-fpl_id").first()
    if not squad_gw:
        raise ValueError("No finished gameweek yet.")
 
    cache_key = f"squad:insights:v2:{team_id}:{squad_gw.fpl_id}:{gameweek.fpl_id}:{lang}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("Squad insights cache read failed", exc_info=True)
 
    state = _fetch_squad(team_id, squad_gw.fpl_id)
    starter_fpl_ids = {fid for fid, pos in state["positions"].items() if pos <= 11}
    starters = list(
        Player.objects.select_related("team").filter(fpl_id__in=starter_fpl_ids)
    )
 
    evals = []
    for position in {p.position for p in starters}:
        group = [p for p in starters if p.position == position]
        population = _get_population(position, window_ids)
        agg = _aggregate_window(position, window_ids, player_ids=[p.id for p in group])
        try:
            fixture_ctx = _fixture_context(gameweek, position)
        except ValueError:  # fixtures not synced yet: skip fixture-based strictness
            fixture_ctx = None
        evals.extend(_evaluate(p, agg, population, len(window_ids), fixture_ctx) for p in group)
 
    chance, move_on = _classify(evals)
 
    insights = []
    if chance:
        insights.append(_insight("another_chance", "underperforming", chance, window_ids, lang))
    if move_on:
        insights.append(_insight("move_on", move_on[1], move_on[0], window_ids, lang))
 
    result = {
        "gameweek": gameweek.name,
        "squad_gameweek": squad_gw.name,
        "window": sorted(window_ids),
        "insights": insights,
    }
    try:
        redis_client.set(cache_key, json.dumps(result), ex=RESULT_TTL)
    except Exception:
        logger.warning("Squad insights cache write failed", exc_info=True)
    return result

