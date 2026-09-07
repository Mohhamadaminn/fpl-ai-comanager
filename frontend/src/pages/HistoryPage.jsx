import { useEffect, useState } from "react";
import client from "../api/client";

export default function HistoryPage() {
  const [predictions, setPredictions] = useState([]);
  const [evaluations, setEvaluations] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      client.get("/predictions/"),
      client.get("/evaluations/"),
    ]).then(([predRes, evalRes]) => {
      setPredictions(predRes.data.results);
      setEvaluations(evalRes.data.results);
      setLoading(false);
    });
  }, []);

  if (loading) return <div>Loading...</div>;

  const evalByGameweek = Object.fromEntries(
    evaluations.map((e) => [e.gameweek, e])
  );

  return (
    <div>
      <h1>Prediction History</h1>
      {predictions.map((pred) => {
        const evaluation = evalByGameweek[pred.gameweek];
        return (
          <div key={pred.id} style={{ border: "1px solid #ccc", padding: "1rem", marginBottom: "1rem" }}>
            <h3>{pred.gameweek_name}</h3>
            <p>Captain: {pred.suggested_captain?.web_name ?? "N/A"}</p>
            <p>
              Transfer: {pred.suggested_transfer_out?.web_name ?? "None"} ➜{" "}
              {pred.suggested_transfer_in?.web_name ?? "None"}
            </p>
            <p><em>{pred.reasoning}</em></p>
            {evaluation && (
              <div>
                <strong>Result:</strong> Captain scored {evaluation.ai_captain_points ?? "?"} pts
                {evaluation.ai_was_correct_captain ? " ✅" : " ❌"}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}