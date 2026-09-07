from rest_framework.routers import DefaultRouter
from .views import AIPredictionViewSet, PredictionEvaluationViewSet

router = DefaultRouter()
router.register(r"predictions", AIPredictionViewSet, basename="prediction")
router.register(r"evaluations", PredictionEvaluationViewSet, basename="evaluation")

urlpatterns = router.urls