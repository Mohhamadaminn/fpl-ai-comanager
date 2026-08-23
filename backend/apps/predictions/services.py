import json
from groq import Groq
from django.conf import settings
from apps.fpl_data.models import Player, Gameweek
from .models import AIPrediction

client = Groq(api_key=settings.GROQ_API_KEY)


def build_player_context(limit=30):
    """Top players by form, minimal fields for the prompt."""
    players = Player.objects.filter(status="a").order_by("-form")[:limit]
    return [
        {
            "id": p.id,
            "name": p.web_name,
            "position": p.position,
            "team": p.team.short_name,
            "price": str(p.price),
            "form": str(p.form),
            "total_points": p.total_points,
        }
        for p in players
    ]


def generate_ai_prediction(gameweek: Gameweek) -> AIPrediction:
    player_context = build_player_context()

    prompt = f"""You are an FPL (Fantasy Premier League) analyst. Based on this player data for {gameweek.name}, suggest:
1. Best captain choice
2. One transfer (player to bring in, optional)

Player data:
{json.dumps(player_context, indent=2)}

Respond ONLY with valid JSON, no other text:
{{
  "captain_id": <player id>,
  "transfer_in_id": <player id or null>,
  "reasoning": "<2-3 sentences explaining your captain and transfer choice>"
}}"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    result = json.loads(response.choices[0].message.content)

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