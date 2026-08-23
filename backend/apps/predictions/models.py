from django.db import models
from django.contrib.auth.models import User
from apps.fpl_data.models import Player, Gameweek


class AIPrediction(models.Model):
    gameweek = models.ForeignKey(Gameweek, on_delete=models.CASCADE, related_name="ai_predictions")
    suggested_captain = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, related_name="+")
    suggested_transfer_out = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    suggested_transfer_in = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reasoning = models.TextField()
    data_snapshot = models.JSONField(default=dict)  # player stats/prices/form at prediction time
    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["gameweek"], name="unique_ai_prediction_per_gameweek"),
        ]
    
    def __str__(self):
        return f"AI Prediction - GW{self.gameweek.fpl_id}"


class UserPrediction(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="predictions")
    gameweek = models.ForeignKey(Gameweek, on_delete=models.CASCADE, related_name="user_predictions")
    chosen_captain = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, related_name="+")
    transfer_out = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    transfer_in = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reasoning = models.TextField(blank=True)
    data_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "gameweek"], name="unique_user_prediction_per_gameweek"),
        ]


    def __str__(self):
        return f"{self.user.username}'s Prediction - GW{self.gameweek.fpl_id}"


class PredictionEvaluation(models.Model):
    gameweek = models.OneToOneField(Gameweek, on_delete=models.CASCADE, related_name="evaluation")

    ai_captain_points = models.IntegerField(null=True, blank=True)
    user_captain_points = models.IntegerField(null=True, blank=True)
    ai_was_correct_captain = models.BooleanField(null=True)
    user_was_correct_captain = models.BooleanField(null=True)

    ai_transfer_points_delta = models.IntegerField(null=True, blank=True)   # points gained/lost from AI's suggested transfer
    user_transfer_points_delta = models.IntegerField(null=True, blank=True)

    evaluated_at = models.DateTimeField(auto_now_add=True)