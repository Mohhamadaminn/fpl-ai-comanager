import asyncio
import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from telegram import Bot

from apps.accounts.models import FPLManagerProfile
from apps.accounts.services import get_manager_gameweek_state, sync_free_transfers
from apps.fpl_data.compare_engine import _target_gameweek
from apps.fpl_data.models import Gameweek
from apps.fpl_data.services import enforce_rtl, sanitize_markdown
from apps.notifications.squad_health import squad_form_context
from apps.notifications.translations import t
from apps.predictions.services import (
    build_squad_health_report,
    explain_differentials,
    generate_ai_prediction,
    get_top_differentials,
    _fixture_indicator,
    _form_indicator,
    _minutes_indicator,
    _ownership_indicator,
    _xgi_indicator,
)

logger = logging.getLogger(__name__)



# The reminder runs every 30 minutes inside the 24h window before the deadline. These
# limits make a run safe to repeat, overlap or cut short without messaging anyone twice.
REMINDER_TASK_SOFT_LIMIT = 1500   # seconds; well under the 30-minute beat interval
REMINDER_TASK_HARD_LIMIT = 1620
REMINDER_LOCK_TTL = REMINDER_TASK_HARD_LIMIT + 120  # lock outlives a killed task, then expires
REMINDER_STATE_TTL = 3 * 24 * 3600
REMINDER_MAX_ATTEMPTS = 3  # per user and gameweek, so a blocked bot can't cost Groq calls forever


def _send_reminder(profile, gw):
    lang = profile.preferred_language
    sync_free_transfers(profile)
    manager_state = get_manager_gameweek_state(profile.fpl_team_id, gw.fpl_id)
    manager_state["free_transfers"] = profile.free_transfers
    pred = generate_ai_prediction(
        gw, manager_state["squad"], manager_state, user=profile.user, lang=lang
    )

    hold_reason = pred.data_snapshot.get("hold_reason") if pred.data_snapshot else None
    hit_cost = pred.data_snapshot.get("hit_cost", 0) if pred.data_snapshot else 0
    hit_note = f" (⚠️ -{hit_cost} pts hit)" if hit_cost else ""

    transfer_line = (
        f"🔄 Transfer: {pred.suggested_transfer_out.web_name if pred.suggested_transfer_out else 'None'} ➜ "
        f"{pred.suggested_transfer_in.web_name if pred.suggested_transfer_in else 'None'}{hit_note}"
    )
    if hold_reason:
        transfer_line = f"🔒 Hold — {hold_reason}"

    message = (
        f"⏰ *{gw.name} {t('deadline_warning', lang)}*\n\n"
        f"🎖 {t('captain_label', lang)}: *{pred.suggested_captain.web_name if pred.suggested_captain else 'N/A'}*\n"
        f"{transfer_line}\n\n"
        f"💭 {sanitize_markdown(pred.reasoning)}"
    )

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    asyncio.run(bot.send_message(chat_id=profile.telegram_chat_id, text=message, parse_mode="Markdown"))


@shared_task(soft_time_limit=REMINDER_TASK_SOFT_LIMIT, time_limit=REMINDER_TASK_HARD_LIMIT)
def send_deadline_reminder_task():
    gw = _target_gameweek()
    if not gw:
        return

    time_to_deadline = gw.deadline_time - timezone.now()
    if not (timedelta(hours=0) < time_to_deadline < timedelta(hours=24)):
        return

    if getattr(gw, "reminder_sent", False):
        return

    # Only one run at a time: a slow run must not be overtaken by the next beat tick.
    lock_key = f"deadline-reminder:lock:{gw.id}"
    if not cache.add(lock_key, "1", timeout=REMINDER_LOCK_TTL):
        logger.info("Deadline reminder for GW %s is already running; skipping this run.", gw.id)
        return

    try:
        profiles = FPLManagerProfile.objects.filter(
            telegram_chat_id__isnull=False, fpl_team_id__isnull=False
        )

        still_pending = 0
        for profile in profiles:
            sent_key = f"deadline-reminder:sent:{gw.id}:{profile.id}"
            attempts_key = f"deadline-reminder:attempts:{gw.id}:{profile.id}"

            if cache.get(sent_key):
                continue  # already got this gameweek's reminder in an earlier run

            cache.add(attempts_key, 0, timeout=REMINDER_STATE_TTL)
            attempts = cache.incr(attempts_key)
            if attempts > REMINDER_MAX_ATTEMPTS:
                logger.error("Giving up on deadline reminder for profile %s (GW %s)", profile.id, gw.id)
                continue

            try:
                _send_reminder(profile, gw)
                cache.set(sent_key, "1", timeout=REMINDER_STATE_TTL)
            except SoftTimeLimitExceeded:
                raise
            except Exception:
                logger.exception(f"send_deadline_reminder_task failed for profile {profile.id}")
                if attempts < REMINDER_MAX_ATTEMPTS:
                    still_pending += 1  # the next run will try this user again

        # Mark the gameweek done only once everybody has either been messaged or given up on.
        if still_pending == 0:
            gw.reminder_sent = True
            gw.save(update_fields=["reminder_sent"])
    finally:
        cache.delete(lock_key)


@shared_task(soft_time_limit=1500, time_limit=1620)
def send_weekly_squad_health_task():
    # Picks only exist for the current (or a past) gameweek: the FPL API returns 404 for the next
    # one until its deadline passes. So the squad is fetched from the current GW; the form data
    # inside squad_form_context() still targets the next GW on its own.
    gw = Gameweek.objects.filter(is_current=True).first() or Gameweek.objects.filter(is_next=True).first()
    if not gw:
        return

    form_ctx = squad_form_context()  # heavy data, built once for all users

    profiles = FPLManagerProfile.objects.filter(
        telegram_chat_id__isnull=False, fpl_team_id__isnull=False
    )

    for profile in profiles:
        try:
            lang = profile.preferred_language
            manager_state = get_manager_gameweek_state(profile.fpl_team_id, gw.fpl_id)
            report = build_squad_health_report(manager_state["squad"], gw, lang, form_ctx=form_ctx)

            if not report:
                message = f"✅ *{t('squad_check_title', lang)} — {gw.name}*\n\n{t('no_concerns', lang)}"
            else:
                lines = [f"⚠️ *{t('squad_check_title', lang)} — {gw.name}*\n"]
                for item in report:
                    lines.append(f"\n*{item['player'].web_name}*")
                    for flag in item["flags"]:
                        lines.append(f"  • {flag}")
                message = "\n".join(lines)

            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            asyncio.run(bot.send_message(chat_id=profile.telegram_chat_id, text=message, parse_mode="Markdown"))

        except Exception:
            logger.exception(f"send_weekly_squad_health_task failed for profile {profile.id}")
            continue


@shared_task(rate_limit="10/m", bind=True, max_retries=2, default_retry_delay=30)
def generate_differentials_task(self, telegram_chat_id, lang="en"):
    try:
        _send_differentials(telegram_chat_id, lang)
    except Exception as exc:
        logger.exception(f"generate_differentials_task failed for chat {telegram_chat_id}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        try:
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            asyncio.run(bot.send_message(chat_id=telegram_chat_id, text=t("differentials_failed", lang)))
        except Exception:
            logger.exception("Also failed to send failure notification to user")


def _send_differentials(telegram_chat_id, lang):
    gw = Gameweek.objects.filter(is_current=True).first()
    if not gw:
        return

    differentials = get_top_differentials(gw)
    reasons = explain_differentials(differentials, gw, lang)

    fallback_reason = (
        "آمار زیرساختی خوبی داره با درصد انتخاب پایین."
        if lang == "fa" else
        "Strong underlying numbers at low ownership."
    )

    if not differentials:
        message = f"🔍 *{t('differentials_title', lang)} — {gw.name}*\n\n{t('no_differentials', lang)}"
    else:
        lines = [f"🔍 *{t('differentials_title', lang)} — {gw.name}*\n"]
        for d in differentials:
            p = d["player"]
            reason = sanitize_markdown(reasons.get(str(p.id), fallback_reason))
            indicators = "\n".join([
                _fixture_indicator(d["next_fdr"], lang),
                _xgi_indicator(d["recent"], lang),
                _minutes_indicator(d["recent"], lang),
                _form_indicator(p.form, lang),
                _ownership_indicator(p.selected_by_percent, lang),
            ])
            opponent_label = ""

            if d["opponent"] is not None:
                venue = "H" if d["is_home"] else "A"
                opponent_label = (
                    f" vs {sanitize_markdown(d['opponent'])} ({venue})"
                )

            player_name = f"{p.web_name}{opponent_label}"

            if lang == "fa":
                ownership = (
                    f"درصد انتخاب: {p.selected_by_percent}%"
                )
            else:
                ownership = (
                    f"{p.selected_by_percent}% {t('owned', lang)}"
                )

            lines.append(
                f"\n*{player_name}*\n"
                f"{ownership}\n"
                f"{indicators}\n"
                f"_{reason}_"
            )
        message = "\n".join(lines)

    message = enforce_rtl(message, lang)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    asyncio.run(bot.send_message(chat_id=telegram_chat_id, text=message, parse_mode="Markdown"))