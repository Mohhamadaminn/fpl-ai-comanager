from django.db import models
from django.contrib.auth.models import User


class FPLManagerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    fpl_team_id = models.PositiveIntegerField(unique=True)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)
    free_transfers = models.PositiveSmallIntegerField(default=1)
    last_synced_gameweek = models.ForeignKey(
        "fpl_data.Gameweek", on_delete=models.SET_NULL, null=True, blank=True
    )

    def __str__(self):
        return f"{self.user.username} - Team {self.fpl_team_id}"
