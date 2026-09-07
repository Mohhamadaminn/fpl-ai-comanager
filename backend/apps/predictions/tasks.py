from celery import shared_task
from apps.fpl_data.models import Gameweek
from .models import AIPrediction, PredictionEvaluation
from .services import evaluate_gameweek


@shared_task
def evaluate_finished_gameweeks_task():
    """Find finished gameweeks with an AIPrediction but no evaluation yet, and evaluate them."""
    finished_gws = Gameweek.objects.filter(finished=True)
    for gw in finished_gws:
        has_prediction = AIPrediction.objects.filter(gameweek=gw).exists()
        already_evaluated = PredictionEvaluation.objects.filter(gameweek=gw).exists()
        if has_prediction and not already_evaluated:
            evaluate_gameweek(gw)