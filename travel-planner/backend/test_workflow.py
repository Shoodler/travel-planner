"""Offline regression tests: no credentials or paid model calls required."""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from agents import Agent
from message_bus import MessageBus
from models import AgentMessage, UserInput
from orchestrator import Orchestrator


class FakeAgent:
    def __init__(self, name, bus, responses, barrier=None):
        self.name, self.bus, self.responses, self.barrier = name, bus, iter(responses), barrier
        self.calls = []

    async def run(self, task, from_agent="planner", metadata=None):
        self.calls.append(task)
        metadata = metadata or {}
        await self.bus.send(AgentMessage(from_agent=self.name, to_agent="system", message_type="status",
                                        content="thinking", metadata={"status": "thinking"}))
        if self.barrier and len(self.calls) == 1:
            self.barrier["started"].add(self.name)
            if len(self.barrier["started"]) == 3:
                self.barrier["ready"].set()
            await asyncio.wait_for(self.barrier["ready"].wait(), 1)
        result = next(self.responses)
        result = result if isinstance(result, str) else json.dumps(result)
        await self.bus.send(AgentMessage(from_agent=self.name, to_agent=from_agent,
                                        message_type="revised_response" if metadata.get("revision") else "response",
                                        content=result, metadata=metadata))
        return result


def fixture(bus, activity_cost=40, revised_cost=30, assembly="malformed", barrier=None):
    return {
        "planner": FakeAgent("planner", bus, [{"transport": 20, "dining": 35, "activities": 45}, assembly]),
        "transport": FakeAgent("transport", bus, [{"suggestions": [{"name": "Metro", "cost": 10}], "total_cost": 10}], barrier),
        "dining": FakeAgent("dining", bus, [{"meals": [{"meal": "lunch", "restaurant": "Cafe", "cost": 30}], "total_cost": 30}], barrier),
        "activities": FakeAgent("activities", bus, [
            {"activities": [{"name": "Museum", "cost": activity_cost}], "total_cost": activity_cost},
            {"activities": [{"name": "Park", "cost": revised_cost}], "total_cost": revised_cost},
            {"activities": [{"name": "Garden", "cost": revised_cost}], "total_cost": revised_cost},
        ], barrier),
    }


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_dispatch_and_fallback(self):
        bus = MessageBus()
        barrier = {"started": set(), "ready": asyncio.Event()}
        agents = fixture(bus, barrier=barrier)
        result = json.loads(await Orchestrator(agents, bus).run(UserInput(city="Tokyo", budget=100)))
        self.assertEqual(barrier["started"], {"transport", "dining", "activities"})
        self.assertEqual(result["total_cost"], 80)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual([item["time"] for item in result["items"]], sorted(item["time"] for item in result["items"]))
        for name in barrier["started"]:
            assignment = next(i for i, m in enumerate(bus.log) if m.message_type == "task_assignment" and m.to_agent == name)
            thinking = next(i for i, m in enumerate(bus.log) if m.message_type == "status" and m.from_agent == name)
            self.assertLess(assignment, thinking)
        self.assertEqual(bus.log[-1].message_type, "final_plan")

    async def test_over_budget_revises_most_expensive_agent(self):
        bus = MessageBus()
        agents = fixture(bus, activity_cost=100)
        result = json.loads(await Orchestrator(agents, bus).run(UserInput(city="Tokyo", budget=100)))
        revisions = [m for m in bus.log if m.message_type == "revision_request"]
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0].to_agent, "activities")
        self.assertIn("Museum", agents["activities"].calls[1])
        self.assertTrue(any(m.message_type == "revised_response" for m in bus.log))
        self.assertEqual(result["total_cost"], 70)

    async def test_tolerance_boundary_does_not_revise(self):
        bus = MessageBus()
        result = json.loads(await Orchestrator(fixture(bus, activity_cost=70), bus).run(UserInput(city="Tokyo", budget=100)))
        self.assertEqual(result["total_cost"], 110)
        self.assertFalse(any(m.message_type == "revision_request" for m in bus.log))

    async def test_planner_cannot_silently_drop_expenses(self):
        bus = MessageBus()
        assembly = {"items": [{"time": "09:00", "title": "Invented free day", "cost": 0, "category": "activity"}]}
        result = json.loads(await Orchestrator(fixture(bus, assembly=assembly), bus).run(UserInput(city="Tokyo", budget=100)))
        self.assertEqual(result["total_cost"], 80)

    async def test_revision_rounds_are_bounded(self):
        bus = MessageBus()
        result = json.loads(await Orchestrator(fixture(bus, activity_cost=100, revised_cost=100), bus).run(UserInput(city="Tokyo", budget=100)))
        self.assertEqual(len([m for m in bus.log if m.message_type == "revision_request"]), 2)
        self.assertEqual(result["total_cost"], 140)

    async def test_bad_specialist_data_is_not_a_free_plan(self):
        bus = MessageBus()
        agents = fixture(bus)
        agents["activities"] = FakeAgent("activities", bus, ["not JSON"])
        with self.assertRaisesRegex(ValueError, "unusable suggestions"):
            await Orchestrator(agents, bus).run(UserInput(city="Tokyo", budget=100))
        self.assertFalse(any(m.message_type == "final_plan" for m in bus.log))

    async def test_agent_emits_thinking_and_revised_response(self):
        bus = MessageBus()
        client = AsyncMock()
        client.chat.completions.create.return_value.choices = [type("Choice", (), {"message": type("Message", (), {"content": '{"ok":true}'})()})()]
        with patch("agents.asyncio.sleep", new=AsyncMock()):
            result = await Agent("dining", "Return JSON", client, bus).run("Revise", metadata={"revision": True})
        self.assertEqual(json.loads(result), {"ok": True})
        self.assertEqual([m.message_type for m in bus.log], ["status", "revised_response"])
        self.assertEqual(client.chat.completions.create.call_args.kwargs["response_format"], {"type": "json_object"})

    async def test_bus_broadcast_and_unsubscribe(self):
        bus = MessageBus()
        first, second = bus.subscribe(), bus.subscribe()
        message = AgentMessage(from_agent="planner", to_agent="dining", message_type="task_assignment", content="hello")
        await bus.send(message)
        self.assertEqual(await first.get(), message)
        self.assertEqual(await second.get(), message)
        bus.unsubscribe(second)
        await bus.send(message)
        self.assertTrue(second.empty())


class ValidationTests(unittest.TestCase):
    def test_bad_inputs(self):
        for city, budget in [("  ", 100), ("Tokyo", 0), ("Tokyo", -1), ("Tokyo", float("inf")), ("Tokyo", float("nan"))]:
            with self.subTest(city=city, budget=budget), self.assertRaises(ValidationError):
                UserInput(city=city, budget=budget)

    def test_json_parsing_and_malformed_assembly(self):
        orchestrator = Orchestrator({}, MessageBus())
        self.assertEqual(orchestrator._parse_json('```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(orchestrator._parse_json("[]"), {})
        self.assertEqual(orchestrator._parse_json("bad output"), {})
        self.assertEqual(orchestrator._to_number(float("nan")), 0)
        bus = MessageBus()
        data = {"transport": {"suggestions": []}, "dining": {"meals": []}, "activities": {"activities": []}}
        for bad in [5, "bad", {"time": "09:00"}]:
            result = orchestrator._build_itinerary("Tokyo", {"items": bad}, data)
            self.assertEqual(result.items, [])


class ApiTests(unittest.TestCase):
    def setUp(self):
        main.sessions.clear()
        main.session_finished.clear()

    def test_health_unknown_session_and_missing_key(self):
        with TestClient(main.app) as client, patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
            self.assertEqual(client.get("/api/stream/unknown").status_code, 404)
            self.assertEqual(client.post("/api/plan", json={"city": "Tokyo", "budget": 100}).status_code, 503)
            self.assertEqual(client.post("/api/plan", json={"city": " ", "budget": -1}).status_code, 422)

    def test_post_stream_replay_and_cleanup(self):
        fake_client = AsyncMock()
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch("main.AsyncGroq", return_value=fake_client), patch("main.create_agents", side_effect=lambda client, bus: fixture(bus)):
            with TestClient(main.app) as client:
                response = client.post("/api/plan", json={"city": "Tokyo", "budget": 100})
                self.assertEqual(response.status_code, 200)
                session_id = response.json()["session_id"]
                stream = client.get(f"/api/stream/{session_id}")
                events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
                self.assertEqual(events[0]["from_agent"], "user")
                self.assertEqual(events[-1]["message_type"], "final_plan")
                self.assertEqual(json.loads(events[-1]["content"])["total_cost"], 80)
                self.assertFalse(main.sessions[session_id].subscribers)
                replay = client.get(f"/api/stream/{session_id}")
                self.assertEqual(replay.text, stream.text)
        fake_client.close.assert_awaited_once()

    def test_worker_error_reaches_stream(self):
        fake_client = AsyncMock()
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch("main.AsyncGroq", return_value=fake_client), patch("main.Orchestrator.run", new=AsyncMock(side_effect=RuntimeError("secret provider details"))):
            with TestClient(main.app) as client:
                session_id = client.post("/api/plan", json={"city": "Tokyo", "budget": 100}).json()["session_id"]
                response = client.get(f"/api/stream/{session_id}")
                self.assertIn('"message_type":"error"', response.text)
                self.assertNotIn("secret provider details", response.text)
                self.assertFalse(main.sessions[session_id].subscribers)


if __name__ == "__main__":
    unittest.main()
