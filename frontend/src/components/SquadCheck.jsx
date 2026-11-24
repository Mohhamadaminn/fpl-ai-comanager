import { useEffect, useState } from "react";
import { getSquadExpectedPoints, getSquadInsights } from "../api/compare";
import PlayerPhoto from "./PlayerPhoto";
import SquadXp from "./SquadXp";

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

const STRENGTH_LABELS = {
  strong: "Strong case",
  moderate: "Moderate case",
  mild: "Mild · keep an eye on",
};

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


function formatMoney(value) {
  return `£${Number(value).toFixed(1)}m`;
}

// How the price ceiling was reached, in words the person can check against their own squad.
function budgetExplanation(playerName, playerPrice, budget, bank) {
  if (budget == null) {
    return `Replacements are in the same price range as ${playerName}.`;
  }
  if (bank == null) {
    return `Up to ${formatMoney(budget)}: ${playerName}'s price. Your bank balance wasn't available, so nothing extra is added.`;
  }
  return `Up to ${formatMoney(budget)}: ${playerName}'s price (${formatMoney(playerPrice)}) plus ${formatMoney(bank)} in your bank. Pricier players aren't included.`;
}

function ReplacementModal({ player, options, budget, bank, onClose }) {
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div className="picker-overlay" onClick={onClose}>
      <div
        className="picker-modal replacement-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Replacements for ${player.name}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="picker-header">
          <h3>Replacements for {player.name}</h3>
          <button className="picker-close" aria-label="Close" onClick={onClose}>✕</button>
        </div>

        <p className="replacement-modal-note">
          {budgetExplanation(player.name, player.price, budget, bank)}
        </p>

        <ul className="squad-replacement-list replacement-modal-list">
          {options.map((option) => (
            <li key={option.id} className="squad-replacement-item">
              <PlayerPhoto
                className="squad-photo"
                name={option.name}
                photoUrl={option.photo_url}
              />
              <div className="squad-replacement-info">
                <strong>{option.name}</strong>
                <span>
                  {option.team} · {option.position} · £{option.price}m
                </span>
                <div className="squad-chips">
                  <span className="squad-chip">
                    {option.recent_points} pts in {option.games} games
                  </span>
                  {option.next_fixtures && (
                    <span className="squad-chip">Next: {option.next_fixtures}</span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ReplacementOptions({ insight, options }) {
  const [open, setOpen] = useState(false);
  const { player, replacement_budget: budget, bank } = insight;

  if (!options || options.length === 0) {
    if (budget == null) return null;
    return (
      <div className="squad-replacement">
        <p className="squad-replacement-range">
          No clearly better replacement up to {formatMoney(budget)}.
        </p>
      </div>
    );
  }

  return (
    <div className="squad-replacement">
      <button
        type="button"
        className="squad-replacement-toggle"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
      >
        Show Replacements
      </button>
      <span className="squad-replacement-range">
        {budget != null ? `Same price range · up to ${formatMoney(budget)}` : "Same price range"}
      </span>

      {open && (
        <ReplacementModal
          player={player}
          options={options}
          budget={budget}
          bank={bank}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

function SquadCheck() {
  const [team, setTeam] = useState(readSaved);
  const [result, setResult] = useState(null);
  const [xp, setXp] = useState(null);
  const [xpError, setXpError] = useState(null);
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
    setXpError(null);

    // The two parts are independent: if one fails the other is still shown.
    const [insights, expected] = await Promise.allSettled([
      getSquadInsights(trimmed, "en"),
      getSquadExpectedPoints(trimmed),
    ]);

    const messageOf = (outcome) => {
      const message = outcome.reason?.response?.data?.error;
      return typeof message === "string" ? message : "Something went wrong.";
    };

    setResult(insights.status === "fulfilled" ? insights.value : null);
    setXp(expected.status === "fulfilled" ? expected.value : null);
    if (insights.status === "fulfilled" || expected.status === "fulfilled") {
      writeSaved(trimmed);
    }
    if (insights.status === "rejected") {
      setError(messageOf(insights));
    }
    if (expected.status === "rejected" && insights.status === "fulfilled") {
      setXpError(messageOf(expected));
    }
    setLoading(false);
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
    <section className="squad-check" id="squad-check">
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

      {!loading && (xp || xpError) && (
        <div className="squad-results reveal">
          {xp && <SquadXp data={xp} />}
          {xpError && <p className="compare-error">{xpError}</p>}
        </div>
      )}

      {!loading && result && (
        <div className="squad-results reveal">
          {xp && <h3 className="squad-subhead">Players to review</h3>}
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
                  {insight.strength && (
                    <span className={`squad-strength squad-strength--${insight.strength}`}>
                      {STRENGTH_LABELS[insight.strength]}
                    </span>
                  )}
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
                  <ReplacementOptions
                    insight={insight}
                    options={
                      insight.replacement_options?.length
                        ? insight.replacement_options
                        : insight.replacement
                          ? [insight.replacement]
                          : []
                    }
                  />
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