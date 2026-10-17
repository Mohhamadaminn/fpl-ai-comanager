function scrollTo(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
}

function Hero() {
  return (
    <section className="hero" id="top">
      <div className="hero-inner">
        <div className="hero-content">
          <span className="hero-eyebrow">AI-Powered FPL Analytics</span>
          <h1>Make Smarter FPL Decisions.</h1>
          <p>Compare players. Analyze the numbers. Find your next winning pick.</p>
          <button className="hero-cta" onClick={() => scrollTo("compare")}>
            Compare Players
          </button>
        </div>

        <div className="hero-visual" aria-hidden="true">
          {/*
            TODO: swap this placeholder for a real image, e.g.:
            <img src={haalandImg} alt="" className="hero-visual-img" />
            import haalandImg from "../assets/hero-player.jpg";
          */}
          <div className="hero-visual-placeholder">
            <svg viewBox="0 0 200 200" width="140" height="140">
              <circle cx="100" cy="100" r="95" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.5" />
              <circle cx="100" cy="100" r="60" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.3" />
              <circle cx="100" cy="70" r="22" fill="currentColor" opacity="0.9" />
              <path d="M60 170 Q100 120 140 170" fill="none" stroke="currentColor" strokeWidth="3" opacity="0.9" />
            </svg>
          </div>
        </div>
      </div>
    </section>
  );
}

export default Hero;