import { useState, useEffect, useCallback } from "react";
import { searchPlayers, getTeams } from "../api/compare";
import CustomSelect from "./CustomSelect";

const POSITIONS = [
  { value: "", label: "All" },
  { value: "GKP", label: "GKP" },
  { value: "DEF", label: "DEF" },
  { value: "MID", label: "MID" },
  { value: "FWD", label: "FWD" },
];

const SORTS = [
  { value: "total_points", label: "Total Points" },
  { value: "form", label: "Form" },
  { value: "price", label: "Price (high-low)" },
  { value: "price_asc", label: "Price (low-high)" },
  { value: "selected_by_percent", label: "Ownership" },
  { value: "name", label: "Name" },
];

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

function PlayerPickerModal({ lockedPosition, excludeId, onSelect, onClose }) {
  const [search, setSearch] = useState("");
  const [position, setPosition] = useState(lockedPosition || "");
  const [team, setTeam] = useState("");
  const [sort, setSort] = useState("total_points");
  const [teams, setTeams] = useState([]);
  const [players, setPlayers] = useState([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getTeams().then(setTeams);
  }, []);

  const fetchPlayers = useCallback((targetPage) => {
    setLoading(true);
    searchPlayers({ search, position, team, sort, page: targetPage })
      .then((data) => {
        const filtered = data.results.filter((p) => p.id !== excludeId);
        setPlayers((prev) => (targetPage === 1 ? filtered : [...prev, ...filtered]));
        setHasMore(data.has_more);
        setPage(targetPage);
      })
      .catch((err) => console.error("Player list fetch failed:", err))
      .finally(() => setLoading(false));
  }, [search, position, team, sort, excludeId]);

  useEffect(() => {
    const handle = setTimeout(() => fetchPlayers(1), 250);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, position, team, sort]);

  return (
    <div className="picker-overlay" onClick={onClose}>
      <div className="picker-modal" onClick={(e) => e.stopPropagation()}>
        <div className="picker-header">
          <h3>Select a Player</h3>
          <button className="picker-close" onClick={onClose}>✕</button>
        </div>

        <input
          className="picker-search"
          type="text"
          placeholder="Search by name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />

        <div className="picker-filters">
          <div className="picker-tabs">
            {POSITIONS.map((p) => (
              <button
                key={p.value}
                className={`picker-tab ${position === p.value ? "active" : ""}`}
                onClick={() => !lockedPosition && setPosition(p.value)}
                disabled={!!lockedPosition}
              >
                {p.label}
              </button>
            ))}
          </div>
          
          <CustomSelect
            value={team}
            onChange={setTeam}
            align="right"
            options={[
              { value: "", label: "All Teams" },
              ...teams.map((t) => ({ value: t.id, label: t.short_name })),
            ]}
          />

          <CustomSelect
            value={sort}
            onChange={setSort}
            align="left"
            options={SORTS}
          />

        </div>

        {lockedPosition && (
          <p className="picker-lock-note">Showing {lockedPosition} only, to match your other pick.</p>
        )}

        <ul className="picker-list">
          {players.map((p) => {
            const badge = statusBadge(p);
            return (
              <li key={p.id} onClick={() => onSelect(p)}>
                <div className="picker-list-avatar">{p.web_name.slice(0, 2).toUpperCase()}</div>
                <div className="picker-list-info">
                  <strong>{p.web_name}</strong>
                  <span>{p.team} · {p.position}</span>
                  {badge && (
                    <span
                      className={`player-status-badge player-status-badge--${badge.tone}`}
                      title={p.news || badge.label}
                    >
                      {badge.label}
                    </span>
                  )}
                </div>
                <span className="picker-list-price">£{p.price}m</span>
              </li>
            );
          })}
          {!loading && players.length === 0 && (
            <li className="picker-empty">No players found</li>
          )}
        </ul>

        {hasMore && (
          <button className="picker-load-more" onClick={() => fetchPlayers(page + 1)} disabled={loading}>
            {loading ? "Loading..." : "Load more"}
          </button>
        )}
      </div>
    </div>
  );
}

export default PlayerPickerModal;