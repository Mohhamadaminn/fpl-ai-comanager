from django.urls import path
from .views import PlayerListView, TeamListView, ComparePlayersView, SquadInsightsView

urlpatterns = [
    path("players/", PlayerListView.as_view(), name="player-list"),
    path("teams/", TeamListView.as_view(), name="team-list"),
    path("compare-players/", ComparePlayersView.as_view(), name="compare-players"),
    path("squad-insights/", SquadInsightsView.as_view(), name="squad-insights"),
]