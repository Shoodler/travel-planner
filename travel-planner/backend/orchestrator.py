import asyncio
import json
import re
import math

from agents import Agent
from message_bus import MessageBus
from models import AgentMessage, Itinerary, ItineraryItem, UserInput

SPECIALISTS = ["transport", "dining", "activities"]
DEFAULT_SPLIT = {"transport": 0.20, "dining": 0.35, "activities": 0.45}
LIST_KEYS = {"transport": "suggestions", "dining": "meals", "activities": "activities"}
BUDGET_TOLERANCE = 1.1
MAX_REVISION_ROUNDS = 2


class Orchestrator:
    """Orchestration layer: drives the Planner through split -> dispatch -> check -> renegotiate -> assemble."""

    def __init__(self, agents: dict[str, Agent], bus: MessageBus):
        self.agents = agents
        self.bus = bus

    async def run(self, user_input: UserInput) -> str:
        planner = self.agents["planner"]
        interests = ", ".join(user_input.interests) or "general sightseeing"
        budget = user_input.budget

        # Step 1 — Planner splits budget
        request = (
            f"Plan a full day in {user_input.city} with a total budget of ${budget:.2f} USD. "
            f"Interests: {interests}."
        )
        await self.bus.send(AgentMessage(
            from_agent="user", to_agent="planner", message_type="task_assignment",
            content=request, metadata={"budget": budget, "step": 1},
        ))
        split_raw = await planner.run(
            f"{request}\nSplit the budget across transport, dining and activities. "
            f"The three amounts must add up to {budget:.2f}. Respond with ONLY the JSON object.",
            from_agent="user",
            metadata={"step": 1},
        )
        split = self._validate_split(self._parse_json(split_raw), budget)

        # Step 2 — Parallel dispatch to specialist agents
        tasks = {
            "transport": (
                f"City: {user_input.city}\nTransport budget: ${split['transport']:.2f} USD\n"
                f"Traveller interests (for choosing routes): {interests}\n"
                "Suggest how to get around the city for a full day (08:00-22:00)."
            ),
            "dining": (
                f"City: {user_input.city}\nDining budget: ${split['dining']:.2f} USD\n"
                f"Food interests / preferences: {interests}\n"
                "Suggest breakfast, lunch and dinner for one person."
            ),
            "activities": (
                f"City: {user_input.city}\nActivities budget: ${split['activities']:.2f} USD\n"
                f"Interests: {interests}\n"
                "Suggest 3-5 activities for a single day between 08:00 and 22:00."
            ),
        }
        for name in SPECIALISTS:
            await self.bus.send(AgentMessage(
                from_agent="planner", to_agent=name, message_type="task_assignment",
                content=tasks[name], metadata={"budget": split[name], "step": 2},
            ))

        pending = [asyncio.create_task(
            self.agents[name].run(tasks[name], metadata={"budget": split[name], "step": 3})
        ) for name in SPECIALISTS]
        try:
            raw_results = await asyncio.gather(*pending)
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        results = {name: self._specialist_result(name, raw) for name, raw in zip(SPECIALISTS, raw_results)}

        # Step 3 & 4 — Check total cost, re-negotiate if over budget (10% tolerance)
        for round_no in range(1, MAX_REVISION_ROUNDS + 1):
            costs = {name: self._category_cost(name, results[name]) for name in SPECIALISTS}
            total = sum(costs.values())
            if total <= budget * BUDGET_TOLERANCE:
                break

            target = max(costs, key=costs.get)
            overage = total - budget
            new_limit = max(costs[target] - overage, 0)
            revision = (
                f"The combined plan costs ${total:.2f}, which is ${overage:.2f} over the user's "
                f"${budget:.2f} budget. Your {target} suggestions are the most expensive part "
                f"(${costs[target]:.2f}). Please reduce your total by at least ${overage:.2f}, "
                f"to ${new_limit:.2f} or less, by choosing cheaper alternatives."
            )
            await self.bus.send(AgentMessage(
                from_agent="planner", to_agent=target, message_type="revision_request",
                content=revision,
                metadata={"budget": round(new_limit, 2), "overage": round(overage, 2),
                          "round": round_no, "step": 4},
            ))
            revised_raw = await self.agents[target].run(
                f"{tasks[target]}\n\nYour previous suggestions were:\n"
                f"{json.dumps(results[target]) if results[target] else raw_results[SPECIALISTS.index(target)]}\n\n"
                f"REVISION REQUEST FROM PLANNER: {revision}\n"
                "Respond with ONLY valid JSON in the same format as before.",
                metadata={"budget": round(new_limit, 2), "revision": True, "round": round_no, "step": 4},
            )
            results[target] = self._specialist_result(target, revised_raw)

        # Step 5 — Planner assembles final itinerary
        await self.bus.send(AgentMessage(
            from_agent="planner", to_agent="system", message_type="status",
            content="planner is assembling the final itinerary...",
            metadata={"status": "thinking", "step": 5},
        ))
        assemble_raw = await planner.run(
            f"Assemble a chronological day itinerary for {user_input.city} from 08:00 to 22:00.\n"
            f"User budget: ${budget:.2f} USD. Interests: {interests}.\n\n"
            f"TRANSPORT AGENT RESULT:\n{json.dumps(results['transport'])}\n\n"
            f"DINING AGENT RESULT:\n{json.dumps(results['dining'])}\n\n"
            f"ACTIVITIES AGENT RESULT:\n{json.dumps(results['activities'])}\n\n"
            "Include every meal, every activity and the transport choice as timeline items, "
            "using the costs given. Respond with ONLY the final plan JSON object.",
            from_agent="planner",
            metadata={"step": 5},
        )
        final = self._build_itinerary(user_input.city, self._parse_json(assemble_raw), results)
        if not final.items:
            raise ValueError("The agents returned no usable itinerary. Please try again.")

        # Step 6 — Broadcast final plan
        final_json = final.model_dump_json()
        await self.bus.send(AgentMessage(
            from_agent="planner", to_agent="user", message_type="final_plan",
            content=final_json, metadata={"total_cost": final.total_cost, "budget": budget, "step": 6},
        ))
        return final_json

    # ---------- helpers ----------

    def _specialist_result(self, name: str, raw: str) -> dict:
        data = self._parse_json(raw)
        items = data.get(LIST_KEYS[name])
        if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
            raise ValueError(f"The {name} agent returned unusable suggestions. Please try again.")
        for item in items:
            cost = item.get("cost")
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                raise ValueError(f"The {name} agent returned an invalid cost. Please try again.")
        return data

    def _parse_json(self, text: str) -> dict:
        if not text:
            return {}
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            pass
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                pass
        return {}

    @staticmethod
    def _to_number(value) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(float(value), 0.0) if math.isfinite(value) else 0.0
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
            if match:
                value = float(match.group())
                return max(value, 0.0) if math.isfinite(value) else 0.0
        return 0.0

    def _validate_split(self, data: dict, budget: float) -> dict:
        split = {name: self._to_number(data.get(name)) for name in SPECIALISTS}
        total = sum(split.values())
        if any(v <= 0 for v in split.values()) or abs(total - budget) > budget * 0.15:
            return {name: round(budget * pct, 2) for name, pct in DEFAULT_SPLIT.items()}
        # Normalise small rounding drift so the split sums exactly to the budget
        return {name: round(v * budget / total, 2) for name, v in split.items()}

    def _category_cost(self, name: str, data: dict) -> float:
        items = data.get(LIST_KEYS[name])
        item_sum = sum(self._to_number(i.get("cost")) for i in items if isinstance(i, dict)) \
            if isinstance(items, list) else 0.0
        declared = self._to_number(data.get("total_cost"))
        # Trust the larger figure so an under-reported total_cost can't hide an overspend
        return max(declared, item_sum)

    def _build_itinerary(self, city: str, data: dict, results: dict) -> Itinerary:
        items = []
        raw_items = data.get("items")
        for raw in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(raw, dict):
                continue
            category = str(raw.get("category", "activity")).lower()
            if category not in ("transport", "dining", "activity"):
                category = "activity"
            items.append(ItineraryItem(
                time=str(raw.get("time", "")),
                title=str(raw.get("title", "Untitled")),
                description=str(raw.get("description", "")),
                cost=self._to_number(raw.get("cost")),
                category=category,
            ))
        summary = str(data.get("summary", ""))
        expected = round(sum(self._category_cost(name, results[name]) for name in SPECIALISTS), 2)
        valid_times = all(re.fullmatch(r"(?:0[89]|1\d|2[01]):[0-5]\d|22:00", item.time) for item in items)
        # Do not let the planner silently omit costs or invent cheaper prices.
        if not items or not valid_times or abs(sum(item.cost for item in items) - expected) > 0.01:
            items = self._fallback_items(results)
            summary = f"A day exploring {city}, assembled from the specialist agents' suggestions. Times are suggested and travel durations are unverified."
        items.sort(key=lambda i: i.time)
        total = round(sum(i.cost for i in items), 2)
        return Itinerary(city=city, items=items, total_cost=total, summary=summary)

    def _fallback_items(self, results: dict) -> list[ItineraryItem]:
        """Deterministic timeline used when the planner's assembly output is unusable."""
        items = []
        for s in results["transport"].get("suggestions") or []:
            if isinstance(s, dict):
                items.append(ItineraryItem(time="08:00", title=str(s.get("name", "Transport")),
                                           description=str(s.get("description", "")),
                                           cost=self._to_number(s.get("cost")), category="transport"))
        default_meal_times = {"breakfast": "08:30", "lunch": "13:00", "dinner": "19:30"}
        for m in results["dining"].get("meals") or []:
            if isinstance(m, dict):
                meal = str(m.get("meal", "meal")).lower()
                items.append(ItineraryItem(time=str(m.get("time") or default_meal_times.get(meal, "12:00")),
                                           title=f"{meal.title()} at {m.get('restaurant', 'a local spot')}",
                                           description=str(m.get("description", "")),
                                           cost=self._to_number(m.get("cost")), category="dining"))
        activity_slots = ["09:30", "11:00", "14:30", "16:30", "21:00"]
        for slot, a in zip(activity_slots, results["activities"].get("activities") or []):
            if isinstance(a, dict):
                items.append(ItineraryItem(time=slot, title=str(a.get("name", "Activity")),
                                           description=str(a.get("description", "")),
                                           cost=self._to_number(a.get("cost")), category="activity"))
        return items
