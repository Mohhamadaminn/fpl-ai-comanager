from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

COMPARE_URL = "/api/compare-players/"
INSIGHTS_URL = "/api/squad-insights/"
COMPARE_BODY = {"player_a_id": 1, "player_b_id": 2}
INSIGHTS_BODY = {"team": "123456"}


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class LLMEndpointThrottlingTests(SimpleTestCase):
    """The Groq/FPL-backed endpoints must be limited per client IP, and the
    client must not be able to dodge the limit by forging X-Forwarded-For."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        rates = mock.patch.dict(
            SimpleRateThrottle.THROTTLE_RATES,
            {"llm_burst": "2/min", "llm_daily": "100/day"},
        )
        rates.start()
        self.addCleanup(rates.stop)
        for target in ("compare_players", "get_squad_insights"):
            patcher = mock.patch(f"apps.fpl_data.views.{target}", return_value={"ok": True})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = APIClient()

    def _post(self, url, body, xff):
        return self.client.post(url, body, format="json", HTTP_X_FORWARDED_FOR=xff)

    def test_compare_is_limited_per_ip(self):
        self.assertEqual(self._post(COMPARE_URL, COMPARE_BODY, "203.0.113.9").status_code, 200)
        self.assertEqual(self._post(COMPARE_URL, COMPARE_BODY, "203.0.113.9").status_code, 200)
        self.assertEqual(self._post(COMPARE_URL, COMPARE_BODY, "203.0.113.9").status_code, 429)

    def test_squad_insights_is_limited_per_ip(self):
        self.assertEqual(self._post(INSIGHTS_URL, INSIGHTS_BODY, "203.0.113.9").status_code, 200)
        self.assertEqual(self._post(INSIGHTS_URL, INSIGHTS_BODY, "203.0.113.9").status_code, 200)
        self.assertEqual(self._post(INSIGHTS_URL, INSIGHTS_BODY, "203.0.113.9").status_code, 429)

    def test_forged_forwarded_for_prefix_does_not_bypass_limit(self):
        # nginx overwrites the header with the real peer address; even if a
        # client-supplied prefix reached Django, only the last hop is trusted.
        statuses = [
            self._post(COMPARE_URL, COMPARE_BODY, f"spoof-{i}, 203.0.113.9").status_code
            for i in range(3)
        ]
        self.assertEqual(statuses, [200, 200, 429])

    def test_different_clients_have_separate_limits(self):
        for _ in range(2):
            self._post(COMPARE_URL, COMPARE_BODY, "203.0.113.9")
        self.assertEqual(self._post(COMPARE_URL, COMPARE_BODY, "203.0.113.9").status_code, 429)
        self.assertEqual(self._post(COMPARE_URL, COMPARE_BODY, "198.51.100.7").status_code, 200)

    def test_both_endpoints_share_one_budget_per_ip(self):
        self._post(COMPARE_URL, COMPARE_BODY, "203.0.113.9")
        self._post(INSIGHTS_URL, INSIGHTS_BODY, "203.0.113.9")
        self.assertEqual(self._post(COMPARE_URL, COMPARE_BODY, "203.0.113.9").status_code, 429)
