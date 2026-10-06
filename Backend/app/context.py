import asyncio
import os

import httpx


async def fetch_context():
    """A missing source stays unknown. No invented traffic or environmental state."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        async def weather():
            try:
                response = await client.get("https://api.open-meteo.com/v1/forecast", params={"latitude": 19.4624, "longitude": -99.1297, "hourly": "temperature_2m,relative_humidity_2m,precipitation", "timezone": "America/Mexico_City", "forecast_days": 1})
                response.raise_for_status()
                from datetime import datetime
                from zoneinfo import ZoneInfo
                hour = datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%dT%H:00")
                hourly = response.json()["hourly"]
                i = hourly["time"].index(hour)
                return {"precipitation_mm": hourly["precipitation"][i], "temp_c": hourly["temperature_2m"][i], "humidity": hourly["relative_humidity_2m"][i]}
            except (httpx.HTTPError, KeyError, ValueError, IndexError):
                return {"precipitation_mm": None, "temp_c": None, "humidity": None}

        async def traffic():
            key = os.getenv("TOMTOM_API_KEY")
            if not key:
                return None
            try:
                response = await client.get("https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json", params={"key": key, "point": "19.4624,-99.1297", "unit": "KMPH"})
                response.raise_for_status()
                data = response.json()["flowSegmentData"]
                return max(0.0, min(1.0, data["currentSpeed"] / data["freeFlowSpeed"])) if data["freeFlowSpeed"] > 0 else None
            except (httpx.HTTPError, KeyError, ValueError, ZeroDivisionError):
                return None
        climate, density = await asyncio.gather(weather(), traffic())
    return {**climate, "traffic_density": density, "env_alert_active": None}
