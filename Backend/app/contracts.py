from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: Literal["rio_consulado"] = "rio_consulado"
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class Context(BaseModel):
    hour: int
    day_of_week: int
    is_weekend: int
    is_holiday: int | None
    precipitation_mm: float | None = None
    temp_c: float | None = None
    humidity: float | None = None
    traffic_density: float | None = None
    env_alert_active: int | None = None


class PredictResponse(BaseModel):
    query_id: str
    station_id: str
    waiting_time_min: float
    confidence_interval: tuple[float, float]
    model_version: str
    timestamp: datetime
    requested_at: datetime
    expires_at: datetime
    source: Literal["mock", "cache", "model"]
    simulation: bool
    context: Context


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    actual_min: float = Field(ge=0, le=180, allow_inf_nan=False)
