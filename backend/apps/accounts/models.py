from django.db import models
from django.contrib.auth.models import User


class FPLManagerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    fpl_team_id = models.PositiveIntegerField(unique=True)

    def __str__(self):
        return f"{self.user.username} - Team {self.fpl_team_id}"
