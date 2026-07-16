import { useState } from "react";
import PhaseForm from "./config/PhaseForm";
import OrchestratorControl from "./config/OrchestratorControl";

const TABS = ["scraper", "matcher", "funnel", "tuner", "applier", "orchestrator"];

export default function ConfigPanel() {
  const [tab, setTab] = useState("scraper");
  return (
    <div className="panel">
      <div className="panel-head">
        ⚙ Configuration <span className="sub">edit settings & control runs</span>
      </div>
      <div className="panel-body">
        <div className="tabs" style={{ marginBottom: 12 }}>
          {TABS.map((t) => (
            <span key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
              {t}
            </span>
          ))}
        </div>
        {tab === "orchestrator" ? <OrchestratorControl /> : <PhaseForm phase={tab} />}
      </div>
    </div>
  );
}
