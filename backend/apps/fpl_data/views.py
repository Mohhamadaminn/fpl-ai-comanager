from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Player, Team
from .serializers import PlayerListSerializer, TeamListSerializer, ComparePlayersRequestSerializer
from .services import compare_players


SORT_FIELDS = {
    "total_points": "-total_points",
    "form": "-form",
    "price": "-price",
    "price_asc": "price",
    "selected_by_percent": "-selected_by_percent",
    "name": "web_name",
}


class PlayerListView(APIView):
    def get(self, request):
        search = request.query_params.get("search", "").strip()
        position = request.query_params.get("position", "").strip().upper()
        team_id = request.query_params.get("team", "").strip()
        sort = request.query_params.get("sort", "total_points").strip()
        page = max(int(request.query_params.get("page", 1)), 1)
        page_size = 25

        qs = Player.objects.filter(status="a").select_related("team")

        if search:
            qs = qs.filter(web_name__icontains=search)
        if position:
            qs = qs.filter(position=position)
        if team_id:
            qs = qs.filter(team_id=team_id)

        order_by = SORT_FIELDS.get(sort, "-total_points")
        qs = qs.order_by(order_by)

        total = qs.count()
        start = (page - 1) * page_size
        results = qs[start:start + page_size]

        return Response({
            "results": PlayerListSerializer(results, many=True).data,
            "total": total,
            "page": page,
            "has_more": start + page_size < total,
        })


class TeamListView(APIView):
    def get(self, request):
        teams = Team.objects.all().order_by("name")
        return Response(TeamListSerializer(teams, many=True).data)


class ComparePlayersView(APIView):
    def post(self, request):
        req = ComparePlayersRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)

        try:
            result = compare_players(
                req.validated_data["player_a_id"],
                req.validated_data["player_b_id"],
                req.validated_data["lang"],
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result)