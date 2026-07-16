import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../state/store";
import type { JobRow } from "../lib/types";

export default function JobListPanel() {
  const filter = useStore((s) => s.filter);
  const setFilter = useStore((s) => s.setFilter);
  const chatJobIds = useStore((s) => s.chatJobIds);
  const chatRunId = useStore((s) => s.chatRunId);
  const setChatResults = useStore((s) => s.setChatResults);
  const refreshKey = useStore((s) => s.refreshKey);

  const [rows, setRows] = useState<JobRow[]>([]);
  const [runIds, setRunIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.runs().then((r) => setRunIds(r.run_ids)).catch(() => {});
  }, []);

  useEffect(() => {
    // An empty chat result means "no jobs matched" — show nothing. Fetching with
    // an empty job_ids would drop the filter and return the whole run instead.
    if (chatJobIds && chatJobIds.length === 0) {
      setRows([]);
      setErr(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setErr(null);
    const params: Record<string, string | number | undefined> = chatJobIds
      ? // Scope must match the one the chat resolved the ids against — /api/jobs
        // ANDs run_id with job_ids, so the panel's own filter would empty the list.
        { job_ids: chatJobIds.join(","), run_id: chatRunId ?? filter.run_id }
      : {
          run_id: filter.run_id,
          min_score: filter.min_score,
          applied: filter.applied === "any" ? undefined : filter.applied === "yes" ? "true" : "false",
          q: filter.q,
          limit: 200,
        };
    api
      .jobs(params)
      .then(setRows)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [filter, chatJobIds, chatRunId, refreshKey]);

  return (
    <div className="panel">
      <div className="panel-head">
        📋 Jobs <span className="sub">{rows.length} shown</span>
        {chatJobIds && (
          <button className="ghost" style={{ marginLeft: "auto" }} onClick={() => setChatResults(null, null)}>
            Showing chat results — clear
          </button>
        )}
      </div>
      <div className="panel-body">
        {!chatJobIds && (
          <div className="filters" style={{ marginBottom: 10 }}>
            <label>Run</label>
            <select value={filter.run_id ?? ""} onChange={(e) => setFilter({ run_id: e.target.value || undefined })}>
              <option value="">latest</option>
              <option value="all">all runs</option>
              {runIds.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
            <label>Min score</label>
            <input
              type="number"
              style={{ width: 70 }}
              value={filter.min_score ?? ""}
              onChange={(e) => setFilter({ min_score: e.target.value ? Number(e.target.value) : undefined })}
            />
            <label>Applied</label>
            <select value={filter.applied} onChange={(e) => setFilter({ applied: e.target.value as any })}>
              <option value="any">any</option>
              <option value="yes">applied</option>
              <option value="no">not applied</option>
            </select>
            <input
              placeholder="search title/company"
              value={filter.q ?? ""}
              onChange={(e) => setFilter({ q: e.target.value || undefined })}
            />
          </div>
        )}

        {loading && <div className="spinner">Loading…</div>}
        {err && <div className="err">{err}</div>}

        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Company</th>
              <th>Run</th>
              <th>Score</th>
              <th>Job</th>
              <th>Resume</th>
              <th>Applied</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.job_id}>
                <td title={r.job_id}>{r.title}</td>
                <td className="muted">{r.company}</td>
                <td className="muted" title={r.run_id ?? undefined}>
                  {r.run_id ? r.run_id.slice(0, 10) : "—"}
                </td>
                <td className="score">{r.relevance_score ?? "—"}</td>
                <td>
                  {r.job_url ? (
                    <a href={r.job_url} target="_blank" rel="noreferrer">open</a>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  {r.resume_available && r.run_id ? (
                    <a href={api.resumeUrl(r.run_id, r.job_id)} target="_blank" rel="noreferrer">PDF</a>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  {r.applied ? (
                    <span className="badge applied">{r.applied_status ?? "applied"}</span>
                  ) : (
                    <span className="badge no">no</span>
                  )}
                </td>
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">No jobs match.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
