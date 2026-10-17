from rest_framework import serializers
from .models import Player, Team


class PlayerListSerializer(serializers.ModelSerializer):
    team = serializers.CharField(source="team.short_name")

    class Meta:
        model = Player
        fields = ["id", "web_name", "position", "team", "price", "status"]


class ComparePlayersRequestSerializer(serializers.Serializer):
    player_a_id = serializers.IntegerField()
    player_b_id = serializers.IntegerField()
    lang = serializers.ChoiceField(choices=["en", "fa"], default="en")



class TeamListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name", "short_name"]