import { useEffect, useState } from "react";

function parseMaybeJSON(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export default function useSSE(sessionId) {
  const [messages, setMessages] = useState([]);
  const [status, setStatus] = useState("idle");
  const [itinerary, setItinerary] = useState(null);

  useEffect(() => {
    setMessages([]);
    setItinerary(null);
    if (!sessionId) {
      setStatus("idle");
      return;
    }
    setStatus("running");

    let finished = false;
    const source = new EventSource(`/api/stream/${sessionId}`);

    source.onopen = () => {
      if (!finished) setStatus("running");
    };

    source.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      setMessages((prev) => [...prev, msg]);

      if (msg.message_type === "final_plan") {
        finished = true;
        setItinerary(parseMaybeJSON(msg.content));
        setStatus("done");
        source.close(); // otherwise EventSource auto-reconnects when the server ends the stream
      } else if (msg.message_type === "error") {
        finished = true;
        setStatus("error");
        source.close();
      }
    };

    source.onerror = () => {
      if (finished) return;
      finished = true;
      setStatus("error");
      source.close();
    };

    return () => source.close();
  }, [sessionId]);

  return { messages, status, itinerary };
}
