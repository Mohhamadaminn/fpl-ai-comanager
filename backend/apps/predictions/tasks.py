import asyncio
import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth.models import User
from telegram import Bot
from django.db import models

from apps.fpl_data.models import Gameweek, Fixture
from apps.accounts.models import FPLManagerProfile
from apps.accounts.services import get_manager_gameweek_state
from .models import AIPrediction, PredictionEvaluation
from .services import build_squad_fingerprint, evaluate_gameweek, generate_ai_prediction


def _get_next_opponent_label(team):
    fixture = (
        Fixture.objects.filter(finished=False)
        .filter(models.Q(team_home=team) | models.Q(team_away=team))
        .order_by("kickoff_time")
        .first()
    )
    if not fixture:
        return ""

    if fixture.team_home_id == team.id:
        return f"(H - {fixture.team_away.short_name})"
    else:
        return f"(A - {fixture.team_home.short_name})"


logger = logging.getLogger(__name__)


@shared_task
def evaluate_finished_gameweeks_task():
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


@shared_task(rate_limit="10/m", bind=True, max_retries=2, default_retry_delay=30)
def generate_prediction_task(self, user_id, gameweek_id, fpl_team_id, telegram_chat_id):
    try:
        user = User.objects.get(id=user_id)
        gameweek = Gameweek.objects.get(id=gameweek_id)
        manager_state = get_manager_gameweek_state(fpl_team_id, gameweek.fpl_id)
        fingerprint = build_squad_fingerprint(manager_state["squad"])

        cached = AIPrediction.objects.filter(gameweek=gameweek, squad_fingerprint=fingerprint).first()
        if cached:
            logger.info(f"Cache hit for gameweek {gameweek.id}, fingerprint {fingerprint[:8]}")
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
            logger.info(f"Cache miss — calling Groq for gameweek {gameweek.id}, fingerprint {fingerprint[:8]}")
            prediction = generate_ai_prediction(
                gameweek, manager_state["squad"], manager_state, user=user
            )

        captain = prediction.suggested_captain.web_name if prediction.suggested_captain else "N/A"
        captain_opponent = (
            _get_next_opponent_label(prediction.suggested_captain.team)
            if prediction.suggested_captain else ""
        )
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
            f"🎖 Captain: *{captain} {captain_opponent}*\n"
            f"{transfer_line}\n\n"
            f"💭 {prediction.reasoning}"
        )

        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        asyncio.run(bot.send_message(chat_id=telegram_chat_id, text=message, parse_mode="Markdown"))

    except Exception as exc:
        logger.exception(f"generate_prediction_task failed for user {user_id}, gameweek {gameweek_id}")

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

        try:
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            asyncio.run(bot.send_message(
                chat_id=telegram_chat_id,
                text="Sorry, I couldn't generate your prediction right now. Please try again in a few minutes."
            ))
        except Exception:
            logger.exception("Also failed to send failure notification to user")