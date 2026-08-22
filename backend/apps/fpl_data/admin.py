# apps/fpl_data/admin.py
from django.contrib import admin
from .models import Team, Player, Gameweek, PlayerGameweekStat

admin.site.register(Team)
admin.site.register(Player)
admin.site.register(Gameweek)
admin.site.register(PlayerGameweekStat)
