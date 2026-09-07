import asyncio
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from telegram import Bot
from apps.fpl_data.models import Gameweek
from apps.accounts.models import FPLManagerProfile
from apps.accounts.services import get_current_squad
from apps.predictions.services import generate_ai_prediction


@shared_task
def send_deadline_reminder_task():
    gw = Gameweek.objects.filter(is_current=True).first() or Gameweek.objects.filter(is_next=True).first()
    if not gw:
        return

    time_to_deadline = gw.deadline_time - timezone.now()
    if not (timedelta(hours=0) < time_to_deadline < timedelta(hours=24)):
        return  # only fire once we're within 24h of deadline

    if getattr(gw, "reminder_sent", False):
        return

    profile = FPLManagerProfile.objects.filter(user__username="fpl_manager").first()
    if not profile or not profile.telegram_chat_id:
        return

    squad = get_current_squad(profile.fpl_team_id, gw.fpl_id)
    pred = generate_ai_prediction(gw, squad)

    message = (
        f"⏰ *{gw.name} deadline in less than 24h!*\n\n"
        f"🎖 Captain: *{pred.suggested_captain.web_name if pred.suggested_captain else 'N/A'}*\n"
        f"🔄 Transfer: {pred.suggested_transfer_out.web_name if pred.suggested_transfer_out else 'None'} ➜ "
        f"{pred.suggested_transfer_in.web_name if pred.suggested_transfer_in else 'None'}\n\n"
        f"💭 {pred.reasoning}"
    )

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    asyncio.run(bot.send_message(chat_id=profile.telegram_chat_id, text=message, parse_mode="Markdown"))
    gw.reminder_sent = True
    gw.save(update_fields=["reminder_sent"])