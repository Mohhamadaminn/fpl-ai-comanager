import json
from django.db import models
from decimal import Decimal
from groq import Groq
from django.conf import settings
from apps.fpl_data.models import Player, Gameweek, Fixture, PlayerGameweekStat
from .models import AIPrediction, PredictionEvaluation

client = Groq(api_key=settings.GROQ_API_KEY)


def _next_fixtures_difficulty(team, n=5):
    """Average FDR for a team's next n unfinished fixtures."""
    fixtures = Fixture.objects.filter(
        finished=False
    ).filter(
        models.Q(team_home=team) | models.Q(team_away=team)
    ).order_by("kickoff_time")[:n]

    if not fixtures:
        return None

    total = 0
    for f in fixtures:
        total += f.difficulty_home if f.team_home_id == team.id else f.difficulty_away
    return round(total / len(fixtures), 2)


def build_player_context(top_n=20, premium_n=10):
    """
    Combine two pools so premium players are never dropped just because
    of a recent form dip:
    - top_n by form (in-form picks / differentials)
    - premium_n by total_points+price (established premiums, regardless of form)
    Dedup by id.
    """
    base_qs = Player.objects.filter(status="a").select_related("team")

    in_form = list(base_qs.order_by("-form")[:top_n])
    premiums = list(base_qs.order_by("-price", "-total_points")[:premium_n])

    seen = {}
    for p in premiums + in_form:  # premiums first so they're not dropped on dedup
        seen[p.id] = p

    context = []
    for p in seen.values():
        context.append({
            "id": p.id,
            "name": p.web_name,
            "position": p.position,
            "team": p.team.short_name,
            "price": str(p.price),
            "form": str(p.form),
            "total_points": p.total_points,
            "selected_by_percent": str(p.selected_by_percent),
            "xGI_season": str(p.expected_goal_involvements),
            "ict_index": str(p.ict_index),
            "status": p.status,
            "news": p.news,
            "next_fixtures_fdr_avg": _next_fixtures_difficulty(p.team),
        })
    return context


def generate_ai_prediction(gameweek: Gameweek) -> AIPrediction:
    player_context = build_player_context()

    prompt = f"""You are an FPL (Fantasy Premier League) analyst making a decision for {gameweek.name}.

DATA:
{json.dumps(player_context, indent=2)}

Field meaning: next_fixtures_fdr_avg is average fixture difficulty over the next 5 games (1=easiest, 5=hardest). xGI_season is season-long expected goal involvement (goals+assists quality), more reliable than raw form.

DECISION RULES (apply in this order):
1. Weight next_fixtures_fdr_avg and xGI_season above short-term form. A single big gameweek is not a trend — do not recommend a player primarily because of one good match.
2. Before picking a captain or transfer target, explicitly compare it against the highest-priced/highest-owned players at the same position in the data. If you pick a lower-owned player over a premium, you must justify why the premium is worse right now (bad fixtures, poor xGI, injury/news flag).
3. Check "status" and "news" — never recommend a player who is injured, doubtful, or suspended without flagging it.
4. If two players are close, prefer the one with the easier next_fixtures_fdr_avg.

TASK — respond ONLY with valid JSON, no other text:
{{
  "captain_id": <player id>,
  "captain_alternatives_considered": [<player id>, <player id>],
  "transfer_in_id": <player id or null>,
  "reasoning": "<3-4 sentences citing the specific stats (form, xGI, FDR) that drove the decision, and why any premium alternative was NOT chosen>"
}}"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )


    raw_content = response.choices[0].message.content
    try:
        result = json.loads(raw_content)
    except json.JSONDecodeError:
        raise ValueError(f"AI returned invalid JSON: {raw_content[:500]}")
    

    captain = Player.objects.filter(id=result["captain_id"]).first()
    transfer_in = Player.objects.filter(id=result.get("transfer_in_id")).first() if result.get("transfer_in_id") else None

    return AIPrediction.objects.update_or_create(
        gameweek=gameweek,
        defaults={
            "suggested_captain": captain,
            "suggested_transfer_in": transfer_in,
            "reasoning": result["reasoning"],
            "data_snapshot": {"players_considered": player_context},
        },
    )[0]


def evaluate_gameweek(gameweek):
    """After a gameweek finishes, score the AI's captain and transfer suggestions against real results."""
    ai_prediction = AIPrediction.objects.filter(gameweek=gameweek).first()
    if not ai_prediction:
        return None

    ai_captain_points = None
    ai_was_correct_captain = None
    if ai_prediction.suggested_captain:
        stat = PlayerGameweekStat.objects.filter(
            player=ai_prediction.suggested_captain, gameweek=gameweek, is_final=True
        ).first()
        if stat:
            ai_captain_points = stat.points * 2  # captain doubles points in FPL
            # "correct" here = captain actually returned positive points
            ai_was_correct_captain = stat.points > 0

    ai_transfer_delta = None
    if ai_prediction.suggested_transfer_in and ai_prediction.suggested_transfer_out:
        in_stat = PlayerGameweekStat.objects.filter(
            player=ai_prediction.suggested_transfer_in, gameweek=gameweek, is_final=True
        ).first()
        out_stat = PlayerGameweekStat.objects.filter(
            player=ai_prediction.suggested_transfer_out, gameweek=gameweek, is_final=True
        ).first()
        if in_stat and out_stat:
            ai_transfer_delta = in_stat.points - out_stat.points

    evaluation, _ = PredictionEvaluation.objects.update_or_create(
        gameweek=gameweek,
        defaults={
            "ai_captain_points": ai_captain_points,
            "ai_was_correct_captain": ai_was_correct_captain,
            "ai_transfer_points_delta": ai_transfer_delta,
        },
    )
    return evaluation