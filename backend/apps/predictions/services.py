import json

from django.db import models
from groq import Groq
from django.conf import settings

from apps.fpl_data.models import (
    Player,
    PlayerGameweekStat,
    Fixture,
    Gameweek,
)
from .models import AIPrediction, PredictionEvaluation


PREDICTION_RESPONSE_SCHEMA = {
    "name": "fpl_prediction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "captain_id": {
                "type": "integer",
                "minimum": 1,
            },
            "captain_alternatives_considered": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": 1,
                },
                "minItems": 2,
            },
            "transfer_in_id": {
                "type": ["integer", "null"],
                "minimum": 1,
            },
            "transfer_out_id": {
                "type": ["integer", "null"],
                "minimum": 1,
            },
            "reasoning": {
                "type": "string",
                "minLength": 1,
            },
        },
        "required": [
            "captain_id",
            "captain_alternatives_considered",
            "transfer_in_id",
            "transfer_out_id",
            "reasoning",
        ],
        "additionalProperties": False,
    },
}


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


def _get_recent_stats_by_player(players, current_gameweek, n=5):
    """Return each player's latest finalized stats using one query."""
    stats_by_player = {player.id: [] for player in players}

    stats = (
        PlayerGameweekStat.objects.filter(
            player_id__in=stats_by_player,
            is_final=True,
            gameweek__fpl_id__lt=current_gameweek.fpl_id,
        )
        .select_related("gameweek")
        .order_by("player_id", "-gameweek__fpl_id")
    )

    for stat in stats:
        player_stats = stats_by_player[stat.player_id]
        if len(player_stats) < n:
            player_stats.append(stat)

    return stats_by_player


def _build_recent_stats(player, current_gameweek, n=5, stats=None):
    if stats is None:
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

    games_available = len(recent["gameweeks"])
    min_minutes = min(180, games_available * 60)  # ~60 min/game minimum, scaled to what's actually available


    if recent["minutes_total"] < min_minutes:
        return False

    if recent["minutes_per_game"] < 45:
        return False

    if recent["xGI"] == 0 and recent["goals"] == 0 and recent["assists"] == 0:
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

def _get_fixture_difficulties_by_team(teams, n=5):
    """Return average FDRs for teams' next fixtures using one query."""
    team_ids = {team.id for team in teams}
    fixtures_by_team = {team_id: [] for team_id in team_ids}

    fixtures = Fixture.objects.filter(
        finished=False,
    ).filter(
        models.Q(team_home_id__in=team_ids) |
        models.Q(team_away_id__in=team_ids)
    ).order_by("kickoff_time")

    for fixture in fixtures:
        for team_id, difficulty in (
            (fixture.team_home_id, fixture.difficulty_home),
            (fixture.team_away_id, fixture.difficulty_away),
        ):
            if team_id in fixtures_by_team and len(fixtures_by_team[team_id]) < n:
                fixtures_by_team[team_id].append(difficulty)

    return {
        team_id: round(sum(known_difficulties) / len(known_difficulties), 2)
        if (known_difficulties := [d for d in difficulties if d is not None])
        else None
        for team_id, difficulties in fixtures_by_team.items()
    }



# ---------------------------------------------------------------------------
# Player context
# ---------------------------------------------------------------------------

def build_player_context(
    gameweek,
    top_n=3,
    premium_n=3,
    candidate_n=5,
    squad=None,
):
    """
    Build a high-quality player pool for the AI.

    The pool intentionally contains:
    - established expensive players
    - players with strong underlying stats
    - players with improving recent form
    - credible differentials
    - the user's current squad (forced in, so transfer_out has real options)

    Cheap players with only a short-term points spike are filtered out.
    """

    base_qs = (
        Player.objects
        .filter(status="a")
        .select_related("team")
    )

    players = list(base_qs)
    recent_stats_by_player = _get_recent_stats_by_player(players, gameweek, n=5)
    fixture_difficulties_by_team = _get_fixture_difficulties_by_team(
        [player.team for player in players], n=5
    )


    candidates = []

    for player in players:

        recent = _build_recent_stats(
            player, gameweek, n=5, stats=recent_stats_by_player[player.id]
        )


        if not _is_transfer_candidate(player, recent):
            continue

        fdr = fixture_difficulties_by_team[player.team_id]

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

    squad_ids = {p.id for p in squad} if squad else set()

    # Ensure current squad players are always considered, even if they
    # didn't pass the quality filter (needed so transfer_out has real options)
    if squad:
        candidate_player_ids = {c["player"].id for c in candidates}
        for player in squad:
            if player.id not in candidate_player_ids:
                recent = _build_recent_stats(
                    player, gameweek, n=5,
                    stats=recent_stats_by_player.get(player.id)  # was: recent_stats_by_player[player.id]
                )
                fdr = fixture_difficulties_by_team.get(player.team_id)  # was: fixture_difficulties_by_team[player.team_id]
                score = _candidate_score(player=player, recent=recent, fdr=fdr)
                candidates.append({"player": player, "recent": recent, "fdr": fdr, "score": score})
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

    # Always include squad players in the final selection, even if they
    # didn't make any of the above cuts.
    for item in candidates:
        if item["player"].id in squad_ids:
            selected[item["player"].id] = item

    context = []

    for item in selected.values():
        player = item["player"]
        recent = item["recent"]

        context.append(
        {
            "id": player.id,
            "name": player.web_name,
            "position": player.position,
            "team": player.team.short_name,
            "price": round(float(player.price), 1),
            "form": round(float(player.form), 1),
            "xGI_season": round(float(player.expected_goal_involvements), 2),
            "xGC_season": round(float(player.expected_goals_conceded), 2),
            "defensive_contribution_per_90": round(float(player.defensive_contribution_per_90), 2),
            "status": player.status,
            **({"news": player.news} if player.news else {}),
            **({"chance_of_playing": player.chance_of_playing_next_round} if player.status != "a" else {}),
            "recent": {
                "xGI": recent["xGI"],
                "xGI_per_90": recent["xGI_per_90"],
                "minutes_per_game": recent["minutes_per_game"],
            },
            "next_fixtures_fdr_avg": item["fdr"],
            "in_current_squad": player.id in squad_ids,
        }
    )

    return context

# ---------------------------------------------------------------------------
# AI prediction
# ---------------------------------------------------------------------------

def generate_ai_prediction(gameweek: Gameweek, squad:None, manager_state:None) -> AIPrediction:

    player_context = build_player_context(gameweek, squad=squad)

    # Captain candidates: ONLY players actually in the squad.
    squad_ids = {p.id for p in squad} if squad else set()

    captain_candidates = [
        {
            "id": p["id"],
            "name": p["name"],
            "position": p["position"],
            "form": p["form"],
            "xGI_season": p["xGI_season"],
            "xGI_per_90": p["recent"]["xGI_per_90"],
            "fdr": p["next_fixtures_fdr_avg"],
            "status": p["status"],
        }
        for p in player_context if p["id"] in squad_ids
    ]


    prompt = f"""

CAPTAIN CANDIDATES:
{json.dumps(captain_candidates, separators=(",", ":"))}

FULL PLAYER POOL:
{json.dumps(player_context, separators=(",", ":"))}

You are an expert Fantasy Premier League analyst making decisions for {gameweek.name}.

Your job is to identify players with the strongest expected future FPL value based on
minutes, underlying statistics, fixtures and sustainable form.

FIELD DEFINITIONS:

- next_fixtures_fdr_avg: Average difficulty of next 5 unfinished fixtures (1=easiest, 5=hardest).
- xGI_season: Season-long expected goal involvement.
- recent.xGI: Recent expected goal involvement.
- recent.xGI_per_90: Recent xGI adjusted for minutes.
- recent.minutes_per_game: Average recent minutes.
- form: Current FPL form.
- in_current_squad: Whether the player is currently owned.

DECISION HIERARCHY:

1. Expected minutes
2. Underlying quality
3. Upcoming fixtures
4. Sustainable recent trend
5. Price/value
6. Ownership/differential potential

TRANSFER TARGETS:

Prefer established or clearly emerging first-team players with:
- reliable/improving minutes
- meaningful season and recent underlying statistics
- good fixtures
- sustainable form

Do NOT recommend a player merely because of:
- low price
- low ownership
- high current form
- one or two clean sheets
- one goal/assist
- one exceptional performance

Recent points must be supported by underlying statistics or a genuine improvement
in role, minutes or fixtures.

POSITION RULES:

DEFENDERS:
Prioritize:
1. Expected minutes
2. Clean-sheet potential from team strength and fixtures
3. xGC_season (lower is better)
4. defensive_contribution_per_90 (higher is better)
5. Recent clean sheets as secondary evidence
6. Attacking threat only as a bonus

Do NOT use xGI/xGI_season as the primary measure for defenders.

For defenders, 10+ defensive contributions (CBIT) in a match is the relevant threshold.

MIDFIELDERS/FORWARDS:
Prioritize:
1. Expected minutes
2. xGI_season
3. Recent xGI
4. xGI_per_90
5. Fixtures
6. Sustainable form
7. defensive_contribution_per_90 only as a minor tiebreaker

For midfielders, 12+ defensive contributions is the relevant threshold.

PREMIUM COMPARISON:

Before selecting a captain or transfer target, compare the player against established
expensive/high-owned players in the SAME position contained in DATA.

Prefer the cheaper/lower-owned player over an established premium only when there is
strong evidence from:
- fixtures
- underlying statistics
- role/minutes
- recent underlying trend
- injury/news concerns affecting the premium

Do not prefer a differential simply because of recent points.

AVAILABILITY:

Never recommend a player with:
- status "i" = injured
- status "d" = doubtful
- status "s" = suspended
- status "u" = unavailable

If a selected player has concerning news or reduced chance of playing, mention it.

CAPTAIN:

- captain_id MUST have in_current_squad=true.
- Only captain a currently owned player.
- Prioritize expected minutes, attacking potential/xGI, fixtures and sustainable form.
- Do not captain someone simply because of one big recent score.
- captain_alternatives_considered must contain 2 genuine alternatives from the current squad.

TRANSFER:

- transfer_out_id MUST have in_current_squad=true.
- transfer_in_id must NOT be in the current squad.
- Prefer reliable minutes, first-team role, underlying quality, fixtures and sustainable form.
- transfer_in price should normally be within ±0.5 of transfer_out price.
- Up to +1.5 additional cost is acceptable only when clearly justified.
- Never recommend a significantly more expensive player without explaining the price gap.
- If there is no clearly worthwhile transfer, return null for BOTH transfer_in_id and transfer_out_id.
- It is better to return null than recommend a weak transfer.

IMPORTANT:

The candidate_score is only a ranking aid. Do NOT blindly follow it.

FINAL OUTPUT:

Respond ONLY with valid JSON.

Use the player's "id" field exactly as provided in DATA.

{{
    "captain_id": <player id>,
    "captain_alternatives_considered": [<player id>, <player id>],
    "transfer_in_id": <player id or null>,
    "transfer_out_id": <player id or null>,
    "reasoning": "<3-4 sentences citing specific stats and explaining the decisions>"
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
        temperature=0.2,
        response_format={
            "type": "json_schema",
            "json_schema": PREDICTION_RESPONSE_SCHEMA,
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

    # ------------------------------------------------------------------
    # Validate required fields.
    # ------------------------------------------------------------------

    captain_id = result.get("captain_id")
    alternatives = result.get("captain_alternatives_considered", [])
    transfer_in_id = result.get("transfer_in_id")
    transfer_out_id = result.get("transfer_out_id")

    if not captain_id:
        raise ValueError("AI did not return captain_id.")

    if not isinstance(alternatives, list):
        raise ValueError("captain_alternatives_considered must be a list.")

    if len(alternatives) < 2:
        raise ValueError("AI must provide two captain alternatives.")

    if not result.get("reasoning"):
        raise ValueError("AI did not return reasoning.")

    # ------------------------------------------------------------------
    # Fetch players.
    # ------------------------------------------------------------------

    captain, captain_was_fallback = _resolve_captain(captain_id, alternatives, squad)

    if not captain:
        raise ValueError("No valid captain could be resolved from AI response or squad.")

    reasoning = result["reasoning"]
    if captain.id != captain_id:
        reasoning = (
            f"⚠️ The AI's original captain pick wasn't in your squad, so {captain.web_name} "
            f"(your in-squad player with the best current form) was selected instead.\n\n{reasoning}"
        )
    
    transfer_in = None
    transfer_out = None

    if transfer_in_id:
        transfer_in = Player.objects.filter(id=transfer_in_id, status="a").first()
        if not transfer_in:
            raise ValueError(f"AI selected invalid/unavailable transfer target: {transfer_in_id}")

    if transfer_out_id:
        transfer_out = Player.objects.filter(id=transfer_out_id).first()
        if not transfer_out:
            raise ValueError(f"AI selected invalid transfer_out target: {transfer_out_id}")

    # Enforce a hard price-gap ceiling regardless of what the AI reasoned,
    # since price constraints are a real FPL budget rule, not a soft preference.
    MAX_PRICE_GAP = 1.5
    if transfer_in and transfer_out:
        price_gap = float(transfer_in.price) - float(transfer_out.price)
        if price_gap > MAX_PRICE_GAP:
            transfer_in = None
            transfer_out = None

    # ------------------------------------------------------------------
    # Save prediction.
    # ------------------------------------------------------------------

    prediction, _ = AIPrediction.objects.update_or_create(
        gameweek=gameweek,
        defaults={
            "suggested_captain": captain,
            "suggested_transfer_in": transfer_in,
            "suggested_transfer_out": transfer_out,
            "reasoning": reasoning,
            "data_snapshot": {
                "players_considered": player_context,
                "captain_alternatives_considered": alternatives,
            },
        },
    )

    if squad is not None and manager_state is not None:
        validation = validate_prediction(prediction, manager_state)

        captain_invalid = any("captain" in e.lower() for e in validation["errors"])
        transfer_invalid = any(
            "transfer" in e.lower() or "position" in e.lower() or "budget" in e.lower()
            for e in validation["errors"]
        )

        if captain_invalid:
            prediction.suggested_captain = None

        if transfer_invalid or validation["recommend_hold"]:
            prediction.suggested_transfer_in = None
            prediction.suggested_transfer_out = None

        if not validation["is_valid"]:
            prediction.data_snapshot["validation_errors"] = validation["errors"]

        if validation["recommend_hold"]:
            prediction.data_snapshot["hold_reason"] = "No free transfer available — holding to avoid a point hit."

        if not transfer_invalid and not validation["recommend_hold"]:
            prediction.data_snapshot["hit_cost"] = validation["hit_cost"]

        prediction.save()

    return prediction
# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_gameweek(gameweek, manager_state=None):
    """
    After a gameweek finishes, score the AI captain and transfer suggestion
    against actual results.
    """

    ai_prediction = AIPrediction.objects.filter(gameweek=gameweek).first()
    if not ai_prediction:
        return None

    captain_multiplier = 2
    if manager_state and manager_state.get("chip_active") == "3xc":
        captain_multiplier = 3

    ai_captain_points = None
    ai_was_correct_captain = None

    if ai_prediction.suggested_captain:
        stat = PlayerGameweekStat.objects.filter(
            player=ai_prediction.suggested_captain, gameweek=gameweek, is_final=True
        ).first()

        if stat:
            ai_captain_points = stat.points * captain_multiplier

            # "Correct" = matched the best-scoring player among the
            # alternatives the AI actually considered, not just "scored
            # above zero" (which nearly every captain does).
            considered_ids = ai_prediction.data_snapshot.get(
                "captain_alternatives_considered", []
            ) + [ai_prediction.suggested_captain_id]

            alt_stats = PlayerGameweekStat.objects.filter(
                player_id__in=considered_ids, gameweek=gameweek, is_final=True
            )

            if alt_stats:
                best_points = max(s.points for s in alt_stats)
                ai_was_correct_captain = stat.points == best_points

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


def validate_prediction(prediction: AIPrediction, manager_state: dict) -> dict:
    errors = []
    squad_ids = {p.id for p in manager_state["squad"]}
    active_chip = manager_state.get("chip_active")

    if prediction.suggested_captain and prediction.suggested_captain.id not in squad_ids:
        errors.append(
            f"Suggested captain {prediction.suggested_captain.web_name} is not in the current squad."
        )

    if prediction.suggested_transfer_out and prediction.suggested_transfer_out.id not in squad_ids:
        errors.append(
            f"Suggested transfer_out {prediction.suggested_transfer_out.web_name} is not in the current squad."
        )
    if prediction.suggested_transfer_in and prediction.suggested_transfer_in.id in squad_ids:
        errors.append(
            f"Suggested transfer_in {prediction.suggested_transfer_in.web_name} is already in the squad."
        )

    if prediction.suggested_transfer_in and prediction.suggested_transfer_out:
        if prediction.suggested_transfer_in.position != prediction.suggested_transfer_out.position:
            errors.append(
                f"Position mismatch: {prediction.suggested_transfer_out.web_name} "
                f"({prediction.suggested_transfer_out.position}) vs "
                f"{prediction.suggested_transfer_in.web_name} ({prediction.suggested_transfer_in.position})"
            )

    hit_cost = 0
    recommend_hold = False

    if prediction.suggested_transfer_in and prediction.suggested_transfer_out:
        available = manager_state["bank"] + float(prediction.suggested_transfer_out.price)
        needed = float(prediction.suggested_transfer_in.price)
        if needed > available:
            errors.append(
                f"Insufficient budget: need {needed}, have {available} "
                f"(bank {manager_state['bank']} + sale {prediction.suggested_transfer_out.price})"
            )

        if active_chip in ("wildcard", "freehit"):
            hit_cost = 0  # unlimited free transfers under these chips, never hold or hit
        else:
            free_transfers = manager_state.get("free_transfers", 0)
            if free_transfers < 1:
                recommend_hold = True
            else:
                hit_cost = 0

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "hit_cost": hit_cost,
        "recommend_hold": recommend_hold,
        "active_chip": active_chip,
    }

def build_squad_health_report(squad, current_gameweek) -> list[dict]:
    """Checks each squad player for concerning signals: declining form/xGI,
    injury/suspension, or a tough upcoming fixture swing."""
    flags = []

    for player in squad:
        player_flags = []

        recent = _build_recent_stats(player, current_gameweek, n=5)
        if recent["form_trend"] == "declining":
            player_flags.append(f"form declining (xGI last 5 GWs: {recent['xGI']})")

        if player.status in ("i", "d", "s", "u"):
            status_labels = {"i": "injured", "d": "doubtful", "s": "suspended", "u": "unavailable"}
            note = status_labels.get(player.status, player.status)
            if player.news:
                note += f" — {player.news}"
            player_flags.append(note)

        fdr = _next_fixtures_difficulty(player.team)
        if fdr is not None and fdr >= 4:
            player_flags.append(f"tough fixtures ahead (avg FDR {fdr})")

        if player_flags:
            flags.append({"player": player, "flags": player_flags})

    return flags


def _resolve_captain(captain_id, alternatives, squad):
    """
    Try the AI's captain pick, then its alternatives, then fall back to
    the squad's own highest-form player. Never raises — always returns
    a valid captain if the squad has at least one available player.
    """
    squad_ids = {p.id for p in squad} if squad else None
    candidate_ids = [captain_id] + [aid for aid in alternatives if aid != captain_id]

    for cid in candidate_ids:
        player = Player.objects.filter(id=cid, status="a").first()
        if player and (squad_ids is None or player.id in squad_ids):
            return player, False  # False = no fallback needed

    if squad:
        available_squad = [p for p in squad if p.status == "a"]
        if available_squad:
            return max(available_squad, key=lambda p: float(p.form)), True  # True = fallback used

    return None, True