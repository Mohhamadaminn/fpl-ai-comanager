from celery import shared_task
from .services import sync_bootstrap_data, sync_live_gameweek_stats
from .models import Gameweek


@shared_task
def sync_bootstrap_task():
    sync_bootstrap_data()


@shared_task
def sync_live_stats_task():
    current_gw = Gameweek.objects.filter(is_current=True).first()
    if current_gw:
        sync_live_gameweek_stats(current_gw.fpl_id)