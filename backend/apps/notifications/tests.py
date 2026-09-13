from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from apps.accounts.models import FPLManagerProfile
from apps.fpl_data.models import Gameweek
from .bot import format_status_message


class StatusMessageTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="fpl_manager")
        self.profile = FPLManagerProfile.objects.create(
            user=user,
            fpl_team_id=12345,
            telegram_chat_id=67890,
            free_transfers=2,
        )

    def test_includes_linked_team_transfer_and_reminder_details(self):
        gameweek = Gameweek.objects.create(
            fpl_id=1,
            name="Gameweek 1",
            deadline_time=timezone.now(),
            is_current=True,
        )

        message = format_status_message(self.profile, gameweek)

        self.assertIn("Team ID: `12345`", message)
        self.assertIn("Free transfers: 2", message)
        self.assertIn("Deadline reminder: Enabled", message)
        self.assertIn("Gameweek 1", message)
        self.assertIn("Deadline:", message)

    def test_reports_when_no_gameweek_is_available(self):
        message = format_status_message(self.profile, None)

        self.assertIn("No current or upcoming gameweek found.", message)
