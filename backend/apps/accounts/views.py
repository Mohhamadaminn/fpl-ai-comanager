from rest_framework import generics, permissions
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.response import Response
from .models import FPLManagerProfile

from .serializers import MeSerializer, RegisterSerializer, FPLTeamLinkSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"


class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class MeView(generics.RetrieveAPIView):
    serializer_class = MeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user



class LinkFPLTeamView(generics.GenericAPIView):
    serializer_class = FPLTeamLinkSerializer
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team_id = serializer.validated_data["team"]

        profile, _ = FPLManagerProfile.objects.get_or_create(user=request.user)

        profile.fpl_team_id = team_id
        profile.save(update_fields=["fpl_team_id"])
        return Response({"fpl_team_id": team_id})

    def delete(self, request):
        FPLManagerProfile.objects.filter(user=request.user).update(fpl_team_id=None)
        return Response(status=204)