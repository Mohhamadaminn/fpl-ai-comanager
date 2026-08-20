from django.db import models


class Team(models.Model):
    """A Premier League club, e.g. Arsenal, Liverpool."""
    fpl_id = models.PositiveIntegerField(unique=True)  # 'id' from FPL API
    name = models.CharField(max_length=100)
    short_name = models.CharField(max_length=10)
    strength = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return self.name


class Player(models.Model):
    class Position(models.TextChoices):
        GOALKEEPER = "GKP", "Goalkeeper"
        DEFENDER = "DEF", "Defender"
        MIDFIELDER = "MID", "Midfielder"
        FORWARD = "FWD", "Forward"

    fpl_id = models.PositiveIntegerField(unique=True)  # 'id' from FPL API
    first_name = models.CharField(max_length=100)
    second_name = models.CharField(max_length=100)
    web_name = models.CharField(max_length=100)  # display name, e.g. "Salah"
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    position = models.CharField(max_length=3, choices=Position.choices)

    price = models.DecimalField(max_digits=4, decimal_places=1)  # now_cost / 10
    form = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    total_points = models.IntegerField(default=0)
    selected_by_percent = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    status = models.CharField(max_length=1, default="a")  # a=available, i=injured, etc.
    chance_of_playing_next_round = models.PositiveSmallIntegerField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.web_name


class Gameweek(models.Model):
    fpl_id = models.PositiveIntegerField(unique=True)  # 'id' / event number
    name = models.CharField(max_length=50)  # "Gameweek 1"
    deadline_time = models.DateTimeField()
    is_current = models.BooleanField(default=False)
    is_next = models.BooleanField(default=False)
    finished = models.BooleanField(default=False)
    average_entry_score = models.PositiveIntegerField(null=True, blank=True)
    highest_score = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return self.name


class PlayerGameweekStat(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="gameweek_stats")
    gameweek = models.ForeignKey(Gameweek, on_delete=models.CASCADE, related_name="player_stats")

    minutes = models.PositiveSmallIntegerField(default=0)
    goals_scored = models.PositiveSmallIntegerField(default=0)
    assists = models.PositiveSmallIntegerField(default=0)
    clean_sheets = models.PositiveSmallIntegerField(default=0)
    points = models.SmallIntegerField(default=0)

    is_final = models.BooleanField(default=False)  # True once the gameweek is officially finished
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("player", "gameweek")

    def __str__(self):
        return f"{self.player.web_name} - GW{self.gameweek.fpl_id}"