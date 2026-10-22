import asyncio
import os
import uuid
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from groq import AsyncGroq

from agents import create_agents
from message_bus import MessageBus
from models import AgentMessage, UserInput
from orchestrator import Orchestrator

# .env lives at the project root (one level above backend/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

@asynccontextmanager
async def lifespan(app):
    yield
    tasks = list(background_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Multi-Agent Travel Planner", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: dict[str, MessageBus] = {}
background_tasks: set[asyncio.Task] = set()  # keep references so tasks aren't garbage-collected
session_finished: dict[str, float] = {}
SESSION_TTL = 3600
MAX_SESSIONS = 100
logger = logging.getLogger(__name__)

TERMINAL_TYPES = ("final_plan", "error")


async def run_orchestration(orchestrator: Orchestrator, bus: MessageBus, user_input: UserInput, client: AsyncGroq, session_id: str):
    try:
        await asyncio.wait_for(orchestrator.run(user_input), timeout=180)
    except Exception as exc:  # surface any failure to the UI instead of hanging the stream
        logger.exception("Planning failed")
        message = "Planning failed. Check the backend logs and Groq configuration, then try again."
        if isinstance(exc, ValueError):
            message = str(exc)
        elif isinstance(exc, asyncio.TimeoutError):
            message = "Planning took too long. Please try again."
        await bus.send(AgentMessage(
            from_agent="system", to_agent="user", message_type="error",
            content=message,
        ))
    finally:
        session_finished[session_id] = time.monotonic()
        await client.close()


@app.post("/api/plan")
async def create_plan(user_input: UserInput):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key in ("your_groq_api_key_here", "gsk_your_key_here"):
        raise HTTPException(503, "Add GROQ_API_KEY to travel-planner/.env and restart the backend.")
    for expired, finished_at in list(session_finished.items()):
        if time.monotonic() - finished_at > SESSION_TTL:
            sessions.pop(expired, None)
            session_finished.pop(expired, None)
    if len(sessions) >= MAX_SESSIONS:
        raise HTTPException(503, "The demo has reached its session limit. Try again later or restart the backend.")
    client = AsyncGroq(api_key=api_key, timeout=40, max_retries=2)
    session_id = str(uuid.uuid4())
    bus = MessageBus()
    sessions[session_id] = bus

    agents = create_agents(client, bus)
    orchestrator = Orchestrator(agents, bus)

    task = asyncio.create_task(run_orchestration(orchestrator, bus, user_input, client, session_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return {"session_id": session_id}


@app.get("/api/stream/{session_id}")
async def stream_events(session_id: str):
    bus = sessions.get(session_id)
    if bus is None:
        return JSONResponse({"error": "session not found"}, status_code=404)

    # Subscribe and snapshot the log with no await in between, so messages sent before the
    # browser connected are replayed exactly once and nothing is missed.
    queue = bus.subscribe()
    history = list(bus.get_log())

    async def event_generator():
        try:
            for message in history:
                yield f"data: {message.model_dump_json()}\n\n"
                if message.message_type in TERMINAL_TYPES:
                    return
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=120)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {message.model_dump_json()}\n\n"
                if message.message_type in TERMINAL_TYPES:
                    break
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}
