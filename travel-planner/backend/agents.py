import asyncio
import os

from groq import AsyncGroq

from message_bus import MessageBus
from models import AgentMessage

SUPERVISOR_MODEL = os.getenv("GROQ_SUPERVISOR_MODEL") or os.getenv("GROQ_PLANNER_MODEL", "openai/gpt-oss-120b")
SPECIALIST_MODEL = os.getenv("GROQ_SPECIALIST_MODEL") or os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
MODEL = SPECIALIST_MODEL

PLANNER_PROMPT = """You are the Planner Agent in a multi-agent travel planning system. Your job is to:
1. Take user input (city, interests, budget) and split the budget across transport (~20%), dining (~35%), and activities (~45%).
2. Review collected suggestions from other agents and assemble a chronological day timeline.
3. If total cost exceeds budget, identify which agent to ask for cheaper alternatives.

When splitting budget, respond with ONLY valid JSON:
{"transport": <amount>, "dining": <amount>, "activities": <amount>}

When assembling the final plan, respond with ONLY valid JSON:
{"items": [{"time": "HH:MM", "title": "...", "description": "...", "cost": <number>, "category": "transport|dining|activity"}], "total_cost": <number>, "summary": "A brief 1-2 sentence summary of the day"}

When requesting revision, respond with plain text explaining what to change and by how much.
Always ensure the total does not exceed the user's budget."""

TRANSPORT_PROMPT = """You are a Transport Agent in a multi-agent travel planning system. Given a city and a transport budget, suggest realistic local transportation options for a full day of exploring. Include options like metro/subway day passes, bus passes, ride-sharing estimates, bicycle rentals, or walking routes.

You MUST respond with ONLY valid JSON in this exact format:
{"suggestions": [{"name": "...", "cost": <number>, "description": "..."}], "total_cost": <number>}

Keep total_cost within the given budget. Be realistic with pricing for the specific city."""

DINING_PROMPT = """You are a Dining Agent in a multi-agent travel planning system. Given a city, food interests/preferences, and a dining budget, suggest breakfast, lunch, and dinner options that match the interests and fit within the budget. Include realistic restaurant names and local cuisine suggestions.

You MUST respond with ONLY valid JSON in this exact format:
{"meals": [{"meal": "breakfast|lunch|dinner", "restaurant": "...", "cuisine": "...", "cost": <number>, "time": "HH:MM", "description": "..."}], "total_cost": <number>}

Keep total_cost within the given budget. Be realistic with pricing for the specific city."""

ACTIVITIES_PROMPT = """You are an Activities Agent in a multi-agent travel planning system. Given a city, interests, and an activities budget, suggest 3-5 sightseeing activities, experiences, or attractions for a day trip. Include realistic attraction names, entry fees, and suggested durations.

You MUST respond with ONLY valid JSON in this exact format:
{"activities": [{"name": "...", "cost": <number>, "duration_hours": <number>, "description": "..."}], "total_cost": <number>}

Keep total_cost within the given budget. Be realistic with pricing for the specific city."""


class Agent:
    def __init__(self, name: str, system_prompt: str, client: AsyncGroq, bus: MessageBus, model: str | None = None):
        self.name = name
        self.system_prompt = system_prompt
        self.client = client
        self.bus = bus
        self.model = model or SPECIALIST_MODEL

    async def run(self, task: str, from_agent: str = "planner", metadata: dict | None = None) -> str:
        metadata = metadata or {}

        await self.bus.send(AgentMessage(
            from_agent=self.name,
            to_agent="system",
            message_type="status",
            content=f"{self.name} is thinking...",
            metadata={**metadata, "status": "thinking"},
        ))

        # Demo pacing; provider quotas still apply across concurrent requests.
        await asyncio.sleep(1)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": task},
            ],
            temperature=0.7,
            max_tokens=4096,
            response_format={"type": "json_object"},
            extra_body={"reasoning_effort": "low"} if self.model.startswith("openai/gpt-oss-") else {},
        )
        result = response.choices[0].message.content or ""

        await self.bus.send(AgentMessage(
            from_agent=self.name,
            to_agent=from_agent,
            message_type="revised_response" if metadata.get("revision") else "response",
            content=result,
            metadata=metadata,
        ))
        return result


def create_agents(client: AsyncGroq, bus: MessageBus) -> dict[str, Agent]:
    return {
        "planner": Agent("planner", PLANNER_PROMPT, client, bus, model=SUPERVISOR_MODEL),
        "transport": Agent("transport", TRANSPORT_PROMPT, client, bus, model=SPECIALIST_MODEL),
        "dining": Agent("dining", DINING_PROMPT, client, bus, model=SPECIALIST_MODEL),
        "activities": Agent("activities", ACTIVITIES_PROMPT, client, bus, model=SPECIALIST_MODEL),
    }
