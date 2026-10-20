import asyncio
from datetime import datetime, timedelta, timezone
from html import escape
import json
import logging

import httpx
import pytest

from app.context import ContextService, cached_sources_valid, traffic_value
from app.environmental import environmental_state
from app.traffic_geometry import summarize_geometry

NOW = datetime(2026, 10, 17, 19, tzinfo=timezone.utc)
REGION = "EN LA ZONA METROPOLITANA DEL VALLE DE MÉXICO (ZMVM)"
ACTIVATION = f"SE ACTIVA LA FASE I DE CONTINGENCIA AMBIENTAL ATMOSFÉRICA POR OZONO {REGION}"
SUSPENSION = f"SE SUSPENDE LA CONTINGENCIA AMBIENTAL ATMOSFÉRICA POR OZONO {REGION}"


def article(title=ACTIVATION, date="2026-10-17 12:00:00", suffix="aviso"):
    return (f'<article><time datetime="{date}">Fecha de publicación</time>'
            f'<h2>{escape(title)}</h2><a href="/comisionambiental/prensa/{suffix}?idiom=es">Leer</a></article>')


def traffic():
    return {"flowSegmentData": {"currentSpeed": 20, "freeFlowSpeed": 40,
            "confidence": 0.9, "roadClosure": False,
            "coordinates": {"coordinate": [{"latitude": 19.4624, "longitude": -99.1297}]}}}


@pytest.mark.parametrize("title,state", [
    (ACTIVATION, 1), (SUSPENSION, 0),
    (f"SE MANTIENE LA FASE I DE CONTINGENCIA AMBIENTAL ATMOSFÉRICA POR OZONO {REGION}", 1),
    (f"CONTINÚA LA FASE I DE CONTINGENCIA AMBIENTAL ATMOSFÉRICA POR OZONO {REGION}", 1),
])
def test_environmental_supported_declarations(title, state):
    result, source = environmental_state(article(title), NOW)
    assert result == state and source["status"] == "available"
    assert source["published_at"] == "2026-10-17T12:00:00-06:00"


def test_official_jquery_archive_is_decoded_without_executing_javascript():
    payload = '$("#prensa").append(' + json.dumps(article(SUSPENSION)) + ');'
    result, metadata = environmental_state(payload, NOW)
    assert result == 0 and metadata["source_url"].startswith("https://www.gob.mx/")


def test_latest_datetime_wins_not_document_order_or_a_keyword_in_body():
    payload = article(SUSPENSION, "2026-10-17 12:30:00") + article(ACTIVATION, "2026-10-17 09:00:00")
    assert environmental_state(payload, NOW)[0] == 0
    # An informational note is not a declaration, even if the body mentions one.
    payload = article("Programa de contingencias y educación ambiental") + "<p>Se activa contingencia ambiental</p>"
    assert environmental_state(payload, NOW)[0] is None


@pytest.mark.parametrize("title,date,reason", [
    (ACTIVATION, "2026-10-18 12:00:00", "future_notice"),
    (ACTIVATION.replace("ATMOSFÉRICA", "ATMOSFÉRICA REGIONAL"), "2026-10-17 12:00:00", "uncertain_geographic_scope"),
    (ACTIVATION.replace(REGION, "EN EL VALLE DE TOLUCA"), "2026-10-17 12:00:00", "uncertain_geographic_scope"),
    (f"Balance de la contingencia ambiental {REGION}", "2026-10-17 12:00:00", "unrecognized_notice"),
    (f"CONTINÚA LA SUSPENSIÓN DE LA CONTINGENCIA AMBIENTAL {REGION}", "2026-10-17 12:00:00", "unrecognized_notice"),
    (f"SE ACTIVA LA FASE PREVENTIVA DE CONTINGENCIA AMBIENTAL {REGION}", "2026-10-17 12:00:00", "unrecognized_notice"),
    (SUSPENSION.replace("SE SUSPENDE", "SE SUSPENDE PARCIALMENTE"), "2026-10-17 12:00:00", "ambiguous_notice"),
    (ACTIVATION, "bad-date", "invalid_notice_date"),
])
def test_unknown_environmental_data_never_becomes_zero(title, date, reason):
    value, metadata = environmental_state(article(title, date), NOW)
    assert value is None and metadata["reason"] == reason


@pytest.mark.parametrize("title,expected", [(SUSPENSION, 0), (ACTIVATION, 1)])
def test_old_last_notice_retains_its_published_state_with_explicit_warning(title, expected):
    result, metadata = environmental_state(article(title, "2026-09-13 15:45:00"), NOW)
    assert result == expected and metadata["status"] == "available"
    assert metadata["state_basis"] == "latest_recognized_official_notice"
    assert metadata["notice_is_old"] is True
    assert metadata["warning"] == "old_notice_carried_forward"
    assert metadata["published_at"] == "2026-09-13T15:45:00-06:00"
    assert "valid_until" not in metadata  # Retrieval cache assigns this, not the old publication.


def test_newer_activation_or_suspension_replaces_old_state_not_unrelated_news():
    old = article(SUSPENSION, "2026-09-13 15:45:00")
    activation = article(ACTIVATION, "2026-10-17 09:00:00")
    suspension = article(SUSPENSION, "2026-10-17 12:00:00")
    news = article("Proyecto de movilidad de CAMe", "2026-10-17 12:30:00")
    assert environmental_state(old + news, NOW)[0] == 0
    assert environmental_state(old + activation + news, NOW)[0] == 1
    assert environmental_state(suspension + activation + old + news, NOW)[0] == 0


def test_newer_ambiguous_or_regional_notice_blocks_old_global_state():
    payload = article(SUSPENSION, "2026-10-17 09:00:00") + article(
        ACTIVATION.replace("ATMOSFÉRICA", "ATMOSFÉRICA REGIONAL"), "2026-10-17 12:00:00")
    assert environmental_state(payload, NOW)[0] is None
    assert environmental_state(article(SUSPENSION) + article(ACTIVATION), NOW)[1]["reason"] == "conflicting_notices"


def test_ozone_suspension_does_not_deactivate_a_different_pollutant_episode():
    payload = article(SUSPENSION) + article(ACTIVATION.replace("OZONO", "PM2.5"), "2026-10-17 10:00:00")
    assert environmental_state(payload, NOW)[1]["reason"] == "multiple_pollutant_episodes"


def test_plural_contingencies_are_not_skipped_as_irrelevant():
    payload = article(f"SE SUSPENDEN LAS CONTINGENCIAS AMBIENTALES {REGION}")
    assert environmental_state(payload, NOW)[0] == 0


def test_missing_structure_and_dates_do_not_pass_as_no_contingency():
    with pytest.raises(ValueError):
        environmental_state("<html>403 Forbidden</html>", NOW)
    assert environmental_state(article(ACTIVATION, ""), NOW)[1]["reason"] == "undated_notice"


def test_speed_ratio_is_not_vehicle_count_and_real_zero_is_preserved():
    value, metadata = traffic_value(traffic())
    assert value == 0.5 and metadata["free_flow_speed_kmh"] == 40
    payload = traffic()
    payload["flowSegmentData"]["currentSpeed"] = 0
    assert traffic_value(payload)[0] == 0
    payload["flowSegmentData"]["currentSpeed"] = 50
    assert traffic_value(payload)[0] == 1


@pytest.mark.parametrize("field,value", [
    ("currentSpeed", -1), ("currentSpeed", True), ("currentSpeed", "20"),
    ("currentSpeed", float("nan")), ("freeFlowSpeed", 0),
    ("confidence", 0.1), ("confidence", 2), ("roadClosure", True),
    ("roadClosure", None), ("coordinates", {}),
])
def test_bad_low_confidence_or_closed_segment_is_not_valid_traffic(field, value):
    payload = traffic()
    payload["flowSegmentData"][field] = value
    with pytest.raises((ValueError, KeyError)):
        traffic_value(payload)


def responder(request):
    if request.url.host == "api.tomtom.com":
        return httpx.Response(200, json=traffic())
    if request.url.host == "www.gob.mx":
        return httpx.Response(200, text=article(SUSPENSION))
    return httpx.Response(200, json={"hourly": {"time": ["2026-10-17T13:00"],
        "precipitation": [0], "temperature_2m": [20], "relative_humidity_2m": [60]}})


def fetch(service, handler=responder):
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await service.fetch(client)
    return asyncio.run(execute())


def test_combined_real_adapters_cache_validated_values_without_exposing_key(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "secret-test-key")
    calls = []
    def handler(request):
        calls.append(request.url.host)
        return responder(request)
    service = ContextService(clock=lambda: NOW)
    first = fetch(service, handler)
    assert (first["traffic_density"], first["env_alert_active"], first["temp_c"]) == (0.5, 0, 20)
    assert "secret-test-key" not in json.dumps(first)
    first["_sources"]["traffic"]["confidence"] = -1
    second = fetch(service, handler)
    assert len(calls) == 3 and second["_sources"]["traffic"]["confidence"] == 0.9


def test_missing_key_does_not_make_a_tomtom_request(monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    def handler(request):
        assert request.url.host != "api.tomtom.com"
        return responder(request)
    result = fetch(ContextService(clock=lambda: NOW), handler)
    assert result["traffic_density"] is None
    assert result["_sources"]["traffic"]["reason"] == "missing_api_key"
    assert result["env_alert_active"] == 0


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_provider_http_errors_do_not_discard_other_sources(monkeypatch, status):
    monkeypatch.setenv("TOMTOM_API_KEY", "test-key")
    def handler(request):
        if request.url.host in {"api.tomtom.com", "www.gob.mx"}:
            return httpx.Response(status)
        return responder(request)
    result = fetch(ContextService(clock=lambda: NOW), handler)
    assert result["traffic_density"] is None and result["env_alert_active"] is None
    assert result["temp_c"] == 20
    assert result["_sources"]["traffic"]["reason"] == f"http_{status}"


def test_expired_success_is_not_served_when_provider_becomes_unavailable(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "test-key")
    clock = [NOW]
    service = ContextService(clock=lambda: clock[0])
    assert fetch(service)["traffic_density"] == 0.5
    clock[0] += timedelta(minutes=6)
    def failed(request):
        raise httpx.ConnectError("offline", request=request)
    result = fetch(service, failed)
    assert result["traffic_density"] is None and result["env_alert_active"] is None
    assert result["_sources"]["environmental"]["status"] == "unavailable"


def test_schema_errors_and_timeout_stay_unknown(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "test-key")
    def bad(request):
        if request.url.host == "api.tomtom.com":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, text="not JSON, HTML, or an official notice")
    result = fetch(ContextService(clock=lambda: NOW), bad)
    assert all(result[key] is None for key in ("traffic_density", "env_alert_active", "temp_c"))


def test_configuration_rejects_long_or_non_finite_ttls(monkeypatch):
    monkeypatch.setenv("ENV_ALERT_CACHE_SECONDS", "604800")
    with pytest.raises(ValueError, match="ENV_ALERT_CACHE_SECONDS"):
        fetch(ContextService(clock=lambda: NOW))


def test_standard_request_logs_redact_api_key(monkeypatch, caplog):
    monkeypatch.setenv("TOMTOM_API_KEY", "private-test-key")
    caplog.set_level(logging.INFO, logger="httpx")
    fetch(ContextService(clock=lambda: NOW))
    assert "private-test-key" not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_prediction_cache_cannot_extend_provider_validity():
    assert cached_sources_valid(None, NOW)  # Backwards-compatible legacy snapshot.
    assert not cached_sources_valid({}, NOW)
    assert cached_sources_valid({"traffic": {"valid_until": (NOW + timedelta(seconds=1)).isoformat()}}, NOW)
    assert not cached_sources_valid({"traffic": {"valid_until": NOW.isoformat()}}, NOW)
    assert not cached_sources_valid({"traffic": {"valid_until": "invalid"}}, NOW)
    assert not cached_sources_valid({"traffic": {}}, NOW)


def test_old_bulletin_uses_short_retrieval_cache_not_expired_publication_date(monkeypatch):
    monkeypatch.setenv("ENV_ALERT_MAX_AGE_HOURS", "1")  # Deprecated; no longer invalidates notices.
    clock = [NOW]
    calls = []
    service = ContextService(clock=lambda: clock[0])
    def handler(request):
        if request.url.host == "www.gob.mx":
            calls.append(request.url.host)
            return httpx.Response(200, text=article(SUSPENSION, "2026-09-13 15:45:00"))
        return responder(request)
    first = fetch(service, handler)
    assert first["env_alert_active"] == 0
    assert first["_sources"]["environmental"]["valid_until"] == (NOW + timedelta(seconds=300)).isoformat()
    clock[0] += timedelta(seconds=60)
    assert fetch(service, handler)["env_alert_active"] == 0
    assert len(calls) == 1
    clock[0] += timedelta(seconds=301)
    assert fetch(service, handler)["env_alert_active"] == 0
    assert len(calls) == 2


def test_geometry_summary_reports_distance_and_orientation_without_certifying_direction():
    points = [{"latitude": 19.4624, "longitude": -99.1307},
              {"latitude": 19.4624, "longitude": -99.1287}]
    summary = summarize_geometry(points, 19.4624, -99.1297)
    assert summary["distance_to_query_point_m"] == 0
    assert summary["polyline_bearing_deg"] == 90
    assert summary["travel_direction_verified"] is False
    reverse = summarize_geometry(points[::-1], 19.4624, -99.1297)
    assert reverse["polyline_bearing_deg"] == 270
    assert reverse["travel_direction_verified"] is False
    assert summarize_geometry([points[0]], 19.4624, -99.1297)["polyline_bearing_deg"] is None
    assert summarize_geometry(points, 19.4634, -99.1297)["distance_to_query_point_m"] == 111.3
