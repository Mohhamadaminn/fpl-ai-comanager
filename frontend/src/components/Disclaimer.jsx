const POINTS = [
  {
    title: "Stats, not orders",
    text: "Everything here is built from official FPL data. A score or an AI-written verdict is one view of the numbers, never an instruction.",
  },
  {
    title: "Not the whole picture",
    text: "Press conferences, rotation, tactics and your own feel for the game don't always show up in the data. Weigh them yourself.",
  },
  {
    title: "You make the call",
    text: "Transfers, captains and chips are your decisions. PuckFPL can't guarantee points or results, and it is not meant to replace your judgement.",
  },
];

function Disclaimer() {
  return (
    <section className="disclaimer" id="disclaimer">
      <div className="disclaimer-inner">
        <img src="/logo.png" alt="PuckFPL" className="disclaimer-logo" />

        <span className="disclaimer-eyebrow">Please read</span>

        <h2>A helper, not a decision-maker.</h2>

        <p className="disclaimer-lead">
          PuckFPL is here to help you see your options more clearly by putting the stats in
          front of you. <strong>It does not replace your decisions</strong>, and it will
          never make them for you.
        </p>

        <ul className="disclaimer-points">
          {POINTS.map((point) => (
            <li key={point.title}>
              <h3>{point.title}</h3>
              <p>{point.text}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export default Disclaimer;
