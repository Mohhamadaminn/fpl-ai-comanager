from rest_framework import viewsets
from rest_framework.permissions import AllowAny
from .models import AIPrediction, PredictionEvaluation
from .serializers import AIPredictionSerializer, PredictionEvaluationSerializer


class AIPredictionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AIPrediction.objects.select_related(
        "gameweek", "suggested_captain", "suggested_transfer_in", "suggested_transfer_out"
    ).order_by("-gameweek__fpl_id")
    serializer_class = AIPredictionSerializer
    permission_classes = [AllowAny]  # single-user project, no auth needed for now


class PredictionEvaluationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PredictionEvaluation.objects.select_related("gameweek").order_by("-gameweek__fpl_id")
    serializer_class = PredictionEvaluationSerializer
    permission_classes = [AllowAny]