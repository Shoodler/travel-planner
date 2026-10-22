import { useEffect, useRef, useState } from "react";

const ICONS = {
  planner: "🧠",
  transport: "🚌",
  dining: "🍽️",
  activities: "🎯",
  user: "👤",
  system: "⚙️",
};

function prettyJSON(text) {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end <= start) return null;
  try {
    return JSON.stringify(JSON.parse(text.slice(start, end + 1)), null, 2);
  } catch {
    return null;
  }
}

function MessageItem({ msg }) {
  const [open, setOpen] = useState(false);
  const json = prettyJSON(msg.content || "");
  const isStatus = msg.message_type === "status";
  const type = msg.metadata?.revision ? "revised_response" : msg.message_type;
  const time = msg.timestamp
    ? new Date(msg.timestamp).toLocaleTimeString()
    : "";

  return (
    <div className={`log-msg ${isStatus ? "log-status" : ""}`}>
      <div className="log-head">
        <span>
          {ICONS[msg.from_agent] || "•"} {msg.from_agent} →{" "}
          {ICONS[msg.to_agent] || "•"} {msg.to_agent}
        </span>
        <span className="log-time">{time}</span>
      </div>
      <span className={`badge badge-${type}`}>{type}</span>
      {json ? (
        <div className="log-content">
          <button className="link-btn" onClick={() => setOpen(!open)}>
            {open ? "Hide JSON" : "View JSON"}
          </button>
          {open && <pre>{json}</pre>}
        </div>
      ) : (
        <div className="log-content">
          {msg.content.length > 100 && !open ? (
            <>
              {msg.content.slice(0, 100)}…{" "}
              <button className="link-btn" onClick={() => setOpen(true)}>
                more
              </button>
            </>
          ) : (
            msg.content
          )}
        </div>
      )}
    </div>
  );
}

export default function MessageLog({ messages }) {
  const panelRef = useRef(null);

  useEffect(() => {
    const panel = panelRef.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }, [messages.length]);

  return (
    <div className="message-log" ref={panelRef}>
      <h2>Agent messages</h2>
      {messages.length === 0 && (
        <p className="muted">Messages between agents will appear here.</p>
      )}
      {messages.map((m, i) => (
        <MessageItem key={i} msg={m} />
      ))}
    </div>
  );
}
