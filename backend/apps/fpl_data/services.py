import requests
import re
import math
import json
import redis
import bisect, json, logging, statistics
from django.db.models import Q, Sum
from groq import Groq
from django.conf import settings
from decimal import Decimal
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

logger = logging.getLogger(__name__)

def sanitize_markdown(text: str) -> str:
    """Strip characters that break Telegram's legacy Markdown parser."""
    if not text:
        return text
    return re.sub(r'([*_`\[\]])', '', text)


RTL_MARK = "\u200F"

def enforce_rtl(text: str, lang: str) -> str:
    """Force RTL paragraph direction for Persian text that mixes in English
    names/numbers, since Telegram's bidi algorithm can otherwise render the
    whole line LTR if it starts with a Latin character."""
    if lang == "fa" and text:
        return RTL_MARK + text
    return text



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



def generate_performance_blurbs(results: list[dict], lang: str = "en") -> dict[int, str]:
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

    language_instruction = (
        "Write every sentence in Persian (Farsi), but keep player names in English exactly as given."
        if lang == "fa" else
        "Write every sentence in English."
    )

    prompt = (
        "You are writing short Fantasy Premier League performance summaries for a Telegram bot. "
        "For each player below, write ONE punchy sentence (max ~20 words) explaining why they "
        "over- or under-performed their expected points this gameweek, using the actual stats given. "
        f"{language_instruction} "
        "Be specific (mention goals/assists/xG/xA/minutes where relevant), vary the phrasing between "
        "players, and do not repeat the same sentence structure twice. No emojis, no hashtags.\n\n"
        "Players:\n" + "\n".join(player_lines)
    )

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        response_format={"type": "json_schema", "json_schema": PERFORMANCE_BLURB_SCHEMA},
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



def format_squad_performance_message(fpl_team_id: int, gameweek: Gameweek, lang: str = "en") -> str:
    cached = get_cached_performance_message(fpl_team_id, gameweek.id, lang)
    if cached:
        return enforce_rtl(cached, lang)

    from apps.accounts.services import get_manager_gameweek_state
    from apps.notifications.translations import t

    state = get_manager_gameweek_state(fpl_team_id, gameweek.fpl_id)
    positions = state["positions"]

    starting_xi = [p for p in state["squad"] if positions.get(p.fpl_id, 0) <= 11]
    bench = [p for p in state["squad"] if positions.get(p.fpl_id, 0) > 11]

    results = squad_performance(starting_xi, gameweek)
    bench_results = squad_performance(bench, gameweek)

    over = [r for r in results if r["label"] == "overperform"]
    under = [r for r in results if r["label"] == "underperform"]
    neutral_count = sum(1 for r in results if r["label"] == "neutral")
    limited_count = sum(1 for r in results if r["label"] == "not_enough_minutes")

    bench_over = [r for r in bench_results if r["label"] == "overperform"]
    bench_under = [r for r in bench_results if r["label"] == "underperform"]

    over.sort(key=lambda r: r["diff"], reverse=True)
    under.sort(key=lambda r: r["diff"])

    notable = over + under
    blurb_success = True
    try:
        blurbs = generate_performance_blurbs(notable, lang)
    except Exception:
        blurbs = {}
        blurb_success = False

    def line(r):
        p = r["player"]  # name stays English — never translated
        pts, xpts, diff = r["points"], r["xpts"], r["diff"]
        verdict = t("verdict_over", lang) if diff > 0 else t("verdict_under", lang)
        blurb = blurbs.get(p.fpl_id)
        if not blurb:
            blurb = "scored more than expected" if diff > 0 else "underdelivered on chances"
        blurb = sanitize_markdown(blurb)
        blurb = enforce_rtl(blurb, lang)
        stat_line = f"{r['goals']}G {r['assists']}A, {r['minutes']}' | xG {r['xg']:.2f} xA {r['xa']:.2f}"
        return (
            f"• *{p.web_name}* ({p.position}) — *{verdict}* — {pts} pts ({t('expected', lang)} ~{xpts})\n"
            f"   _{blurb}_\n"
            f"   `{stat_line}`"
        )

    parts = [f"📊 *{gameweek.name} — {t('gw_title', lang)}*\n"]

    if over:
        parts.append(f"🔥 *{t('overperformed', lang)}*")
        parts.extend(line(r) for r in over)
        parts.append("")

    if under:
        parts.append(f"🧊 *{t('underperformed', lang)}*")
        parts.extend(line(r) for r in under)
        parts.append("")

    if bench_over or bench_under:
        parts.append(f"🪑 *{t('on_bench', lang)}*")
        for r in bench_over:
            parts.append(f"• {r['player'].web_name} — {r['points']} pts / {r['xpts']} xPts")
        for r in bench_under:
            parts.append(f"• {r['player'].web_name} — {r['points']} pts / {r['xpts']} xPts")
        parts.append("")

    footer_bits = []
    if neutral_count:
        footer_bits.append(f"{neutral_count} {t('played_as_expected', lang)}")
    if limited_count:
        footer_bits.append(f"{limited_count} {t('limited_minutes', lang)}")
    if footer_bits:
        parts.append("ℹ️ " + ", ".join(footer_bits))

    if not over and not under:
        parts.append(t("nobody_stood_out", lang))

    message = "\n".join(parts).strip()

    if blurb_success:
        cache_performance_message(fpl_team_id, gameweek.id, lang, message)

    return enforce_rtl(message, lang)



redis_client = redis.Redis.from_url(settings.CELERY_BROKER_URL)

RATE_LIMIT_PER_USER_SECONDS = 30       # one /performance call per user per 30s
RATE_LIMIT_GLOBAL_MAX_PER_MINUTE = 20  # protect overall Groq quota


def check_rate_limit(chat_id: int) -> str | None:
    user_key = f"perf:ratelimit:user:{chat_id}"
    if redis_client.get(user_key):
        return "rate_limited"

    global_key = "perf:ratelimit:global"
    count = redis_client.incr(global_key)
    if count == 1:
        redis_client.expire(global_key, 60)
    if count > RATE_LIMIT_GLOBAL_MAX_PER_MINUTE:
        return "rate_limited_global"

    redis_client.set(user_key, 1, ex=RATE_LIMIT_PER_USER_SECONDS)
    return None


def get_last_finished_gameweek():
    return Gameweek.objects.filter(finished=True).order_by("-fpl_id").first()


def get_cached_performance_message(fpl_team_id: int, gameweek_id: int, lang: str) -> str | None:
    cached = redis_client.get(f"perf:msg:{fpl_team_id}:{gameweek_id}:{lang}")
    return cached.decode("utf-8") if cached else None


def cache_performance_message(fpl_team_id: int, gameweek_id: int, lang: str, message: str):
    redis_client.set(f"perf:msg:{fpl_team_id}:{gameweek_id}:{lang}", message)




COMPARISON_RESPONSE_SCHEMA = {
    "name": "fpl_player_comparison",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "2-3 natural, conversational sentences explaining why the winner is the "
                    "better pick between these two same-position players — like a knowledgeable "
                    "friend giving advice, not a stats readout. Whenever you refer to a player, "
                    "use the placeholder {player_<id>} (e.g. {player_123}) with the id from the "
                    "input data — NEVER write a player's actual name. Cite 1-2 of the most "
                    "compelling stats described in plain words, never as raw 'label number' "
                    "pairs (e.g. never 'form 8.0' or 'xGI 4.2')."
                ),
            },
        },
        "required": ["verdict"],
        "additionalProperties": False,
    },
}


def _substitute_player_placeholders(text: str, id_to_name: dict) -> str:
    def _replace(match):
        pid = int(match.group(1))
        return id_to_name.get(pid, match.group(0))
    return re.sub(r'\{player_(\d+)\}', _replace, text)


# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
RECENT_WINDOW = 5            # finalized gameweeks counted as "recent"
PRIOR_MINUTES = 180          # pseudo-minutes used to shrink small-sample per-90 rates
MIN_POP_MINUTES = 180        # season minutes needed to be part of the comparison pool
FULL_MINUTES_PER_GW = 80     # >= this many recent minutes/GW => no playing-time penalty
CLOSE_CALL_MARGIN = 3.0      # score gap (0-100 scale) below which we say "too close"
POPULATION_TTL = 30 * 60     # seconds
VERDICT_TTL = 3 * 60 * 60    # seconds
 
# Weights per position (each row sums to 1.0). Playing time and availability are
# applied as multipliers on top, not as weighted axes.
POSITION_WEIGHTS = {
    "GKP": {"form": 0.17, "xgc_season": 0.22, "cs_season": 0.13, "bps_recent": 0.18, "fixtures_fdr": 0.30},
    "DEF": {"form": 0.17, "xgi_recent": 0.14, "xgi_season": 0.12, "xgc_season": 0.16,
            "dc_recent": 0.16, "fixtures_fdr": 0.25},
    "MID": {"form": 0.19, "xgi_recent": 0.28, "xgi_season": 0.22, "dc_recent": 0.06, "fixtures_fdr": 0.25},
    "FWD": {"form": 0.19, "xgi_recent": 0.32, "xgi_season": 0.24, "fixtures_fdr": 0.25},
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
    "xgi_recent": {"label": "Recent xGI", "num": "recent_xgi", "den": "recent_minutes", "lower": False},
    "xgi_season": {"label": "Season xGI", "num": "season_xgi", "den": "season_minutes", "lower": False},
    "xgc_season": {"label": "Def. Solidity", "num": "season_xgc", "den": "season_minutes", "lower": True},
    "cs_season": {"label": "Clean Sheets", "num": "season_cs", "den": "season_minutes", "lower": False},
    "bps_recent": {"label": "Shot-stopping (BPS)", "num": "recent_bps", "den": "recent_minutes", "lower": False},
    "dc_recent": {"label": "Defensive Actions", "num": "recent_dc", "den": "recent_minutes", "lower": False},
}
EXTRA_LABELS = {"form": "Form", "fixtures_fdr": "Fixtures", "minutes_per_game": "Minutes"}
 
 
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
 
 
def _norm(value, lo, hi):
    return 0.5 if hi == lo else (value - lo) / (hi - lo)
 
 
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
 
 
def _aggregate_stats(position, gameweek, window_ids, player_ids=None):
    """Season + recent-window totals per player, in ONE query."""
    recent = Q(gameweek__fpl_id__in=window_ids)
    qs = PlayerGameweekStat.objects.filter(
        is_final=True,
        gameweek__fpl_id__lt=gameweek.fpl_id,
        player__position=position,
    )
    if player_ids is not None:
        qs = qs.filter(player_id__in=player_ids)
 
    rows = qs.values("player_id").annotate(
        season_minutes=Sum("minutes"),
        recent_minutes=Sum("minutes", filter=recent),
        season_xgi=Sum("expected_goal_involvements"),
        recent_xgi=Sum("expected_goal_involvements", filter=recent),
        season_xgc=Sum("expected_goals_conceded"),
        season_cs=Sum("clean_sheets"),
        recent_bps=Sum("bps", filter=recent),
        recent_dc=Sum("defensive_contribution", filter=recent),
    )
    return {
        row["player_id"]: {k: float(v or 0) for k, v in row.items() if k != "player_id"}
        for row in rows
    }
 
 
_EMPTY_AGG = {
    "season_minutes": 0.0, "recent_minutes": 0.0, "season_xgi": 0.0, "recent_xgi": 0.0,
    "season_xgc": 0.0, "season_cs": 0.0, "recent_bps": 0.0, "recent_dc": 0.0,
}
 
 
# ---------------------------------------------------------------------------
# Same-position population (what percentiles are measured against)
# ---------------------------------------------------------------------------
def _build_population(position, gameweek, window_ids):
    agg = _aggregate_stats(position, gameweek, window_ids)
    pool = {pid: a for pid, a in agg.items() if a["season_minutes"] >= MIN_POP_MINUTES}
    if len(pool) < 8:  # very early season: fall back to anyone who has played
        pool = {pid: a for pid, a in agg.items() if a["season_minutes"] > 0}
    if not pool:
        raise ValueError("Not enough finished-gameweek data to compare players yet.")
 
    forms = dict(Player.objects.filter(id__in=list(pool)).values_list("id", "form"))
    metrics, medians = {}, {}
 
    for key in POSITION_WEIGHTS[position]:
        spec = METRICS.get(key)
        if not spec:
            continue
        raw = [a[spec["num"]] / a[spec["den"]] * 90 for a in pool.values() if a[spec["den"]] > 0]
        median = statistics.median(raw) if raw else 0.0
        medians[key] = median
        metrics[key] = sorted(_shrunk_rate(a[spec["num"]], a[spec["den"]], median) for a in pool.values())
 
    metrics["form"] = sorted(float(forms.get(pid, 0)) for pid in pool)
    return {"metrics": metrics, "medians": medians, "pool_size": len(pool)}
 
 
def _get_population(position, gameweek, window_ids):
    key = f"compare:pop:v2:{position}:{gameweek.fpl_id}:{window_ids[0]}"
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
# Fixtures for the target gameweek only
# ---------------------------------------------------------------------------
def _side_ease(own, opp, is_home, fdr, mode, ranges):
    """0-1: how favourable one fixture is for `own`, from the player's point of view."""
    if mode == "attack":
        opp_v = opp.strength_defence_away if is_home else opp.strength_defence_home
        own_v = own.strength_attack_home if is_home else own.strength_attack_away
        opp_rng, own_rng = ranges["def"], ranges["att"]
    else:
        opp_v = opp.strength_attack_away if is_home else opp.strength_attack_home
        own_v = own.strength_defence_home if is_home else own.strength_defence_away
        opp_rng, own_rng = ranges["att"], ranges["def"]
 
    if opp_v is None or own_v is None:  # strengths not synced -> fall back to official FDR
        return (5 - fdr) / 4 if fdr else 0.5
    return 0.7 * (1 - _norm(opp_v, *opp_rng)) + 0.3 * _norm(own_v, *own_rng)
 
 
def _fixture_context(gameweek, position):
    """Per team: summed fixture ease for the gameweek (DGW > 1 fixture > blank), plus fixture info."""
    fixtures = list(
        Fixture.objects.filter(gameweek=gameweek).select_related("team_home", "team_away")
    )
    if not fixtures:
        raise ValueError(f"Fixtures for {gameweek.name} are not synced yet.")
 
    teams = list(Team.objects.all())
 
    def _range(fields):
        vals = [getattr(t, f) for t in teams for f in fields if getattr(t, f) is not None]
        return (min(vals), max(vals)) if vals else (0, 0)
 
    ranges = {
        "att": _range(("strength_attack_home", "strength_attack_away")),
        "def": _range(("strength_defence_home", "strength_defence_away")),
    }
 
    blend = FIXTURE_MODE_BLEND[position]
    values = {t.id: 0.0 for t in teams}
    info = {t.id: [] for t in teams}
 
    for f in fixtures:
        sides = (
            (f.team_home, f.team_away, True, f.difficulty_home),
            (f.team_away, f.team_home, False, f.difficulty_away),
        )
        for own, opp, is_home, fdr in sides:
            ease = sum(w * _side_ease(own, opp, is_home, fdr, mode, ranges) for mode, w in blend.items())
            values[own.id] += ease
            info[own.id].append({"opponent": opp.short_name, "home": is_home, "fdr": fdr})
 
    return {"sorted_values": sorted(values.values()), "values": values, "info": info}
 
 
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
 
 
# ---------------------------------------------------------------------------
# Scoring one player
# ---------------------------------------------------------------------------
def _score_player(player, agg, window_ids, population, fixture_ctx):
    position = player.position
    stats = agg.get(player.id, _EMPTY_AGG)
    fixture_list = fixture_ctx["info"].get(player.team_id, [])
    fdrs = [f["fdr"] for f in fixture_list if f["fdr"] is not None]
    avg_fdr = round(sum(fdrs) / len(fdrs), 2) if fdrs else None
    fixture_pct = _percentile(fixture_ctx["values"].get(player.team_id, 0.0), fixture_ctx["sorted_values"])
 
    breakdown = {}
    base = 0.0
    for key, weight in POSITION_WEIGHTS[position].items():
        if key == "form":
            value = float(player.form)
            pct = _percentile(value, population["metrics"]["form"])
        elif key == "fixtures_fdr":
            value, pct = avg_fdr, fixture_pct
        else:
            spec = METRICS[key]
            value = _shrunk_rate(stats[spec["num"]], stats[spec["den"]], population["medians"][key])
            pct = _percentile(value, population["metrics"][key], spec["lower"])
        label = METRICS[key]["label"] if key in METRICS else EXTRA_LABELS[key]
        breakdown[key] = {
            "label": label,
            "value": round(value, 2) if value is not None else None,
            "score": pct,
            "weight": weight,
        }
        base += pct * weight
 
    minutes_per_gw = stats["recent_minutes"] / len(window_ids)
    playing_factor = min(minutes_per_gw / FULL_MINUTES_PER_GW, 1.0)
    breakdown["minutes_per_game"] = {
        "label": EXTRA_LABELS["minutes_per_game"],
        "value": round(minutes_per_gw, 1),
        "score": round(playing_factor * 100, 1),
        "weight": 0,  # multiplier, see below
    }
 
    availability = _availability(player)
    final = base * playing_factor * availability["multiplier"]
    price = float(player.price)
 
    return {
        "id": player.id,
        "name": player.web_name,
        "team": player.team.short_name,
        "position": position,
        "price": round(price, 1),
        "status": player.status,
        "score": round(final, 2),
        "base_score": round(base, 2),
        "score_per_million": round(final / price, 2) if price > 0 else None,
        "low_sample": stats["season_minutes"] < MIN_POP_MINUTES,
        "availability": availability,
        "fixtures": fixture_list,
        "breakdown": breakdown,
    }
 
 
# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def compare_players(player_a_id: int, player_b_id: int, lang: str = "en") -> dict:
    if player_a_id == player_b_id:
        raise ValueError("Choose two different players.")
 
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
    population = _get_population(position, gameweek, window_ids)
    agg = _aggregate_stats(position, gameweek, window_ids, player_ids=[player_a.id, player_b.id])
    fixture_ctx = _fixture_context(gameweek, position)
 
    entry_a = _score_player(player_a, agg, window_ids, population, fixture_ctx)
    entry_b = _score_player(player_b, agg, window_ids, population, fixture_ctx)
 
    winner_id = player_a.id if entry_a["score"] >= entry_b["score"] else player_b.id
    margin = round(abs(entry_a["score"] - entry_b["score"]), 2)
    close_call = margin < CLOSE_CALL_MARGIN
 
    try:
        verdict = generate_comparison_verdict(entry_a, entry_b, winner_id, gameweek, lang, close_call)
    except Exception:
        logger.exception("Comparison verdict generation failed")
        verdict = ""
 
    return {
        "gameweek": gameweek.name,
        "winner_id": winner_id,
        "margin": margin,
        "close_call": close_call,
        "players": {str(entry_a["id"]): entry_a, str(entry_b["id"]): entry_b},
        "verdict": verdict,
    }
 
 
# ---------------------------------------------------------------------------
# LLM verdict (replacement for the old generate_comparison_verdict)
# ---------------------------------------------------------------------------
def _verdict_summary(entry):
    return {
        "id": entry["id"],
        "team": entry["team"],
        "position": entry["position"],
        "overall_score": entry["score"],
        "availability": entry["availability"],
        "fixtures_next_gameweek": entry["fixtures"],
        "metrics": {
            v["label"]: {"value": v["value"], "percentile_vs_same_position": v["score"]}
            for v in entry["breakdown"].values()
        },
    }
 
 
def generate_comparison_verdict(entry_a: dict, entry_b: dict, winner_id: int, gameweek,
                                lang: str = "en", close_call: bool = False) -> str:
    low_id, high_id = sorted([entry_a["id"], entry_b["id"]])
    cache_key = f"compare:verdict:v2:{low_id}:{high_id}:{gameweek.id}:{lang}"
    cached = redis_client.get(cache_key)
    if cached:
        return cached.decode("utf-8")
 
    language_word = "Persian" if lang == "fa" else "English"
    summary = {
        "player_a": _verdict_summary(entry_a),
        "player_b": _verdict_summary(entry_b),
        "winner_id": winner_id,
        "close_call": close_call,
    }
 
    closeness = (
        "The scores are very close, so say it is a narrow call and mention what could swing it."
        if close_call else
        "Explain clearly why the winner is the better pick."
    )
 
    prompt = f"""Compare these two FPL players for {gameweek.name}, both in the same position.
This is only about the upcoming gameweek, not the long term.
 
{json.dumps(summary, indent=2)}
 
Player with id {winner_id} scores higher overall. {closeness}
Write a short verdict (2-3 sentences) in {language_word}, citing 1-2 of the most compelling
reasons in natural language. Percentiles are for your reasoning only; describe them in words
(e.g. "among the best defenders"), never as numbers. If a player's availability is doubtful
or a team has no fixture / a double gameweek, say so.
 
Whenever you refer to a player, use {{player_<id>}} (e.g. {{player_{winner_id}}}) - never write
their actual name.
 
Respond ONLY with valid JSON:
{{"verdict": "<2-3 sentences>"}}"""
 
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        response_format={"type": "json_schema", "json_schema": COMPARISON_RESPONSE_SCHEMA},
    )
 
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "", 1).replace("```", "").strip()
 
    try:
        verdict = json.loads(raw).get("verdict", "")
    except json.JSONDecodeError:
        logger.warning("Verdict JSON could not be parsed: %r", raw[:200])
        return ""
 
    id_to_name = {entry_a["id"]: entry_a["name"], entry_b["id"]: entry_b["name"]}
    verdict = sanitize_markdown(_substitute_player_placeholders(verdict, id_to_name))
 
    redis_client.set(cache_key, verdict, ex=VERDICT_TTL)
    return verdict
 



