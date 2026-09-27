import requests
from groq import Groq
import redis
from django.conf import settings
import json
from decimal import Decimal
import math
from .models import Team, Player, Gameweek, Fixture, PlayerGameweekStat

FPL_BASE_URL = "https://fantasy.premierleague.com/api"


PERFORMANCE_BLURB_SCHEMA = {
    "name": "fpl_performance_blurbs",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "blurbs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "player_id": {"type": "integer", "minimum": 1},
                        "blurb": {"type": "string", "minLength": 1},
                    },
                    "required": ["player_id", "blurb"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["blurbs"],
        "additionalProperties": False,
    },
}

client = Groq(api_key=settings.GROQ_API_KEY)


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
                "expected_goals_conceded": player_data.get("expected_goals_conceded") or 0,
                "clearances_blocks_interceptions": player_data.get("clearances_blocks_interceptions") or 0,
                "recoveries": player_data.get("recoveries") or 0,
                "tackles": player_data.get("tackles") or 0,
                "defensive_contribution": player_data.get("defensive_contribution") or 0,
                "defensive_contribution_per_90": player_data.get("defensive_contribution_per_90") or 0,
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
                "expected_goals_conceded": stats.get("expected_goals_conceded") or 0,  # NEW
                "bps": stats.get("bps", 0),
                "ict_index": stats.get("ict_index") or 0,
                "is_final": gameweek.finished,
                "clearances_blocks_interceptions": stats.get("clearances_blocks_interceptions") or 0,
                "recoveries": stats.get("recoveries") or 0,
                "tackles": stats.get("tackles") or 0,
                "defensive_contribution": stats.get("defensive_contribution") or 0,
            },
        )


GOAL_POINTS = {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 4}
ASSIST_POINTS = 3
CLEAN_SHEET_POINTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
MIN_MINUTES = 60
DIFF_THRESHOLD = Decimal("2.0")
DC_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}  # GKP not eligible   
DC_POINTS = 2



def expected_points(stat: PlayerGameweekStat) -> Decimal:
    position = stat.player.position
    xg = Decimal(stat.expected_goals)
    xa = Decimal(stat.expected_assists)
    xgc = Decimal(stat.expected_goals_conceded)

    appearance = Decimal(2 if stat.minutes >= 60 else (1 if stat.minutes > 0 else 0))
    goal_pts = xg * GOAL_POINTS.get(position, 4)
    assist_pts = xa * ASSIST_POINTS

    cs_pts = Decimal(0)
    gc_penalty = Decimal(0)
    if position in ("GKP", "DEF", "MID") and stat.minutes >= 60:
        cs_prob = Decimal(math.exp(-float(xgc)))
        cs_pts = cs_prob * CLEAN_SHEET_POINTS.get(position, 0)
        if position in ("GKP", "DEF"):
            gc_penalty = xgc / 2

    # NEW: mirror defensive contribution points (already counted in actual stat.points)
    dc_pts = Decimal(0)
    threshold = DC_THRESHOLD.get(position)
    if threshold and stat.defensive_contribution >= threshold:
        dc_pts = Decimal(DC_POINTS)

    return appearance + goal_pts + assist_pts + cs_pts - gc_penalty + dc_pts



def performance_label(stat: PlayerGameweekStat) -> dict:
    if stat.minutes < MIN_MINUTES:
        return {"label": "not_enough_minutes", "emoji": "⏱️", "diff": None, "xpts": None}

    xpts = expected_points(stat)
    diff = Decimal(stat.points) - xpts

    if diff > DIFF_THRESHOLD:
        label, emoji = "overperform", "🔥"
    elif diff < -DIFF_THRESHOLD:
        label, emoji = "underperform", "🧊"
    else:
        label, emoji = "neutral", "⚪"

    return {"label": label, "emoji": emoji, "diff": round(diff, 2), "xpts": round(xpts, 2)}


def squad_performance(squad: list, gameweek: Gameweek) -> list[dict]:
    stats = {
        s.player_id: s
        for s in PlayerGameweekStat.objects.filter(player__in=squad, gameweek=gameweek)
    }
    results = []
    for player in squad:
        stat = stats.get(player.id)
        if not stat:
            continue
        perf = performance_label(stat)
        results.append({
            "player": player,
            "points": stat.points,
            "minutes": stat.minutes,
            "goals": stat.goals_scored,
            "assists": stat.assists,
            "xg": float(stat.expected_goals),
            "xa": float(stat.expected_assists),
            **perf,
        })
    return results



def generate_performance_blurbs(results: list[dict]) -> dict[int, str]:
    """results: output of squad_performance(), already filtered to over/under performers."""
    if not results:
        return {}

    player_lines = []
    for r in results:
        p = r["player"]
        player_lines.append(
            f"id={p.fpl_id}, name={p.web_name}, position={p.position}, "
            f"points={r['points']}, xPts={r['xpts']}, diff={r['diff']}, "
            f"minutes={r['minutes']}, goals={r['goals']}, assists={r['assists']}, "
            f"xG={r['xg']:.2f}, xA={r['xa']:.2f}, label={r['label']}"
        )

    prompt = (
        "You are writing short Fantasy Premier League performance summaries for a Telegram bot. "
        "For each player below, write ONE punchy sentence (max ~20 words) explaining why they "
        "over- or under-performed their expected points this gameweek, using the actual stats given. "
        "Be specific (mention goals/assists/xG/xA/minutes where relevant), vary the phrasing between "
        "players, and do not repeat the same sentence structure twice. No emojis, no hashtags.\n\n"
        "Players:\n" + "\n".join(player_lines)
    )

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        response_format={
            "type": "json_schema",
            "json_schema": PERFORMANCE_BLURB_SCHEMA,
        },
    )

    raw_content = response.choices[0].message.content.strip()
    if raw_content.startswith("```"):
        raw_content = raw_content.replace("```json", "", 1)
        raw_content = raw_content.replace("```", "")
        raw_content = raw_content.strip()

    try:
        result = json.loads(raw_content)
    except json.JSONDecodeError:
        raise ValueError(f"AI returned invalid JSON: {raw_content[:1000]}")

    blurbs = result.get("blurbs", [])
    if not isinstance(blurbs, list):
        raise ValueError("blurbs must be a list.")

    return {b["player_id"]: b["blurb"] for b in blurbs}



def format_squad_performance_message(fpl_team_id: int, gameweek: Gameweek) -> str:
    cached = get_cached_performance_message(fpl_team_id, gameweek.id)
    if cached:
        return cached

    from apps.accounts.services import get_manager_gameweek_state

    state = get_manager_gameweek_state(fpl_team_id, gameweek.fpl_id)
    positions = state["positions"]

    # NEW — only players in the starting XI (position 1-11) count as notable
    starting_xi = [p for p in state["squad"] if positions.get(p.fpl_id, 0) <= 11]
    bench = [p for p in state["squad"] if positions.get(p.fpl_id, 0) > 11]

    results = squad_performance(starting_xi, gameweek)
    bench_results = squad_performance(bench, gameweek)

    over = [r for r in results if r["label"] == "overperform"]
    under = [r for r in results if r["label"] == "underperform"]
    neutral_count = sum(1 for r in results if r["label"] == "neutral")
    limited_count = sum(1 for r in results if r["label"] == "not_enough_minutes")

    # NEW — bench notables get a separate, lower-key mention
    bench_over = [r for r in bench_results if r["label"] == "overperform"]
    bench_under = [r for r in bench_results if r["label"] == "underperform"]

    over.sort(key=lambda r: r["diff"], reverse=True)
    under.sort(key=lambda r: r["diff"])

    notable = over + under

    blurb_success = True
    try:
        blurbs = generate_performance_blurbs(notable)
    except Exception:
        blurbs = {}
        blurb_success = False

    def line(r):
        p = r["player"]
        pts, xpts, diff = r["points"], r["xpts"], r["diff"]
        verdict = "Overperform" if diff > 0 else "Underperform"
        blurb = blurbs.get(p.fpl_id)
        if not blurb:
            blurb = "scored more than expected" if diff > 0 else "underdelivered on chances"
        stat_line = f"{r['goals']}G {r['assists']}A, {r['minutes']}' | xG {r['xg']:.2f} xA {r['xa']:.2f}"
        return (
            f"• *{p.web_name}* ({p.position}) — *{verdict}* — {pts} pts (expected ~{xpts})\n"
            f"   _{blurb}_\n"
            f"   `{stat_line}`"
        )

    parts = [f"📊 *{gameweek.name} — Who Beat Their Numbers?*\n"]

    if over:
        parts.append("🔥 *Overperformed*")
        parts.extend(line(r) for r in over)
        parts.append("")

    if under:
        parts.append("🧊 *Underperformed*")
        parts.extend(line(r) for r in under)
        parts.append("")

    if bench_over or bench_under:
        parts.append("🪑 *On the bench*")
        for r in bench_over:
            parts.append(f"• {r['player'].web_name} would've overperformed ({r['points']} pts vs {r['xpts']} xPts)")
        for r in bench_under:
            parts.append(f"• {r['player'].web_name} would've underperformed ({r['points']} pts vs {r['xpts']} xPts)")
        parts.append("")

    footer_bits = []
    if neutral_count:
        footer_bits.append(f"{neutral_count} played about as expected")
    if limited_count:
        footer_bits.append(f"{limited_count} didn't play enough minutes")
    if footer_bits:
        parts.append("ℹ️ " + ", ".join(footer_bits))

    if not over and not under:
        parts.append("Nobody in your XI stood out this week — everyone performed close to expectations.")

    message = "\n".join(parts).strip()

    if blurb_success:
        cache_performance_message(fpl_team_id, gameweek.id, message)

    return message


redis_client = redis.Redis.from_url(settings.CELERY_BROKER_URL)

RATE_LIMIT_PER_USER_SECONDS = 30       # one /performance call per user per 30s
RATE_LIMIT_GLOBAL_MAX_PER_MINUTE = 20  # protect overall Groq quota


def check_rate_limit(chat_id: int) -> str | None:
    """Returns an error message if rate-limited, else None."""
    user_key = f"perf:ratelimit:user:{chat_id}"
    if redis_client.get(user_key):
        return "Slow down a bit — try again in a few seconds."

    global_key = "perf:ratelimit:global"
    count = redis_client.incr(global_key)
    if count == 1:
        redis_client.expire(global_key, 60)
    if count > RATE_LIMIT_GLOBAL_MAX_PER_MINUTE:
        return "Lots of requests right now — please try again in a minute."

    redis_client.set(user_key, 1, ex=RATE_LIMIT_PER_USER_SECONDS)
    return None


def get_last_finished_gameweek():
    return Gameweek.objects.filter(finished=True).order_by("-fpl_id").first()


def get_cached_performance_message(fpl_team_id: int, gameweek_id: int) -> str | None:
    cached = redis_client.get(f"perf:msg:{fpl_team_id}:{gameweek_id}")
    return cached.decode("utf-8") if cached else None


def cache_performance_message(fpl_team_id: int, gameweek_id: int, message: str):
    # No expiry — a finished gameweek's stats never change, so this is valid forever.
    redis_client.set(f"perf:msg:{fpl_team_id}:{gameweek_id}", message)