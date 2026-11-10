const FEATURES = [
  "Compare players in the same position",
  "Make smarter short-term and long-term FPL decisions",
  "Get personalized transfer recommendations or second chances for players based on your squad",
];

function TelegramPromo() {
  return (
    <section className="telegram-promo" id="vision">
      <div className="telegram-promo-inner">
        <div className="telegram-promo-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor">
            <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 2a8 8 0 1 1 0 16 8 8 0 0 1 0-16Zm0 2a6 6 0 1 0 0 12 6 6 0 0 0 0-12Zm0 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z" />
          </svg>
        </div>

        <h2>Make Every FPL Decision Count.</h2>

        <p className="telegram-promo-sub">
          From comparing individual players to analyzing your entire squad,
          FPL Vision helps you turn official data into smarter decisions.
        </p>

        <ul className="telegram-promo-features">
          {FEATURES.map((feature) => (
            <li key={feature}>
              <span className="telegram-promo-check">✓</span> {feature}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export default TelegramPromo;