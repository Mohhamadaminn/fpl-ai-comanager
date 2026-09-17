from celery import shared_task
from apps.fpl_data.models import Gameweek
from .models import AIPrediction, PredictionEvaluation
from .services import build_squad_fingerprint, evaluate_gameweek


@shared_task
def evaluate_finished_gameweeks_task():
    from apps.accounts.models import FPLManagerProfile
    from apps.accounts.services import get_manager_gameweek_state

    profile = FPLManagerProfile.objects.filter(user__username="fpl_manager").first()

    finished_gws = Gameweek.objects.filter(finished=True)
    for gw in finished_gws:
        has_prediction = AIPrediction.objects.filter(gameweek=gw).exists()
        already_evaluated = PredictionEvaluation.objects.filter(gameweek=gw).exists()
        if has_prediction and not already_evaluated:
            manager_state = None
            if profile:
                try:
                    manager_state = get_manager_gameweek_state(profile.fpl_team_id, gw.fpl_id)
                except Exception:
                    pass  # fall back to default 2x multiplier if this fails
            evaluate_gameweek(gw, manager_state)


@shared_task(rate_limit="10/m")
def generate_prediction_task(user_id, gameweek_id, fpl_team_id, telegram_chat_id):
    from django.contrib.auth.models import User
    from apps.fpl_data.models import Gameweek
    from apps.accounts.services import get_manager_gameweek_state
    from .services import generate_ai_prediction
    from .models import AIPrediction

    user = User.objects.get(id=user_id)
    gameweek = Gameweek.objects.get(id=gameweek_id)
    manager_state = get_manager_gameweek_state(fpl_team_id, gameweek.fpl_id)
    fingerprint = build_squad_fingerprint(manager_state["squad"])

    cached = AIPrediction.objects.filter(gameweek=gameweek, squad_fingerprint=fingerprint).first()
    if cached:
        prediction, _ = AIPrediction.objects.update_or_create(
            user=user, gameweek=gameweek,
            defaults={
                "squad_fingerprint": fingerprint,
                "suggested_captain": cached.suggested_captain,
                "suggested_transfer_in": cached.suggested_transfer_in,
                "suggested_transfer_out": cached.suggested_transfer_out,
                "reasoning": cached.reasoning,
                "data_snapshot": cached.data_snapshot,
            },
        )
    else:
        prediction = generate_ai_prediction(
            gameweek, manager_state["squad"], manager_state, user=user
        )

    # ---- Formatting logic moved here from the bot handler ----
    captain = prediction.suggested_captain.web_name if prediction.suggested_captain else "N/A"
    transfer_in = prediction.suggested_transfer_in.web_name if prediction.suggested_transfer_in else "None"
    transfer_out = prediction.suggested_transfer_out.web_name if prediction.suggested_transfer_out else "None"
    hold_reason = prediction.data_snapshot.get("hold_reason") if prediction.data_snapshot else None
    hit_cost = prediction.data_snapshot.get("hit_cost", 0) if prediction.data_snapshot else 0
    hit_note = f" (⚠️ -{hit_cost} pts hit)" if hit_cost else ""

    transfer_line = f"🔄 Transfer: {transfer_out} ➜ {transfer_in}{hit_note}"
    if hold_reason:
        transfer_line = f"🔒 Hold — {hold_reason}"

    message = (
        f"📊 *{gameweek.name} AI Prediction*\n\n"
        f"🎖 Captain: *{captain}*\n"
        f"{transfer_line}\n\n"
        f"💭 {prediction.reasoning}"
    )

    from telegram import Bot
    from django.conf import settings
    import asyncio
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    asyncio.run(bot.send_message(chat_id=telegram_chat_id, text=message, parse_mode="Markdown"))