import { useState } from "react";
import { scrollToId } from "../utils/scroll";

function Header() {
  const [menuOpen, setMenuOpen] = useState(false);

  const handleNavClick = (id) => {
    setMenuOpen(false);
    scrollToId(id);
  };

  return (
    <header className="site-header">
      <div className="site-header-inner">
        <button className="site-logo" onClick={() => scrollToId("top")}>
          <span className="site-logo-mark">⚡</span> FPL Edge
        </button>

        <nav className={`site-nav ${menuOpen ? "open" : ""}`}>
          <button onClick={() => handleNavClick("top")}>Home</button>
          <button onClick={() => handleNavClick("compare")}>Player Comparison</button>
          <button onClick={() => handleNavClick("telegram")}>Telegram Bot</button>
        </nav>

        <button
          className="site-nav-toggle"
          aria-label="Toggle menu"
          onClick={() => setMenuOpen((v) => !v)}
        >
          <span />
          <span />
          <span />
        </button>
      </div>
    </header>
  );
}

export default Header;