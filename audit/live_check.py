"""One bounded live workflow. Never prints credentials or raw provider errors."""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "travel-planner" / "backend"))

import main
from groq import AsyncGroq
from agents import create_agents
from message_bus import MessageBus
from models import UserInput
from orchestrator import Orchestrator


async def check():
    import os
    key = os.getenv("GROQ_API_KEY", "")
    if not key or key == "your_groq_api_key_here":
        raise RuntimeError("Configure the local .env first")
    bus = MessageBus()
    client = AsyncGroq(api_key=key, timeout=30, max_retries=0)
    start = time.monotonic()
    report = {"input": {"city": "Tokyo", "interests": ["history", "street food"], "budget": 100}}
    try:
        raw = await asyncio.wait_for(Orchestrator(create_agents(client, bus), bus).run(UserInput(**report["input"])), timeout=150)
        report["outcome"] = "completed"
        report["itinerary"] = json.loads(raw)
    except Exception as exc:
        report["outcome"] = "failed"
        report["error_type"] = type(exc).__name__
        report["http_status"] = getattr(exc, "status_code", None)
        body = getattr(exc, "body", {})
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                report["provider_error_code"] = error.get("code")
                report["provider_message"] = str(error.get("message", "")).replace(key, "[redacted]")[:500]
        if report["http_status"] == 404:
            try:
                models = await client.models.list()
                report["available_models"] = [model.id for model in models.data]
            except Exception as diagnostic:
                report["model_listing_error"] = type(diagnostic).__name__
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - start, 2)
        report["events"] = [m.model_dump() for m in bus.log]
        await client.close()
    (ROOT / "audit" / "live-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("events", "itinerary")}, indent=2))
    if "itinerary" in report:
        print(json.dumps({"total_cost": report["itinerary"]["total_cost"], "items": len(report["itinerary"]["items"]), "events": len(report["events"])}, indent=2))


if __name__ == "__main__":
    asyncio.run(check())
