from django.db import models
from django.contrib.auth.models import User


class FPLManagerProfile(models.Model):

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        PERSIAN = "fa", "فارسی"



    user = models.OneToOneField(User, on_delete=models.CASCADE)
    fpl_team_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)
    free_transfers = models.PositiveSmallIntegerField(default=1)
    last_synced_gameweek = models.ForeignKey(
        "fpl_data.Gameweek", on_delete=models.SET_NULL, null=True, blank=True
    )
    preferred_language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.ENGLISH
    )


    def __str__(self):
        return f"{self.user.username} - Team {self.fpl_team_id}"
