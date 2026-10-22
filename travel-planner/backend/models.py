from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class UserInput(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    interests: list[str] = Field(default_factory=list, max_length=20)
    budget: float = Field(gt=0, le=1000000, allow_inf_nan=False)

    @field_validator("city")
    @classmethod
    def clean_city(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter a city name")
        return value

    @field_validator("interests")
    @classmethod
    def clean_interests(cls, values):
        if any(len(value) > 100 for value in values):
            raise ValueError("Each interest must be at most 100 characters")
        return [value.strip() for value in values if value.strip()]


class AgentMessage(BaseModel):
    """The single message schema used for ALL inter-agent communication."""

    from_agent: str  # user | planner | transport | dining | activities | system
    to_agent: str
    message_type: str  # task_assignment | response | revision_request | revised_response | final_plan | status | error
    content: str
    metadata: dict = Field(default_factory=dict)
    timestamp: str = ""

    def model_post_init(self, __context) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class ItineraryItem(BaseModel):
    time: str
    title: str
    description: str = ""
    cost: float = 0
    category: str  # transport | dining | activity


class Itinerary(BaseModel):
    city: str
    items: list[ItineraryItem]
    total_cost: float
    summary: str = ""
