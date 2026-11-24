// Club badge from the official Premier League resources (same host as the player photos).
// `code` is the club's FPL "code" (Team.code), not its FPL id. If the badge pattern ever changes,
// this is the one line to update.
const BADGE_URL = "https://resources.premierleague.com/premierleague/badges/70/t{code}.png";

export function badgeOf(code) {
  return code ? BADGE_URL.replace("{code}", code) : null;
}
