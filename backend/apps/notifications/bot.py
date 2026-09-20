import re
import logging
from asgiref.sync import sync_to_async
from telegram import Update
from telegram import BotCommand
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
    def _save_chat_id():
        user, _ = User.objects.get_or_create(username="fpl_manager")
        FPLManagerProfile.objects.update_or_create(
            user=user, defaults={"telegram_chat_id": update.effective_chat.id}
        )

    await sync_to_async(_save_chat_id)()
    await update.message.reply_text(
        "Hey! I'm your FPL AI Co-Manager. Use /setteamid to link your team, "
        "or /prediction to get this gameweek's suggestion."
    )

async def fixtures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.models import Gameweek, Fixture
    from django.db import models
    from apps.accounts.models import FPLManagerProfile
    from apps.accounts.services import get_manager_gameweek_state

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(telegram_chat_id=update.effective_chat.id).first()
    )()
    if not profile or not profile.fpl_team_id:
        await update.message.reply_text("No team linked yet. Use /setteamid first.")
        return

    gw = await sync_to_async(lambda: Gameweek.objects.filter(is_current=True).first())()
    if not gw:
        await update.message.reply_text("Couldn't find the current gameweek.")
        return

    def _get_fixtures():
        state = get_manager_gameweek_state(profile.fpl_team_id, gw.fpl_id)
        squad_teams = {p.team for p in state["squad"]}

        seen_fixture_ids = set()
        lines = []

        for team in sorted(squad_teams, key=lambda t: t.name):
            fixture = (
                Fixture.objects.filter(finished=False)
                .filter(models.Q(team_home=team) | models.Q(team_away=team))
                .order_by("kickoff_time")
                .first()
            )
            if not fixture:
                lines.append(f"*{team.name}* — no upcoming fixture found")
                continue

            if fixture.id in seen_fixture_ids:
                continue  # already shown from the other team's perspective
            seen_fixture_ids.add(fixture.id)

            if fixture.team_home_id == team.id:
                home_team, away_team = team, fixture.team_away
                fdr = fixture.difficulty_home
            else:
                home_team, away_team = fixture.team_home, team
                fdr = fixture.difficulty_away

            fdr_icon = "🟢" if fdr and fdr <= 2 else "🟡" if fdr == 3 else "🔴"
            kickoff = fixture.kickoff_time.strftime("%a %H:%M") if fixture.kickoff_time else "TBD"

            lines.append(f"{fdr_icon} *{home_team.short_name}* vs *{away_team.short_name}* — {kickoff}")

        return lines

    lines = await sync_to_async(_get_fixtures)()

    message = f"📅 *Your Squad's Upcoming Fixtures*\n\n" + "\n".join(lines)
    await update.message.reply_text(message, parse_mode="Markdown")


async def prediction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.models import Gameweek
    from apps.accounts.models import FPLManagerProfile
    from apps.predictions.tasks import generate_prediction_task

    gw = await sync_to_async(lambda: Gameweek.objects.filter(is_current=True).first())()
    if not gw:
        await update.message.reply_text("Couldn't find the current gameweek.")
        return

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(telegram_chat_id=update.effective_chat.id).first()
    )()
    if not profile or not profile.fpl_team_id:
        await update.message.reply_text("No team linked yet. Use /setteamid first.")
        return

    await update.message.reply_text("Got it — I'll send your prediction shortly.")

    await sync_to_async(generate_prediction_task.delay)(
        profile.user_id, gw.id, profile.fpl_team_id, update.effective_chat.id
    )



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


async def differentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.notifications.tasks import generate_differentials_task

    await update.message.reply_text("Looking for differentials, one moment...")
    await sync_to_async(generate_differentials_task.delay)(update.effective_chat.id)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.models import Gameweek
    from apps.accounts.models import FPLManagerProfile

    def _get_data():
        profile = FPLManagerProfile.objects.filter(user__username="fpl_manager").first()
        gw = Gameweek.objects.filter(is_current=True).first() or Gameweek.objects.filter(is_next=True).first()
        return profile, gw

    profile, gw = await sync_to_async(_get_data)()

    if not profile or not profile.fpl_team_id:
        await update.message.reply_text("No team linked yet. Use /setteamid to get started.")
        return

    team_id = profile.fpl_team_id
    free_transfers = profile.free_transfers
    reminders_status = "✅ Enabled" if profile.telegram_chat_id else "❌ Not set (send /start to enable)"

    if gw:
        gw_name = gw.name
        deadline = gw.deadline_time.strftime("%Y-%m-%d %H:%M UTC")
    else:
        gw_name = "Unknown"
        deadline = "N/A"

    message = (
        f"📋 *Status*\n\n"
        f"🆔 Team ID: {team_id}\n"
        f"📅 Gameweek: {gw_name}\n"
        f"⏰ Deadline: {deadline}\n"
        f"🔁 Free transfers: {free_transfers}\n"
        f"🔔 Reminders: {reminders_status}"
    )
    await update.message.reply_text(message, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🤖 *FPL AI Co-Manager — Commands*\n\n"
        "/start — Get started and enable reminders\n"
        "/fixtures — Show your squad's teams' next fixtures\n"
        "/setteamid — Link your FPL team (paste your team link)\n"
        "/myteamid — Show your linked team ID\n"
        "/prediction — Get this gameweek's AI suggestion\n"
        "/differentials — Get 3 low-ownership picks worth watching\n"
        "/status — Show team ID, gameweek, deadline, free transfers, reminder status\n"
        "/help — Show this list"
        
    )
    await update.message.reply_text(message, parse_mode="Markdown")



async def set_bot_commands(application: Application):
    commands = [
        BotCommand("start", "Get started and enable reminders"),
        BotCommand("fixtures", "your players fixtures"),
        BotCommand("setteamid", "Link your FPL team"),
        BotCommand("myteamid", "Show your linked team ID"),
        BotCommand("prediction", "Get this gameweek's AI suggestion"),
        BotCommand("differentials", "Get three differentials"),
        BotCommand("status", "Show team status and deadline"),
        BotCommand("help", "Show all commands"),
    ]
    await application.bot.set_my_commands(commands)


def build_application():
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).post_init(set_bot_commands).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("fixtures", fixtures))
    application.add_handler(CommandHandler("prediction", prediction))
    application.add_handler(setteamid_conversation)
    application.add_handler(CommandHandler("myteamid", my_team_id))
    application.add_handler(CommandHandler("differentials", differentials))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("help", help_command))
    return application


