import re
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from .models import FPLManagerProfile, User


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["email", "password", "display_name"]

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already registered.")
        return value

    def validate(self, attrs):
        validate_password(attrs["password"], User(email=attrs["email"]))
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        FPLManagerProfile.objects.create(user=user)
        return user


class MeSerializer(serializers.ModelSerializer):
    fpl_team_id = serializers.IntegerField(source="fplmanagerprofile.fpl_team_id", read_only=True)
    preferred_language = serializers.CharField(source="fplmanagerprofile.preferred_language", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "display_name", "fpl_team_id", "preferred_language"]


class FPLTeamLinkSerializer(serializers.Serializer):
    team = serializers.CharField(help_text="FPL team ID or official team URL")

    def validate_team(self, value):
        value = value.strip()
        match = re.search(r"/entry/(\d+)", value) or re.fullmatch(r"(\d+)", value)
        if not match:
            raise serializers.ValidationError("Enter a valid FPL team ID or link.")
        team_id = int(match.group(1))
        if team_id <= 0:
            raise serializers.ValidationError("Invalid team ID.")
        return team_id