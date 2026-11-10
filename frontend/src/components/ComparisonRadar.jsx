import { useState } from "react";
import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip,
} from "recharts";

const COLOR_A = "var(--accent)";
const COLOR_B = "#38bdf8";

// Below this chart width the axis labels are wrapped onto two lines and the radar is drawn
// smaller, so labels such as "Fixture Difficulty" are not cut off at the screen edge.
const NARROW_CHART_PX = 440;

function WrappedTick({ x, y, textAnchor, payload }) {
  const words = String(payload.value).split(" ");
  return (
    <text x={x} y={y} textAnchor={textAnchor} fill="var(--text)" fontSize={12}>
      {words.map((word, i) => (
        <tspan key={i} x={x} dy={i === 0 ? `${0.35 - (words.length - 1) * 0.55}em` : "1.1em"}>
          {word}
        </tspan>
      ))}
    </text>
  );
}

// Our own legend instead of recharts' <Legend />: recharts sorts legend items alphabetically by
// default, so the names could end up in the opposite order to the stat table below. Here the order
// is always Player A (left) then Player B (right), the same as the cards and the table.
function RadarLegend({ nameA, nameB }) {
  const item = { display: "inline-flex", alignItems: "center", gap: 6, fontSize: 16 };
  const dot = (color) => ({
    width: 12, height: 12, borderRadius: 2, background: color, display: "inline-block",
  });
  return (
    <div
      style={{ display: "flex", justifyContent: "center", gap: 20, flexWrap: "wrap", marginTop: 4 }}
    >
      <span style={{ ...item, color: COLOR_A }}>
        <i style={dot(COLOR_A)} />
        {nameA}
      </span>
      <span style={{ ...item, color: COLOR_B }}>
        <i style={dot(COLOR_B)} />
        {nameB}
      </span>
    </div>
  );
}

function ComparisonRadar({ playerA, playerB }) {
  const [narrow, setNarrow] = useState(false);
  const keys = Object.keys(playerA.breakdown);

  // Series use fixed keys instead of player names: names can collide (two players
  // with the same web name) or contain dots (e.g. "J.Timber"), which recharts may
  // treat as a nested path when used as a dataKey.
  const sameName = playerA.name === playerB.name;
  const nameA = sameName ? `${playerA.name} (${playerA.team})` : playerA.name;
  const nameB = sameName ? `${playerB.name} (${playerB.team})` : playerB.name;

  const data = keys.map((key) => ({
    metric: playerA.breakdown[key].label || key,
    a: playerA.breakdown[key].score ?? 0,
    b: playerB.breakdown[key].score ?? 0,
  }));

  return (
    <>
      <ResponsiveContainer
        width="100%"
        height={340}
        onResize={(width) => setNarrow(width < NARROW_CHART_PX)}
      >
        <RadarChart data={data} outerRadius={narrow ? "72%" : "80%"}>
          <PolarGrid stroke="var(--border)" />
          <PolarAngleAxis
            dataKey="metric"
            tick={narrow ? WrappedTick : { fill: "var(--text)", fontSize: 13 }}
          />
          <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
          <Radar
            name={nameA}
            dataKey="a"
            stroke={COLOR_A}
            fill={COLOR_A}
            fillOpacity={0.3}
          />
          <Radar
            name={nameB}
            dataKey="b"
            stroke={COLOR_B}
            fill={COLOR_B}
            fillOpacity={0.25}
          />
          <Tooltip />
        </RadarChart>
      </ResponsiveContainer>
      <RadarLegend nameA={nameA} nameB={nameB} />
    </>
  );
}

export default ComparisonRadar;