import math
from datetime import datetime, timezone
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from apps.fpl_data import expected_points as xp
from apps.fpl_data.models import Fixture, Gameweek, Player, PlayerGameweekStat, Team


# ---------------------------------------------------------------------------
# helpers: a tiny league without a database
# ---------------------------------------------------------------------------
def appearance(gw, minutes=90, xg=0.0, xa=0.0, saves=0, bonus=0, yc=0, rc=0, dc=0, points=2):
    return {"gw": gw, "minutes": minutes, "xg": xg, "xa": xa, "saves": saves, "bonus": bonus,
            "yc": yc, "rc": rc, "dc": dc, "points": points}


def match(gw, team, home, gf, ga, xgf=None, xga=None):
    return {"gw": gw, "team": team, "home": home, "gf": gf, "ga": ga, "xgf": xgf, "xga": xga}


def league_matches(gws=range(1, 9)):
    """Teams 1-4. Team 1 scores a lot and concedes little, team 4 the opposite, 2 and 3 are average."""
    out = []
    for gw in gws:
        for home, away, hg, ag in ((1, 4, 3, 0), (2, 3, 1, 1)):
            out.append(match(gw, home, True, hg, ag))
            out.append(match(gw, away, False, ag, hg))
    return out


def tiny_dataset(target=9):
    rows = {
        1: [appearance(gw, xg=0.6, xa=0.2, points=6) for gw in range(1, target)],    # FWD, team 1
        2: [appearance(gw, xg=0.15, xa=0.05, points=3) for gw in range(1, target)],  # FWD, team 2
        3: [appearance(gw, dc=11, points=6) for gw in range(1, target)],             # DEF, team 1
        4: [appearance(gw, dc=11, points=2) for gw in range(1, target)],             # DEF, team 4
        5: [appearance(gw, saves=3, points=4) for gw in range(1, target)],           # GKP, team 2
        6: [],                                                                       # FWD, never played, team 3
    }
    players = {1: ("FWD", 1), 2: ("FWD", 2), 3: ("DEF", 1), 4: ("DEF", 4), 5: ("GKP", 2), 6: ("FWD", 3)}
    schedule = {
        target: {
            1: [{"team": 1, "opp": 4, "home": True, "fdr": 2}],
            4: [{"team": 4, "opp": 1, "home": False, "fdr": 5}],
            2: [{"team": 2, "opp": 3, "home": True, "fdr": 3}],
            3: [{"team": 3, "opp": 2, "home": False, "fdr": 3}],
        }
    }
    names = {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD"}
    return xp.Dataset(rows=rows, players=players, matches=league_matches(range(1, target)),
                      schedule=schedule, team_names=names)


# ---------------------------------------------------------------------------
# distributions
# ---------------------------------------------------------------------------
class DistributionTests(SimpleTestCase):
    def test_every_component_is_a_probability_distribution(self):
        for pmf in (
            xp._count_pmf(0.7, 4, xp.GOAL_KMAX),
            xp._defence_pmf("DEF", 1.3, True),
            xp._defence_pmf("MID", 1.3, False),
            xp._saves_pmf(3.4),
            xp._bonus_pmf(1.1),
            xp._bernoulli(0.3, -1),
        ):
            self.assertAlmostEqual(sum(pmf.values()), 1.0, places=9)

    def test_clean_sheet_probability_is_poisson_zero(self):
        pmf = xp._defence_pmf("DEF", 1.0, True)
        self.assertAlmostEqual(pmf[4], math.exp(-1.0), places=9)     # k = 0 -> 4 points
        self.assertAlmostEqual(pmf[0], math.exp(-1.0), places=9)     # k = 1 -> 0, k = 2 -> -1 ... see below
        self.assertIn(-1, pmf)

    def test_cameo_cannot_earn_a_clean_sheet_and_forwards_have_no_defence_part(self):
        self.assertNotIn(4, xp._defence_pmf("DEF", 0.5, False))
        self.assertEqual(xp._defence_pmf("MID", 0.0, True), {1: 1.0})

    def test_goals_conceded_penalty_only_hits_gkp_and_def(self):
        self.assertTrue(any(points < 0 for points in xp._defence_pmf("GKP", 2.0, True)))
        self.assertFalse(any(points < 0 for points in xp._defence_pmf("MID", 2.0, True)))

    def test_goal_points_mean_matches_expected_goals(self):
        self.assertAlmostEqual(xp.pmf_mean(xp._count_pmf(0.5, 4, xp.GOAL_KMAX)), 2.0, places=3)

    def test_bonus_keeps_its_mean(self):
        self.assertAlmostEqual(xp.pmf_mean(xp._bonus_pmf(1.2)), 1.2, places=9)

    def test_convolution_adds_independent_outcomes(self):
        self.assertEqual(xp._convolve({0: 0.5, 1: 0.5}, {0: 0.5, 2: 0.5}), {0: 0.25, 1: 0.25, 2: 0.25, 3: 0.25})

    def test_quantiles(self):
        pmf = {0: 0.05, 1: 0.10, 5: 0.70, 9: 0.15}
        self.assertEqual(xp.pmf_quantile(pmf, 0.10), 1)
        self.assertEqual(xp.pmf_quantile(pmf, 0.90), 9)
        self.assertEqual(xp.pmf_quantile({4: 1.0}, 0.10), 4)


# ---------------------------------------------------------------------------
# team model
# ---------------------------------------------------------------------------
class TeamModelTests(SimpleTestCase):
    def test_needs_finished_matches(self):
        with self.assertRaises(ValueError):
            xp.build_team_model([])

    def test_strong_attack_and_defence_rate_above_average(self):
        model = xp.build_team_model(league_matches())
        self.assertGreater(model.att[1], 1.0)
        self.assertLess(model.deff[1], 1.0)
        self.assertLess(model.att[4], 1.0)
        self.assertGreater(model.deff[4], 1.0)

    def test_weak_defence_helps_the_opposing_attacker_and_hurts_its_own_clean_sheet_chance(self):
        model = xp.build_team_model(league_matches())
        names = {1: "AAA", 4: "DDD"}
        vs_leaky = xp.match_context(model, {"team": 1, "opp": 4, "home": True, "fdr": 2}, names)
        vs_solid = xp.match_context(model, {"team": 4, "opp": 1, "home": False, "fdr": 5}, names)
        self.assertGreater(vs_leaky.att_mult, 1.0)
        self.assertLess(vs_solid.att_mult, 1.0)
        self.assertLess(vs_leaky.lam_against, vs_solid.lam_against)

    def test_home_matches_score_more_in_a_league_with_home_advantage(self):
        model = xp.build_team_model(league_matches())
        self.assertGreater(model.mu_home, model.mu_away)

    def test_xg_is_used_when_present_but_results_dominate(self):
        plain = xp.build_team_model(league_matches())
        # team 2 creates a lot more than it scores; everyone else's xG equals their goals
        with_xg = xp.build_team_model(
            [dict(m, xgf=m["gf"] + (1.0 if m["team"] == 2 else 0.0), xga=m["ga"]) for m in league_matches()]
        )
        self.assertGreater(with_xg.att[2], plain.att[2])
        self.assertLess(with_xg.att[2] / plain.att[2], 1.5)

    def test_playing_more_home_games_does_not_inflate_a_rating(self):
        # team 1 always at home, team 2 always away, both score and concede their venue's average
        matches = []
        for gw in range(1, 9):
            matches += [match(gw, 1, True, 2, 1), match(gw, 2, False, 1, 2)]
            matches += [match(gw, 3, False, 1, 2), match(gw, 4, True, 2, 1)]
        model = xp.build_team_model(matches)
        for team in (1, 2, 3, 4):
            self.assertAlmostEqual(model.att[team], 1.0, places=6)
            self.assertAlmostEqual(model.deff[team], 1.0, places=6)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------
class ProfileTests(SimpleTestCase):
    POP = {"FWD": {"xg": 0.3, "xa": 0.1, "saves": 0.0, "yc": 0.1, "rc": 0.005, "bonus": 0.4, "dc": 0.05}}
    TEAM_GWS = list(range(1, 9))

    def profile(self, apps):
        return xp.build_profile("FWD", apps, self.TEAM_GWS, self.POP)

    def test_regular_starter(self):
        profile = self.profile([appearance(gw, xg=0.4) for gw in range(1, 9)])
        self.assertGreater(profile.p_sel, 0.9)
        self.assertEqual(profile.f60, 1.0)
        self.assertFalse(profile.low_data)

    def test_rotation_lowers_selection_chance(self):
        used = {1, 2, 3, 5, 8}
        profile = self.profile([appearance(gw) for gw in sorted(used)])
        regular = self.profile([appearance(gw) for gw in range(1, 9)])
        self.assertLess(profile.p_sel, regular.p_sel)

    def test_games_missed_before_a_return_are_not_held_against_him(self):
        # out for gameweeks 1-4, then back and used in 5-8
        returned = self.profile([appearance(gw) for gw in range(5, 9)])
        self.assertGreater(returned.p_sel, 0.8)

    def test_cameo_player(self):
        profile = self.profile([appearance(gw, minutes=20) for gw in range(1, 9)])
        self.assertEqual(profile.f60, 0.0)
        self.assertAlmostEqual(profile.m_sub, 20.0)

    def test_player_without_appearances_never_plays(self):
        profile = self.profile([])
        self.assertEqual(profile.p_sel, 0.0)
        self.assertTrue(profile.low_data)

    def test_rate_is_pulled_towards_the_position_average_for_small_samples(self):
        small = self.profile([appearance(1, minutes=90, xg=2.0)])
        big = self.profile([appearance(gw, minutes=90, xg=2.0) for gw in range(1, 9)])
        self.assertLess(small.xg90, big.xg90)
        self.assertGreater(small.xg90, self.POP["FWD"]["xg"])

    def test_not_synced_stats_fall_back_to_the_position_average(self):
        apps = [dict(appearance(gw), saves=None, bonus=None, yc=None, rc=None) for gw in range(1, 9)]
        profile = self.profile(apps)
        self.assertAlmostEqual(profile.yc90, self.POP["FWD"]["yc"])
        self.assertAlmostEqual(profile.bonus_full, self.POP["FWD"]["bonus"])


class PreviousSeasonPriorTests(SimpleTestCase):
    POP = ProfileTests.POP
    TEAM_GWS = [1, 2]

    def test_without_a_prior_nothing_changes(self):
        apps = [appearance(1, xg=0.1), appearance(2, xg=0.1)]
        base = xp.build_profile("FWD", apps, self.TEAM_GWS, self.POP)
        same = xp.build_profile("FWD", apps, self.TEAM_GWS, self.POP, prior=None)
        self.assertEqual(base, same)

    def test_a_strong_previous_season_lifts_a_tiny_sample_but_this_season_still_wins_with_time(self):
        prior = {"minutes": 2500, "xg90": 0.8, "xa90": 0.2, "saves90": 0.0, "yc90": 0.1, "rc90": 0.0}
        early = [appearance(1, xg=0.1)]
        without = xp.build_profile("FWD", early, [1], self.POP)
        with_prior = xp.build_profile("FWD", early, [1], self.POP, prior=prior)
        self.assertGreater(with_prior.xg90, without.xg90)

        late = [appearance(gw, xg=0.1) for gw in range(1, 39)]
        gws = list(range(1, 39))
        late_with = xp.build_profile("FWD", late, gws, self.POP, prior=prior)
        late_without = xp.build_profile("FWD", late, gws, self.POP)
        self.assertLess(late_with.xg90 - late_without.xg90, with_prior.xg90 - without.xg90)

    def test_prior_flows_through_the_prediction(self):
        dataset = tiny_dataset()
        base = xp.predict_gameweek(dataset, 9, [2])[2]["xp"]
        dataset.priors = {2: {"minutes": 3000, "xg90": 0.9, "xa90": 0.4, "saves90": 0.0, "yc90": 0.1, "rc90": 0.0}}
        self.assertGreater(xp.predict_gameweek(dataset, 9, [2])[2]["xp"], base)


# ---------------------------------------------------------------------------
# whole prediction
# ---------------------------------------------------------------------------
class PredictionTests(SimpleTestCase):
    def setUp(self):
        self.dataset = tiny_dataset()
        self.out = xp.predict_gameweek(self.dataset, 9, [1, 2, 3, 4, 5, 6], with_pmf=True)

    def test_each_distribution_sums_to_one_and_mean_is_xp(self):
        for result in self.out.values():
            self.assertAlmostEqual(sum(result["pmf"].values()), 1.0, places=6)
            self.assertAlmostEqual(xp.pmf_mean(result["pmf"]), result["xp"], places=6)

    def test_range_brackets_the_mean(self):
        for result in self.out.values():
            self.assertLessEqual(result["low"], result["xp"] + 1e-9)
            self.assertGreaterEqual(result["high"], result["xp"] - 1e-9)

    def test_range_is_never_narrower_than_the_percentiles(self):
        pmf = {0: 0.09, 8: 0.91}     # mean 7.28, P10 = 8: the mean sits under the percentile
        summary = xp.summarize(pmf)
        self.assertEqual((summary["low"], summary["high"]), (7, 8))

    def test_better_attacker_with_better_fixture_scores_more(self):
        self.assertGreater(self.out[1]["xp"], self.out[2]["xp"])

    def test_defender_with_a_clean_sheet_favourite_scores_more(self):
        self.assertGreater(self.out[3]["xp"], self.out[4]["xp"])

    def test_player_who_never_played_gets_zero(self):
        self.assertEqual(self.out[6]["xp"], 0.0)
        self.assertEqual(self.out[6]["p_play"], 0.0)

    def test_fixture_info_is_reported(self):
        self.assertEqual(self.out[1]["fixtures"], [{"opponent": "DDD", "home": True, "fdr": 2}])

    def test_injured_player_is_zero_and_doubtful_halves_the_expectation(self):
        fit = self.out[1]["xp"]
        out = xp.predict_gameweek(self.dataset, 9, [1], availability={1: 0.0})
        self.assertEqual(out[1]["xp"], 0.0)
        half = xp.predict_gameweek(self.dataset, 9, [1], availability={1: 0.5})
        self.assertAlmostEqual(half[1]["xp"], fit / 2, places=6)

    def test_blank_gameweek_is_zero_points_and_no_range(self):
        self.dataset.schedule[9].pop(2)
        out = xp.predict_gameweek(self.dataset, 9, [2])
        self.assertEqual((out[2]["xp"], out[2]["low"], out[2]["high"], out[2]["fixtures"]), (0.0, 0, 0, []))

    def test_double_gameweek_roughly_doubles_expectation_and_widens_range(self):
        single = xp.predict_gameweek(self.dataset, 9, [1])[1]
        self.dataset.schedule[9][1].append({"team": 1, "opp": 4, "home": True, "fdr": 2})   # same match twice
        double = xp.predict_gameweek(self.dataset, 9, [1])[1]
        self.assertEqual(len(double["fixtures"]), 2)
        self.assertAlmostEqual(double["xp"], single["xp"] * 2, places=6)
        self.assertGreater(double["high"] - double["low"], single["high"] - single["low"])

    def test_only_earlier_gameweeks_are_used(self):
        """Adding a huge game in the target gameweek itself must not change the prediction."""
        before = xp.predict_gameweek(self.dataset, 9, [1])[1]["xp"]
        self.dataset.rows[1].append(appearance(9, xg=5.0, points=30))
        self.dataset.matches.append(match(9, 1, True, 9, 0))
        self.assertEqual(xp.predict_gameweek(self.dataset, 9, [1])[1]["xp"], before)

    def test_goalkeeper_gets_points_from_saves(self):
        base = xp.predict_gameweek(self.dataset, 9, [5])[5]["xp"]
        self.dataset.rows[5] = [appearance(gw, saves=0, points=2) for gw in range(1, 9)]
        self.assertLess(xp.predict_gameweek(self.dataset, 9, [5])[5]["xp"], base)

    def test_needs_finished_matches(self):
        self.dataset.matches = []
        with self.assertRaises(ValueError):
            xp.predict_gameweek(self.dataset, 9, [1])


# ---------------------------------------------------------------------------
# captain and formation
# ---------------------------------------------------------------------------
def entry(pid, xp_value, position="MID", status="a", p_play=0.95, fixtures=None, fdr=3, prime=0.0):
    if fixtures is None:
        fixtures = [{"opponent": "BOU", "home": True, "fdr": fdr}]
    return {"id": pid, "name": f"P{pid}", "xp": xp_value, "position": position, "status": status,
            "p_play": p_play, "fixtures": fixtures, "low": 0, "high": 10, "prime": prime}


class CaptainTests(SimpleTestCase):
    def test_highest_expected_score_is_captain_and_next_is_vice(self):
        xi = [entry(1, 5.0), entry(2, 8.0), entry(3, 6.5)]
        captain, vice = xp.pick_captains(xi)
        self.assertEqual((captain["id"], vice["id"]), (2, 3))

    def test_never_an_injured_doubtful_suspended_or_blank_player(self):
        xi = [entry(1, 9.0, status="i"), entry(2, 9.0, status="d"), entry(3, 9.0, status="s"),
              entry(4, 9.0, fixtures=[]), entry(5, 4.0)]
        captain, vice = xp.pick_captains(xi)
        self.assertEqual(captain["id"], 5)
        self.assertIsNone(vice)

    def test_nobody_eligible(self):
        self.assertEqual(xp.pick_captains([entry(1, 9.0, status="i")]), (None, None))

    def test_practically_level_players_are_split_by_certainty_to_play(self):
        xi = [entry(1, 7.0, p_play=0.80), entry(2, 6.8, p_play=0.97)]
        captain, _ = xp.pick_captains(xi)
        self.assertEqual(captain["id"], 2)
        xi = [entry(1, 7.0, p_play=0.80), entry(2, 6.0, p_play=0.97)]      # clearly worse: stays second
        self.assertEqual(xp.pick_captains(xi)[0]["id"], 1)

    def test_reasons(self):
        xi = [entry(1, 7.0, fdr=2), entry(2, 4.0)]
        reasons = xp.captain_reasons(xi[0], xi)
        self.assertEqual(reasons, ["Home vs BOU", "Easy fixture", "Almost certain to play",
                                   "Highest predicted score in your XI"])
        double = entry(3, 5.0, fixtures=[{"opponent": "CHE", "home": False, "fdr": 4},
                                         {"opponent": "LIV", "home": True, "fdr": 4}])
        self.assertEqual(xp.captain_reasons(double, xi + [double])[:3], ["Double gameweek", "Away at CHE", "Home vs LIV"])

    def test_prime_player_beats_a_cheaper_player_with_a_slightly_better_number(self):
        # the reported case: a premium forward against a strong defence vs a cheap player with a soft fixture
        haaland = entry(1, 5.6, "FWD", prime=0.95, fixtures=[{"opponent": "LIV", "home": True, "fdr": 4}])
        cheap = entry(2, 6.5, "MID", prime=0.3, fixtures=[{"opponent": "BOU", "home": True, "fdr": 2}])
        captain, vice = xp.pick_captains([cheap, haaland])
        self.assertEqual((captain["id"], vice["id"]), (1, 2))

    def test_prime_player_is_still_passed_over_when_his_number_is_clearly_lower(self):
        haaland = entry(1, 3.5, "FWD", prime=0.95)
        cheap = entry(2, 6.5, "MID", prime=0.3)
        self.assertEqual(xp.pick_captains([haaland, cheap])[0]["id"], 2)

    def test_prime_player_who_is_unfit_or_unlikely_to_play_is_not_captain(self):
        cheap = entry(2, 5.0, "MID", prime=0.1)
        for sick in (entry(1, 9.0, "FWD", prime=1.0, status="d"), entry(1, 9.0, "FWD", prime=1.0, p_play=0.5)):
            self.assertEqual(xp.pick_captains([sick, cheap])[0]["id"], 2)

    def test_defenders_and_keepers_are_only_a_last_resort(self):
        defender = entry(1, 9.0, "DEF", prime=1.0)
        attacker = entry(2, 3.0, "FWD")
        self.assertEqual(xp.pick_captains([defender, attacker])[0]["id"], 2)
        self.assertEqual(xp.pick_captains([defender])[0]["id"], 1)

    def test_prime_index(self):
        pool = [(5.0 + i * 0.1, float(i)) for i in range(100)]    # price and ownership both rise together
        top = xp.prime_index(14.9, 99.0, pool)
        middle = xp.prime_index(10.0, 50.0, pool)
        bottom = xp.prime_index(5.0, 0.0, pool)
        self.assertGreater(top, 0.95)
        self.assertLess(middle, 0.3)
        self.assertLess(bottom, 0.01)
        self.assertEqual(xp.prime_index(10.0, 10.0, []), 0.0)
        # an expensive but little-owned player is not fully prime
        self.assertLess(xp.prime_index(14.9, 0.0, pool), top)

    def test_premium_player_gets_a_reason_chip(self):
        star = entry(1, 6.0, "FWD", prime=0.9)
        self.assertIn("Premium player", xp.captain_reasons(star, [star]))
        self.assertNotIn("Premium player", xp.captain_reasons(entry(2, 6.0, prime=0.2), [star]))

    def test_doubling_a_distribution(self):
        self.assertEqual(xp._double({0: 0.5, 3: 0.5}), {0: 0.5, 6: 0.5})

    def test_formation(self):
        xi = [entry(i, 1, "GKP") for i in (1,)] + [entry(i, 1, "DEF") for i in (2, 3, 4)] \
            + [entry(i, 1, "MID") for i in (5, 6, 7, 8, 9)] + [entry(i, 1, "FWD") for i in (10, 11)]
        self.assertEqual(xp._formation(xi), "3-5-2")
        self.assertIsNone(xp._formation(xi[:5]))


# ---------------------------------------------------------------------------
# database loader and the squad entry point
# ---------------------------------------------------------------------------
class LoadDatasetAndSquadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teams = [Team.objects.create(fpl_id=i, name=f"Team {i}", short_name=f"T{i}") for i in range(1, 5)]
        cls.gws = [
            Gameweek.objects.create(
                fpl_id=i, name=f"Gameweek {i}", finished=i <= 8, is_next=(i == 9),
                deadline_time=datetime(2026, 9, i, 12, tzinfo=timezone.utc),
            )
            for i in range(1, 10)
        ]
        fixture_id = 0
        for gw in cls.gws:
            for home, away, hg, ag in ((1, 4, 3, 0), (2, 3, 1, 1)):
                fixture_id += 1
                done = gw.fpl_id <= 8
                Fixture.objects.create(
                    fpl_id=fixture_id, gameweek=gw, team_home=cls.teams[home - 1], team_away=cls.teams[away - 1],
                    difficulty_home=2, difficulty_away=4, finished=done,
                    team_home_score=hg if done else None, team_away_score=ag if done else None,
                )
        cls.players = []
        spec = [(1, "FWD", 1), (2, "MID", 2), (3, "DEF", 1), (4, "GKP", 2), (5, "FWD", 3), (6, "DEF", 4)]
        for fpl_id, position, team_no in spec:
            cls.players.append(Player.objects.create(
                fpl_id=fpl_id, code=1000 + fpl_id, first_name="P", second_name=str(fpl_id), web_name=f"P{fpl_id}",
                team=cls.teams[team_no - 1], position=position, price=Decimal("5.5"),
                official_ep_next=Decimal("4.2") if fpl_id == 1 else None,
            ))
        for player in cls.players:
            for gw in cls.gws[:8]:
                PlayerGameweekStat.objects.create(
                    player=player, gameweek=gw, minutes=90, expected_goals=Decimal("0.4"),
                    expected_assists=Decimal("0.1"), points=5, is_final=True, defensive_contribution=11,
                    # stats synced before the new fields existed stay NULL
                    saves=None if gw.fpl_id < 5 else 2, bonus=None if gw.fpl_id < 5 else 1,
                    yellow_cards=None, red_cards=None,
                )

    def test_load_dataset_reads_rows_matches_and_schedule(self):
        dataset = xp.load_dataset(9)
        self.assertEqual(len(dataset.rows), 6)
        self.assertEqual(len(dataset.rows[self.players[0].id]), 8)
        self.assertEqual(len(dataset.matches), 8 * 4)
        self.assertEqual(set(dataset.schedule[9]), {1, 2, 3, 4})
        self.assertIsNone(dataset.rows[self.players[0].id][0]["saves"])
        self.assertEqual(dataset.rows[self.players[0].id][-1]["saves"], 2)
        self.assertEqual(dataset.matches[0]["xgf"] is not None, True)

    def test_unfinished_fixtures_do_not_count_as_results(self):
        dataset = xp.load_dataset(10)
        self.assertTrue(all(m["gw"] <= 8 for m in dataset.matches))

    def test_end_to_end_prediction_on_loaded_data(self):
        dataset = xp.load_dataset(9)
        out = xp.predict_gameweek(dataset, 9, [p.id for p in self.players])
        self.assertEqual(len(out), 6)
        self.assertTrue(all(r["xp"] > 0 for r in out.values()))

    def test_squad_entry_point(self):
        slots = {p.fpl_id: i + 1 for i, p in enumerate(self.players)}
        slots[6] = 12   # one benched player
        with mock.patch("apps.fpl_data.squad_insights._load_squad", return_value=(slots, "Gameweek 8", 1, 0.5)):
            result = xp.get_squad_expected_points("https://fantasy.premierleague.com/entry/123456/event/8")
        self.assertEqual(result["gameweek"]["id"], 9)
        self.assertEqual(result["squad_gameweek"], "Gameweek 8")
        self.assertEqual(result["pending_transfers"], 1)
        self.assertEqual([e["slot"] for e in result["starting_xi"]], [1, 2, 3, 4, 5])
        self.assertEqual([e["slot"] for e in result["bench"]], [12])
        self.assertEqual(result["data_through_gameweek"], 8)
        first = result["starting_xi"][0]
        self.assertNotIn("official_xp", first)
        self.assertTrue(first["photo_url"].endswith("1001.png"))
        self.assertLessEqual(first["low"], first["high"])
        total = result["xi_total"]
        captain = result["recommended_captain"]["player"]
        self.assertTrue(total["includes_captain"])
        # the captain counts twice in the total
        self.assertAlmostEqual(
            total["xp"], sum(e["xp"] for e in result["starting_xi"]) + captain["xp"], delta=0.3
        )
        self.assertLessEqual(total["low"], total["xp"])
        self.assertGreaterEqual(total["high"], total["xp"])

    def test_captain_is_in_the_xi_and_flagged_once(self):
        slots = {p.fpl_id: i + 1 for i, p in enumerate(self.players)}
        slots[6] = 12
        with mock.patch("apps.fpl_data.squad_insights._load_squad", return_value=(slots, "Gameweek 8", 0, 0.5)):
            result = xp.get_squad_expected_points("123456")
        block = result["recommended_captain"]
        flagged = [e for e in result["starting_xi"] if e["is_captain"]]
        self.assertEqual([e["id"] for e in flagged], [block["player"]["id"]])
        self.assertEqual(sum(e["is_vice_captain"] for e in result["starting_xi"]), 1)
        self.assertFalse(any(e["is_captain"] or e["is_vice_captain"] for e in result["bench"]))
        self.assertAlmostEqual(block["points_as_captain"], block["player"]["xp"] * 2, delta=0.01)
        self.assertTrue(block["reasons"])
        self.assertEqual(result["formation"], None)    # only 5 starters in this tiny squad

    def test_backtest_command_reports_error_and_coverage(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("backtest_xp", "--from-gw", "4", "--to-gw", "8", stdout=out)
        text = out.getvalue()
        self.assertIn("MAE", text)
        self.assertIn("in P10-P90", text)
        self.assertIn("all", text)          # the summary line
        for gw in range(4, 9):
            self.assertRegex(text, rf"(?m)^\s*{gw}\s+\d+")

    def test_bad_team_input(self):
        with self.assertRaises(ValueError):
            xp.get_squad_expected_points("not a team")


# ---------------------------------------------------------------------------
# endpoint
# ---------------------------------------------------------------------------
@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class SquadXpEndpointTests(SimpleTestCase):
    URL = "/api/squad-xp/"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        rates = mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"llm_burst": "2/min", "llm_daily": "100/day"})
        rates.start()
        self.addCleanup(rates.stop)
        self.client = APIClient()

    def post(self, body):
        return self.client.post(self.URL, body, format="json", HTTP_X_FORWARDED_FOR="203.0.113.9")

    def test_returns_the_result(self):
        with mock.patch("apps.fpl_data.views.get_squad_expected_points", return_value={"ok": True}) as fn:
            response = self.post({"team": "123456"})
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        fn.assert_called_once_with("123456")

    def test_value_errors_become_400_with_a_message(self):
        with mock.patch("apps.fpl_data.views.get_squad_expected_points", side_effect=ValueError("Team not found.")):
            response = self.post({"team": "1"})
        self.assertEqual((response.status_code, response.json()), (400, {"error": "Team not found."}))

    def test_team_is_required(self):
        self.assertEqual(self.post({}).status_code, 400)

    def test_is_limited_per_ip_like_the_other_squad_endpoint(self):
        with mock.patch("apps.fpl_data.views.get_squad_expected_points", return_value={"ok": True}):
            statuses = [self.post({"team": "1"}).status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])
