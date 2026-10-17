"""Diagnostics for the MySair integration (D1).

Dumps the in-memory state (config entry, API session, AWS credentials, MQTT
client state) for debugging from the Home Assistant UI, redacting any
credential or token before exposing it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT_ENTRY = {"email", "password", "access_token", "refresh_token"}
TO_REDACT_API = {
    "access_token",
    "refresh_token_value",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_security_token",
    "aws_mqtt_user",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Build the diagnostics dump for a MySair config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    mqtt_client = data["mqtt"]

    api_state = {
        "access_token": api.access_token,
        "refresh_token_value": api.refresh_token_value,
        **(api.aws_credentials or {}),
    }

    mqtt_state = None
    if mqtt_client is not None:
        last_message_at = mqtt_client.last_message_at
        mqtt_state = {
            "connected": mqtt_client.connected,
            "reconnect_attempt": mqtt_client.reconnect_attempt,
            "last_message_at": last_message_at.isoformat() if last_message_at else None,
            "total_reconnects": mqtt_client.total_reconnects,
            "parse_strict_count": mqtt_client.parse_strict_count,
            "parse_fallback_count": mqtt_client.parse_fallback_count,
            "parse_error_count": mqtt_client.parse_error_count,
            "last_close_code": mqtt_client.last_close_code,
            "last_close_msg": mqtt_client.last_close_msg,
        }

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT_ENTRY),
        "installations": data["installations"],
        "devices": data["devices"],
        "api": async_redact_data(api_state, TO_REDACT_API),
        "mqtt": mqtt_state,
        "zone_capabilities": _zone_capabilities(data.get("coordinator")),
    }


_CAPABILITY_FIELDS = (
    "allow_heat",
    "allow_cool",
    "allow_floor",
    "allow_fan",
    "allow_timer",
    "allow_programs",
)


def _zone_capabilities(coordinator) -> dict[str, Any]:
    """Capabilities of each zone from the last status (no personal data)."""
    if coordinator is None:
        return {}
    return {
        ctl: {
            zone_id: {field: zone.get(field) for field in _CAPABILITY_FIELDS}
            for zone_id, zone in zones.items()
        }
        for ctl, zones in coordinator.zones().items()
    }
