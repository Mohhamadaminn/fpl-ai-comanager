from celery import shared_task
from apps.fpl_data.models import Gameweek
from .models import AIPrediction, PredictionEvaluation
from .services import evaluate_gameweek


@shared_task
def evaluate_finished_gameweeks_task():
    from apps.accounts.models import FPLManagerProfile
    from apps.accounts.services import get_manager_gameweek_state

    profile = FPLManagerProfile.objects.filter(user__username="fpl_manager").first()

    finished_gws = Gameweek.objects.filter(finished=True)
    for gw in finished_gws:
        has_prediction = AIPrediction.objects.filter(gameweek=gw).exists()
        already_evaluated = PredictionEvaluation.objects.filter(gameweek=gw).exists()
        if has_prediction and not already_evaluated:
            manager_state = None
            if profile:
                try:
                    manager_state = get_manager_gameweek_state(profile.fpl_team_id, gw.fpl_id)
                except Exception:
                    pass  # fall back to default 2x multiplier if this fails
            evaluate_gameweek(gw, manager_state)