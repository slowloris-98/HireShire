// Minimal SSE-over-POST reader: parses `event:`/`data:` frames from a streamed
// fetch response (EventSource only supports GET, so the chat endpoint needs this).

export interface SSEHandlers {
  onEvent: (event: string, data: string) => void;
  signal?: AbortSignal;
}

export async function streamPost(
  url: string,
  body: unknown,
  handlers: SSEHandlers,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: handlers.signal,
  });
  if (!res.body) throw new Error("No response body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // sse-starlette terminates lines with CRLF; strip CR so frame/line splits
    // on "\n\n" / "\n" work regardless of the server's newline style.
    buffer += decoder.decode(value, { stream: true }).replace(/\r/g, "");

    // SSE frames are separated by a blank line.
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      }
      handlers.onEvent(event, dataLines.join("\n"));
    }
  }
}
