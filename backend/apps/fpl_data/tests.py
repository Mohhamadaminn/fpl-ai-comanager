from django.test import SimpleTestCase

from .compare_engine import _recent_minutes_risk


class RepeatedShortAppearancesTests(SimpleTestCase):
    def test_two_of_latest_three_short_appearances_trigger_moderate_risk(self):
        self.assertEqual(_recent_minutes_risk([40, 75, 55], 75, 40), "rotation")
        self.assertEqual(_recent_minutes_risk([75, 40, 55], 75, 75), "rotation")
        self.assertEqual(_recent_minutes_risk([59, 60, 59], 75, 75), "rotation")

    def test_consecutive_severe_short_appearances_trigger_strong_risk(self):
        self.assertEqual(_recent_minutes_risk([45, 40, 75], 75, 75), "severe")

    def test_two_low_gameweeks_trigger_strong_risk(self):
        self.assertEqual(_recent_minutes_risk([75, 80, 90], 45, 45), "severe")

    def test_only_latest_three_appearances_are_considered(self):
        self.assertIsNone(_recent_minutes_risk([90, 85, 90, 30, 20], 75, 75))

    def test_consistently_high_minutes_do_not_trigger_risk(self):
        self.assertIsNone(_recent_minutes_risk([90, 85, 60], 75, 75))
        self.assertIsNone(_recent_minutes_risk([90, 85, 70], 75, 75))

    def test_fewer_than_three_appearances_need_two_short_games(self):
        self.assertEqual(_recent_minutes_risk([40, 55], 75, 75), "rotation")
        self.assertEqual(_recent_minutes_risk([40], 30, 75), "single")
        self.assertIsNone(_recent_minutes_risk([], None, None))

    def test_one_low_latest_game_gets_the_lighter_risk(self):
        self.assertEqual(_recent_minutes_risk([75, 80, 90], 35, 75), "single")
