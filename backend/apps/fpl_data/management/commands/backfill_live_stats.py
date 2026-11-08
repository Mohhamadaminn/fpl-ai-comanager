import time

from django.core.management.base import BaseCommand
from requests import RequestException

from apps.fpl_data.models import Gameweek
from apps.fpl_data.services import sync_live_gameweek_stats


class Command(BaseCommand):
    help = "Backfill player stats for all finished gameweeks."

    def handle(self, *args, **options):
        gws = Gameweek.objects.filter(finished=True).order_by("fpl_id")
        failed = []
        for gw in gws:
            try:
                sync_live_gameweek_stats(gw.fpl_id)
                self.stdout.write(self.style.SUCCESS(f"GW{gw.fpl_id} done"))
            except RequestException as exc:
                failed.append(gw.fpl_id)
                self.stderr.write(f"GW{gw.fpl_id} failed: {exc}")
            time.sleep(1)

        if failed:
            self.stderr.write(self.style.ERROR(f"Failed gameweeks: {failed}. Re-run to retry."))