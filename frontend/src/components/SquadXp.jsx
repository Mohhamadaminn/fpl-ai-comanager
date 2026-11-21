import PlayerPhoto from "./PlayerPhoto";
import { statusBadge } from "../utils/status";

const ROWS = ["GKP", "DEF", "MID", "FWD"];

function fixtureText(fixtures) {
  if (!fixtures.length) return "No game";
  if (fixtures.length > 1) return `${fixtures.length} games`;
  const [f] = fixtures;
  return `${f.opponent} (${f.home ? "H" : "A"})`;
}

function PitchPlayer({ player }) {
  const badge = statusBadge(player);
  const points = Math.round(player.xp);
  const tooltip = [
    player.name,
    badge && (player.news ? `${badge.label}: ${player.news}` : badge.label),
    player.low_data && "Few games played, so this prediction is less reliable",
  ]
    .filter(Boolean)
    .join(" — ");

  return (
    <div
      className={`pp${player.is_captain ? " pp--captain" : ""}`}
      title={tooltip}
      aria-label={`${player.name}, predicted ${points} points, likely ${player.low} to ${player.high}`}
    >
      <div className="pp-shirt">
        <PlayerPhoto className="pp-photo" name={player.name} photoUrl={player.photo_url} />
        {player.is_captain && <span className="pp-armband">C</span>}
        {player.is_vice_captain && <span className="pp-armband pp-armband--vice">V</span>}
        {badge && <span className={`pp-status pp-status--${badge.tone}`}>!</span>}
      </div>
      <div className="pp-name">
        <strong>{player.name}</strong>
        <span>{fixtureText(player.fixtures)}</span>
      </div>
      <div className="pp-points">
        {points}
        {player.low_data && <i>*</i>}
      </div>
      <div className="pp-range">
        {player.low}–{player.high}
      </div>
    </div>
  );
}

function CaptainCard({ captain }) {
  const { player, vice, reasons } = captain;

  return (
    <section className="cap-card" aria-label="Recommended captain">
      <span className="cap-label">Recommended captain</span>
      <div className="cap-main">
        <PlayerPhoto className="cap-photo" name={player.name} photoUrl={player.photo_url} />
        <div className="cap-info">
          <strong className="cap-name">{player.name}</strong>
          <span className="cap-sub">
            {player.team} · {player.position}
          </span>
          <div className="cap-points">
            <b>{Math.round(captain.points_as_captain)}</b>
            <span>points as captain</span>
          </div>
          <span className="cap-sub">
            ({Math.round(player.xp)} without the armband, likely{" "}
            <span className="nowrap">
              {player.low}–{player.high}
            </span>
            )
          </span>
        </div>
      </div>

      {reasons.length > 0 && (
        <ul className="cap-reasons">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {vice && (
        <p className="cap-vice">
          Vice-captain: <strong>{vice.name}</strong> · {Math.round(vice.xp)} pts
        </p>
      )}
    </section>
  );
}

function SquadXp({ data }) {
  const { xi_total: total } = data;
  const rows = ROWS.map((position) => data.starting_xi.filter((p) => p.position === position));

  return (
    <section className="squad-xp" aria-label="Predicted points">
      <header className="squad-xp-header">
        <h3>Predicted points · {data.gameweek.name}</h3>
        <p>
          The big number under each player is his predicted points for this gameweek. The small
          numbers are the likely range, from a poor week to a good one.
        </p>
      </header>

      {data.recommended_captain && <CaptainCard captain={data.recommended_captain} />}

      <div className="pitch">
        {data.formation && <span className="pitch-formation">{data.formation}</span>}
        {rows.map(
          (players, index) =>
            players.length > 0 && (
              <div className="pitch-row" key={ROWS[index]}>
                {players.map((player) => (
                  <PitchPlayer key={player.id} player={player} />
                ))}
              </div>
            ),
        )}
      </div>

      <div className="xp-total">
        <span>Predicted team points</span>
        <strong>{Math.round(total.xp)}</strong>
        <span className="xp-total-range">
          likely {total.low}–{total.high}
          {total.includes_captain ? " · captain counted twice" : ""}
        </span>
      </div>

      {data.bench.length > 0 && (
        <div className="bench">
          <h4 className="bench-title">Substitutes</h4>
          <div className="bench-row">
            {data.bench.map((player) => (
              <div className="bench-slot" key={player.id}>
                <span className="bench-position">{player.position}</span>
                <PitchPlayer player={player} />
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="xp-footnote">
        {data.lineup_note} Based on gameweeks up to {data.data_through_gameweek}. * means few games
        played, so the prediction is less reliable. Own goals and penalty misses are not included.
      </p>
    </section>
  );
}

export default SquadXp;
