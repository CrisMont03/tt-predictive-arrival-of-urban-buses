from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: Literal["rio_consulado"] = "rio_consulado"
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class Context(BaseModel):
    hour: int = Field(ge=0, le=23)
    day_of_week: int = Field(ge=0, le=6)
    is_weekend: Literal[0, 1]
    is_holiday: Literal[0, 1] | None
    precipitation_mm: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    temp_c: float | None = Field(default=None, allow_inf_nan=False)
    humidity: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    traffic_density: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    env_alert_active: Literal[0, 1] | None = None


class PredictResponse(BaseModel):
    query_id: str
    station_id: str
    waiting_time_min: float = Field(ge=0, le=45, allow_inf_nan=False)
    confidence_interval: tuple[Annotated[float, Field(ge=0, allow_inf_nan=False)], Annotated[float, Field(ge=0, allow_inf_nan=False)]]
    model_version: str = Field(min_length=1)
    timestamp: datetime
    requested_at: datetime
    expires_at: datetime
    source: Literal["mock", "cache", "model"]
    simulation: bool
    context: Context
    context_sources: dict[str, dict[str, Any]] | None = None

    @model_validator(mode="after")
    def valid_snapshot(self):
        if self.confidence_interval[0] > self.confidence_interval[1]:
            raise ValueError("Intervalo invertido")
        if any(value.utcoffset() is None for value in (self.timestamp, self.requested_at, self.expires_at)):
            raise ValueError("Las fechas requieren zona horaria")
        if self.expires_at <= self.timestamp:
            raise ValueError("La expiración debe ser posterior a la generación")
        if (self.source == "mock" and not self.simulation) or (self.source == "model" and self.simulation):
            raise ValueError("Fuente y simulación inconsistentes")
        return self


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    actual_min: float = Field(ge=0, le=180, allow_inf_nan=False)
