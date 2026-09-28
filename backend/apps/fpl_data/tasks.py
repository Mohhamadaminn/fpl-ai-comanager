from celery import shared_task
import asyncio
from telegram import Bot
import logging
from django.conf import settings
from .services import sync_bootstrap_data, sync_live_gameweek_stats, sync_fixtures, format_squad_performance_message
from .models import Gameweek



@shared_task
def sync_bootstrap_task():
    sync_bootstrap_data()


@shared_task
def sync_live_stats_task():
    current_gw = Gameweek.objects.filter(is_current=True).first()
    if current_gw:
        sync_live_gameweek_stats(current_gw.fpl_id)


@shared_task
def sync_fixtures_task():
    sync_fixtures()



logger = logging.getLogger(__name__)


@shared_task(rate_limit="10/m", bind=True, max_retries=2, default_retry_delay=30)
def generate_performance_task(self, fpl_team_id, gameweek_id, telegram_chat_id, lang="en"):
    from apps.notifications.translations import t
    try:
        gameweek = Gameweek.objects.get(id=gameweek_id)
        message = format_squad_performance_message(fpl_team_id, gameweek, lang)

        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        asyncio.run(bot.send_message(chat_id=telegram_chat_id, text=message, parse_mode="Markdown"))

    except Exception as exc:
        logger.exception(f"generate_performance_task failed for team {fpl_team_id}, gameweek {gameweek_id}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        try:
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            asyncio.run(bot.send_message(chat_id=telegram_chat_id, text=t("performance_failed", lang)))
        except Exception:
            logger.exception("Also failed to send failure notification to user")