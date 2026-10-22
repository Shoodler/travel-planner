# 🗺️ Multi-Agent Travel Day Planner — Implementation Instructions

> **Hand this document to a coding agent. It contains everything needed to build the project from scratch.**

---

## 1. Project Overview

Build a **Multi-Agent Travel Day Planner** — a web app where the user enters a **city**, **interests**, and **budget**, and 4 AI agents collaborate to build a full day itinerary. The main output is a **visual UI** that demonstrates multi-agent communication in real-time.

**How it works:**
1. User submits input
2. **Planner Agent** (orchestrator) splits the budget and dispatches tasks
3. **Transport**, **Dining**, and **Activities** agents work **in parallel**, each calling Groq API
4. Planner collects results, checks total cost against budget
5. If over budget → Planner sends a **revision request** to the most expensive agent (re-negotiation loop)
6. Planner assembles a chronological **final itinerary**
7. Every inter-agent message is streamed to the frontend via **SSE** and shown in a **live animated graph + message log**

**Key patterns demonstrated:** parallel dispatch, fan-out/fan-in aggregation, re-negotiation feedback loop, event-driven streaming.

---

## 2. Tech Stack

| Layer | Technology | Version / Notes |
|---|---|---|
| Backend | **FastAPI** (Python) | `fastapi==0.115.0` |
| Server | **Uvicorn** | `uvicorn==0.31.0` |
| LLM API | **Groq SDK** | `groq==0.11.0` — use `AsyncGroq` client |
| LLM Model | `llama-3.1-8b-instant` | Fastest model on Groq free tier |
| Validation | **Pydantic** | `pydantic==2.9.0` |
| Env vars | **python-dotenv** | `python-dotenv==1.0.1` |
| Frontend | **React** (with Vite) | Latest stable |
| Graph UI | **@xyflow/react** | React Flow v12 — node-edge graph visualization |
| Real-time | **Server-Sent Events (SSE)** | Native `EventSource` API on frontend, `StreamingResponse` on backend |

---

## 3. File Structure

```
d:\Programming\Project\travel-planner\
├── backend\
│   ├── main.py              # FastAPI app, SSE endpoint, /plan endpoint
│   ├── agents.py            # Agent class + 4 agent definitions with system prompts
│   ├── orchestrator.py      # Orchestration flow logic (the step-by-step pipeline)
│   ├── message_bus.py       # Communication layer (message routing + SSE broadcasting)
│   ├── models.py            # Pydantic models (AgentMessage, UserInput, Itinerary)
│   └── requirements.txt     # Python dependencies
│
├── frontend\
│   ├── index.html           # Vite HTML entry
│   ├── package.json         # React + @xyflow/react dependencies
│   ├── vite.config.js       # Vite config with proxy to backend
│   └── src\
│       ├── App.jsx           # Main layout: input form + graph + sidebar + itinerary
│       ├── main.jsx          # React entry point
│       ├── components\
│       │   ├── InputForm.jsx     # City, interests, budget form
│       │   ├── AgentGraph.jsx    # Live animated node-edge graph (React Flow)
│       │   ├── MessageLog.jsx    # Scrolling chat log of all agent messages
│       │   └── Itinerary.jsx     # Final day plan card
│       ├── hooks\
│       │   └── useSSE.js         # Custom hook to consume SSE stream
│       └── styles\
│           └── app.css           # All styling
│
├── .env                      # GROQ_API_KEY=gsk_...
└── README.md                 # Setup + run instructions
```

**Total: 16 files. Do NOT create any additional files.**

---

## 4. Backend Specifications

### 4.1 `backend/models.py`

Define these Pydantic models:

**`UserInput`**
| Field | Type | Description |
|---|---|---|
| `city` | `str` | City name |
| `interests` | `list[str]` | User interests (e.g., ["street food", "history"]) |
| `budget` | `float` | Total budget in USD |

**`AgentMessage`** — the core message schema used for ALL inter-agent communication
| Field | Type | Description |
|---|---|---|
| `from_agent` | `str` | Sender name: `"user"`, `"planner"`, `"transport"`, `"dining"`, `"activities"`, or `"system"` |
| `to_agent` | `str` | Recipient name (same set as above) |
| `message_type` | `str` | One of: `"task_assignment"`, `"response"`, `"revision_request"`, `"revised_response"`, `"final_plan"`, `"status"`, `"error"` |
| `content` | `str` | The actual message content (text or JSON string) |
| `metadata` | `dict` | Optional metadata (e.g., `{"budget": 50, "step": 2, "status": "thinking"}`) — default `{}` |
| `timestamp` | `str` | ISO 8601 timestamp — auto-set in `model_post_init` if empty |

**`ItineraryItem`**
| Field | Type | Description |
|---|---|---|
| `time` | `str` | Time slot (e.g., "09:00") |
| `title` | `str` | Item name |
| `description` | `str` | Brief description |
| `cost` | `float` | Cost in USD |
| `category` | `str` | One of: `"transport"`, `"dining"`, `"activity"` |

**`Itinerary`**
| Field | Type | Description |
|---|---|---|
| `city` | `str` | City name |
| `items` | `list[ItineraryItem]` | Ordered timeline items |
| `total_cost` | `float` | Sum of all costs |
| `summary` | `str` | 1-2 sentence summary |

---

### 4.2 `backend/message_bus.py` — Communication Layer

This is the **central communication hub**. All agent messages flow through it, and it broadcasts every message to SSE subscribers.

**Class: `MessageBus`**

| Method | Signature | Description |
|---|---|---|
| `__init__` | `()` | Initialize `self.log: list[AgentMessage] = []` and `self.subscribers: list[asyncio.Queue] = []` |
| `send` | `async (message: AgentMessage)` | Append to `self.log`, then `await queue.put(message)` for every subscriber queue |
| `subscribe` | `() -> asyncio.Queue` | Create a new `asyncio.Queue`, append to `self.subscribers`, return it |
| `unsubscribe` | `(queue: asyncio.Queue)` | Remove queue from `self.subscribers` |
| `get_log` | `() -> list[AgentMessage]` | Return full message history |
| `clear` | `()` | Reset `self.log` to empty list |

---

### 4.3 `backend/agents.py` — Agent Layer

**Class: `Agent`**

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Agent identifier (e.g., `"dining"`) |
| `system_prompt` | `str` | Role-specific prompt for the LLM |
| `client` | `AsyncGroq` | Shared Groq async client |
| `bus` | `MessageBus` | Shared message bus |

**Method: `run(self, task: str, from_agent: str = "planner", metadata: dict = {}) -> str`**

Implementation steps:
1. Send a **status message** via bus: `from_agent=self.name, to_agent="system", message_type="status", content=f"{self.name} is thinking...", metadata={"status": "thinking"}`
2. `await asyncio.sleep(1)` — small delay to respect Groq rate limits
3. Call Groq: `await self.client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": self.system_prompt}, {"role": "user", "content": task}], temperature=0.7, max_tokens=1024)`
4. Extract `result = response.choices[0].message.content`
5. Send a **response message** via bus: `from_agent=self.name, to_agent=from_agent, message_type="response", content=result, metadata=metadata`
6. Return `result`

**Factory function: `create_agents(client: AsyncGroq, bus: MessageBus) -> dict[str, Agent]`**

Creates and returns a dict with 4 agents. Their **full system prompts**:

#### Planner Agent (`"planner"`)
```
You are the Planner Agent in a multi-agent travel planning system. Your job is to:
1. Take user input (city, interests, budget) and split the budget across transport (~20%), dining (~35%), and activities (~45%).
2. Review collected suggestions from other agents and assemble a chronological day timeline.
3. If total cost exceeds budget, identify which agent to ask for cheaper alternatives.

When splitting budget, respond with ONLY valid JSON:
{"transport": <amount>, "dining": <amount>, "activities": <amount>}

When assembling the final plan, respond with ONLY valid JSON:
{"items": [{"time": "HH:MM", "title": "...", "description": "...", "cost": <number>, "category": "transport|dining|activity"}], "total_cost": <number>, "summary": "A brief 1-2 sentence summary of the day"}

When requesting revision, respond with plain text explaining what to change and by how much.
Always ensure the total does not exceed the user's budget.
```

#### Transport Agent (`"transport"`)
```
You are a Transport Agent in a multi-agent travel planning system. Given a city and a transport budget, suggest realistic local transportation options for a full day of exploring. Include options like metro/subway day passes, bus passes, ride-sharing estimates, bicycle rentals, or walking routes.

You MUST respond with ONLY valid JSON in this exact format:
{"suggestions": [{"name": "...", "cost": <number>, "description": "..."}], "total_cost": <number>}

Keep total_cost within the given budget. Be realistic with pricing for the specific city.
```

#### Dining Agent (`"dining"`)
```
You are a Dining Agent in a multi-agent travel planning system. Given a city, food interests/preferences, and a dining budget, suggest breakfast, lunch, and dinner options that match the interests and fit within the budget. Include realistic restaurant names and local cuisine suggestions.

You MUST respond with ONLY valid JSON in this exact format:
{"meals": [{"meal": "breakfast|lunch|dinner", "restaurant": "...", "cuisine": "...", "cost": <number>, "time": "HH:MM", "description": "..."}], "total_cost": <number>}

Keep total_cost within the given budget. Be realistic with pricing for the specific city.
```

#### Activities Agent (`"activities"`)
```
You are an Activities Agent in a multi-agent travel planning system. Given a city, interests, and an activities budget, suggest 3-5 sightseeing activities, experiences, or attractions for a day trip. Include realistic attraction names, entry fees, and suggested durations.

You MUST respond with ONLY valid JSON in this exact format:
{"activities": [{"name": "...", "cost": <number>, "duration_hours": <number>, "description": "..."}], "total_cost": <number>}

Keep total_cost within the given budget. Be realistic with pricing for the specific city.
```

---

### 4.4 `backend/orchestrator.py` — Orchestration Layer

**Class: `Orchestrator`**

| Field | Type |
|---|---|
| `agents` | `dict[str, Agent]` |
| `bus` | `MessageBus` |

**Method: `run(self, user_input: UserInput) -> str`**

This is the core workflow. Implement it step by step:

**Step 1 — Planner splits budget:**
- Send an `AgentMessage` via bus: `from_agent="user", to_agent="planner", message_type="task_assignment"` with content describing the city, budget, and interests
- Call `planner.run()` with a prompt asking to split the budget
- Parse the JSON response to get `{"transport": X, "dining": Y, "activities": Z}`
- If JSON parsing fails, use default split: transport=20%, dining=35%, activities=45% of total budget

**Step 2 — Parallel dispatch to specialist agents:**
- Send 3 `task_assignment` messages via bus (planner → transport, planner → dining, planner → activities) each containing the task description and allocated budget
- Use `asyncio.gather()` to run all 3 agents in parallel
- Each agent's task prompt should include: city, their allocated budget, and relevant interests

**Step 3 — Check total cost:**
- Parse JSON from each agent's response (use a `_parse_json` helper that tries `json.loads()`, and if that fails, tries to find `{...}` within the text)
- Sum up `total_cost` from each agent's response

**Step 4 — Re-negotiate if over budget (>10% tolerance):**
- Identify the most expensive category
- Calculate the overage amount
- Send a `revision_request` message via bus from planner to that agent
- Call that agent's `run()` with a prompt explaining the overage and asking for cheaper alternatives (include their original suggestions in the prompt so they have context)
- The agent responds with `revised_response` (message type is just "response" from the agent, the bus message sent by orchestrator is the `revision_request`)
- Update that category's result with the revised response

**Step 5 — Planner assembles final itinerary:**
- Send a status message: planner is assembling the final itinerary
- Call `planner.run()` with ALL three agents' results, asking it to create a chronological timeline from 8:00 AM to 10:00 PM

**Step 6 — Broadcast final plan:**
- Send a `final_plan` message via bus: `from_agent="planner", to_agent="user"`
- Return the final result

**Helper: `_parse_json(self, text: str) -> dict`**
- Try `json.loads(text)`
- If fails, find first `{` and last `}` in text, try parsing that substring
- If all fails, return `{}`

---

### 4.5 `backend/main.py` — FastAPI Application

**Setup:**
- Load `.env` with `load_dotenv()`
- Global `sessions: dict[str, MessageBus] = {}`
- Add CORS middleware allowing all origins
- Title: `"Multi-Agent Travel Planner"`

**Endpoints:**

#### `POST /api/plan`
- Accepts `UserInput` body
- Generates a `session_id` (UUID4)
- Creates a new `MessageBus`, stores in `sessions[session_id]`
- Creates `AsyncGroq` client from `os.getenv("GROQ_API_KEY")`
- Creates agents via `create_agents(client, bus)`
- Creates `Orchestrator(agents, bus)`
- Launches `orchestrator.run(user_input)` as a **background async task** (`asyncio.create_task`)
- Wrap the background task in try/except — on error, send an `error` type message via bus
- Returns `{"session_id": session_id}`

#### `GET /api/stream/{session_id}`
- If session_id not in sessions, return error
- Subscribe to the session's MessageBus
- Return a `StreamingResponse` with `media_type="text/event-stream"` and headers `Cache-Control: no-cache`, `Connection: keep-alive`
- The generator:
  - Loops forever, awaiting messages from the queue with a 120-second timeout
  - On message: yield `f"data: {message.model_dump_json()}\n\n"`
  - On timeout: yield `f": keepalive\n\n"` (SSE comment for keepalive)
  - If `message.message_type` is `"final_plan"` or `"error"`, break the loop
  - In `finally` block, unsubscribe the queue

#### `GET /api/health`
- Returns `{"status": "ok"}`

---

### 4.6 `backend/requirements.txt`

```
fastapi==0.115.0
uvicorn==0.31.0
groq==0.11.0
pydantic==2.9.0
python-dotenv==1.0.1
```

---

## 5. Frontend Specifications

### 5.1 `frontend/package.json`

Dependencies:
- `react`, `react-dom` (latest)
- `@xyflow/react` (latest — this is React Flow v12)

Dev dependencies:
- `vite`, `@vitejs/plugin-react`

Scripts:
- `dev`: `vite`
- `build`: `vite build`

### 5.2 `frontend/vite.config.js`

- Use `@vitejs/plugin-react`
- Add proxy: `/api` → `http://localhost:8000` (so frontend can call backend without CORS issues in dev)

### 5.3 `frontend/index.html`

Standard Vite HTML entry loading `src/main.jsx`. Title: "Multi-Agent Travel Day Planner".

### 5.4 `frontend/src/main.jsx`

Standard React 18 entry: `createRoot` + render `<App />`.

---

### 5.5 `frontend/src/hooks/useSSE.js`

**Custom hook: `useSSE(sessionId)`**

Returns: `{ messages, status, itinerary }`

| State | Type | Description |
|---|---|---|
| `messages` | `AgentMessage[]` | Array of all received messages |
| `status` | `string` | `"idle"`, `"running"`, `"done"`, `"error"` |
| `itinerary` | `object \| null` | Parsed final plan (set when `message_type === "final_plan"`) |

Implementation:
- When `sessionId` changes (and is truthy), create `new EventSource(\`/api/stream/\${sessionId}\`)`
- Set `status = "running"` on open
- On message: parse JSON, append to messages array
  - If `message_type === "final_plan"`: try to parse `content` as JSON, set as itinerary, set `status = "done"`
  - If `message_type === "error"`: set `status = "error"`
- On error: set `status = "error"`
- Cleanup: close EventSource on unmount / sessionId change

---

### 5.6 `frontend/src/components/InputForm.jsx`

A simple form with:
- **City** — text input
- **Interests** — text input (comma-separated, split into array on submit)
- **Budget** — number input (USD)
- **Submit button** — "Plan My Day!" — disabled while loading

On submit:
- `POST /api/plan` with `{ city, interests: [...], budget }`
- Receive `{ session_id }` and pass it up to parent (via `onSubmit` prop)

Style: Clean card with rounded corners, subtle shadow. Inputs should be clearly labeled.

---

### 5.7 `frontend/src/components/AgentGraph.jsx` — ⭐ Core Visual Component

This is the **centerpiece** of the UI. Uses `@xyflow/react` to render a live animated graph.

**Nodes (4 total):**

| Node ID | Label | Position (approximate) | Color |
|---|---|---|---|
| `planner` | 🧠 Planner | Top center | Blue |
| `transport` | 🚌 Transport | Right | Green |
| `dining` | 🍽️ Dining | Bottom left | Orange |
| `activities` | 🎯 Activities | Bottom right | Purple |

**Node states** — each node visually changes based on agent status:
| State | Visual |
|---|---|
| Idle | Grey background, muted |
| Thinking | Pulsing/glowing animation with the agent's color, label says "thinking..." |
| Done | Solid agent color, checkmark or ✓ |
| Revising | Orange border, pulsing |

**Edges:**
- Define edges based on the messages received
- When a message flows from agent A → agent B, draw/highlight that edge
- Use React Flow's `animated: true` property on active edges
- Edge label briefly shows the message type (e.g., "task_assignment", "revision_request")

**How to determine node states from messages:**
- When `message_type === "status"` and `metadata.status === "thinking"` → set that agent's node to "thinking"
- When `message_type === "response"` → set `from_agent` node to "done"
- When `message_type === "revision_request"` → set `to_agent` node to "revising"
- When `message_type === "revised_response"` or a response after revision → set back to "done"

**Props:** `messages` (array of AgentMessage objects from useSSE)

**Important React Flow setup:**
- Import `ReactFlow, Background, Controls` from `@xyflow/react`
- Import `@xyflow/react/dist/style.css`
- Wrap in a div with explicit `width` and `height` (React Flow requires this)
- Use `fitView` prop
- Disable user interaction (no drag/pan needed): `nodesDraggable={false}`, `nodesConnectable={false}`
- Use custom node styling via `style` property on node objects (background color, border, border-radius, padding, font-size, text-align center)

---

### 5.8 `frontend/src/components/MessageLog.jsx`

A scrolling sidebar showing every inter-agent message chronologically.

**For each message, display:**
- Agent icon/emoji based on `from_agent` (🧠 Planner, 🚌 Transport, 🍽️ Dining, 🎯 Activities, 👤 User, ⚙️ System)
- Arrow showing direction: `from_agent → to_agent`
- Message type as a small colored badge/tag
- Content preview (truncated to ~100 chars for long JSON responses, expandable on click or just show a "View JSON" label for JSON content)
- Timestamp

**Skip** messages with `message_type === "status"` (those are for the graph, not the log) — or show them in a muted/grey style.

**Auto-scroll** to bottom when new messages arrive.

**Props:** `messages` (array of AgentMessage objects from useSSE)

---

### 5.9 `frontend/src/components/Itinerary.jsx`

Displayed only when the plan is complete. Renders the final day itinerary.

**Layout:** A timeline/card view:
- City name as header
- Summary text
- Each item as a card/row: `time | icon | title | description | $cost`
- Category icons: 🚌 transport, 🍽️ dining, 🎯 activity
- Total cost displayed at the bottom
- Color-coded left border per category

**Props:** `itinerary` (the parsed final plan object — could be raw JSON string or parsed object, handle both)

**Important:** The planner's final response may not perfectly match the expected JSON schema. The component should handle this gracefully — if it receives a string, try to parse it. If parsing fails, display the raw text in a formatted way.

---

### 5.10 `frontend/src/App.jsx`

**Main layout:**

```
┌──────────────────────────────────────────────────────────────┐
│  🗺️ Multi-Agent Travel Day Planner          (header bar)     │
├──────────────┬──────────────────────────┬────────────────────┤
│              │                          │                    │
│  InputForm   │     AgentGraph           │   MessageLog       │
│  (left,      │     (center,             │   (right,          │
│   ~250px)    │      flex-grow)          │    ~300px)         │
│              │                          │                    │
├──────────────┴──────────────────────────┴────────────────────┤
│  Itinerary (full width, shown when done)                     │
└──────────────────────────────────────────────────────────────┘
```

**State management:**
- `sessionId` — set when form is submitted
- Use `useSSE(sessionId)` to get `{ messages, status, itinerary }`
- Pass `messages` to `AgentGraph` and `MessageLog`
- Pass `itinerary` to `Itinerary` (only render when `status === "done"`)

**Form submit handler:**
```javascript
async function handleSubmit(formData) {
    const res = await fetch("/api/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
    });
    const { session_id } = await res.json();
    setSessionId(session_id);
}
```

---

### 5.11 `frontend/src/styles/app.css`

Keep styling **minimal and clean**. Key requirements:

- **Dark-ish background** (e.g., `#0f172a` or `#1a1a2e`) with light text — looks professional and makes the graph pop
- **Font:** System font stack or `Inter` from Google Fonts
- **Layout:** CSS Grid or Flexbox for the 3-column layout. The main content area should fill `100vh`
- **Input form:** Card with `background: rgba(255,255,255,0.05)`, rounded corners, subtle border
- **Agent graph area:** Takes up the most space, dark background so React Flow nodes stand out
- **Message log:** Scrollable panel, messages styled as small cards with slight background differentiation
- **Itinerary:** Clean card at the bottom with timeline styling
- **Node colors:** Planner = `#3b82f6` (blue), Transport = `#22c55e` (green), Dining = `#f97316` (orange), Activities = `#a855f7` (purple)
- **Pulsing animation:** CSS keyframe animation for the "thinking" state on nodes:
  ```css
  @keyframes pulse {
      0%, 100% { box-shadow: 0 0 0 0 rgba(color, 0.4); }
      50% { box-shadow: 0 0 0 10px rgba(color, 0); }
  }
  ```
- **Message type badges:** Small rounded labels with category colors
- **Responsive is NOT required** — this is a demo, fixed desktop layout is fine

---

## 6. Root Files

### `.env` (at project root `travel-planner/.env`)

```
GROQ_API_KEY=gsk_your_key_here
```

### `README.md`

Write a clean README with:
- Project title and one-line description
- Architecture diagram (can reference the 3 layers: Communication, Orchestration, Agent)
- Tech stack list
- Setup instructions:
  ```
  # Backend
  cd backend
  pip install -r requirements.txt
  # Create .env in project root with GROQ_API_KEY
  uvicorn main:app --reload

  # Frontend (new terminal)
  cd frontend
  npm install
  npm run dev
  ```
- Screenshot placeholder
- Brief explanation of the multi-agent workflow

---

## 7. Critical Implementation Notes

> [!IMPORTANT]
> **These are non-negotiable requirements. Do not skip any of them.**

1. **Every agent message MUST go through the `MessageBus`.** This is the entire point of the communication layer. Agents should never communicate directly.

2. **The `orchestrator.run()` method must send `task_assignment` messages via the bus BEFORE calling the agent's `run()` method.** This ensures the frontend sees the assignment message arrive before the agent starts "thinking."

3. **Use `asyncio.gather()` for parallel dispatch** of Transport, Dining, and Activities agents. This is a key demo point — the UI should show 3 agents "thinking" simultaneously.

4. **The re-negotiation loop (budget check → revision_request → revised response) MUST be implemented.** Even if it doesn't always trigger, it must be there and must work. This is the main collaboration demo. Use a 10% tolerance: only re-negotiate if `total_cost > budget * 1.1`.

5. **SSE stream must include ALL message types** — `task_assignment`, `status`, `response`, `revision_request`, `final_plan`. The frontend uses these to drive both the graph animation and the message log.

6. **React Flow nodes must visually change state** (idle → thinking → done, and potentially → revising → done). This is what makes the demo visually compelling.

7. **The `_parse_json` helper must be robust.** LLMs sometimes wrap JSON in markdown code blocks or add preamble text. Try `json.loads(text)` first, then search for `{` and `}` boundaries. Defaults/fallbacks must exist so the app never crashes on unexpected LLM output.

8. **Add `await asyncio.sleep(1)` before each Groq API call** to stay within free-tier rate limits (~30 req/min).

9. **The frontend must handle the case where the itinerary content is a raw string** (not parseable JSON). Display it formatted as text in that case.

10. **React Flow requires its parent container to have explicit width and height.** Use `style={{ width: '100%', height: '100%' }}` on the wrapper div, and ensure the parent has a defined height (not auto).

---

## 8. Orchestration Sequence (Visual Reference)

```
USER                    PLANNER              TRANSPORT    DINING    ACTIVITIES
  |                        |                     |           |          |
  |-- task_assignment ---→ |                     |           |          |
  |                        |                     |           |          |
  |                    [splits budget]            |           |          |
  |                        |                     |           |          |
  |                  ←---- response               |           |          |
  |                        |                     |           |          |
  |                        |-- task_assignment -→ |           |          |
  |                        |-- task_assignment ----------→   |          |
  |                        |-- task_assignment ----------------------→  |
  |                        |                     |           |          |
  |                        |              [all 3 agents think in parallel]
  |                        |                     |           |          |
  |                        | ←---- response ---- |           |          |
  |                        | ←---- response -------------- |          |
  |                        | ←---- response -----------------------  |
  |                        |                     |           |          |
  |                   [checks budget]             |           |          |
  |                        |                     |           |          |
  |           (if over budget)                    |           |          |
  |                        |-- revision_request → |           |          |
  |                        | ←---- response ----  |           |          |
  |                        |                     |           |          |
  |                   [assembles final plan]       |           |          |
  |                        |                     |           |          |
  | ←----- final_plan ---- |                     |           |          |
```

---

## 9. Checklist Before Calling Done

- [ ] Backend starts with `uvicorn main:app --reload` without errors
- [ ] Frontend starts with `npm run dev` without errors
- [ ] Submitting the form triggers SSE events
- [ ] Agent graph shows 4 nodes with correct labels and colors
- [ ] Nodes animate (pulse) when agents are thinking
- [ ] Edges animate when messages flow between agents
- [ ] Message log shows messages appearing in real-time
- [ ] 3 specialist agents run in parallel (visible in UI as simultaneous "thinking" states)
- [ ] Re-negotiation works when total cost exceeds budget by >10%
- [ ] Final itinerary renders as a timeline when planning is complete
- [ ] No crashes on unexpected LLM output (graceful fallbacks everywhere)
- [ ] Total file count is exactly 16

