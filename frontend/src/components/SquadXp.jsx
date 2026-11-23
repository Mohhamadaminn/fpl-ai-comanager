import { useEffect, useRef, useState } from "react";
import PlayerPhoto from "./PlayerPhoto";
import { statusBadge } from "../utils/status";

const ROWS = ["GKP", "DEF", "MID", "FWD"];

function fixtureText(fixtures) {
  if (!fixtures.length) return "No game";
  if (fixtures.length > 1) return `${fixtures.length} games`;
  const [f] = fixtures;
  return `${f.opponent} (${f.home ? "H" : "A"})`;
}

function PitchPlayer({ player, selected, onSelect }) {
  const { verdict } = player;
  const badge = statusBadge(player);

  return (
    <button
      type="button"
      className={`pp pp--${verdict.tone}${player.is_captain ? " pp--captain" : ""}${selected ? " pp--selected" : ""}`}
      aria-pressed={selected}
      aria-label={`${player.name}, ${verdict.label}. Show details`}
      onClick={() => onSelect(player.id)}
    >
      <span className="pp-shirt">
        <PlayerPhoto className="pp-photo" name={player.name} photoUrl={player.photo_url} />
        {player.is_captain && <span className="pp-armband">C</span>}
        {player.is_vice_captain && <span className="pp-armband pp-armband--vice">V</span>}
        {badge && <span className={`pp-status pp-status--${badge.tone}`}>!</span>}
      </span>
      <span className="pp-plate">
        <strong>{player.name}</strong>
        <small>{fixtureText(player.fixtures)}</small>
      </span>
      <span className={`pp-verdict pp-verdict--${verdict.tone}`}>{verdict.label}</span>
    </button>
  );
}

function PlayerDetail({ player, onClose, innerRef }) {
  const { verdict } = player;
  const badge = statusBadge(player);
  const fixtures = player.fixtures.length
    ? player.fixtures.map((f) => `${f.opponent} (${f.home ? "H" : "A"})`).join(" + ")
    : "No game this gameweek";

  return (
    <section className="pd" ref={innerRef} tabIndex={-1} aria-label={`${player.name} details`}>
      <div className="pd-head">
        <PlayerPhoto className="pd-photo" name={player.name} photoUrl={player.photo_url} />
        <div className="pd-title">
          <strong>{player.name}</strong>
          <span>
            {player.team} · {player.position} · £{player.price}m
          </span>
          <span>{fixtures}</span>
        </div>
        <span className={`pp-verdict pp-verdict--${verdict.tone} pd-verdict`}>{verdict.label}</span>
      </div>

      <p className="pd-note">{verdict.note}</p>

      <div className="pd-tags">
        {badge && <span className={`player-status-badge player-status-badge--${badge.tone}`}>{badge.label}</span>}
        {player.faces_top_team && <span className="pd-tag">Faces a top-5 side</span>}
        {player.premium && <span className="pd-tag pd-tag--prime">Premium player</span>}
        {player.low_data && <span className="pd-tag">Few games played</span>}
      </div>

      {player.recent_points.length > 0 && (
        <div className="pd-recent">
          <span>Last {player.recent_points.length} games</span>
          <ul>
            {player.recent_points.map((points, index) => (
              <li key={index}>{points}</li>
            ))}
          </ul>
        </div>
      )}

      <button type="button" className="pd-back" onClick={onClose}>
        ↑ Back to lineup
      </button>
    </section>
  );
}

function CaptainCard({ captain, onSelect }) {
  const { player, vice, reasons, alternatives = [] } = captain;

  return (
    <section className="cap-card" aria-label="Suggested captain">
      <PlayerPhoto className="cap-photo" name={player.name} photoUrl={player.photo_url} />
      <div className="cap-info">
        <span className="cap-label">Suggested captain</span>
        <strong className="cap-name">{player.name}</strong>
        {reasons.length > 0 && (
          <ul className="cap-reasons">
            {reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        )}
      </div>

      {(alternatives.length > 0 || vice) && (
        <p className="cap-also">
          Also consider:{" "}
          {[...alternatives, ...(vice && !alternatives.some((a) => a.id === vice.id) ? [vice] : [])]
            .slice(0, 3)
            .map((option, index) => (
              <span key={option.id}>
                {index > 0 && ", "}
                <button type="button" className="cap-link" onClick={() => onSelect(option.id)}>
                  {option.name}
                </button>
              </span>
            ))}
          . The armband is your call.
        </p>
      )}
    </section>
  );
}

function SquadXp({ data }) {
  const [selectedId, setSelectedId] = useState(null);
  const detailRef = useRef(null);
  const pitchRef = useRef(null);
  const { xi_estimate: estimate } = data;
  const rows = ROWS.map((position) => data.starting_xi.filter((p) => p.position === position));
  const selected = [...data.starting_xi, ...data.bench].find((p) => p.id === selectedId) || null;

  const behaviour = () =>
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";

  // Picking a player takes the page straight to his details instead of leaving them below the fold.
  useEffect(() => {
    if (selectedId === null) return;
    detailRef.current?.scrollIntoView({ behavior: behaviour(), block: "center" });
    detailRef.current?.focus({ preventScroll: true });
  }, [selectedId]);

  const select = (id) => setSelectedId(id);
  const close = () => {
    setSelectedId(null);
    pitchRef.current?.scrollIntoView({ behavior: behaviour(), block: "center" });
  };

  return (
    <section className="squad-xp" aria-label="Your gameweek outlook">
      <header className="squad-xp-header">
        <h3>Outlook · {data.gameweek.name}</h3>
        <p>A guide for the week, not a forecast. Tap a player for details; the decisions are yours.</p>
      </header>

      <div className="pitch" ref={pitchRef}>
        {data.formation && <span className="pitch-formation">{data.formation}</span>}
        <div className="pitch-rows">
          {rows.map(
            (players, index) =>
              players.length > 0 && (
                <div className="pitch-row" key={ROWS[index]}>
                  {players.map((player) => (
                    <PitchPlayer
                      key={player.id}
                      player={player}
                      selected={player.id === selectedId}
                      onSelect={select}
                    />
                  ))}
                </div>
              ),
          )}
        </div>

        {data.bench.length > 0 && (
          <div className="bench">
            <span className="bench-title">Bench</span>
            <div className="bench-row">
              {data.bench.map((player) => (
                <PitchPlayer
                  key={player.id}
                  player={player}
                  selected={player.id === selectedId}
                  onSelect={select}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      {estimate && (
        <div className="xp-total">
          <span>Starting XI estimate</span>
          <strong>
            {estimate.low}–{estimate.high} points
          </strong>
          <small>{estimate.includes_captain ? "Captain counted twice. " : ""}Real weeks swing more than this.</small>
        </div>
      )}

      {selected && <PlayerDetail player={selected} onClose={close} innerRef={detailRef} />}

      {data.recommended_captain && <CaptainCard captain={data.recommended_captain} onSelect={select} />}
      {!data.recommended_captain && data.captain_note && <p className="cap-note">{data.captain_note}</p>}

      <p className="xp-footnote">
        {data.lineup_note} Based on gameweeks up to {data.data_through_gameweek}.
      </p>
    </section>
  );
}

export default SquadXp;
