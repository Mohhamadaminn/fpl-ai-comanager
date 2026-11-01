from rest_framework import serializers
from .models import Player, Team


class PlayerListSerializer(serializers.ModelSerializer):
    team = serializers.CharField(source="team.short_name")

    class Meta:
        model = Player
        fields = [
            "id", "web_name", "position", "team", "price", "status",
            "code", "chance_of_playing_next_round", "news",
        ]

class ComparePlayersRequestSerializer(serializers.Serializer):
    player_a_id = serializers.IntegerField()
    player_b_id = serializers.IntegerField()
    lang = serializers.ChoiceField(choices=["en", "fa"], default="en")
    horizon = serializers.IntegerField(min_value=1, max_value=5, default=1)
 


class TeamListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name", "short_name"]


class SquadInsightsRequestSerializer(serializers.Serializer):
    team = serializers.CharField(max_length=200)  # official FPL link or plain team id
    lang = serializers.ChoiceField(choices=["en", "fa"], default="en")
 
