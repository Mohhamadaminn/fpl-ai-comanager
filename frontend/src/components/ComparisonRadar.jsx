import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Legend, Tooltip,
} from "recharts";

const METRIC_LABELS = {
  form: "Form",
  xgi_season: "Season xGI",
  xgi_recent: "Recent xGI",
  fixtures_fdr: "Fixtures",
  minutes_per_game: "Minutes",
  xgc_season: "Def. Solidity",
};

function ComparisonRadar({ playerA, playerB }) {
  const keys = Object.keys(playerA.breakdown);

  const data = keys.map((key) => ({
    metric: METRIC_LABELS[key] || key,
    [playerA.name]: playerA.breakdown[key].score ?? 0,
    [playerB.name]: playerB.breakdown[key].score ?? 0,
  }));

  return (
    <ResponsiveContainer width="100%" height={360}>
      <RadarChart data={data}>
        <PolarGrid stroke="var(--border)" />
        <PolarAngleAxis dataKey="metric" tick={{ fill: "var(--text)", fontSize: 13 }} />
        <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
        <Radar
          name={playerA.name}
          dataKey={playerA.name}
          stroke="var(--accent)"
          fill="var(--accent)"
          fillOpacity={0.3}
        />
        <Radar
          name={playerB.name}
          dataKey={playerB.name}
          stroke="#38bdf8"
          fill="#38bdf8"
          fillOpacity={0.25}
        />
        <Legend />
        <Tooltip />
      </RadarChart>
    </ResponsiveContainer>
  );
}

export default ComparisonRadar;