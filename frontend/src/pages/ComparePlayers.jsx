import { useState } from "react";
import PlayerSearchSelect from "../components/PlayerSearchSelect";
import ComparisonRadar from "../components/ComparisonRadar";
import { comparePlayers } from "../api/compare";

const METRIC_LABELS = {
  form: "Form",
  xgi_season: "Season xGI",
  xgi_recent: "Recent xGI",
  fixtures_fdr: "Fixtures",
  minutes_per_game: "Minutes",
  xgc_season: "Defensive Solidity",
};

function ComparePlayers() {
  const [playerA, setPlayerA] = useState(null);
  const [playerB, setPlayerB] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const position = playerA?.position || playerB?.position || null;

  async function handleCompare() {
    setLoading(true);
    setError(null);
    try {
      const data = await comparePlayers(playerA.id, playerB.id);
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
            ? "Ready — hit Compare Players to see the verdict."
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

          <div className="compare-cards">
            {[entryA, entryB].map((entry) => (
              <div
                key={entry.id}
                className={`compare-card ${entry.id === result.winner_id ? "winner" : ""}`}
              >
                {entry.id === result.winner_id && (
                  <span className="winner-badge">Better Pick</span>
                )}
                <h3>{entry.name}</h3>
                <p className="compare-card-meta">
                  {entry.team} · {entry.position} · £{entry.price}m
                </p>
                <div className="compare-score">
                  <span className="compare-score-value">{entry.score}</span>
                  <span className="compare-score-label">score</span>
                </div>
              </div>
            ))}
          </div>

          <div className="compare-chart-wrap">
            <ComparisonRadar playerA={entryA} playerB={entryB} />
          </div>

          <div className="compare-stat-table">
            {Object.keys(entryA.breakdown).map((key) => (
              <div className="compare-stat-row" key={key}>
                <span className="compare-stat-value left">
                  {entryA.breakdown[key].value ?? "—"}
                </span>
                <span className="compare-stat-label">{METRIC_LABELS[key] || key}</span>
                <span className="compare-stat-value right">
                  {entryB.breakdown[key].value ?? "—"}
                </span>
              </div>
            ))}
          </div>

          {result.verdict && (
            <div className="compare-verdict">
              <span className="compare-verdict-label">Verdict</span>
              <p>{result.verdict}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default ComparePlayers;