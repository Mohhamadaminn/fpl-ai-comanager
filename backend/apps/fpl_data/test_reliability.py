from unittest import mock

import requests
from django.conf import settings
from django.test import SimpleTestCase

from apps.fpl_data import services
from apps.fpl_data.llm import make_groq_client


class GroqClientTests(SimpleTestCase):
    def test_client_uses_configured_timeout_and_retries(self):
        client = make_groq_client()
        self.assertEqual(client.max_retries, settings.GROQ_MAX_RETRIES)
        self.assertEqual(client.timeout.read, settings.GROQ_TIMEOUT_SECONDS)
        self.assertEqual(client.timeout.connect, 5.0)


class FplTimeoutTests(SimpleTestCase):
    def test_bootstrap_and_fixtures_requests_have_timeouts(self):
        with mock.patch.object(services.requests, "get", side_effect=requests.Timeout) as get:
            for fn in (services.sync_bootstrap_data, services.sync_fixtures):
                with self.assertRaises(requests.Timeout):
                    fn()
                self.assertEqual(get.call_args.kwargs.get("timeout"), services.FPL_TIMEOUT)


class CeleryLimitsTests(SimpleTestCase):
    def test_limits_configured_and_sync_tasks_retry(self):
        from apps.fpl_data.tasks import sync_bootstrap_task, sync_fixtures_task
        self.assertLess(settings.CELERY_TASK_SOFT_TIME_LIMIT, settings.CELERY_TASK_TIME_LIMIT)
        for task in (sync_bootstrap_task, sync_fixtures_task):
            self.assertEqual(task.max_retries, 3)
            self.assertIn(requests.RequestException, task.autoretry_for)
