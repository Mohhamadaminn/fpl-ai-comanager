from rest_framework import serializers
from .models import AIPrediction, PredictionEvaluation


class PlayerMiniSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    web_name = serializers.CharField()


class AIPredictionSerializer(serializers.ModelSerializer):
    suggested_captain = PlayerMiniSerializer(read_only=True)
    suggested_transfer_in = PlayerMiniSerializer(read_only=True)
    suggested_transfer_out = PlayerMiniSerializer(read_only=True)
    gameweek_name = serializers.CharField(source="gameweek.name", read_only=True)

    class Meta:
        model = AIPrediction
        fields = [
            "id", "gameweek", "gameweek_name",
            "suggested_captain", "suggested_transfer_in", "suggested_transfer_out",
            "reasoning", "created_at",
        ]


class PredictionEvaluationSerializer(serializers.ModelSerializer):
    gameweek_name = serializers.CharField(source="gameweek.name", read_only=True)

    class Meta:
        model = PredictionEvaluation
        fields = [
            "id", "gameweek", "gameweek_name",
            "ai_captain_points", "ai_was_correct_captain",
            "ai_transfer_points_delta",
            "evaluated_at",
        ]