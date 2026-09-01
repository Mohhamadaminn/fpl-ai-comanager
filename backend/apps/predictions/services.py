import json

from groq import Groq
from django.conf import settings
from django.db import models

from apps.fpl_data.models import (
    Player,
    PlayerGameweekStat,
    Fixture,
    Gameweek,
)
from .models import AIPrediction, PredictionEvaluation


client = Groq(api_key=settings.GROQ_API_KEY)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _next_fixtures(team, n=5):
    """Return the team's next n unfinished fixtures."""
    return list(
        Fixture.objects.filter(
            finished=False,
        )
        .filter(
            models.Q(team_home=team) |
            models.Q(team_away=team)
        )
        .order_by("kickoff_time")[:n]
    )


def _next_fixtures_difficulty(team, n=5):
    """Average FDR for a team's next n unfinished fixtures."""
    fixtures = _next_fixtures(team, n)

    if not fixtures:
        return None

    difficulties = []

    for fixture in fixtures:
        if fixture.team_home_id == team.id:
            difficulty = fixture.difficulty_home
        else:
            difficulty = fixture.difficulty_away

        if difficulty is not None:
            difficulties.append(difficulty)

    if not difficulties:
        return None

    return round(sum(difficulties) / len(difficulties), 2)


# ---------------------------------------------------------------------------
# Recent player statistics
# ---------------------------------------------------------------------------

def _get_recent_stats(player, current_gameweek, n=5):
    """
    Return the player's latest n finalized gameweeks before the current GW.
    """
    return list(
        PlayerGameweekStat.objects.filter(
            player=player,
            is_final=True,
            gameweek__fpl_id__lt=current_gameweek.fpl_id,
        )
        .select_related("gameweek")
        .order_by("-gameweek__fpl_id")[:n]
    )


def _build_recent_stats(player, current_gameweek, n=5):
    stats = _get_recent_stats(player, current_gameweek, n)

    if not stats:
        return {
            "gameweeks": [],
            "points": [],
            "minutes": [],
            "goals": 0,
            "assists": 0,
            "clean_sheets": 0,
            "xG": 0,
            "xA": 0,
            "xGI": 0,
            "bps": 0,
            "ict_index": 0,
            "minutes_total": 0,
            "minutes_per_game": 0,
            "xGI_per_90": 0,
            "form_trend": "insufficient_data",
        }

    # Reverse so the oldest GW comes first.
    stats = list(reversed(stats))

    points = [stat.points for stat in stats]
    minutes = [stat.minutes for stat in stats]

    total_minutes = sum(minutes)
    total_xgi = sum(float(stat.expected_goal_involvements) for stat in stats)

    xgi_per_90 = (
        round((total_xgi / total_minutes) * 90, 3)
        if total_minutes > 0
        else 0
    )

    # Simple trend:
    # last 2 GWs vs first 2 GWs.
    if len(points) >= 4:
        first_avg = sum(points[:2]) / 2
        last_avg = sum(points[-2:]) / 2

        if last_avg > first_avg + 1:
            form_trend = "improving"
        elif last_avg < first_avg - 1:
            form_trend = "declining"
        else:
            form_trend = "stable"
    else:
        form_trend = "limited_data"

    return {
        "gameweeks": [
            stat.gameweek.fpl_id
            for stat in stats
        ],
        "points": points,
        "minutes": minutes,

        "goals": sum(stat.goals_scored for stat in stats),
        "assists": sum(stat.assists for stat in stats),
        "clean_sheets": sum(stat.clean_sheets for stat in stats),

        "xG": round(
            sum(float(stat.expected_goals) for stat in stats),
            2,
        ),
        "xA": round(
            sum(float(stat.expected_assists) for stat in stats),
            2,
        ),
        "xGI": round(total_xgi, 2),

        "bps": sum(stat.bps for stat in stats),

        "ict_index": round(
            sum(float(stat.ict_index) for stat in stats),
            1,
        ),

        "minutes_total": total_minutes,
        "minutes_per_game": round(
            total_minutes / len(stats),
            1,
        ),

        "xGI_per_90": xgi_per_90,

        "form_trend": form_trend,
    }


# ---------------------------------------------------------------------------
# Candidate filtering
# ---------------------------------------------------------------------------

def _is_transfer_candidate(player, recent):
    """
    Hard quality filter for transfer targets.

    The purpose is NOT to find the best player.
    The purpose is to remove players who should not realistically be
    considered as transfer targets in the first place.
    """

    # No recent data -> not enough evidence.
    if not recent["gameweeks"]:
        return False

    # Player needs meaningful recent minutes.
    if recent["minutes_total"] < 180:
        return False

    # Prefer players who are actually playing regularly.
    if recent["minutes_per_game"] < 45:
        return False

    # If the player has almost no underlying involvement and his appeal
    # is only recent points/clean sheets, don't send him to the AI.
    if (
        recent["xGI"] == 0
        and recent["goals"] == 0
        and recent["assists"] == 0
    ):
        return False

    return True


def _candidate_score(player, recent, fdr):
    """
    Rough pre-ranking score.

    This is NOT the final FPL decision.
    It only determines which credible players are worth sending to the LLM.
    """

    score = 0.0

    # Strong preference for reliable minutes.
    score += min(recent["minutes_total"] / 450, 1.0) * 25

    # Underlying attacking stats.
    score += min(float(player.expected_goal_involvements) / 6, 1.0) * 25

    # Recent underlying stats.
    score += min(recent["xGI"] / 2.5, 1.0) * 20

    # xGI efficiency.
    score += min(recent["xGI_per_90"] / 0.8, 1.0) * 15

    # Recent form is useful, but deliberately has lower weight.
    score += min(float(player.form) / 10, 1.0) * 10

    # Fixtures.
    if fdr is not None:
        score += max(0, (5 - fdr) / 4) * 5

    return round(score, 2)


# ---------------------------------------------------------------------------
# Player context
# ---------------------------------------------------------------------------

def build_player_context(
    gameweek,
    top_n=30,
    premium_n=15,
    candidate_n=25,
):
    """
    Build a high-quality player pool for the AI.

    The pool intentionally contains:
    - established expensive players
    - players with strong underlying stats
    - players with improving recent form
    - credible differentials

    Cheap players with only a short-term points spike are filtered out.
    """

    base_qs = (
        Player.objects
        .filter(status="a")
        .select_related("team")
    )

    players = list(base_qs)

    candidates = []

    for player in players:
        recent = _build_recent_stats(player, gameweek, n=5)

        if not _is_transfer_candidate(player, recent):
            continue

        fdr = _next_fixtures_difficulty(player.team)

        score = _candidate_score(
            player=player,
            recent=recent,
            fdr=fdr,
        )

        candidates.append(
            {
                "player": player,
                "recent": recent,
                "fdr": fdr,
                "score": score,
            }
        )

    # ------------------------------------------------------------------
    # Keep several different types of players.
    # ------------------------------------------------------------------

    # Highest score overall.
    by_score = sorted(
        candidates,
        key=lambda x: x["score"],
        reverse=True,
    )

    # Expensive / established players.
    premiums = sorted(
        candidates,
        key=lambda x: (
            float(x["player"].price),
            x["player"].total_points,
        ),
        reverse=True,
    )[:premium_n]

    # Best recent form.
    in_form = sorted(
        candidates,
        key=lambda x: float(x["player"].form),
        reverse=True,
    )[:top_n]

    # Best underlying xGI.
    best_xgi = sorted(
        candidates,
        key=lambda x: float(x["player"].expected_goal_involvements),
        reverse=True,
    )[:top_n]

    # Combine and deduplicate.
    selected = {}

    for item in (
        premiums +
        in_form +
        best_xgi +
        by_score[:candidate_n]
    ):
        selected[item["player"].id] = item

    context = []

    for item in selected.values():
        player = item["player"]
        recent = item["recent"]

        context.append(
            {
                # This is the Django PK because the AI result is later
                # used with Player.objects.filter(id=...).
                "id": player.id,

                "name": player.web_name,
                "position": player.position,
                "team": player.team.short_name,

                "price": float(player.price),
                "ownership": float(player.selected_by_percent),
                "total_points": player.total_points,

                # Current season data.
                "form": float(player.form),
                "xG_season": float(player.expected_goals),
                "xA_season": float(player.expected_assists),
                "xGI_season": float(
                    player.expected_goal_involvements
                ),
                "ict_index_season": float(player.ict_index),

                # Availability.
                "status": player.status,
                "news": player.news,
                "chance_of_playing_next_round": (
                    player.chance_of_playing_next_round
                ),

                # Recent performance.
                "recent": recent,

                # Upcoming fixtures.
                "next_fixtures_fdr_avg": item["fdr"],

                # Internal pre-ranking only.
                "candidate_score": item["score"],
            }
        )

    return context


# ---------------------------------------------------------------------------
# AI prediction
# ---------------------------------------------------------------------------

def generate_ai_prediction(gameweek: Gameweek) -> AIPrediction:

    player_context = build_player_context(gameweek)

    prompt = f"""
You are an expert Fantasy Premier League analyst making decisions for {gameweek.name}.

Your job is NOT to chase recent points.

Your job is to identify players whose expected future FPL value is supported
by reliable minutes, underlying statistics, fixture quality and sustainable form.

DATA:
{json.dumps(player_context, indent=2)}

FIELD DEFINITIONS:

- next_fixtures_fdr_avg:
  Average difficulty of the next 5 unfinished fixtures.
  1 = easiest, 5 = hardest.

- xGI_season:
  Season-long expected goal involvement.

- recent.xGI:
  Expected goal involvement accumulated over the recent gameweeks shown.

- recent.xGI_per_90:
  Recent expected goal involvement adjusted for minutes.

- recent.minutes_total:
  Total minutes over the recent gameweeks.

- recent.minutes_per_game:
  Average minutes per recent gameweek.

- recent.form_trend:
  Indicates whether recent points are improving, stable or declining.

IMPORTANT:
The candidate_score is only a pre-filtering/ranking aid.
Do NOT blindly follow it.

==================================================
DECISION HIERARCHY
==================================================

Apply these priorities in this order:

1. EXPECTED MINUTES
2. UNDERLYING QUALITY
3. UPCOMING FIXTURES
4. SUSTAINABLE RECENT TREND
5. PRICE / VALUE
6. OWNERSHIP / DIFFERENTIAL POTENTIAL

Do NOT reverse this order.

==================================================
TRANSFER TARGET QUALITY FILTER
==================================================

A transfer target should normally be an established or clearly emerging
first-team FPL asset.

Prefer players who:

- regularly play significant minutes
- have a secure or improving starting role
- have meaningful season-long xGI
- have meaningful recent xGI
- have good or improving underlying statistics
- have reasonable upcoming fixtures
- have sustainable recent form

Do NOT recommend a player merely because:

- he is cheap
- he has low ownership
- he has high current form
- he scored heavily in one gameweek
- he kept one or two clean sheets
- he got one goal or assist
- he has recently received bonus points

Price and ownership are secondary factors.

A cheap player is NOT automatically a good transfer target.

==================================================
ANTI-RECENCY RULE
==================================================

One or two good gameweeks do NOT constitute a reliable trend.

If a player's recent points are mainly explained by:

- clean sheets
- one goal
- one assist
- unusually high finishing
- a single exceptional performance
- other isolated events

then downgrade that player unless his underlying statistics also support the improvement.

For defenders in particular:

DO NOT recommend a defender primarily because of one or two clean sheets.

Clean sheets must be supported by:
- reliable minutes
- good fixtures
- defensive potential
- and preferably some attacking threat.

==================================================
ESTABLISHED PLAYER PRIORITY
==================================================

When comparing a cheap/low-owned player against an established player,
prefer the established player when their underlying numbers and expected
minutes are clearly stronger.

Do NOT select a cheap differential simply because he has recently scored
more FPL points.

A lower-owned player should only beat an established player when there is
strong evidence of a genuine improvement in:

- role
- minutes
- xGI
- xGI/90
- recent underlying statistics
- and/or fixtures.

==================================================
RECENT FORM
==================================================

Form matters, but it must be interpreted as a TREND rather than a single number.

Consider:

- points across multiple gameweeks
- recent minutes
- recent xGI
- recent xGI/90
- form_trend

A player with form 10 but weak recent underlying statistics should NOT
automatically beat a player with form 6 and strong/improving underlying data.

==================================================
POSITION-SPECIFIC RULES
==================================================

DEFENDERS:

Do not prioritize defenders simply because of recent clean sheets.

Prioritize:

1. expected minutes
2. upcoming fixtures
3. clean-sheet potential
4. attacking threat
5. sustainable underlying statistics

MIDFIELDERS / FORWARDS:

Prioritize:

1. expected minutes
2. xGI_season
3. recent xGI
4. xGI_per_90
5. upcoming fixtures
6. sustained form

==================================================
PREMIUM COMPARISON
==================================================

Before selecting a captain or transfer target, compare him against established
high-priced and/or highly-owned players in the SAME position contained in DATA.

If selecting a lower-owned or cheaper player over an established premium,
the reasoning MUST explain why the premium is currently inferior.

Valid reasons include:

- significantly worse fixtures
- significantly weaker underlying statistics
- poor recent underlying trend
- reduced expected minutes
- injury/news concerns

Do NOT reject a premium simply because another player had more points in
one recent gameweek.

==================================================
AVAILABILITY
==================================================

Never recommend a player with:

- status "i" = injured
- status "d" = doubtful
- status "s" = suspended
- status "u" = unavailable

If a player has a concerning news flag or reduced chance of playing,
downgrade him and explicitly mention it in reasoning if he is selected.

==================================================
CAPTAIN
==================================================

Captain selection should prioritize:

1. expected minutes
2. attacking potential / xGI
3. fixture quality
4. sustainable form

Do not captain a player simply because he scored heavily last gameweek.

The captain alternatives must be genuine alternatives, not random players.

==================================================
TRANSFER
==================================================

Select the player with the strongest combination of:

- reliable minutes
- established/emerging first-team role
- season xGI
- recent xGI
- xGI/90
- fixture quality
- sustainable form

If no player is clearly good enough to recommend,
return null for transfer_in_id.

It is better to return null than to recommend a weak transfer target.

==================================================
FINAL OUTPUT
==================================================

Respond ONLY with valid JSON.

Use the player's "id" field exactly as provided in DATA.

Format:

{{
    "captain_id": <player id>,
    "captain_alternatives_considered": [<player id>, <player id>],
    "transfer_in_id": <player id or null>,
    "reasoning": "<3-4 sentences citing specific stats and explaining the decision>"
}}
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        temperature=0.1,
    )

    raw_content = response.choices[0].message.content.strip()

    # Handle accidental markdown fences.
    if raw_content.startswith("```"):
        raw_content = raw_content.replace("```json", "", 1)
        raw_content = raw_content.replace("```", "")
        raw_content = raw_content.strip()

    try:
        result = json.loads(raw_content)
    except json.JSONDecodeError:
        raise ValueError(
            f"AI returned invalid JSON: {raw_content[:1000]}"
        )

    # ------------------------------------------------------------------
    # Validate required fields.
    # ------------------------------------------------------------------

    captain_id = result.get("captain_id")
    alternatives = result.get(
        "captain_alternatives_considered",
        [],
    )
    transfer_in_id = result.get("transfer_in_id")

    if not captain_id:
        raise ValueError("AI did not return captain_id.")

    if not isinstance(alternatives, list):
        raise ValueError(
            "captain_alternatives_considered must be a list."
        )

    if len(alternatives) < 2:
        raise ValueError(
            "AI must provide two captain alternatives."
        )

    if not result.get("reasoning"):
        raise ValueError("AI did not return reasoning.")

    # ------------------------------------------------------------------
    # Fetch players.
    # ------------------------------------------------------------------

    captain = Player.objects.filter(
        id=captain_id,
        status="a",
    ).first()

    if not captain:
        raise ValueError(
            f"AI selected invalid/unavailable captain: {captain_id}"
        )

    transfer_in = None

    if transfer_in_id:
        transfer_in = Player.objects.filter(
            id=transfer_in_id,
            status="a",
        ).first()

        if not transfer_in:
            raise ValueError(
                f"AI selected invalid/unavailable transfer target: "
                f"{transfer_in_id}"
            )

    # ------------------------------------------------------------------
    # Save prediction.
    # ------------------------------------------------------------------

    prediction, _ = AIPrediction.objects.update_or_create(
        gameweek=gameweek,
        defaults={
            "suggested_captain": captain,
            "suggested_transfer_in": transfer_in,
            "reasoning": result["reasoning"],
            "data_snapshot": {
                "players_considered": player_context,
                "captain_alternatives_considered": alternatives,
            },
        },
    )

    return prediction


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_gameweek(gameweek):
    """
    After a gameweek finishes, score the AI captain and transfer suggestion
    against actual results.
    """

    ai_prediction = (
        AIPrediction.objects
        .filter(gameweek=gameweek)
        .first()
    )

    if not ai_prediction:
        return None

    # ---------------------------------------------------------------
    # Captain
    # ---------------------------------------------------------------

    ai_captain_points = None
    ai_was_correct_captain = None

    if ai_prediction.suggested_captain:
        stat = PlayerGameweekStat.objects.filter(
            player=ai_prediction.suggested_captain,
            gameweek=gameweek,
            is_final=True,
        ).first()

        if stat:
            ai_captain_points = stat.points * 2
            ai_was_correct_captain = stat.points > 0

    # ---------------------------------------------------------------
    # Transfer
    # ---------------------------------------------------------------

    ai_transfer_delta = None

    if (
        ai_prediction.suggested_transfer_in
        and ai_prediction.suggested_transfer_out
    ):
        in_stat = PlayerGameweekStat.objects.filter(
            player=ai_prediction.suggested_transfer_in,
            gameweek=gameweek,
            is_final=True,
        ).first()

        out_stat = PlayerGameweekStat.objects.filter(
            player=ai_prediction.suggested_transfer_out,
            gameweek=gameweek,
            is_final=True,
        ).first()

        if in_stat and out_stat:
            ai_transfer_delta = (
                in_stat.points - out_stat.points
            )

    # ---------------------------------------------------------------
    # Save evaluation
    # ---------------------------------------------------------------

    evaluation, _ = PredictionEvaluation.objects.update_or_create(
        gameweek=gameweek,
        defaults={
            "ai_captain_points": ai_captain_points,
            "ai_was_correct_captain": ai_was_correct_captain,
            "ai_transfer_points_delta": ai_transfer_delta,
        },
    )

    return evaluation