import { useState } from "react";

// Official player photo with an initials fallback (missing code, blocked or broken image).
function PlayerPhoto({ name, photoUrl, className = "" }) {
  const [failed, setFailed] = useState(false);

  if (!photoUrl || failed) {
    return (
      <div className={`${className} player-photo-fallback`}>
        {name.slice(0, 2).toUpperCase()}
      </div>
    );
  }

  return (
    <img
      className={className}
      src={photoUrl}
      alt={name}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

export default PlayerPhoto;