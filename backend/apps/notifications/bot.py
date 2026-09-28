import re
import logging
from asgiref.sync import sync_to_async
from telegram import Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
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
    from apps.accounts.services import get_or_create_profile_for_chat
    from apps.notifications.translations import t

    profile = await sync_to_async(get_or_create_profile_for_chat)(update.effective_chat.id)
    await update.message.reply_text(t("welcome", profile.preferred_language))

    

async def fixtures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.models import Gameweek, Fixture
    from apps.fpl_data.services import enforce_rtl
    from django.db import models
    from apps.accounts.models import FPLManagerProfile
    from apps.accounts.services import get_manager_gameweek_state
    from apps.notifications.translations import t

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(telegram_chat_id=update.effective_chat.id).first()
    )()

    lang = profile.preferred_language if profile else "en"

    if not profile or not profile.fpl_team_id:
        await update.message.reply_text(t("no_team_linked", lang))
        return

    gw = await sync_to_async(lambda: Gameweek.objects.filter(is_current=True).first())()
    if not gw:
        await update.message.reply_text(t("no_current_gameweek", lang))
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
                lines.append(f"*{team.name}* — {t('no_upcoming_fixture', lang)}")
                continue

            if fixture.id in seen_fixture_ids:
                continue
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

    message = f"📅 *{t('fixtures_title', lang)}*\n\n" + "\n".join(lines)
    message = enforce_rtl(message, lang)
    await update.message.reply_text(message, parse_mode="Markdown")

async def myteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.models import Gameweek, PlayerGameweekStat
    from apps.accounts.models import FPLManagerProfile
    from apps.accounts.services import get_manager_gameweek_state
    from apps.notifications.translations import t

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(
            telegram_chat_id=update.effective_chat.id
        ).first()
    )()

    lang = profile.preferred_language if profile else "en"

    if not profile or not profile.fpl_team_id:
        await update.message.reply_text(t("no_team_linked", lang))
        return

    gw = await sync_to_async(
        lambda: Gameweek.objects.filter(is_current=True).first()
    )()

    if not gw:
        await update.message.reply_text(t("no_current_gameweek", lang))
        return

    def _get_team_points():
        state = get_manager_gameweek_state(
            profile.fpl_team_id,
            gw.fpl_id,
        )

        squad = state["squad"]
        positions = state["positions"]

        captain_fpl_id = state["captain_fpl_id"]
        vice_captain_fpl_id = state["vice_captain_fpl_id"]

        stats_by_player = {
            s.player_id: s
            for s in PlayerGameweekStat.objects.filter(
                gameweek=gw,
                player__in=squad,
            )
        }

        starting_xi = [
            p for p in squad
            if positions.get(p.fpl_id, 0) <= 11
        ]

        bench = [
            p for p in squad
            if positions.get(p.fpl_id, 0) > 11
        ]

        total_points = 0
        sections = []

        for pos, emoji, title_key in [
            ("GKP", "🧤", "pos_gkp"),
            ("DEF", "🛡", "pos_def"),
            ("MID", "🎯", "pos_mid"),
            ("FWD", "⚡", "pos_fwd"),
        ]:
            players = [
                p for p in starting_xi
                if p.position == pos
            ]

            if not players:
                continue

            lines = [f"{emoji} *{t(title_key, lang)}*"]

            for p in players:
                stat = stats_by_player.get(p.id)
                pts = stat.points if stat else 0

                is_captain = p.fpl_id == captain_fpl_id
                is_vice = p.fpl_id == vice_captain_fpl_id

                multiplier = 2 if is_captain else 1
                effective_pts = pts * multiplier
                total_points += effective_pts

                tag = (
                    " (C)"
                    if is_captain
                    else " (VC)"
                    if is_vice
                    else ""
                )

                live_marker = (
                    ""
                    if (stat and stat.is_final)
                    else f" 🔴 {t('live', lang)}"
                    if stat
                    else ""
                )

                lines.append(
                    f"• {p.web_name}{tag}: "
                    f"{effective_pts} pts{live_marker}"
                )

            sections.append("\n".join(lines))

        bench_lines = [f"🪑 *{t('bench', lang)}*"]

        for p in bench:
            stat = stats_by_player.get(p.id)
            pts = stat.points if stat else 0

            live_marker = (
                ""
                if (stat and stat.is_final)
                else f" 🔴 {t('live', lang)}"
                if stat
                else ""
            )

            bench_lines.append(
                f"• {p.web_name}: {pts} pts{live_marker}"
            )

        sections.append("\n".join(bench_lines))

        return sections, total_points

    sections, total_points = await sync_to_async(_get_team_points)()

    message = (
        f"⚽ *{t('your_team_title', lang)} — {gw.name}*\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"📊 {t('total', lang)}: *{total_points} pts*\n"
        f"━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(sections)
    )

    await update.message.reply_text(
        message,
        parse_mode="Markdown",
    )

async def prediction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.models import Gameweek
    from apps.accounts.models import FPLManagerProfile
    from apps.predictions.tasks import generate_prediction_task
    from apps.notifications.translations import t

    gw = await sync_to_async(lambda: Gameweek.objects.filter(is_current=True).first())()
    if not gw:
        await update.message.reply_text(t("no_current_gameweek", "en"))
        return

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(telegram_chat_id=update.effective_chat.id).first()
    )()
    if not profile or not profile.fpl_team_id:
        await update.message.reply_text(t("no_team_linked", "en"))
        return

    lang = profile.preferred_language
    await update.message.reply_text(t("prediction_pending", lang))

    await sync_to_async(generate_prediction_task.delay)(
        profile.user_id, gw.id, profile.fpl_team_id, update.effective_chat.id, lang
    )



async def setteamid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.accounts.services import get_or_create_profile_for_chat
    from apps.notifications.translations import t

    profile = await sync_to_async(get_or_create_profile_for_chat)(update.effective_chat.id)
    await update.message.reply_text(t("setteamid_prompt", profile.preferred_language))
    return WAITING_FOR_TEAM_LINK


async def setteamid_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from django.db import IntegrityError
    from apps.accounts.services import get_or_create_profile_for_chat
    from apps.notifications.translations import t

    chat_id = update.effective_chat.id
    team_id = extract_team_id(update.message.text)

    def _save():
        profile = get_or_create_profile_for_chat(chat_id)
        if team_id is None:
            return profile.preferred_language, "not_found"
        try:
            profile.fpl_team_id = team_id
            profile.save(update_fields=["fpl_team_id"])
        except IntegrityError:
            # fpl_team_id is unique — another chat already linked this team
            return profile.preferred_language, "taken"
        return profile.preferred_language, "ok"

    lang, result = await sync_to_async(_save)()

    if result == "not_found":
        await update.message.reply_text(t("team_id_not_found", lang))
        return WAITING_FOR_TEAM_LINK
    if result == "taken":
        await update.message.reply_text(t("team_id_taken", lang))
        return ConversationHandler.END

    await update.message.reply_text(t("team_saved", lang))
    return ConversationHandler.END


async def setteamid_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.accounts.services import get_or_create_profile_for_chat
    from apps.notifications.translations import t

    profile = await sync_to_async(get_or_create_profile_for_chat)(update.effective_chat.id)
    await update.message.reply_text(t("cancelled", profile.preferred_language))
    return ConversationHandler.END



setteamid_conversation = ConversationHandler(
    entry_points=[CommandHandler("setteamid", setteamid_start)],
    states={
        WAITING_FOR_TEAM_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, setteamid_receive)],
    },
    fallbacks=[CommandHandler("cancel", setteamid_cancel)],
)


async def my_team_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.accounts.models import FPLManagerProfile
    from apps.notifications.translations import t

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(telegram_chat_id=update.effective_chat.id).first()
    )()
    lang = profile.preferred_language if profile else "en"

    if profile and profile.fpl_team_id:
        await update.message.reply_text(f"{t('your_team_id', lang)}: {profile.fpl_team_id}")
    else:
        await update.message.reply_text(t("no_team_id_set", lang))



async def differentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.notifications.tasks import generate_differentials_task

    await update.message.reply_text("Looking for differentials, one moment...")
    await sync_to_async(generate_differentials_task.delay)(update.effective_chat.id)



async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.services import get_last_finished_gameweek, check_rate_limit
    from apps.fpl_data.tasks import generate_performance_task
    from apps.accounts.models import FPLManagerProfile
    from apps.notifications.translations import t

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(telegram_chat_id=update.effective_chat.id).first()
    )()
    lang = profile.preferred_language if profile else "en"

    rate_limit_error = await sync_to_async(check_rate_limit)(update.effective_chat.id)
    if rate_limit_error:
        await update.message.reply_text(t(rate_limit_error, lang))  # see note below
        return

    if not profile or not profile.fpl_team_id:
        await update.message.reply_text(t("no_team_linked", lang))
        return

    gw = await sync_to_async(get_last_finished_gameweek)()
    if not gw:
        await update.message.reply_text(t("no_finished_gameweek", lang))
        return

    await update.message.reply_text(t("crunching_numbers", lang))
    await sync_to_async(generate_performance_task.delay)(
        profile.fpl_team_id, gw.id, update.effective_chat.id, lang
    )




async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton("🇮🇷 فارسی", callback_data="lang_fa")],
    ])
    await update.message.reply_text("Choose your language / زبان خود را انتخاب کنید:", reply_markup=keyboard)


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.accounts.services import get_or_create_profile_for_chat

    query = update.callback_query
    lang = query.data.replace("lang_", "")

    def _save():
        profile = get_or_create_profile_for_chat(update.effective_chat.id)
        profile.preferred_language = lang
        profile.save(update_fields=["preferred_language"])

    await sync_to_async(_save)()
    await query.answer()
    confirm = "Language set to English ✅" if lang == "en" else "زبان به فارسی تغییر کرد ✅"
    await query.edit_message_text(confirm)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.fpl_data.models import Gameweek
    from apps.fpl_data.services import enforce_rtl
    from apps.accounts.models import FPLManagerProfile
    from apps.notifications.translations import t

    def _get_data():
        profile = FPLManagerProfile.objects.filter(
            telegram_chat_id=update.effective_chat.id
        ).first()
        gw = Gameweek.objects.filter(is_current=True).first() or Gameweek.objects.filter(is_next=True).first()
        return profile, gw

    profile, gw = await sync_to_async(_get_data)()

    lang = profile.preferred_language if profile else "en"

    if not profile or not profile.fpl_team_id:
        await update.message.reply_text(t("no_team_linked_status", lang))
        return

    team_id = profile.fpl_team_id
    free_transfers = profile.free_transfers
    reminders_status = t("reminders_enabled", lang) if profile.telegram_chat_id else t("reminders_disabled", lang)

    if gw:
        gw_name = gw.name
        deadline = gw.deadline_time.strftime("%Y-%m-%d %H:%M UTC")
    else:
        gw_name = t("unknown", lang)
        deadline = "N/A"

    message = (
        f"📋 *{t('status_title', lang)}*\n\n"
        f"🆔 {t('team_id_label', lang)}: {team_id}\n"
        f"📅 {t('gameweek_label', lang)}: {gw_name}\n"
        f"⏰ {t('deadline_label', lang)}: {deadline}\n"
        f"🔁 {t('free_transfers_label', lang)}: {free_transfers}\n"
        f"🔔 {t('reminders_label', lang)}: {reminders_status}"
    )
    message = enforce_rtl(message, lang)
    await update.message.reply_text(message, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from apps.accounts.models import FPLManagerProfile
    from apps.notifications.translations import t

    profile = await sync_to_async(
        lambda: FPLManagerProfile.objects.filter(telegram_chat_id=update.effective_chat.id).first()
    )()
    lang = profile.preferred_language if profile else "en"

    await update.message.reply_text(t("help_text", lang), parse_mode="Markdown")


async def set_bot_commands(application: Application):
    en_commands = [
        BotCommand("start", "Get started and enable reminders"),
        BotCommand("fixtures", "your players fixtures"),
        BotCommand("myteam", "your team"),
        BotCommand("setteamid", "Link your FPL team"),
        BotCommand("myteamid", "Show your linked team ID"),
        BotCommand("prediction", "Get this gameweek's AI suggestion"),
        BotCommand("differentials", "Get three differentials"),
        BotCommand("performance", "see which players are overperform or underperform"),
        BotCommand("status", "Show team status and deadline"),
        BotCommand("language", "Change language"),
        BotCommand("help", "Show all commands"),
    ]

    fa_commands = [
        BotCommand("start", "شروع و فعال‌سازی یادآوری‌ها"),
        BotCommand("fixtures", "بازی‌های بعدی بازیکنانت"),
        BotCommand("myteam", "تیم تو"),
        BotCommand("setteamid", "اتصال تیم فانتزی"),
        BotCommand("myteamid", "نمایش شناسه تیم متصل‌شده"),
        BotCommand("prediction", "پیشنهاد هوش مصنوعی این گیم‌ویک"),
        BotCommand("differentials", "سه بازیکن کم‌انتخاب"),
        BotCommand("performance", "بازیکنان بهتر یا بدتر از انتظار"),
        BotCommand("status", "وضعیت تیم و ددلاین"),
        BotCommand("language", "تغییر زبان"),
        BotCommand("help", "نمایش همه دستورات"),
    ]

    await application.bot.set_my_commands(en_commands)
    await application.bot.set_my_commands(en_commands, language_code="en")
    await application.bot.set_my_commands(fa_commands, language_code="fa")

def build_application():
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).post_init(set_bot_commands).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("fixtures", fixtures))
    application.add_handler(CommandHandler("myteam", myteam))
    application.add_handler(CommandHandler("prediction", prediction))
    application.add_handler(setteamid_conversation)
    application.add_handler(CommandHandler("myteamid", my_team_id))
    application.add_handler(CommandHandler("differentials", differentials))
    application.add_handler(CommandHandler("performance", performance))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CallbackQueryHandler(language_callback, pattern="^lang_"))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("help", help_command))
    return application


