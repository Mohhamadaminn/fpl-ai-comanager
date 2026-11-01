import { useState } from "react";
import PlayerPickerModal from "./PlayerPickerModal";

const STATUS_BADGES = {
  i: "Injured",
  s: "Suspended",
  u: "Unavailable",
  n: "Not in squad",
  d: "Doubtful",
};

function statusBadge(player) {
  if (player.status && player.status !== "a") {
    return {
      label: STATUS_BADGES[player.status] || "Unavailable",
      tone: player.status === "d" ? "doubtful" : "out",
    };
  }
  const chance = player.chance_of_playing_next_round;
  if (chance !== null && chance !== undefined && chance < 100) {
    return { label: "Doubtful", tone: "doubtful" };
  }
  return null;
}

function PlayerSearchSelect({ label, position, excludeId, value, onSelect }) {
  const [modalOpen, setModalOpen] = useState(false);

  if (value) {
    const badge = statusBadge(value);
    return (
      <div className="player-select">
        <span className="player-select-label">{label}</span>
        <div className="player-select-chosen">
          <div className="player-select-chosen-avatar">
            {value.web_name.slice(0, 2).toUpperCase()}
          </div>
          <div className="player-select-chosen-info">
            <strong>{value.web_name}</strong>
            <span>{value.team} · {value.position} · £{value.price}m</span>
            {badge && (
              <span
                className={`player-status-badge player-status-badge--${badge.tone}`}
                title={value.news || badge.label}
              >
                {badge.label}
              </span>
            )}
            {badge && value.news && (
              <span className="player-status-news">{value.news}</span>
            )}
          </div>
          <button type="button" onClick={() => onSelect(null)}>Change</button>
        </div>
      </div>
    );
  }

  return (
    <div className="player-select">
      <span className="player-select-label">{label}</span>
      <button className="player-select-trigger" onClick={() => setModalOpen(true)}>
        + Select a player
      </button>
      {modalOpen && (
        <PlayerPickerModal
          lockedPosition={position}
          excludeId={excludeId}
          onSelect={(p) => { onSelect(p); setModalOpen(false); }}
          onClose={() => setModalOpen(false)}
        />
      )}
    </div>
  );
}

export default PlayerSearchSelect;