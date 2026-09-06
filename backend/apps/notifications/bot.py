import re
import logging
from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from django.conf import settings
from django.contrib.auth.models import User
from apps.accounts.models import FPLManagerProfile

logger = logging.getLogger(__name__)

WAITING_FOR_TEAM_LINK = 1
TEAM_ID_PATTERN = re.compile(r"entry/(\d+)")


def extract_team_id(text: str) -> int | None:
    match = TEAM_ID_PATTERN.search(text)
    if match:
        return int(match.group(1))
    if text.strip().isdigit():
        return int(text.strip())
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm your FPL AI Co-Manager. Use /setteamid to link your team, "
        "or /prediction to get this gameweek's suggestion."
    )


async def prediction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.models import Gameweek
    from apps.accounts.models import FPLManagerProfile
    from apps.accounts.services import get_current_squad
    from apps.predictions.services import generate_ai_prediction

    await update.message.reply_text("Analyzing this gameweek, one moment...")

    gw = await sync_to_async(lambda: Gameweek.objects.filter(is_current=True).first())()
    if not gw:
        await update.message.reply_text("Couldn't find the current gameweek.")
        return

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(user__username="fpl_manager").first()
    )()
    if not profile:
        await update.message.reply_text("No team linked yet. Use /setteamid first.")
        return

    try:
        squad = await sync_to_async(get_current_squad)(profile.fpl_team_id, gw.fpl_id)
        pred = await sync_to_async(generate_ai_prediction)(gw, squad)
    except Exception as e:
        logger.exception("Prediction failed")
        await update.message.reply_text(f"Something went wrong generating the prediction: {e}")
        return

    captain = await sync_to_async(lambda: pred.suggested_captain.web_name if pred.suggested_captain else "N/A")()
    transfer_in = await sync_to_async(lambda: pred.suggested_transfer_in.web_name if pred.suggested_transfer_in else "None")()
    transfer_out = await sync_to_async(lambda: pred.suggested_transfer_out.web_name if pred.suggested_transfer_out else "None")()

    message = (
        f"📊 *{gw.name} AI Prediction*\n\n"
        f"🎖 Captain: *{captain}*\n"
        f"🔄 Transfer: {transfer_out} ➜ {transfer_in}\n\n"
        f"💭 {pred.reasoning}"
    )
    await update.message.reply_text(message, parse_mode="Markdown")


async def setteamid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Go to your Fantasy team, click on the Points tab, and paste the URL link here."
    )
    return WAITING_FOR_TEAM_LINK


async def setteamid_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_input = update.message.text
    team_id = extract_team_id(raw_input)

    if team_id is None:
        await update.message.reply_text("Couldn't find a team ID in that. Please paste your team link again.")
        return WAITING_FOR_TEAM_LINK

    def _save():
        user, _ = User.objects.get_or_create(username="fpl_manager")
        profile, _ = FPLManagerProfile.objects.update_or_create(
            user=user, defaults={"fpl_team_id": team_id}
        )
        return profile

    await sync_to_async(_save)()
    await update.message.reply_text("Your team has been saved!")
    return ConversationHandler.END


async def setteamid_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


setteamid_conversation = ConversationHandler(
    entry_points=[CommandHandler("setteamid", setteamid_start)],
    states={
        WAITING_FOR_TEAM_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, setteamid_receive)],
    },
    fallbacks=[CommandHandler("cancel", setteamid_cancel)],
)


async def my_team_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    def _get():
        return FPLManagerProfile.objects.filter(user__username="fpl_manager").first()

    profile = await sync_to_async(_get)()
    if profile:
        await update.message.reply_text(f"Your team ID: {profile.fpl_team_id}")
    else:
        await update.message.reply_text("No team ID set yet. Use /setteamid.")


def build_application():
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prediction", prediction))
    application.add_handler(setteamid_conversation)
    application.add_handler(CommandHandler("myteamid", my_team_id))
    return application