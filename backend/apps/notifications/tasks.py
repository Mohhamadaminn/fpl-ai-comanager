import asyncio
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from telegram import Bot
from asyncio.log import logger
from apps.fpl_data.models import Gameweek
from apps.fpl_data.services import enforce_rtl, sanitize_markdown
from apps.accounts.models import FPLManagerProfile
from apps.predictions.services import generate_ai_prediction
from apps.accounts.services import get_manager_gameweek_state, sync_free_transfers


@shared_task
def send_deadline_reminder_task():
    from apps.notifications.translations import t

    gw = Gameweek.objects.filter(is_current=True).first() or Gameweek.objects.filter(is_next=True).first()
    if not gw:
        return

    time_to_deadline = gw.deadline_time - timezone.now()
    if not (timedelta(hours=0) < time_to_deadline < timedelta(hours=24)):
        return

    if getattr(gw, "reminder_sent", False):
        return

    profiles = FPLManagerProfile.objects.filter(
        telegram_chat_id__isnull=False, fpl_team_id__isnull=False
    )

    from apps.accounts.services import get_manager_gameweek_state, sync_free_transfers

    for profile in profiles:
        try:
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

        except Exception:
            logger.exception(f"send_deadline_reminder_task failed for profile {profile.id}")
            continue

    gw.reminder_sent = True
    gw.save(update_fields=["reminder_sent"])

@shared_task
def send_weekly_squad_health_task():
    from apps.fpl_data.models import Gameweek
    from apps.accounts.models import FPLManagerProfile
    from apps.accounts.services import get_manager_gameweek_state
    from apps.predictions.services import build_squad_health_report
    from apps.notifications.translations import t

    gw = Gameweek.objects.filter(is_current=True).first() or Gameweek.objects.filter(is_next=True).first()
    if not gw:
        return

    profiles = FPLManagerProfile.objects.filter(
        telegram_chat_id__isnull=False, fpl_team_id__isnull=False
    )

    for profile in profiles:
        try:
            lang = profile.preferred_language
            manager_state = get_manager_gameweek_state(profile.fpl_team_id, gw.fpl_id)
            report = build_squad_health_report(manager_state["squad"], gw, lang)

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


@shared_task(rate_limit="10/m")
def generate_differentials_task(telegram_chat_id, lang="en"):
    from apps.fpl_data.models import Gameweek
    from apps.notifications.translations import t
    from apps.predictions.services import (
        get_top_differentials, explain_differentials,
        _fixture_indicator, _xgi_indicator, _minutes_indicator,
        _form_indicator, _ownership_indicator,
    )

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
            lines.append(f"\n*{p.web_name}* ({p.selected_by_percent}% {t('owned', lang)})\n{indicators}\n_{reason}_")
        message = "\n".join(lines)

    message = enforce_rtl(message, lang)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    asyncio.run(bot.send_message(chat_id=telegram_chat_id, text=message, parse_mode="Markdown"))