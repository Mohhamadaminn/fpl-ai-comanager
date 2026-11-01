import { useEffect, useState } from "react";
import { getSquadInsights } from "../api/compare";
import PlayerPhoto from "./PlayerPhoto";

const STORAGE_KEY = "fpl-squad-check-team";

function readSaved() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function writeSaved(value) {
  try {
    window.localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // Storage can be unavailable (private mode, blocked site data); the page works without it.
  }
}

function StatChips({ insight }) {
  const { stats, player } = insight;
  const isAttacker = player.position === "MID" || player.position === "FWD";

  return (
    <div className="squad-chips">
      <span className="squad-chip">
        {stats.points} pts in last {stats.gameweeks.length} GW
      </span>
      <span className="squad-chip">{stats.season_points} pts this season</span>
      {isAttacker ? (
        <span className="squad-chip">{stats.xgi} xGI</span>
      ) : (
        <span className="squad-chip">{stats.xgc} xGC</span>
      )}
      <span className="squad-chip">{stats.minutes_per_gameweek} min / GW</span>
    </div>
  );
}

function SquadCheck() {
  const [team, setTeam] = useState(readSaved);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function run(value) {
    const trimmed = value.trim();
    if (!trimmed) {
      setError("Paste your FPL team link or ID first.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const data = await getSquadInsights(trimmed, "en");
      setResult(data);
      writeSaved(trimmed);
    } catch (e) {
      const message = e.response?.data?.error;
      setResult(null);
      setError(typeof message === "string" ? message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (team.trim()) run(team);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleSubmit(event) {
    event.preventDefault();
    run(team);
  }

  const windowLabel =
    result && result.window?.length
      ? `Gameweeks ${result.window[0]}-${result.window[result.window.length - 1]}`
      : null;

  return (
    <section className="squad-check">
      <header className="squad-check-header">
        <span className="compare-eyebrow">Squad Check</span>
        <h2>How is your squad doing?</h2>
        <p>
          Paste your official FPL team link or ID to spot who has been unlucky and who might be
          worth replacing.
        </p>
      </header>

      <form className="squad-form" onSubmit={handleSubmit}>
        <input
          className="squad-input"
          type="text"
          value={team}
          onChange={(e) => setTeam(e.target.value)}
          placeholder="https://fantasy.premierleague.com/entry/123456/..."
          aria-label="FPL team link or ID"
        />
        <button className="compare-button" type="submit" disabled={loading}>
          {loading ? (
            <span className="compare-button-loading">
              <span className="compare-spinner" /> Checking...
            </span>
          ) : (
            "Check Squad"
          )}
        </button>
      </form>
      <p className="squad-hint">
        Open your team on the official FPL site: the number after /entry/ in the address is your
        team ID.
      </p>

      {error && <p className="compare-error">{error}</p>}

      {loading && (
        <div className="compare-skeleton">
          <div className="compare-skeleton-card" />
          <div className="compare-skeleton-card" />
        </div>
      )}

      {!loading && result && (
        <div className="squad-results reveal">
          <p className="squad-meta">
            {result.squad_gameweek} squad
            {windowLabel ? ` · based on ${windowLabel}` : ""}
          </p>

          {result.insights.length === 0 ? (
            <p className="compare-empty-hint">
              No standout players in your starting XI right now. Nobody looks unlucky or worth
              replacing.
            </p>
          ) : (
            <div className="squad-cards">
              {result.insights.map((insight) => (
                <article
                  key={insight.type}
                  className={`squad-card ${insight.type === "another_chance" ? "chance" : "move"}`}
                >
                  <span className="squad-card-title">{insight.title}</span>
                  <div className="squad-card-player">
                    <PlayerPhoto
                      className="squad-photo"
                      name={insight.player.name}
                      photoUrl={insight.player.photo_url}
                    />
                    <div className="squad-card-info">
                      <strong>{insight.player.name}</strong>
                      <span>
                        {insight.player.team} · {insight.player.position} · £
                        {insight.player.price}m
                      </span>
                    </div>
                  </div>
                  <p className="squad-card-reason">{insight.reason}</p>
                  <StatChips insight={insight} />
                </article>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default SquadCheck;