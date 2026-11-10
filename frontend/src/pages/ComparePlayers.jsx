import { lazy, Suspense, useState } from "react";
import PlayerSearchSelect from "../components/PlayerSearchSelect";
import SquadCheck from "../components/SquadCheck";
import PlayerPhoto from "../components/PlayerPhoto";
import { comparePlayers } from "../api/compare";

// recharts is the bulk of the bundle and is only needed once a comparison is shown.
const ComparisonRadar = lazy(() => import("../components/ComparisonRadar"));

const HORIZONS = [1, 2, 3, 4, 5];

// Shown on the radar only; the stat table below skips it.
const TABLE_HIDDEN_KEYS = ["minutes_per_game"];

function horizonLabel(n) {
  return n === 1 ? "Next gameweek" : `Next ${n} gameweeks`;
}

function fixturesLine(fixtures) {
  if (!fixtures || fixtures.length === 0) return "No fixture";
  return fixtures
    .map((f) => `${f.opponent} (${f.home ? "H" : "A"})`)
    .join(" · ");
}

// Plain-English notes from the API: availability, long absences, just back from injury.
function PlayerNotes({ notes }) {
  if (!notes || notes.length === 0) return null;
  return (
    <ul className="compare-card-notes">
      {notes.map((n) => (
        <li key={n.type} className={`compare-card-note compare-card-note--${n.level}`}>
          {n.text}
        </li>
      ))}
    </ul>
  );
}

function ComparePlayers() {
  const [playerA, setPlayerA] = useState(null);
  const [playerB, setPlayerB] = useState(null);
  const [horizon, setHorizon] = useState(1);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const position = playerA?.position || playerB?.position || null;

  async function handleCompare() {
    setLoading(true);
    setError(null);
    try {
      const data = await comparePlayers(playerA.id, playerB.id, horizon);
      setResult(data);
    } catch (e) {
      setError(e.response?.data?.error || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  const entryA = result?.players[playerA?.id];
  const entryB = result?.players[playerB?.id];

  return (
    <div className="compare-page">
      <header className="compare-header">
        <span className="compare-eyebrow">Player Comparison</span>
        <h2>Who's the better pick?</h2>
        <p>Pick two players in the same position and see who's the better pick right now.</p>
      </header>

      <div className="compare-selectors">
        <PlayerSearchSelect
          label="Player A"
          position={position}
          excludeId={playerB?.id}
          value={playerA}
          onSelect={(p) => { setPlayerA(p); setResult(null); setError(null); }}
        />
        <span className="compare-vs">VS</span>
        <PlayerSearchSelect
          label="Player B"
          position={position}
          excludeId={playerA?.id}
          value={playerB}
          onSelect={(p) => { setPlayerB(p); setResult(null); setError(null); }}
        />
      </div>

      <div className="compare-horizon" role="group" aria-label="Gameweeks to look ahead">
        <span className="compare-horizon-label">Look ahead</span>
        <div className="compare-horizon-options">
          {HORIZONS.map((n) => (
            <button
              key={n}
              type="button"
              className={`compare-horizon-option ${n === horizon ? "active" : ""}`}
              aria-pressed={n === horizon}
              onClick={() => { setHorizon(n); setResult(null); setError(null); }}
            >
              {n}
            </button>
          ))}
        </div>
        <span className="compare-horizon-hint">{horizonLabel(horizon)}</span>
      </div>

      <div className="compare-action">
        <button
          className="compare-button"
          disabled={!playerA || !playerB || loading}
          onClick={handleCompare}
        >
          {loading ? (
            <span className="compare-button-loading">
              <span className="compare-spinner" /> Comparing...
            </span>
          ) : (
            "Compare Players"
          )}
        </button>
      </div>

      {error && <p className="compare-error">{error}</p>}

      {!result && !loading && !error && (playerA || playerB) && (
        <p className="compare-empty-hint">
          {playerA && playerB
            ? "Ready - hit Compare Players to see the verdict."
            : "Pick a second player to compare."}
        </p>
      )}

      {loading && (
        <div className="compare-skeleton">
          <div className="compare-skeleton-card" />
          <div className="compare-skeleton-card" />
        </div>
      )}

      {result && entryA && entryB && (
        <div className="compare-results reveal">
          <span className="compare-gw">{result.gameweek}</span>

          {result.caution && <p className="compare-caution">{result.caution}</p>}

          <div className="compare-cards">
            {[entryA, entryB].map((entry) => {
              // A player who is ruled out is never shown as the better pick.
              const isWinner = entry.id === result.winner_id && entry.recommended !== false;
              return (
                <div
                  key={entry.id}
                  className={`compare-card ${isWinner ? "winner" : ""}`}
                >
                  {isWinner && (
                    <span className="winner-badge">
                      {result.close_call ? "Slight Edge" : "Better Pick"}
                    </span>
                  )}
                  <PlayerPhoto
                    className="compare-card-photo"
                    name={entry.name}
                    photoUrl={entry.photo_url}
                  />
                  <h3>{entry.name}</h3>
                  <p className="compare-card-meta">
                    {entry.team} · {entry.position} · £{entry.price}m
                  </p>
                  {entry.recommended === false && (
                    <span className="player-status-badge player-status-badge--out">
                      Not recommended
                    </span>
                  )}
                  <div className="compare-score">
                    <span className="compare-score-value">{entry.score}</span>
                    <span className="compare-score-label">score</span>
                  </div>
                  <p className="compare-card-fixtures">{fixturesLine(entry.fixtures)}</p>
                  <PlayerNotes notes={entry.notes} />
                </div>
              );
            })}
          </div>

          {result.close_call && (
            <p className="compare-close-note">
              Too close to call: the scores are only {result.margin} points apart.
            </p>
          )}

          <p className="compare-score-note">
            Scores reflect performance only: form (recent games, big scores and the season),
            underlying stats, team strength, upcoming fixtures (home or away) and playing time.
            Price is not included. Injuries and suspensions are shown as notes: a player who is
            ruled out is never recommended.
          </p>

          <div className="compare-chart-wrap">
            <Suspense fallback={null}>
              <ComparisonRadar playerA={entryA} playerB={entryB} />
            </Suspense>
          </div>

          <div className="compare-stat-table">
            {/* Column heads: same names, colours and order as the radar legend (A = green, B = blue). */}
            <div className="compare-stat-row compare-stat-head">
              <span className="compare-stat-value left compare-side-a">
                <i className="compare-dot compare-dot--a" />
                {entryA.name}
              </span>
              <span className="compare-stat-label" />
              <span className="compare-stat-value right compare-side-b">
                {entryB.name}
                <i className="compare-dot compare-dot--b" />
              </span>
            </div>
            {Object.keys(entryA.breakdown)
              .filter((key) => !TABLE_HIDDEN_KEYS.includes(key))
              .map((key) => {
                const a = entryA.breakdown[key];
                const b = entryB.breakdown[key];
                // The better side is decided by the score (what the radar draws), not by the raw
                // number, because for fixtures (FDR) and goals conceded a lower number is better.
                let better;
                if (key === "form") {
                  // Form row shows raw points, so the highlight follows raw points too (per game if the
                  // number of games differs, so 3 games are not compared with 5).
                  const sameGames = a.games === b.games;
                  const va = sameGames ? a.recent_points : a.recent_points_per_game;
                  const vb = sameGames ? b.recent_points : b.recent_points_per_game;
                  better = va == null || vb == null || va === vb ? null : va > vb ? "a" : "b";
                } else {
                  const diff = (a.score ?? 0) - (b.score ?? 0);
                  better = Math.abs(diff) < 0.5 ? null : diff > 0 ? "a" : "b";
                }
                return (
                  <div className="compare-stat-row" key={key}>
                    <span className={`compare-stat-value left ${better === "a" ? "compare-better-a" : ""}`}>
                      {a.display ?? a.value ?? "-"}
                    </span>
                    <span className="compare-stat-label">
                      {a.label || key}
                    </span>
                    <span className={`compare-stat-value right ${better === "b" ? "compare-better-b" : ""}`}>
                      {b.display ?? b.value ?? "-"}
                    </span>
                  </div>
                );
              })}
              {(() => {
                const sa = entryA.season_points;
                const sb = entryB.season_points;
                if (sa == null || sb == null) return null;
                const better = sa === sb ? null : sa > sb ? "a" : "b";
                return (
                  <div className="compare-stat-row">
                    <span className={`compare-stat-value left ${better === "a" ? "compare-better-a" : ""}`}>{sa}</span>
                    <span className="compare-stat-label">Season points</span>
                    <span className={`compare-stat-value right ${better === "b" ? "compare-better-b" : ""}`}>{sb}</span>
                  </div>
                );
              })()}
            <p className="compare-stat-hint">
              The highlighted number is the better one. For fixtures (FDR) and goals conceded, lower is better.
            </p>
          </div>

          {result.verdict && (
            <div className="compare-verdict">
              <span className="compare-verdict-label">Verdict</span>
              <p>{result.verdict}</p>
            </div>
          )}
        </div>
      )}

      <SquadCheck />
    </div>
  );
}

export default ComparePlayers;