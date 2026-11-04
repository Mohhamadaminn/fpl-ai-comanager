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

          <p>
            Compare players. Analyze the numbers. Find your next winning pick.
          </p>

          <button
            className="hero-cta"
            onClick={() => scrollTo("compare")}
          >
            Compare Players
          </button>
        </div>


        <div className="hero-visual" aria-hidden="true">
          <div className="hero-avatar">
            <svg className="hero-crown" viewBox="0 0 64 48">
              <defs>
                <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="#ffe08a" />
                  <stop offset="1" stopColor="#f0a920" />
                </linearGradient>
              </defs>
              <path d="M4 42 L8 12 L22 26 L32 6 L42 26 L56 12 L60 42 Z" fill="url(#gold)" />
              <rect x="4" y="38" width="56" height="7" rx="2" fill="#d98e0f" />
              <circle cx="8" cy="11" r="3.5" fill="#ffe08a" />
              <circle cx="32" cy="5" r="3.5" fill="#ffe08a" />
              <circle cx="56" cy="11" r="3.5" fill="#ffe08a" />
            </svg>
            <img src="/haaland-meditation.png" alt="" className="hero-haaland" />
          </div>
        </div>



      </div>
    </section>
  );
}

export default Hero;