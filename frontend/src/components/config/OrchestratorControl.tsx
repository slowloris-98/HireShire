import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { RunState } from "../../lib/types";

const PHASES = ["orchestrator", "scraper", "matcher", "tuner", "applier"];

export default function OrchestratorControl() {
  const [states, setStates] = useState<Record<string, RunState>>({});
  const [logPhase, setLogPhase] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const poll = () =>
    api.runStatus().then((list) => {
      const map: Record<string, RunState> = {};
      for (const s of list) map[s.phase] = s;
      setStates(map);
    }).catch(() => {});

  useEffect(() => {
    poll();
    const t = setInterval(poll, 3000);
    return () => clearInterval(t);
  }, []);

  function openLog(phase: string) {
    esRef.current?.close();
    setLog("");
    setLogPhase(phase);
    const es = new EventSource(`/api/runs/${phase}/logs`);
    es.addEventListener("log", (e) => setLog((l) => l + (e as MessageEvent).data + "\n"));
    es.onerror = () => {};
    esRef.current = es;
  }

  useEffect(() => () => esRef.current?.close(), []);

  async function start(phase: string) {
    setErr(null);
    const flags = phase === "orchestrator" ? { once: true } : phase === "applier" ? { dry_run: true } : {};
    try {
      await api.startRun(phase, flags);
      poll();
      openLog(phase);
    } catch (e) {
      setErr(String(e));
    }
  }

  async function stop(phase: string) {
    setErr(null);
    try { await api.stopRun(phase); poll(); } catch (e) { setErr(String(e)); }
  }

  return (
    <div>
      {PHASES.map((p) => {
        const s = states[p];
        const running = s?.running;
        return (
          <div className="field" key={p}>
            <div className="row">
              <span className="name" style={{ width: 110 }}>{p}</span>
              <span className={`pill ${running ? "run" : "stop"}`}>{running ? "running" : "stopped"}</span>
              {running ? (
                <button className="danger" onClick={() => stop(p)}>Stop</button>
              ) : (
                <button onClick={() => start(p)}>Run now</button>
              )}
              <button className="ghost" onClick={() => openLog(p)}>Logs</button>
              {s?.pid && <span className="muted">pid {s.pid}</span>}
              {!running && s?.last_exit != null && (
                <span className="muted">exit {s.last_exit}</span>
              )}
            </div>
          </div>
        );
      })}
      <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
        Orchestrator runs once (scraper→matcher→tuner). Tuner/applier gates come from their config
        (enable_tuner / enable_applier). Applier runs in dry-run from here.
      </div>
      {err && <div className="err">{err}</div>}
      {logPhase && (
        <>
          <div className="muted" style={{ marginBottom: 4 }}>{logPhase} log</div>
          <div className="log">{log || "waiting for output…"}</div>
        </>
      )}
    </div>
  );
}
