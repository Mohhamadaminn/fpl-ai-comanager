from django.contrib import admin
from .models import AIPrediction, UserPrediction, PredictionEvaluation

admin.site.register(AIPrediction)
admin.site.register(UserPrediction)
admin.site.register(PredictionEvaluation)
