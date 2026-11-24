import { useState } from "react";
import { badgeOf } from "../utils/badge";

// Small club badge. Renders nothing when the club has no code yet or the image fails to load,
// so the club name next to it always stays readable.
function TeamBadge({ code, size = 20 }) {
  const [failed, setFailed] = useState(false);
  const src = badgeOf(code);
  if (!src || failed) return null;

  return (
    <img
      className="team-badge"
      src={src}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

export default TeamBadge;
