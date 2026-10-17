import { TELEGRAM_BOT_URL } from "../config/links";

const FEATURES = [
  "AI-powered captain & transfer suggestions each gameweek",
  "See which of your players over- or underperformed, with reasons",
  "Low-ownership differential picks worth watching",
  "Your squad's upcoming fixtures with difficulty ratings",
  "Deadline reminders and weekly squad health checks",
  "Available in English and Persian",
];

function TelegramPromo() {
  return (
    <section className="telegram-promo" id="telegram">
      <div className="telegram-promo-inner">
        <div className="telegram-promo-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor">
            <path d="M21.5 4.5 2.7 11.9c-1.3.5-1.3 1.2-.2 1.6l4.8 1.5 1.8 5.6c.2.6.4.8.9.8.4 0 .6-.2.9-.5l2.2-2.1 4.6 3.4c.8.5 1.4.2 1.6-.8l3-14c.3-1.3-.5-1.9-1.8-1.4Zm-3.3 3.6-8 7.3-.3 3-1.5-4.9 9.4-5.9c.4-.3.8.1.4.5Z" />
          </svg>
        </div>
        <h2>Take FPL Edge with You, on Telegram</h2>
        <p className="telegram-promo-sub">
          Everything you see here, plus AI predictions and squad tracking, right in your chat.
        </p>

        <ul className="telegram-promo-features">
          {FEATURES.map((f) => (
            <li key={f}>
              <span className="telegram-promo-check">✓</span> {f}
            </li>
          ))}
        </ul>

        {TELEGRAM_BOT_URL ? (
          <a
            className="telegram-promo-cta"
            href={TELEGRAM_BOT_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open the Telegram Bot
          </a>
        ) : (
          <p className="telegram-promo-pending">Bot link coming soon.</p>
        )}
      </div>
    </section>
  );
}

export default TelegramPromo;