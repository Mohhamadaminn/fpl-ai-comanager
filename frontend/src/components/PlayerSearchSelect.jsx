import { useState } from "react";
import PlayerPickerModal from "./PlayerPickerModal";
import PlayerPhoto from "./PlayerPhoto";
import { photoOf } from "../utils/photo";
import { statusBadge } from "../utils/status";

function PlayerSearchSelect({ label, position, excludeId, value, onSelect }) {
  const [modalOpen, setModalOpen] = useState(false);

  if (value) {
    const badge = statusBadge(value);
    return (
      <div className="player-select">
        <span className="player-select-label">{label}</span>
        <div className="player-select-chosen">
          <PlayerPhoto
            className="player-select-chosen-photo"
            name={value.web_name}
            photoUrl={photoOf(value)}
          />
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