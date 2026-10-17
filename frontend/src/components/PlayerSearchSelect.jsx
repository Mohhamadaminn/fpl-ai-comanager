import { useState } from "react";
import PlayerPickerModal from "./PlayerPickerModal";

function PlayerSearchSelect({ label, position, excludeId, value, onSelect }) {
  const [modalOpen, setModalOpen] = useState(false);

  if (value) {
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