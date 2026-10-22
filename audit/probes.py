"""Safe, offline audit reproductions. No provider calls or network service needed."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "travel-planner" / "backend"))

from fastapi.testclient import TestClient
import main
from message_bus import MessageBus
from models import UserInput
from orchestrator import Orchestrator
from test_workflow import fixture


def run_probes():
    results = {}
    planner = Orchestrator({}, MessageBus())
    specialists = {
        "transport": {"suggestions": [{"name": "Metro", "cost": 10}], "total_cost": 10},
        "dining": {"meals": [{"meal": "lunch", "restaurant": "Cafe", "cost": 30}], "total_cost": 30},
        "activities": {"activities": [{"name": "Museum", "cost": 40, "duration_hours": 8}], "total_cost": 40},
    }
    fabricated = {"items": [{"time": "09:00", "title": "Unapproved helicopter ride", "cost": 80, "category": "activity"}]}
    plan = planner._build_itinerary("Tokyo", fabricated, specialists)
    results["same_total_replacement"] = {"accepted_title": plan.items[0].title, "accepted_items": len(plan.items), "total": plan.total_cost}

    oversized = {**specialists, "activities": {"activities": [{"name": f"Stop {i}", "cost": 10} for i in range(6)], "total_cost": 60}}
    plan = planner._build_itinerary("Tokyo", {}, oversized)
    results["fallback_truncates_sixth_activity"] = {"specialist_item_total": 100, "final_total": plan.total_cost, "activity_count": sum(i.category == "activity" for i in plan.items)}

    bad_time = {**specialists, "dining": {"meals": [{"meal": "lunch", "restaurant": "Cafe", "time": "25:90", "cost": 30}]}}
    plan = planner._build_itinerary("Tokyo", {}, bad_time)
    results["fallback_accepts_invalid_time"] = {"times": [i.time for i in plan.items]}

    named = planner._specialist_result("dining", '{"meals":[{"cost":0}],"total_cost":0}')
    results["missing_names_and_meals_accepted"] = named

    inflated = {**specialists, "activities": {"activities": [{"name": "Museum", "cost": 40}], "total_cost": 400}}
    plan = planner._build_itinerary("Tokyo", {}, inflated)
    results["declared_cost_inconsistency"] = {"budget_check_total": sum(planner._category_cost(k, v) for k, v in inflated.items()), "final_display_total": plan.total_cost}

    bus = MessageBus()
    agents = fixture(bus, activity_cost=100, revised_cost=100)
    plan = json.loads(asyncio.run(Orchestrator(agents, bus).run(UserInput(city="Tokyo", budget=100))))
    results["unresolved_overspend_still_final"] = {"budget": 100, "final_cost": plan["total_cost"], "last_event": bus.log[-1].message_type}

    main.sessions.clear()
    main.session_finished.clear()
    with patch.dict(os.environ, {"GROQ_API_KEY": "offline-only"}), patch("main.AsyncGroq", return_value=AsyncMock()), patch("main.create_agents", side_effect=lambda client, bus: fixture(bus)):
        with TestClient(main.app) as client:
            statuses = []
            first_id = None
            for _ in range(101):
                response = client.post("/api/plan", json={"city": "Tokyo", "budget": 100}, headers={"Origin": "https://untrusted.example"})
                statuses.append(response.status_code)
                if first_id is None:
                    first_id = response.json()["session_id"]
                    results["cross_origin_post"] = {"status": response.status_code, "allow_origin": response.headers.get("access-control-allow-origin")}
            results["session_slot_exhaustion"] = {"accepted": statuses.count(200), "last_status": statuses[-1], "sessions": len(main.sessions)}
            response = client.get(f"/api/stream/{first_id}")
            results["stream_without_owner_auth"] = {"status": response.status_code, "contains_trip_data": "Tokyo" in response.text}
    main.sessions.clear()
    main.session_finished.clear()
    return results


if __name__ == "__main__":
    report = run_probes()
    (ROOT / "audit" / "probe-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
