import apiClient from "./client";

export async function searchPlayers({ search, position, team, sort, page = 1 }) {
  const params = { page };
  if (search) params.search = search;
  if (position) params.position = position;
  if (team) params.team = team;
  if (sort) params.sort = sort;
  const { data } = await apiClient.get("/players/", { params });
  return data;
}

export async function getTeams() {
  const { data } = await apiClient.get("/teams/");
  return data;
}

export async function comparePlayers(playerAId, playerBId, lang = "en") {
  const { data } = await apiClient.post("/compare-players/", {
    player_a_id: playerAId,
    player_b_id: playerBId,
    lang,
  });
  return data;
}