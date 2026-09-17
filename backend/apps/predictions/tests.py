import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.fpl_data.models import Gameweek, Player, PlayerGameweekStat, Team
from apps.predictions.services import build_player_context, generate_ai_prediction


class GenerateAIPredictionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tester")
        self.team = Team.objects.create(fpl_id=1, name="Arsenal", short_name="ARS")
        self.gameweek = Gameweek.objects.create(
            fpl_id=1,
            name="Gameweek 1",
            deadline_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        self.squad_player = Player.objects.create(
            fpl_id=1,
            first_name="Squad",
            second_name="Player",
            web_name="Squad Player",
            team=self.team,
            position=Player.Position.MIDFIELDER,
            price=7.0,
        )
        self.outside_squad_player = Player.objects.create(
            fpl_id=2,
            first_name="Outside",
            second_name="Player",
            web_name="Outside Player",
            team=self.team,
            position=Player.Position.MIDFIELDER,
            price=7.0,
        )

    @patch("apps.predictions.services.client")
    @patch("apps.predictions.services.build_player_context", return_value=[])
    def test_removes_captain_when_validation_finds_them_outside_squad(
        self, _build_player_context, mock_client
    ):
        response_content = json.dumps(
            {
                "captain_id": self.outside_squad_player.id,
                "captain_alternatives_considered": [
                    self.squad_player.id,
                    self.outside_squad_player.id,
                ],
                "transfer_in_id": None,
                "transfer_out_id": None,
                "reasoning": "A test prediction.",
            }
        )
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content=response_content))]
        mock_client.chat.completions.create.return_value = mock_response

        prediction = generate_ai_prediction(
            self.gameweek,
            squad=[self.squad_player],
            manager_state={
                "squad": [self.squad_player],
                "bank": 0,
                "free_transfers": 1,
            },
            user=self.user,
        )

        prediction.refresh_from_db()
        self.assertIsNone(prediction.suggested_captain)
        self.assertEqual(
            prediction.data_snapshot["validation_errors"],
            ["Suggested captain Outside Player is not in the current squad."],
        )


class BuildPlayerContextTests(TestCase):
    def test_fetches_player_stats_in_bulk(self):
        team = Team.objects.create(fpl_id=1, name="Arsenal", short_name="ARS")
        previous_gameweek = Gameweek.objects.create(
            fpl_id=1,
            name="Gameweek 1",
            deadline_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
            finished=True,
        )
        current_gameweek = Gameweek.objects.create(
            fpl_id=2,
            name="Gameweek 2",
            deadline_time=datetime(2026, 8, 8, tzinfo=timezone.utc),
        )
        players = [
            Player.objects.create(
                fpl_id=index,
                first_name=f"Player {index}",
                second_name="Test",
                web_name=f"Player {index}",
                team=team,
                position=Player.Position.MIDFIELDER,
                price=7.0,
            )
            for index in range(1, 4)
        ]
        PlayerGameweekStat.objects.bulk_create(
            [
                PlayerGameweekStat(
                    player=player,
                    gameweek=previous_gameweek,
                    minutes=90,
                    goals_scored=1,
                    is_final=True,
                )
                for player in players
            ]
        )

        with CaptureQueriesContext(connection) as queries:
            context = build_player_context(current_gameweek)

        self.assertEqual(len(queries), 3)
        self.assertCountEqual(
            [player["id"] for player in context], [player.id for player in players]
        )
