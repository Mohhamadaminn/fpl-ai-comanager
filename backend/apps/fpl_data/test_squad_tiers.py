from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.fpl_data import squad_insights as si


def ev(name="X", price=7.0, position="MID", mpg=80.0, underlying=40.0, actual=40.0, availability=1.0,
       engine_score=55.0, form_score=55.0, recent_ppg=4.0, earlier_ppg=4.0, games=5, fixture_score=50.0,
       earlier=True):
    """A hand-made evaluation with just the fields the classification reads."""
    player = SimpleNamespace(id=hash(name) % 10000, web_name=name, price=price, position=position, news="",
                             team=SimpleNamespace(short_name="TST"))
    return {
        "player": player,
        "totals": {"xgi": 2.0, "xgc": 3.0, "goals": 1, "assists": 1, "cs": 1, "points": 12},
        "mpg": mpg,
        "actual": actual,
        "underlying": underlying,
        "gap": round(underlying - actual, 1),
        "availability": {"multiplier": availability},
        "fixture": {"weighted_score": fixture_score, "next_score": 50.0, "fixtures": [], "hard": False},
        "engine": {
            "score": engine_score,
            "breakdown": {
                "form": {"score": form_score, "weight": 0.5, "games": games, "recent_points": int(recent_ppg * games),
                         "recent_points_per_game": recent_ppg},
                "fixtures_fdr": {"score": fixture_score, "weight": 0.2},
            },
        },
        "earlier": {"games": 6, "ppg": earlier_ppg} if earlier else None,
    }


def fine_player(name):
    """Average in every respect: no card at any tier."""
    return ev(name, underlying=45, actual=50, engine_score=55)


class ChanceTierTests(SimpleTestCase):
    def test_strict_player_is_a_strong_case(self):
        star = ev("Star", underlying=70, actual=30)              # gap 40
        chance, move_on = si._classify([star, fine_player("A")])
        self.assertEqual((chance[0], chance[1]), (star, "strong"))

    def test_medium_rules_find_a_smaller_gap(self):
        mid = ev("Mid", mpg=55, underlying=52, actual=38)        # gap 14: fails strict (gap 20 / mpg 60)
        self.assertFalse(si._is_chance(mid))
        self.assertTrue(si._is_chance(mid, "moderate"))
        chance, _ = si._classify([mid, fine_player("A")])
        self.assertEqual((chance[0], chance[1]), (mid, "moderate"))

    def test_strict_wins_over_medium_when_both_exist(self):
        strict, medium = ev("S", underlying=70, actual=30), ev("M", mpg=55, underlying=52, actual=38)
        self.assertEqual(si._classify([medium, strict])[0][0], strict)

    def test_default_level_is_the_original_strict_one(self):
        # the backtest calls _is_chance(ev) and must keep seeing the old rule
        self.assertFalse(si._is_chance(ev(mpg=55, underlying=52, actual=38)))


class MoveOnTierTests(SimpleTestCase):
    def test_deep_slump_is_strong(self):
        slump = ev("Slump", form_score=35, recent_ppg=1.5, earlier_ppg=5.0, engine_score=40)
        _, move_on = si._classify([slump, fine_player("A")])
        self.assertEqual((move_on[0], move_on[1], move_on[2]), (slump, "slump", "strong"))

    def test_mild_slump_is_moderate(self):
        # form 48 and a 0.7 drop: not a slump by the strict rule (<=45, >=1.0), but by the medium rule
        mild = ev("Mild", form_score=48, recent_ppg=3.3, earlier_ppg=4.0, engine_score=52)
        self.assertIsNone(si._slump_info(mild))
        self.assertIsNotNone(si._slump_info(mild, "moderate"))
        _, move_on = si._classify([mild, fine_player("A")])
        self.assertEqual((move_on[0], move_on[1], move_on[2]), (mild, "slump", "moderate"))

    def test_low_minutes_thresholds(self):
        fifty = ev("Fifty", mpg=50)
        self.assertIsNone(si._move_on_signal(fifty))                       # strict: under 45
        self.assertEqual(si._move_on_signal(fifty, "moderate")[1], "minutes")   # medium: under 55

    def test_outlook_threshold_is_looser_in_the_medium_tier(self):
        weak = ev("W", engine_score=48, form_score=60)
        self.assertIsNone(si._move_on_signal(weak))
        self.assertEqual(si._move_on_signal(weak, "moderate")[1], "outlook")

    def test_unavailable_player_is_always_a_strong_move_on(self):
        out = ev("Out", availability=0.0)
        _, move_on = si._classify([out, fine_player("A")])
        self.assertEqual((move_on[1], move_on[2]), ("unavailable", "strong"))

    def test_slump_default_level_is_strict_for_telegram_and_backtest(self):
        mild = ev("Mild", form_score=48, recent_ppg=3.3, earlier_ppg=4.0)
        self.assertIsNone(si._slump_info(mild))


class WeakFallbackTests(SimpleTestCase):
    def test_a_quiet_squad_gets_one_mild_move_on_card_for_the_worst_outlook(self):
        # nobody trips a strict or medium rule (all above the position average) but one is the weakest of the XI
        squad = [ev("A", engine_score=72), ev("B", engine_score=63), ev("Meh", engine_score=56, form_score=55)]
        chance, move_on = si._classify(squad)
        self.assertIsNone(chance)
        self.assertEqual((move_on[0]["player"].web_name, move_on[1], move_on[2]), ("Meh", "watch", "mild"))

    def test_a_genuinely_excellent_xi_gets_no_mild_move_on_card(self):
        squad = [ev("A", engine_score=80), ev("B", engine_score=70), ev("C", engine_score=66)]
        self.assertIsNone(si._weak_fallback(squad)[1])

    def test_otherwise_the_mild_card_is_the_biggest_gap(self):
        squad = [ev(n, engine_score=80, underlying=u, actual=a) for n, u, a in (("A", 40, 40), ("B", 48, 40), ("C", 45, 44))]
        chance, move_on = si._classify(squad)
        self.assertIsNone(move_on)
        self.assertEqual((chance[0]["player"].web_name, chance[1]), ("B", "mild"))

    def test_weak_tier_only_runs_when_nothing_else_was_found(self):
        star = ev("Star", underlying=70, actual=30)
        meh = ev("Meh", engine_score=47, form_score=60)       # would be a mild move-on, but strict chance exists
        chance, move_on = si._classify([star, meh])
        self.assertEqual(chance[1], "strong")
        self.assertEqual(move_on[2], "moderate")              # the medium tier finds him (outlook <= 50) instead

    def test_weak_fallback_never_flags_an_unavailable_or_doubtful_player(self):
        doubtful = ev("D", availability=0.5, engine_score=30)
        squad = [doubtful, ev("A", engine_score=70)]
        _, move_on = si._weak_fallback(squad)
        self.assertIsNone(move_on)

    def test_chance_fallback_needs_minutes_and_availability(self):
        benchwarmer = ev("Bench", mpg=10, underlying=48, actual=20, engine_score=80)
        hurt = ev("Hurt", availability=0.25, underlying=48, actual=20, engine_score=80)
        self.assertEqual(si._weak_fallback([benchwarmer, hurt]), (None, None))

    def test_nothing_to_show_for_an_empty_squad(self):
        self.assertEqual(si._classify([]), (None, None))


class MildWordingTests(SimpleTestCase):
    def test_mild_chance_text_is_soft_for_attack_and_defence(self):
        attack = ev("Att", position="FWD", underlying=48, actual=40)
        text = si._template_reason("another_chance", "mild", attack, 5)
        self.assertIn("small bounce back", text)
        self.assertNotIn("likely to follow", text)
        defence = ev("Def", position="DEF", underlying=48, actual=40)
        self.assertIn("small bounce back", si._template_reason("another_chance", "mild", defence, 5))

    def test_watch_text_is_honest_about_being_mild(self):
        text = si._template_reason("move_on", "watch", ev("Meh", engine_score=49), 5)
        self.assertIn("Nothing alarming yet", text)
        self.assertIn("keep an eye", text)
        self.assertNotIn("Consider a replacement", text)

    def test_strong_wording_is_unchanged(self):
        text = si._template_reason("another_chance", "underperforming", ev("Att", position="MID"), 5)
        self.assertIn("likely to follow", text)
