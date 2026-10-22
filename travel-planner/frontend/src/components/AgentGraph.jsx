import { useMemo } from "react";
import { ReactFlow, Background, Controls, MarkerType } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

const AGENTS = {
  planner: {
    label: "🧠 Planner",
    color: "#3b82f6",
    position: { x: 260, y: 0 },
  },
  transport: {
    label: "🚌 Transport",
    color: "#22c55e",
    position: { x: 540, y: 170 },
  },
  dining: { label: "🍽️ Dining", color: "#f97316", position: { x: 20, y: 320 } },
  activities: {
    label: "🎯 Activities",
    color: "#a855f7",
    position: { x: 380, y: 320 },
  },
};
const SPECIALISTS = ["transport", "dining", "activities"];
const STATE_TEXT = {
  idle: "idle",
  thinking: "thinking...",
  done: "✓ done",
  revising: "revising...",
  error: "interrupted",
};

function deriveState(messages) {
  const nodes = Object.fromEntries(Object.keys(AGENTS).map((k) => [k, "idle"]));
  const revising = new Set();
  const edges = {}; // specialist -> { type, toAgent, index }

  messages.forEach((m, index) => {
    const { from_agent: from, to_agent: to, message_type: type } = m;
    if (
      type === "status" &&
      m.metadata?.status === "thinking" &&
      nodes[from] !== undefined
    ) {
      nodes[from] = revising.has(from) ? "revising" : "thinking";
    } else if (type === "response" || type === "revised_response") {
      if (nodes[from] !== undefined) nodes[from] = "done";
      revising.delete(from);
    } else if (type === "revision_request" && nodes[to] !== undefined) {
      nodes[to] = "revising";
      revising.add(to);
    } else if (type === "final_plan") {
      nodes.planner = "done";
    }

    const specialist = SPECIALISTS.includes(to)
      ? to
      : SPECIALISTS.includes(from)
        ? from
        : null;
    if (
      specialist &&
      type !== "status" &&
      (from === "planner" || to === "planner")
    ) {
      edges[specialist] = { type, toAgent: to, index };
    }
  });
  return { nodes, edges, last: messages.length - 1 };
}

export default function AgentGraph({ messages, status }) {
  const { nodes, edges } = useMemo(() => {
    const state = deriveState(messages);
    if (status === "error") {
      Object.keys(state.nodes).forEach((id) => {
        if (["thinking", "revising"].includes(state.nodes[id]))
          state.nodes[id] = "error";
      });
    }

    const flowNodes = Object.entries(AGENTS).map(([id, a]) => {
      const s = state.nodes[id];
      const active = s !== "idle";
      return {
        id,
        position: a.position,
        className: `agent-node agent-${s}`,
        data: {
          label: (
            <div>
              <div className="agent-node-title">{a.label}</div>
              <div className="agent-node-state">{STATE_TEXT[s]}</div>
            </div>
          ),
        },
        style: {
          "--agent-color": a.color,
          background:
            s === "done" ? a.color : active ? `${a.color}33` : "#334155",
          color: active ? "#fff" : "#94a3b8",
          border: `2px solid ${s === "revising" ? "#f97316" : active ? a.color : "#475569"}`,
          borderRadius: 12,
          padding: 12,
          fontSize: 14,
          textAlign: "center",
          width: 170,
        },
      };
    });

    const flowEdges = SPECIALISTS.map((id) => {
      const e = state.edges[id];
      const working = ["thinking", "revising"].includes(state.nodes[id]);
      const recent = e && state.last - e.index < 4;
      const animated = status === "running" && Boolean(working || recent);
      const color =
        e?.type === "revision_request"
          ? "#f97316"
          : animated
            ? AGENTS[id].color
            : "#475569";
      const marker = { type: MarkerType.ArrowClosed, color };
      const towardsAgent = !e || e.toAgent === id;
      return {
        id: `planner-${id}`,
        source: "planner",
        target: id,
        animated,
        label: e?.type,
        labelStyle: { fill: "#e2e8f0", fontSize: 11 },
        labelBgStyle: { fill: "#1e293b" },
        style: { stroke: color, strokeWidth: animated ? 2.5 : 1.5 },
        // Arrow shows message direction: planner -> agent for tasks, agent -> planner for responses
        markerEnd: towardsAgent ? marker : undefined,
        markerStart: towardsAgent ? undefined : marker,
      };
    });

    return { nodes: flowNodes, edges: flowEdges };
  }, [messages, status]);

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
      >
        <Background color="#334155" gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
