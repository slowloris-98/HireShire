import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { ConfigResponse } from "../../lib/types";

function TagEditor({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState("");
  return (
    <div>
      <div className="taglist">
        {value.map((t, i) => (
          <span key={i} className="tag">
            {t}
            <button onClick={() => onChange(value.filter((_, k) => k !== i))}>×</button>
          </span>
        ))}
      </div>
      <input
        style={{ marginTop: 4 }}
        placeholder="add + Enter"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && draft.trim()) {
            e.preventDefault();
            onChange([...value, draft.trim()]);
            setDraft("");
          }
        }}
      />
    </div>
  );
}

export default function PhaseForm({ phase }: { phase: string }) {
  const [cfg, setCfg] = useState<ConfigResponse | null>(null);
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    setErr(null);
    api.getConfig(phase).then((c) => { setCfg(c); setEdits({}); }).catch((e) => setErr(String(e)));
  };
  useEffect(load, [phase]);

  if (err) return <div className="err">{err}</div>;
  if (!cfg) return <div className="spinner">Loading…</div>;

  const val = (k: string) => (k in edits ? edits[k] : cfg.values[k]);
  const set = (k: string, v: unknown) => setEdits((e) => ({ ...e, [k]: v }));
  const dirty = Object.keys(edits).length > 0;

  async function save() {
    setStatus(null);
    setErr(null);
    try {
      const res = await api.putConfig(phase, edits);
      setCfg((c) => (c ? { ...c, values: { ...c.values, ...res.values } } : c));
      setEdits({});
      setStatus("Saved ✓");
      setTimeout(() => setStatus(null), 2000);
    } catch (e) {
      setErr(String(e));
    }
  }

  return (
    <div>
      {Object.keys(cfg.values).map((k) => {
        const type = cfg.types[k];
        const v = val(k);
        return (
          <div className="field" key={k}>
            <div className="row">
              <span className="name">{k}</span>
              {type === "bool" && (
                <input type="checkbox" checked={!!v} onChange={(e) => set(k, e.target.checked)} />
              )}
              {(type === "int" || type === "float") && (
                <input
                  type="number"
                  step={type === "float" ? "0.01" : "1"}
                  style={{ width: 110 }}
                  value={v === null || v === undefined ? "" : (v as number)}
                  onChange={(e) => set(k, e.target.value === "" ? null : Number(e.target.value))}
                />
              )}
              {type === "str" && (
                <input
                  style={{ flex: 1 }}
                  value={(v as string) ?? ""}
                  onChange={(e) => set(k, e.target.value)}
                />
              )}
              {type === "enum" && (
                <select value={(v as string) ?? ""} onChange={(e) => set(k, e.target.value || null)}>
                  <option value="">(none)</option>
                  {(cfg.options[k] || []).map((o) => (
                    <option key={o} value={o}>{o}</option>
                  ))}
                </select>
              )}
            </div>
            {type === "str_list" && (
              <TagEditor value={(v as string[]) || []} onChange={(nv) => set(k, nv)} />
            )}
            <div className="doc">{cfg.docs[k]}</div>
          </div>
        );
      })}
      <div className="row" style={{ gap: 10, marginTop: 8 }}>
        <button onClick={save} disabled={!dirty}>Save</button>
        <button className="ghost" onClick={load} disabled={!dirty}>Reset</button>
        {status && <span style={{ color: "var(--good)" }}>{status}</span>}
        {err && <span className="err">{err}</span>}
      </div>
    </div>
  );
}
