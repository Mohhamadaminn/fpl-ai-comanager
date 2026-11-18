import threading
from types import SimpleNamespace
from unittest import mock

from celery.exceptions import Retry
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.notifications import tasks, bot
from apps.notifications.translations import t

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "rel-tests"}}


def _gw():
    return SimpleNamespace(id=7, fpl_id=7, name="GW7", reminder_sent=False,
                           deadline_time=__import__("django.utils.timezone", fromlist=["x"]).now()
                           + __import__("datetime").timedelta(hours=5),
                           save=mock.Mock())


def _profiles(n):
    return [SimpleNamespace(id=i, telegram_chat_id=100 + i, fpl_team_id=i, preferred_language="en", user=None)
            for i in range(1, n + 1)]


@override_settings(CACHES=LOCMEM)
class ReminderTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.gw = _gw()
        self.sent = []

    def _run(self, profiles, failing=()):
        def fake_send(profile, gw):
            if profile.id in failing:
                raise RuntimeError("boom")
            self.sent.append(profile.id)

        with mock.patch.object(tasks, "_target_gameweek", return_value=self.gw), \
             mock.patch.object(tasks.FPLManagerProfile.objects, "filter", return_value=profiles), \
             mock.patch.object(tasks, "_send_reminder", side_effect=fake_send):
            tasks.send_deadline_reminder_task.run()

    def test_each_user_messaged_once_across_runs(self):
        p = _profiles(3)
        self._run(p)
        self.gw.reminder_sent = False  # simulate a repeat run (flag lost/ not yet visible)
        self._run(p)
        self.assertEqual(sorted(self.sent), [1, 2, 3])
        self.assertTrue(self.gw.save.called)

    def test_failed_user_retried_only_and_flag_waits(self):
        p = _profiles(3)
        self._run(p, failing={2})
        self.assertEqual(sorted(self.sent), [1, 3])
        self.assertFalse(self.gw.save.called)  # user 2 still pending
        self._run(p)  # next beat tick, user 2 recovers
        self.assertEqual(sorted(self.sent), [1, 2, 3])
        self.assertTrue(self.gw.save.called)

    def test_gives_up_after_max_attempts(self):
        p = _profiles(1)
        for _ in range(tasks.REMINDER_MAX_ATTEMPTS):
            self._run(p, failing={1})
        self.assertTrue(self.gw.save.called)  # given up -> gameweek closed
        self._run(p)
        self.assertEqual(self.sent, [])

    def test_lock_blocks_overlap_and_is_released(self):
        p = _profiles(1)
        cache.add(f"deadline-reminder:lock:{self.gw.id}", "1", 60)
        self._run(p)
        self.assertEqual(self.sent, [])
        cache.delete(f"deadline-reminder:lock:{self.gw.id}")
        self._run(p)
        self.assertEqual(self.sent, [1])
        self.assertIsNone(cache.get(f"deadline-reminder:lock:{self.gw.id}"))

    def test_time_limits_set(self):
        self.assertEqual(tasks.send_deadline_reminder_task.soft_time_limit, tasks.REMINDER_TASK_SOFT_LIMIT)
        self.assertEqual(tasks.send_deadline_reminder_task.time_limit, tasks.REMINDER_TASK_HARD_LIMIT)


class FailureMessageTests(SimpleTestCase):
    def test_translations_exist(self):
        for lang in ("en", "fa"):
            for key in ("prediction_failed", "differentials_failed"):
                self.assertTrue(t(key, lang))

    def _run_final_failure(self, task, *args):
        task.push_request(retries=task.max_retries)
        try:
            with mock.patch.object(tasks, "Bot") as Bot, mock.patch.object(tasks.asyncio, "run") as run:
                task.run(*args)
            return Bot, run
        finally:
            task.pop_request()

    def test_differentials_retries_then_notifies(self):
        task = tasks.generate_differentials_task
        with mock.patch.object(tasks, "_send_differentials", side_effect=RuntimeError("x")):
            task.push_request(retries=0)
            try:
                with mock.patch.object(task, "retry", side_effect=Retry()):
                    with self.assertRaises(Retry):
                        task.run(5, "en")
            finally:
                task.pop_request()
            _, run = self._run_final_failure(task, 5, "en")
            self.assertTrue(run.called)

    def test_prediction_failure_sends_message(self):
        from apps.predictions import tasks as ptasks
        task = ptasks.generate_prediction_task
        task.push_request(retries=task.max_retries)
        try:
            with mock.patch.object(ptasks.User.objects, "get", side_effect=RuntimeError("x")), \
                 mock.patch.object(ptasks, "Bot") as Bot, mock.patch.object(ptasks.asyncio, "run"):
                task.run(1, 1, 1, 55, "en")
            sent = Bot.return_value.send_message.call_args.kwargs["text"]
            self.assertEqual(sent, t("prediction_failed", "en"))
        finally:
            task.pop_request()


class BotTests(SimpleTestCase):
    async def test_db_sync_recycles_connections_off_main_thread(self):
        main = threading.get_ident()
        with mock.patch.object(bot, "close_old_connections") as coc:
            tid = await bot.db_sync(lambda: threading.get_ident())()
        self.assertEqual(coc.call_count, 2)
        self.assertNotEqual(tid, main)

    @override_settings(BOT_CONCURRENT_UPDATES=5, TELEGRAM_BOT_TOKEN="123456:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789")
    def test_application_processes_updates_concurrently(self):
        app = bot.build_application()
        self.assertEqual(app.update_processor.max_concurrent_updates, 5)
