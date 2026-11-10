const STATUS_BADGES = {
  i: "Injured",
  s: "Suspended",
  u: "Unavailable",
  n: "Not in squad",
  d: "Doubtful",
};

// Badge shown next to a player who is not fully available, or null when he is fine.
export function statusBadge(player) {
  if (player.status && player.status !== "a") {
    return {
      label: STATUS_BADGES[player.status] || "Unavailable",
      tone: player.status === "d" ? "doubtful" : "out",
    };
  }
  const chance = player.chance_of_playing_next_round;
  if (chance !== null && chance !== undefined && chance < 100) {
    return { label: "Doubtful", tone: "doubtful" };
  }
  return null;
}
