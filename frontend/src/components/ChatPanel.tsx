import { useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { streamPost } from "../lib/sse";
import { api } from "../lib/api";
import { useStore } from "../state/store";
import type { RunProposal } from "../lib/types";

interface Msg {
  role: "user" | "assistant";
  content: string;
  tools: string[];
  proposal?: RunProposal;
}

// Agent links point at external job boards.
const mdComponents: Components = {
  a: ({ children, ...props }) => (
    <a {...props} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
};

export default function ChatPanel() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const setChatJobIds = useStore((s) => s.setChatJobIds);
  const refresh = useStore((s) => s.refresh);
  const bodyRef = useRef<HTMLDivElement>(null);

  const scroll = () =>
    requestAnimationFrame(() => {
      if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    });

  const patchLast = (fn: (m: Msg) => Msg) => {
    setMessages((ms) => {
      const copy = ms.slice();
      copy[copy.length - 1] = fn(copy[copy.length - 1]);
      return copy;
    });
    scroll();
  };

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setInput("");
    setBusy(true);
    setMessages((ms) => [
      ...ms,
      { role: "user", content: text, tools: [] },
      { role: "assistant", content: "", tools: [] },
    ]);
    scroll();

    try {
      await streamPost(
        "/api/chat",
        { message: text, history },
        {
          onEvent: (event, data) => {
            if (event === "token") {
              patchLast((m) => ({ ...m, content: m.content + data }));
            } else if (event === "tool_call") {
              try {
                const { name } = JSON.parse(data);
                patchLast((m) => ({ ...m, tools: [...m.tools, name] }));
              } catch {}
            } else if (event === "job_results") {
              try {
                const { job_ids } = JSON.parse(data);
                setChatJobIds(job_ids as string[]);
                refresh();
              } catch {}
            } else if (event === "run_proposal") {
              try {
                patchLast((m) => ({ ...m, proposal: JSON.parse(data) as RunProposal }));
              } catch {}
            } else if (event === "error") {
              patchLast((m) => ({ ...m, content: m.content + `\n\n[error: ${data}]` }));
            }
          },
        },
      );
    } catch (e) {
      patchLast((m) => ({ ...m, content: m.content + `\n\n[connection error]` }));
    } finally {
      setBusy(false);
    }
  }

  async function confirmProposal(p: RunProposal) {
    try {
      if (p.action === "run") await api.startRun(p.phase, p.flags);
      else await api.stopRun(p.phase);
      patchLast((m) => ({
        ...m,
        content: m.content + `\n\n[${p.action === "run" ? "Started" : "Stopped"} ${p.phase}]`,
        proposal: undefined,
      }));
    } catch (e) {
      patchLast((m) => ({ ...m, content: m.content + `\n\n[failed: ${e}]`, proposal: undefined }));
    }
  }

  return (
    <div className="panel chat">
      <div className="panel-head">
        💬 Chat with your data <span className="sub">ask about jobs, runs, and settings</span>
      </div>
      <div className="panel-body" ref={bodyRef}>
        {messages.length === 0 && (
          <div className="muted">
            Try: “top 10 matches from the latest run”, “what does the funnel threshold mean?”,
            or “run the scraper”.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="role">{m.role}</div>
            {m.tools.map((t, k) => (
              <span key={k} className="toolchip">⚙ {t}</span>
            ))}
            <div className="bubble">
              {m.role === "assistant" && m.content ? (
                <Markdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                  {m.content}
                </Markdown>
              ) : (
                m.content || (busy && i === messages.length - 1 ? "…" : "")
              )}
            </div>
            {m.proposal && (
              <div className="proposal">
                <div className="title">
                  Confirm: {m.proposal.action} {m.proposal.phase}
                </div>
                <div className="muted" style={{ marginBottom: 8 }}>
                  {JSON.stringify(m.proposal.flags)}
                </div>
                <button onClick={() => confirmProposal(m.proposal!)}>Confirm</button>{" "}
                <button className="ghost" onClick={() => patchLast((x) => ({ ...x, proposal: undefined }))}>
                  Cancel
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="chat-input">
        <textarea
          rows={2}
          value={input}
          placeholder="Ask something…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button onClick={send} disabled={busy}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
