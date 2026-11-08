const PHOTO_URL = "https://resources.premierleague.com/premierleague25/photos/players/110x140/{code}.png";

export function photoOf(p) {
  if (p.photo_url) return p.photo_url;
  return p.code ? PHOTO_URL.replace("{code}", p.code) : null;
}