from django.core.management.base import BaseCommand
from apps.notifications.bot import build_application


class Command(BaseCommand):
    help = "Runs the Telegram bot (polling mode)"

    def handle(self, *args, **options):
        application = build_application()
        self.stdout.write("Bot started, polling...")
        application.run_polling()