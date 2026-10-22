import { useState } from "react";
import InputForm from "./components/InputForm";
import AgentGraph from "./components/AgentGraph";
import MessageLog from "./components/MessageLog";
import Itinerary from "./components/Itinerary";
import useSSE from "./hooks/useSSE";

const STATUS = {
  idle: "Ready to explore",
  running: "Agents collaborating",
  done: "Your day is ready",
  error: "Planning interrupted",
};

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [budget, setBudget] = useState(0);
  const { messages, status, itinerary } = useSSE(sessionId);
  const loading = submitting || status === "running";
  const streamError = messages.findLast(
    (message) => message.message_type === "error",
  )?.content;

  async function handleSubmit(formData) {
    setSubmitting(true);
    setError("");
    setSessionId(null);
    try {
      const response = await fetch("/api/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
        signal: AbortSignal.timeout(20000),
      });
      const data = await response.json();
      if (!response.ok)
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "Check your trip details and try again.",
        );
      if (!data.session_id)
        throw new Error("The server did not return a planning session.");
      setBudget(formData.budget);
      setSessionId(data.session_id);
    } catch (err) {
      setError(
        err.name === "TypeError"
          ? "Cannot reach the planner. Make sure the backend is running."
          : err.message,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">↗</span>
          <div>
            Day, together
            <span className="eyebrow">MULTI-AGENT TRAVEL PLANNER</span>
          </div>
        </div>
        <span className={`connection-status status-${status}`} role="status">
          <i />
          {submitting ? "Starting your trip" : STATUS[status]}
        </span>
      </header>
      <main>
        <div className="intro">
          <span className="eyebrow">ONE CITY. FOUR PERSPECTIVES.</span>
          <h1>A day worth going out for.</h1>
          <p>
            Tell us what you love. Watch your travel team put the pieces
            together.
          </p>
        </div>
        {(error || status === "error") && (
          <div className="error-banner" role="alert">
            {error ||
              streamError ||
              "The live connection was interrupted. Please plan your day again."}
          </div>
        )}
        <div className="workspace">
          <aside>
            <InputForm onSubmit={handleSubmit} loading={loading} />
            <div className="trip-note">
              <span>✦</span>
              <p>
                Four agents, one shared plan.
                <br />
                Transport, dining, and activities work in parallel, with a
                planner to bring it all together.
              </p>
            </div>
          </aside>
          <section
            className="card graph-panel"
            aria-label="Live agent collaboration"
          >
            <div className="panel-heading">
              <div>
                <span className="eyebrow">THE COLLABORATION</span>
                <h2>Your travel team</h2>
              </div>
              <span className="small-tag">4 agents</span>
            </div>
            <div className="graph-canvas">
              <AgentGraph messages={messages} status={status} />
            </div>
            <div className="graph-legend">
              <span>
                <i className="legend-idle" />
                Idle
              </span>
              <span>
                <i className="legend-thinking" />
                Thinking
              </span>
              <span>
                <i className="legend-revising" />
                Revising
              </span>
              <span>
                <i className="legend-done" />
                Done
              </span>
            </div>
          </section>
          <section
            className="card log-panel"
            aria-label="Agent message history"
          >
            <MessageLog messages={messages} />
          </section>
        </div>
        {status === "done" && (
          <Itinerary itinerary={itinerary} budget={budget} />
        )}
        <footer>
          College proof of concept · AI-generated suggestions and USD estimates;
          venues, prices, and availability are unverified.
        </footer>
      </main>
    </div>
  );
}
