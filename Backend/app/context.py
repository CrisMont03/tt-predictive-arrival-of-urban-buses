"""Real providers, short-lived cache and auditable unavailable values (never zero)."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import os
import logging
import re
from zoneinfo import ZoneInfo

import httpx

from .environmental import CAME_URL, environmental_state
from .traffic_geometry import summarize_geometry

LATITUDE, LONGITUDE = 19.4624, -99.1297
TRAFFIC_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


class RedactAPIKey(logging.Filter):
    def filter(self, record):
        # httpx's INFO request logger includes query strings by default.
        record.msg = re.sub(r'''([?&]key=)[^&\s"']+''', r"\1[REDACTED]", record.getMessage())
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(RedactAPIKey())


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def setting(name, default, minimum, maximum):
    raw = os.getenv(name)
    result = default if raw in (None, "") else float(raw)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"Invalid {name}")
    return result


def cached_sources_valid(sources, now):
    if sources is None:
        return True  # Legacy snapshots; normal prediction expiration still applies.
    if not sources:
        return False
    try:
        return all(datetime.fromisoformat(source["valid_until"]) > now for source in sources.values())
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def traffic_value(payload, minimum_confidence=0.5):
    data = payload["flowSegmentData"]
    current, free, confidence = data["currentSpeed"], data["freeFlowSpeed"], data["confidence"]
    if not all(number(value) for value in (current, free, confidence)):
        raise ValueError("invalid_traffic_numbers")
    if current < 0 or free <= 0 or not 0 <= confidence <= 1:
        raise ValueError("invalid_traffic_range")
    if confidence < minimum_confidence:
        raise ValueError("low_confidence")
    if data.get("roadClosure") is not False:
        raise ValueError("closed_or_unknown_road")
    coordinates = data["coordinates"]["coordinate"]
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("missing_segment")
    if any(not isinstance(point, dict) or not number(point.get("latitude")) or
           not number(point.get("longitude")) or not -90 <= point["latitude"] <= 90 or
           not -180 <= point["longitude"] <= 180 for point in coordinates):
        raise ValueError("invalid_segment")
    return min(1.0, current / free), {
        "current_speed_kmh": current, "free_flow_speed_kmh": free,
        "confidence": confidence, "road_closure": False,
        "segment_coordinates": coordinates,
        "geometry": summarize_geometry(coordinates, LATITUDE, LONGITUDE),
        "definition": "min(1,currentSpeed/freeFlowSpeed); 1=free_flow,0=stopped",
    }


class ContextService:
    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = {}

    async def cached(self, key, ttl, provider):
        now = self.clock()
        stored = self.cache.get(key)
        if stored and now < stored["expires"]:
            return deepcopy(stored["value"])
        value, metadata = await provider()
        checked = self.clock()
        metadata = {**metadata, "checked_at": checked.isoformat()}
        expiry = checked + timedelta(seconds=ttl if metadata["status"] == "available" else 15)
        if metadata.get("valid_until"):
            expiry = min(expiry, datetime.fromisoformat(metadata["valid_until"]))
        metadata["valid_until"] = expiry.isoformat()
        result = (value, metadata)
        self.cache[key] = {"expires": expiry, "value": deepcopy(result)}
        return result

    @staticmethod
    async def get(client, url, **kwargs):
        response = await client.get(url, **kwargs)
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("response_too_large")
        return response

    async def weather(self, client):
        try:
            response = await self.get(client, WEATHER_URL, params={
                "latitude": LATITUDE, "longitude": LONGITUDE,
                "hourly": "temperature_2m,relative_humidity_2m,precipitation",
                "timezone": "America/Mexico_City", "forecast_days": 1,
            })
            hour = self.clock().astimezone(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%dT%H:00")
            hourly = response.json()["hourly"]
            index = hourly["time"].index(hour)
            values = {"precipitation_mm": hourly["precipitation"][index],
                      "temp_c": hourly["temperature_2m"][index],
                      "humidity": hourly["relative_humidity_2m"][index]}
            if not all(number(v) for v in values.values()) or not (
                values["precipitation_mm"] >= 0 and 0 <= values["humidity"] <= 100
            ):
                raise ValueError("invalid_weather")
            return values, {"status": "available", "source_url": WEATHER_URL,
                            "observation_at": hour, "timezone": "America/Mexico_City"}
        except (httpx.HTTPError, KeyError, ValueError, IndexError, TypeError):
            return {"precipitation_mm": None, "temp_c": None, "humidity": None}, {
                "status": "unavailable", "reason": "weather_request_or_schema", "source_url": WEATHER_URL}

    async def traffic(self, client, api_key, minimum_confidence):
        metadata = {"source_url": TRAFFIC_URL, "point": {"latitude": LATITUDE, "longitude": LONGITUDE}}
        if not api_key:
            return None, {**metadata, "status": "unavailable", "reason": "missing_api_key"}
        try:
            response = await self.get(client, TRAFFIC_URL, params={
                "key": api_key, "point": f"{LATITUDE},{LONGITUDE}", "unit": "KMPH",
            })
            value, detail = traffic_value(response.json(), minimum_confidence)
            return value, {**metadata, **detail, "status": "available", "reason": None}
        except httpx.HTTPStatusError as error:
            return None, {**metadata, "status": "unavailable", "reason": f"http_{error.response.status_code}"}
        except (httpx.HTTPError, KeyError, ValueError, TypeError, IndexError):
            # Never log exceptions/request URLs: TomTom puts the secret in the query.
            return None, {**metadata, "status": "unavailable", "reason": "traffic_request_or_schema"}

    async def environmental(self, client, old_notice_hours):
        try:
            response = await self.get(client, CAME_URL)
            value, metadata = environmental_state(response.text, self.clock(), old_notice_hours)
            return value, {"source_url": CAME_URL, **metadata}
        except httpx.HTTPStatusError as error:
            return None, {"source_url": CAME_URL, "status": "unavailable",
                          "reason": f"http_{error.response.status_code}"}
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
            return None, {"source_url": CAME_URL, "status": "unavailable",
                          "reason": "environmental_request_or_schema"}

    async def fetch(self, client=None):
        if client is None:
            async with httpx.AsyncClient(timeout=3.5, follow_redirects=False) as owned:
                return await self.fetch(owned)
        api_key = os.getenv("TOMTOM_API_KEY", "").strip()
        confidence = setting("TOMTOM_MIN_CONFIDENCE", 0.5, 0, 1)
        traffic_ttl = setting("TOMTOM_CACHE_SECONDS", 60, 0, 300)
        env_ttl = setting("ENV_ALERT_CACHE_SECONDS", 300, 0, 900)
        old_notice_hours = setting("ENV_ALERT_OLD_NOTICE_HOURS", 24, 1, 8760)
        now = self.clock().astimezone(ZoneInfo("America/Mexico_City"))
        weather, traffic, environmental = await asyncio.gather(
            self.cached(("weather", now.strftime("%Y%m%d%H")), 60, lambda: self.weather(client)),
            self.cached(("traffic", api_key, confidence), traffic_ttl,
                        lambda: self.traffic(client, api_key, confidence)),
            self.cached(("environmental", old_notice_hours), env_ttl,
                        lambda: self.environmental(client, old_notice_hours)),
        )
        self.cache = {key: item for key, item in self.cache.items() if item["expires"] > self.clock()}
        return {**weather[0], "traffic_density": traffic[0], "env_alert_active": environmental[0],
                "_sources": {"weather": weather[1], "traffic": traffic[1], "environmental": environmental[1]}}


_service = ContextService()


async def fetch_context():
    return await _service.fetch()
