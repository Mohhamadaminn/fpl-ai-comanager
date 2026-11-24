from celery import shared_task
import asyncio
import requests
from telegram import Bot
import logging
from django.conf import settings
from .services import sync_bootstrap_data, sync_live_gameweek_stats, sync_fixtures, format_squad_performance_message
from .models import Gameweek, PlayerGameweekStat


# The FPL API regularly errors or times out (especially while it updates after a gameweek).
# Retry network failures with exponential backoff instead of waiting for the next schedule slot.
FPL_SYNC_RETRY = {
    "autoretry_for": (requests.RequestException,),
    "retry_backoff": 30,
    "retry_backoff_max": 600,
    "retry_jitter": True,
    "max_retries": 3,
}


@shared_task(**FPL_SYNC_RETRY)
def sync_bootstrap_task():
    sync_bootstrap_data()
    for gameweek in Gameweek.objects.filter(finished=True):
        if PlayerGameweekStat.objects.filter(gameweek=gameweek, is_final=False).exists():
            sync_live_gameweek_stats(gameweek.fpl_id)


def finalize_finished_gameweeks():
    """Mark a current gameweek final once every fixture is complete.

    FPL's bootstrap endpoint can lag behind the fixtures/live endpoint. Fixture
    completion is therefore used as the local finalization signal, while the
    regular bootstrap task remains responsible for FPL's authoritative flags.
    """
    current = Gameweek.objects.filter(is_current=True).first()
    if not current:
        return

    fixtures = current.fixtures.all()
    if not fixtures.exists() or fixtures.filter(finished=False).exists():
        return False

    current.finished = True
    current.is_current = False
    current.save(update_fields=["finished", "is_current"])

    PlayerGameweekStat.objects.filter(gameweek=current).update(is_final=True)
    sync_live_gameweek_stats(current.fpl_id)

    next_gameweek = (
        Gameweek.objects.filter(fpl_id__gt=current.fpl_id)
        .order_by("fpl_id")
        .first()
    )
    if next_gameweek:
        Gameweek.objects.filter(is_next=True).exclude(pk=next_gameweek.pk).update(is_next=False)
        next_gameweek.is_next = True
        next_gameweek.save(update_fields=["is_next"])

    return True


@shared_task(**FPL_SYNC_RETRY)
def sync_live_stats_task():
    current_gw = Gameweek.objects.filter(is_current=True).first()
    if current_gw:
        if not finalize_finished_gameweeks():
            sync_live_gameweek_stats(current_gw.fpl_id)


@shared_task(**FPL_SYNC_RETRY)
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