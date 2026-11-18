import {
  GITHUB_URL,
  LINKEDIN_URL,
  CONTACT_EMAIL,
} from "../config/links";

function Footer() {
  const links = [
    GITHUB_URL && { label: "GitHub", href: GITHUB_URL },
    LINKEDIN_URL && { label: "LinkedIn", href: LINKEDIN_URL },
    CONTACT_EMAIL && { label: "Email", href: `mailto:${CONTACT_EMAIL}` },
  ].filter(Boolean);

  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <p className="site-footer-desc">
          <strong>FPL Vision</strong> — Make Smarter FPL Decisions.
        </p>

        {links.length > 0 && (
          <div className="site-footer-links">
            {links.map((l) => (
              <a
                key={l.label}
                href={l.href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {l.label}
              </a>
            ))}
          </div>
        )}

        <p className="site-footer-sub">
          Not affiliated with the Premier League or Fantasy Premier League.
        </p>

        <p className="site-footer-disclaimer">
          FPL Vision is an independent, unofficial project. It is not affiliated with, endorsed
          by or connected to the Premier League, Fantasy Premier League or any club or player.
          All names, logos, player photos and trademarks belong to their respective owners.
          Data comes from the official FPL API. Scores and AI-written verdicts are for
          information only; they can be wrong and do not guarantee FPL results.
        </p>
      </div>
    </footer>
  );
}

export default Footer;