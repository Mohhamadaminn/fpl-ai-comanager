from django.core.management.base import BaseCommand, CommandError

from apps.fpl_data.expected_points import load_dataset, predict_gameweek

MIN_PLAY_PROBABILITY = 0.5     # only players the model itself expects to play are scored


class Command(BaseCommand):
    help = (
        "Replays the expected-points model on finished gameweeks: for each one it predicts using ONLY "
        "earlier gameweeks, then compares with the real points. Shows the average error, the bias, "
        "how often the real points fell inside the P10-P90 range (should be near 80%) and how the model "
        "compares with a naive 'average of his last 5 games' guess."
    )

    def add_arguments(self, parser):
        parser.add_argument("--from-gw", type=int, default=None, help="First gameweek to test (default: 6).")
        parser.add_argument("--to-gw", type=int, default=None, help="Last gameweek to test (default: latest finished).")

    def handle(self, *args, **options):
        dataset = load_dataset()
        played_gws = sorted({r["gw"] for rs in dataset.rows.values() for r in rs})
        if len(played_gws) < 3:
            raise CommandError("Need at least 3 finished gameweeks with stats.")

        start = options["from_gw"] or 6
        end = options["to_gw"] or played_gws[-1]
        self.stdout.write(
            "Availability (injuries, doubts) was not recorded for past gameweeks, so every player counts as fit.\n"
            f"Only players with a predicted chance of playing >= {MIN_PLAY_PROBABILITY:.0%} are scored.\n"
        )
        self.stdout.write(f"{'GW':>3} {'n':>5} {'MAE':>6} {'bias':>6} {'in P10-P90':>11} {'naive MAE':>10}")

        totals = {"n": 0, "abs": 0.0, "bias": 0.0, "inside": 0, "naive_abs": 0.0}
        for gw in range(start, end + 1):
            if gw not in played_gws:
                continue
            actual = {pid: next((r["points"] for r in rs if r["gw"] == gw), 0) for pid, rs in dataset.rows.items()}
            candidates = [pid for pid in dataset.rows if dataset.players.get(pid)]
            try:
                predictions = predict_gameweek(dataset, gw, candidates)
            except ValueError as exc:
                self.stdout.write(f"{gw:>3}  skipped: {exc}")
                continue

            n = absolute = bias = inside = naive_absolute = 0
            for pid, pred in predictions.items():
                if pred["p_play"] < MIN_PLAY_PROBABILITY:
                    continue
                before = [r["points"] for r in dataset.rows[pid] if r["gw"] < gw][-5:]
                if not before:
                    continue
                points = actual[pid]
                n += 1
                absolute += abs(points - pred["xp"])
                bias += pred["xp"] - points
                inside += pred["low"] <= points <= pred["high"]
                naive_absolute += abs(points - sum(before) / len(before))
            if not n:
                continue
            self.stdout.write(
                f"{gw:>3} {n:>5} {absolute / n:>6.2f} {bias / n:>+6.2f} {inside / n:>10.0%} {naive_absolute / n:>10.2f}"
            )
            totals["n"] += n
            totals["abs"] += absolute
            totals["bias"] += bias
            totals["inside"] += inside
            totals["naive_abs"] += naive_absolute

        if totals["n"]:
            n = totals["n"]
            self.stdout.write(self.style.SUCCESS(
                f"all {n:>5} {totals['abs'] / n:>6.2f} {totals['bias'] / n:>+6.2f} "
                f"{totals['inside'] / n:>10.0%} {totals['naive_abs'] / n:>10.2f}"
            ))
            self.stdout.write(
                "bias = predicted minus real (negative: the model is too low). "
                "A range that is inside far less than 80% of the time is too narrow."
            )
