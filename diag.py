from apps.fpl_data.compare_engine import _target_gameweek, _team_ratings, _team_gw_records, _side_ease
from apps.fpl_data.models import Fixture, Team

gw = _target_gameweek()
r = _team_ratings(gw)
rec = _team_gw_records(gw)
teams = {t.id: t for t in Team.objects.all()}

print("TEAM att leaky res | matches res_matches pts xg xgc")
for tid, x in sorted(r.items(), key=lambda i: -i[1]["res"]):
    recs = rec.get(tid, [])
    print(
        teams[tid].short_name, round(x["att"]), round(x["leaky"]), round(x["res"]), "|",
        sum(a["matches"] for a in recs), sum(a["res_matches"] for a in recs),
        sum(a["pts"] for a in recs),
        round(sum(a["xg"] for a in recs), 1), round(sum(a["xgc"] for a in recs), 1),
    )

print()
print("FIXTURES gameweek", gw.fpl_id)
for f in Fixture.objects.filter(gameweek=gw).select_related("team_home", "team_away"):
    sides = (
        (f.team_home, f.team_away, True, f.difficulty_home),
        (f.team_away, f.team_home, False, f.difficulty_away),
    )
    for own, opp, home, fdr in sides:
        print(
            own.short_name, "vs", opp.short_name, "H" if home else "A", "fdr", fdr,
            "own_share0.3:", round(_side_ease(own, opp, home, fdr, "attack", r, 0.3), 3),
            "own_share0:", round(_side_ease(own, opp, home, fdr, "attack", r, 0.0), 3),
        )